"""Runtime state, broadcasts, and task runners for the OMADS GUI."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

from fastapi import WebSocket

from .review_flow import (
    ReviewRuntimeContext,
    build_manual_synthesis_prompt as _build_manual_synthesis_prompt,
    review_display_name as _review_display_name,
    review_focus_description as _review_focus_description,
    review_runtime_label as _review_runtime_label,
    run_claude_manual_review_step as _review_run_claude_manual_review_step,
    run_claude_manual_synthesis_step as _review_run_claude_manual_synthesis_step,
    run_codex_manual_review_step as _review_run_codex_manual_review_step,
    run_codex_manual_synthesis_step as _review_run_codex_manual_synthesis_step,
)
from .streaming import (
    parse_claude_stream_line,
    parse_codex_jsonl_line,
)
from .state import (
    _append_timeline_event,
    _build_cli_env,
    _build_process_failure_text,
    _get_active_project_id,
    _get_chat_session,
    _get_setting,
    _get_settings_snapshot,
    _load_project_memory,
    _save_project_memory,
    _set_chat_session,
    _update_claude_limit_status,
)

# Active WebSocket connections (lock protects add/remove/iterate)
_connections_lock = threading.Lock()
_connections: list[WebSocket] = []

# Running process used for stop handling; lock protects against race conditions
_process_lock = threading.Lock()
_active_process: subprocess.Popen | None = None
_task_cancelled: bool = False
_last_files_changed: list[str] = []  # Most recently changed files (for "Last task" review scope)
_pending_review_fixes: dict[str, str] = {}  # {repo_path: fixes_text} per project


class _ReservedProcessSlot:
    """Sentinel used while a worker thread is still starting a subprocess."""

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        return None


_RESERVED_PROCESS_SLOT = _ReservedProcessSlot()


def _try_reserve_task_slot() -> bool:
    """Reserve the global task slot before handing work to a background thread."""
    global _active_process, _task_cancelled
    with _process_lock:
        if _active_process and _active_process.poll() is None:
            return False
        _task_cancelled = False
        _active_process = _RESERVED_PROCESS_SLOT
        return True


def _release_reserved_task_slot() -> None:
    """Release a reservation that never reached the subprocess start stage."""
    global _active_process
    with _process_lock:
        if _active_process is _RESERVED_PROCESS_SLOT:
            _active_process = None


def _capture_repo_change_snapshot(target_repo: str) -> dict[str, object]:
    """Capture a lightweight snapshot of the current Git working tree."""
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=target_repo,
            timeout=10,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {"status_lines": [], "changed_files": [], "diff_text": ""}

        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            cwd=target_repo,
            timeout=15,
        )
        status_lines = [line.rstrip() for line in status.stdout.splitlines() if line.strip()]
        changed_files = [line[3:].strip() if len(line) > 3 else line.strip() for line in status_lines]

        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--submodule=diff", "HEAD", "--"],
            capture_output=True,
            text=True,
            cwd=target_repo,
            timeout=20,
        )
        return {
            "status_lines": status_lines,
            "changed_files": changed_files,
            "diff_text": diff.stdout,
        }
    except Exception:
        return {"status_lines": [], "changed_files": [], "diff_text": ""}


def _repo_snapshot_changed(before: dict[str, object], after: dict[str, object]) -> bool:
    """Return whether the repo snapshot changed during one task run."""
    return (
        before.get("status_lines") != after.get("status_lines")
        or before.get("diff_text") != after.get("diff_text")
    )


def _merge_changed_files(*groups: list[str]) -> list[str]:
    """Merge changed file lists while preserving order."""
    merged: list[str] = []
    for group in groups:
        for path in group:
            if path and path not in merged:
                merged.append(path)
    return merged


async def broadcast(msg: dict) -> None:
    """Send one message to all connected clients."""
    with _connections_lock:
        snapshot = list(_connections)
    dead = []
    for ws in snapshot:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    if dead:
        with _connections_lock:
            for ws in dead:
                try:
                    _connections.remove(ws)
                except ValueError:
                    pass


def broadcast_sync(msg: dict, *, proj_id_override: str | None = None) -> None:
    """Synchronous wrapper for `broadcast` used from worker threads."""
    # Persist all live runtime events through the unified per-project timeline.
    proj_id = proj_id_override or _get_active_project_id()
    if proj_id:
        try:
            _append_timeline_event(proj_id, dict(msg))
        except Exception:
            pass
    with _connections_lock:
        snapshot = list(_connections)
    for ws in snapshot:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(msg), _loop)
        except Exception:
            pass


_loop: asyncio.AbstractEventLoop | None = None


def _run_builder_session_thread(ws: WebSocket, user_text: str) -> None:
    """Route one chat task to the currently selected primary builder."""
    builder_agent = _get_setting("builder_agent", "claude")
    if builder_agent == "codex":
        _run_codex_session_thread(ws, user_text)
    else:
        _run_claude_session_thread(ws, user_text)


def _forward_claude_stream_line(
    line: str,
    *,
    agent_label: str,
    send: callable,
    text_buffer: list[str] | None = None,
    rate_limit_source: str = "task_stream",
) -> tuple[str | None, str]:
    """Parse one Claude stream-json line and emit the corresponding UI events."""
    session_id: str | None = None
    result_text = ""
    for event in parse_claude_stream_line(line):
        kind = event["kind"]
        if kind == "session_id" and not session_id:
            session_id = event["session_id"]
        elif kind == "tool":
            send({
                "type": "stream_tool",
                "agent": agent_label,
                "tool": event["tool"],
                "description": event["description"],
                "detail": event["detail"],
            })
        elif kind == "text":
            send({"type": "stream_text", "agent": agent_label, "text": event["text"]})
            if text_buffer is not None:
                text_buffer.append(event["text"])
        elif kind == "thinking":
            send({"type": "stream_thinking", "agent": agent_label, "text": event["text"]})
        elif kind == "tool_result":
            send({
                "type": "stream_result",
                "agent": agent_label,
                "text": event["text"],
                "is_error": event["is_error"],
            })
        elif kind == "result":
            result_text = event["text"]
        elif kind == "rate_limit":
            limit = _update_claude_limit_status(event["rate_limit_info"], source=rate_limit_source)
            send({"type": "claude_limit_update", "limit": limit})
    return session_id, result_text


def _forward_codex_stream_line(
    line: str,
    *,
    agent_label: str,
    send: callable,
    text_buffer: list[str],
) -> None:
    """Parse one Codex JSONL line and emit readable text chunks."""
    for text in parse_codex_jsonl_line(line):
        text_buffer.append(text)
        send({"type": "stream_text", "agent": agent_label, "text": text})

def _run_claude_session_thread(ws: WebSocket, user_text: str) -> None:
    """Run one task through Claude Code and trigger the automatic breaker step."""
    import time as _time

    settings_snapshot = _get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    repo_key = str(Path(target_repo).resolve())
    model = settings_snapshot.get("claude_model", "sonnet")
    effort = settings_snapshot.get("claude_effort", "high")
    auto_review = settings_snapshot.get("auto_review", True)
    agent_label = "Claude Code"

    # Freeze the project ID at task start so it stays correct across project switches
    _frozen_proj_id = _get_active_project_id()

    def send(msg: dict):
        broadcast_sync(msg, proj_id_override=_frozen_proj_id)

    send({"type": "agent_status", "agent": agent_label, "status": "Working..."})

    # Persist the user input through the unified timeline.
    proj_id = _frozen_proj_id
    if proj_id:
        _append_timeline_event(proj_id, {"type": "user_input", "text": user_text})

    try:
        with _process_lock:
            if _task_cancelled:
                return
        env = _build_cli_env()
        before_snapshot = _capture_repo_change_snapshot(target_repo)

        # OMADS context for Claude CLI
        omads_context = (
            "You are working inside OMADS (Orchestrated Multi-Agent Development System). "
            "The user interacts with you through a web GUI. "
            "After every code change, Codex CLI automatically reviews your code in the background. "
            "If Codex finds issues, you will receive the findings as the next message and should fix them. "
            "Respond in English.\n\n"
        )

        # Load project memory only for a new session to save tokens on --resume
        session_id = _get_chat_session(repo_key)
        if not session_id:
            project_memory = _load_project_memory(target_repo)
            if project_memory:
                omads_context += (
                    "You have the following context from previous sessions and the project:\n\n"
                    + project_memory + "\n\n"
                    "Use this context to continue seamlessly without requiring the user "
                    "to explain where the work left off."
                )

        # Claude CLI with stream-json for live output
        cmd = ["claude", "-p", user_text, "--output-format", "stream-json",
               "--verbose", "--model", model,
               "--effort", effort,
               "--append-system-prompt", omads_context]

        # Resume the session if one already exists (chat memory)
        if session_id:
            cmd.extend(["--resume", session_id])

        # --permission-mode is intentionally disabled because it triggers
        # the afk-mode beta header bug in CLI v2.1.74.
        # Permissions are controlled through ~/.claude/settings.json instead.

        if _task_cancelled:
            return
        start_time = _time.time()
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=target_repo, env=env,
        )
        with _process_lock:
            _active_process = process

        output_lines = []
        final_result = ""
        captured_session_id = None
        files_changed = []

        for line in process.stdout:
            if _task_cancelled:
                process.kill()
                send({"type": "task_stopped", "text": "Stopped."})
                break

            for event in parse_claude_stream_line(line):
                kind = event["kind"]
                if kind == "session_id" and not captured_session_id:
                    captured_session_id = event["session_id"]
                elif kind == "tool":
                    if event["tool"] in ("Write", "Edit"):
                        file_path = event.get("file_path", "")
                        if file_path and file_path not in files_changed:
                            files_changed.append(file_path)
                    send({
                        "type": "stream_tool",
                        "agent": agent_label,
                        "tool": event["tool"],
                        "description": event["description"],
                        "detail": event["detail"],
                    })
                elif kind == "text":
                    send({"type": "stream_text", "agent": agent_label, "text": event["text"]})
                    output_lines.append(event["text"])
                elif kind == "thinking":
                    send({"type": "stream_thinking", "agent": agent_label, "text": event["text"]})
                elif kind == "tool_result":
                    send({
                        "type": "stream_result",
                        "agent": agent_label,
                        "text": event["text"],
                        "is_error": event["is_error"],
                    })
                elif kind == "result":
                    final_result = event["text"]
                elif kind == "rate_limit":
                    limit = _update_claude_limit_status(event["rate_limit_info"], source="task_stream")
                    send({"type": "claude_limit_update", "limit": limit})

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        with _process_lock:
            _active_process = None
        elapsed = round(_time.time() - start_time)
        success = process.returncode == 0 and not _task_cancelled

        if not _task_cancelled and process.returncode != 0:
            result_text = final_result if final_result else "\n".join(output_lines)
            send({
                "type": "task_error",
                "text": _build_process_failure_text(
                    "Claude Code Task",
                    process.returncode,
                    result_text=result_text,
                    output_lines=output_lines,
                ),
            })
            return

        # Save the session ID for follow-up messages (chat memory)
        if captured_session_id and success:
            _set_chat_session(repo_key, captured_session_id)

        after_snapshot = _capture_repo_change_snapshot(target_repo) if success else before_snapshot
        snapshot_files = after_snapshot.get("changed_files", []) if _repo_snapshot_changed(before_snapshot, after_snapshot) else []
        files_changed = _merge_changed_files(files_changed, snapshot_files)

        # Remember the most recently changed files for the "Last task" review scope
        global _last_files_changed
        if files_changed:
            _last_files_changed = list(files_changed)

        # Show the result only if nothing was streamed live
        result_text = final_result if final_result else "\n".join(output_lines)
        if result_text and not output_lines:
            send({"type": "chat_response", "agent": agent_label, "text": result_text})

        send({"type": "agent_status", "agent": agent_label, "status": f"Done ({elapsed}s)"})

        # Update project memory with a summary for the next session
        if success and (output_lines or result_text):
            summary_parts = []
            if files_changed:
                summary_parts.append(f"Changed files: {', '.join(f[-60:] for f in files_changed[:20])}")
            # Use the latest output as a compact context summary
            recent_output = "\n".join(output_lines[-10:]) if output_lines else result_text
            summary_parts.append(f"Latest task: {user_text[:200]}")
            summary_parts.append(f"Result: {recent_output[:2000]}")
            _save_project_memory(target_repo, "\n".join(summary_parts))

        # === CODEX AUTO REVIEW ===
        # If Claude changed files and auto review is enabled, Codex reviews them
        if files_changed and auto_review and success:
            review_findings = _run_codex_auto_review(ws, target_repo, files_changed, send)

            # If Codex found issues, send the findings back to Claude
            if review_findings:
                send({"type": "agent_status", "agent": "Claude Code",
                      "status": "Fixing Codex findings..."})

                fix_prompt = (
                    "The Codex auto-reviewer found the following issues in your code. "
                    "Please fix them:\n\n" + review_findings + "\n\n"
                    "Rules: Fix ONLY the reported issues. No scope creep."
                )

                # Start a fresh Claude CLI run for the fix step
                fix_cmd = ["claude", "-p", fix_prompt, "--output-format", "stream-json",
                           "--verbose", "--model", model,
                           "--effort", effort,
                           "--append-system-prompt", omads_context]
                fix_session = _get_chat_session(repo_key)
                if fix_session:
                    fix_cmd.extend(["--resume", fix_session])

                if _task_cancelled:
                    return
                fix_process = subprocess.Popen(
                    fix_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, cwd=target_repo, env=env,
                )
                with _process_lock:
                    _active_process = fix_process

                fix_output_lines = []
                fix_session_id = None

                for fline in fix_process.stdout:
                    if _task_cancelled:
                        fix_process.kill()
                        break
                    parsed_session_id, parsed_result = _forward_claude_stream_line(
                        fline,
                        agent_label="Claude Code",
                        send=send,
                        text_buffer=fix_output_lines,
                        rate_limit_source="fix_stream",
                    )
                    if parsed_session_id and not fix_session_id:
                        fix_session_id = parsed_session_id
                    if parsed_result and not fix_output_lines:
                        send({"type": "chat_response", "agent": "Claude Code", "text": parsed_result})

                try:
                    fix_process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    fix_process.kill()
                    fix_process.wait()
                with _process_lock:
                    _active_process = None

                if fix_process.returncode != 0 and not _task_cancelled:
                    send({
                        "type": "task_error",
                        "text": _build_process_failure_text(
                            "Claude Code fix run",
                            fix_process.returncode,
                            output_lines=fix_output_lines,
                        ),
                    })
                else:
                    if fix_session_id and fix_process.returncode == 0:
                        _set_chat_session(repo_key, fix_session_id)
                    send({"type": "agent_status", "agent": "Claude Code", "status": "Fixes applied"})

    except FileNotFoundError:
        send({"type": "chat_response", "agent": "System",
              "text": "Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"})
    except subprocess.TimeoutExpired:
        with _process_lock:
            if _active_process:
                _active_process.kill()
        send({"type": "chat_response", "agent": "System", "text": "Timeout — Claude CLI took too long."})
    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Claude session error: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": "An internal error occurred. See the server log for details."})
    finally:
        with _process_lock:
            _active_process = None
        # Persist partial memory even after a crash or rate limit using what we have so far
        try:
            if output_lines:
                crash_summary = (
                    f"Latest task (interrupted): {user_text[:200]}\n"
                    f"Output so far: {chr(10).join(output_lines[-5:])}"
                )
                _save_project_memory(target_repo, crash_summary)
        except Exception:
            pass
        # Unlock the frontend
        send({"type": "unlock"})


def _run_codex_session_thread(ws: WebSocket, user_text: str) -> None:
    """Run one chat/coding task through Codex as the selected builder."""
    import select
    import time as _time

    settings_snapshot = _get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)
    claude_model = settings_snapshot.get("claude_model", "sonnet")
    claude_effort = settings_snapshot.get("claude_effort", "high")
    auto_review = settings_snapshot.get("auto_review", True)
    agent_label = "Codex"

    _frozen_proj_id = _get_active_project_id()

    def send(msg: dict):
        broadcast_sync(msg, proj_id_override=_frozen_proj_id)

    send({"type": "agent_status", "agent": agent_label, "status": "Working..."})

    proj_id = _frozen_proj_id
    if proj_id:
        _append_timeline_event(proj_id, {"type": "user_input", "text": user_text})

    output_lines: list[str] = []
    try:
        with _process_lock:
            if _task_cancelled:
                return
        env = _build_cli_env()
        before_snapshot = _capture_repo_change_snapshot(target_repo)
        project_memory = _load_project_memory(target_repo)
        prompt_parts = [
            "You are working inside OMADS (Orchestrated Multi-Agent Development System).",
            "The user interacts with you through a web GUI.",
            "You are the currently selected primary builder for this task.",
            "After your code changes, the reviewer agent will check the result and you may receive findings to address.",
            "Respond in English.",
        ]
        if project_memory:
            prompt_parts.append("\nProject context:\n" + project_memory)
        prompt_parts.append("\nUser request:\n" + user_text)
        prompt = "\n".join(prompt_parts)

        cmd = [
            "codex", "exec",
            "-s", "workspace-write",
            "--skip-git-repo-check",
            "--json",
            "-C", str(target_repo),
        ]
        if codex_model:
            cmd.extend(["-m", codex_model])
        if codex_reasoning:
            cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
        if codex_fast:
            cmd.extend(["-c", 'service_tier="fast"'])
        cmd.append("-")

        if _task_cancelled:
            return
        start_time = _time.time()
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(target_repo),
            env=env,
        )
        with _process_lock:
            _active_process = process

        process.stdin.write(prompt)
        process.stdin.close()

        _INACTIVITY_LIMIT = 900
        last_output_time = _time.time()
        while True:
            if _task_cancelled:
                process.kill()
                process.wait()
                send({"type": "task_stopped", "text": "Stopped."})
                return
            if _time.time() - last_output_time > _INACTIVITY_LIMIT:
                process.kill()
                process.wait()
                send({
                    "type": "task_error",
                    "text": "Codex task timed out after 15 minutes without output.",
                })
                return
            ready, _, _ = select.select([process.stdout], [], [], 5.0)
            if not ready:
                continue
            line = process.stdout.readline()
            if not line:
                break
            last_output_time = _time.time()
            _forward_codex_stream_line(
                line,
                agent_label=agent_label,
                send=send,
                text_buffer=output_lines,
            )

        process.wait(timeout=10)
        with _process_lock:
            _active_process = None

        elapsed = round(_time.time() - start_time)
        result_text = "\n".join(output_lines).strip()
        if process.returncode != 0 and not _task_cancelled:
            send({
                "type": "task_error",
                "text": _build_process_failure_text(
                    "Codex Task",
                    process.returncode,
                    result_text=result_text,
                    output_lines=output_lines,
                ),
            })
            return

        after_snapshot = _capture_repo_change_snapshot(target_repo)
        files_changed = after_snapshot.get("changed_files", []) if _repo_snapshot_changed(before_snapshot, after_snapshot) else []

        global _last_files_changed
        if files_changed:
            _last_files_changed = list(files_changed)

        if result_text and not output_lines:
            send({"type": "chat_response", "agent": agent_label, "text": result_text})

        send({"type": "agent_status", "agent": agent_label, "status": f"Done ({elapsed}s)"})

        if result_text:
            summary_parts = []
            if files_changed:
                summary_parts.append(f"Changed files: {', '.join(f[-60:] for f in files_changed[:20])}")
            summary_parts.append(f"Latest task: {user_text[:200]}")
            summary_parts.append(f"Result: {result_text[:2000]}")
            _save_project_memory(target_repo, "\n".join(summary_parts))

        if files_changed and auto_review:
            review_findings = _run_claude_auto_review(
                target_repo,
                files_changed,
                send,
                model=claude_model,
                effort=claude_effort,
            )

            if review_findings:
                send({"type": "agent_status", "agent": "Codex", "status": "Fixing Claude findings..."})
                fix_prompt = (
                    "The Claude reviewer found the following issues in your code. "
                    "Review the findings carefully, fix the valid issues, and keep any non-issues unchanged.\n\n"
                    + review_findings
                    + "\n\nRules: Fix ONLY the reported issues. No scope creep. Respond in English."
                )

                fix_cmd = [
                    "codex", "exec",
                    "-s", "workspace-write",
                    "--skip-git-repo-check",
                    "--json",
                    "-C", str(target_repo),
                ]
                if codex_model:
                    fix_cmd.extend(["-m", codex_model])
                if codex_reasoning:
                    fix_cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
                if codex_fast:
                    fix_cmd.extend(["-c", 'service_tier="fast"'])
                fix_cmd.append("-")

                if _task_cancelled:
                    return
                fix_process = subprocess.Popen(
                    fix_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=str(target_repo),
                    env=env,
                )
                with _process_lock:
                    _active_process = fix_process

                fix_output_lines: list[str] = []
                fix_process.stdin.write(fix_prompt)
                fix_process.stdin.close()

                _INACTIVITY_LIMIT = 900
                last_fix_output_time = _time.time()
                while True:
                    if _task_cancelled:
                        fix_process.kill()
                        fix_process.wait()
                        return
                    if _time.time() - last_fix_output_time > _INACTIVITY_LIMIT:
                        fix_process.kill()
                        fix_process.wait()
                        send({
                            "type": "task_error",
                            "text": "Codex fix run timed out after 15 minutes without output.",
                        })
                        return
                    ready, _, _ = select.select([fix_process.stdout], [], [], 5.0)
                    if not ready:
                        continue
                    line = fix_process.stdout.readline()
                    if not line:
                        break
                    last_fix_output_time = _time.time()
                    _forward_codex_stream_line(
                        line,
                        agent_label=agent_label,
                        send=send,
                        text_buffer=fix_output_lines,
                    )

                fix_process.wait(timeout=10)
                with _process_lock:
                    _active_process = None

                if fix_process.returncode != 0 and not _task_cancelled:
                    send({
                        "type": "task_error",
                        "text": _build_process_failure_text(
                            "Codex fix run",
                            fix_process.returncode,
                            result_text="\n".join(fix_output_lines).strip(),
                            output_lines=fix_output_lines,
                        ),
                    })
                else:
                    send({"type": "agent_status", "agent": "Codex", "status": "Fixes applied"})

    except FileNotFoundError:
        send({
            "type": "chat_response",
            "agent": "System",
            "text": "Codex CLI not found. Install it with: npm install -g @openai/codex",
        })
    except subprocess.TimeoutExpired:
        with _process_lock:
            if _active_process:
                _active_process.kill()
        send({"type": "chat_response", "agent": "System", "text": "Timeout — Codex took too long."})
    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Codex session error: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": "An internal error occurred. See the server log for details."})
    finally:
        with _process_lock:
            _active_process = None
        send({"type": "unlock"})


def _review_runtime_context() -> ReviewRuntimeContext:
    """Build the small dependency bundle used by review-specific helpers."""

    def process_started(process: subprocess.Popen) -> None:
        global _active_process
        with _process_lock:
            _active_process = process

    def process_finished() -> None:
        global _active_process
        with _process_lock:
            _active_process = None

    def is_task_cancelled() -> bool:
        return _task_cancelled

    def store_review_session(repo_key: str, session_id: str) -> None:
        _set_chat_session(repo_key, session_id, scope="review")

    return ReviewRuntimeContext(
        build_cli_env=_build_cli_env,
        build_process_failure_text=_build_process_failure_text,
        forward_claude_stream_line=_forward_claude_stream_line,
        forward_codex_stream_line=_forward_codex_stream_line,
        get_settings_snapshot=_get_settings_snapshot,
        is_task_cancelled=is_task_cancelled,
        load_project_memory=_load_project_memory,
        process_started=process_started,
        process_finished=process_finished,
        store_review_session=store_review_session,
    )


def _run_claude_manual_review_step(**kwargs):
    """Delegate one Claude review step to the review-specific runtime module."""
    return _review_run_claude_manual_review_step(_review_runtime_context(), **kwargs)


def _run_codex_manual_review_step(**kwargs):
    """Delegate one Codex review step to the review-specific runtime module."""
    return _review_run_codex_manual_review_step(_review_runtime_context(), **kwargs)


def _run_claude_manual_synthesis_step(**kwargs):
    """Delegate one Claude synthesis step to the review-specific runtime module."""
    return _review_run_claude_manual_synthesis_step(_review_runtime_context(), **kwargs)


def _run_codex_manual_synthesis_step(**kwargs):
    """Delegate one Codex synthesis step to the review-specific runtime module."""
    return _review_run_codex_manual_synthesis_step(_review_runtime_context(), **kwargs)


def _run_review_thread(ws: WebSocket, scope: str, focus: str, custom_scope: str, custom_focus: str = "") -> None:
    """Run the configurable three-step manual review flow with transparent progress.

    Step 1: Reviewer 1 inspects the selected scope.
    Step 2: Reviewer 2 independently reviews the same scope.
    Step 3: Reviewer 1 returns for synthesis and the final fix plan.

    scope: 'project' | 'last_task' | 'custom'
    focus: 'all' | 'security' | 'bugs' | 'performance' | 'custom'
    custom_scope: path(s) when scope='custom'
    custom_focus: free-text focus when focus='custom'
    """
    import time as _time

    global _active_process, _task_cancelled, _pending_review_fixes
    with _process_lock:
        if _task_cancelled:
            if _active_process is _RESERVED_PROCESS_SLOT:
                _active_process = None
            return

    settings_snapshot = _get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    model = settings_snapshot.get("claude_model", "sonnet")
    effort = settings_snapshot.get("claude_effort", "high")
    review_first = settings_snapshot.get("review_first_reviewer", "claude")
    review_second = settings_snapshot.get("review_second_reviewer", "codex")
    if review_first not in ("claude", "codex"):
        review_first = "claude"
    if review_second not in ("claude", "codex"):
        review_second = "codex"
    if review_second == review_first:
        review_second = "codex" if review_first == "claude" else "claude"
    pipeline_desc = f"{_review_display_name(review_first)} -> {_review_display_name(review_second)} -> {_review_display_name(review_first)}"

    # Freeze the project ID at task start
    _frozen_proj_id = _get_active_project_id()

    def send(msg: dict):
        broadcast_sync(msg, proj_id_override=_frozen_proj_id)

    # Determine scope
    if scope == "last_task" and _last_files_changed:
        review_files = _last_files_changed
        scope_desc = f"Last task ({len(review_files)} files)"
    elif scope == "custom" and custom_scope.strip():
        review_files = [f.strip() for f in custom_scope.split(",") if f.strip()]
        scope_desc = f"Custom: {custom_scope[:100]}"
    else:
        review_files = []
        scope_desc = "Whole project"

    focus_desc = _review_focus_description(focus, custom_focus)

    file_hint = ""
    if review_files:
        file_hint = f"\n\nPay special attention to these files:\n" + "\n".join(f"- {f}" for f in review_files[:30])

    repo_key = str(Path(target_repo).resolve())
    first_step_label = _review_runtime_label(review_first)
    second_step_label = _review_runtime_label(review_second)
    synthesis_label = _review_runtime_label(review_first, synthesis=True)
    proj_id = _frozen_proj_id
    _pending_review_fixes.pop(repo_key, None)
    if proj_id:
        _append_timeline_event(
            proj_id,
            {
                "type": "user_input",
                "text": f"Start review — Scope: {scope_desc}, Focus: {focus_desc}",
            },
        )

    send({
        "type": "stream_text",
        "agent": "Review",
        "text": (
            f"**Review started**\n"
            f"Scope: {scope_desc}\n"
            f"Focus: {focus_desc}\n"
            f"Flow: {pipeline_desc}"
        ),
    })

    first_session_id = _get_chat_session(repo_key, scope="review")

    try:
        # ── STEP 1: Reviewer 1 ────────────────────────────────────
        send({"type": "agent_status", "agent": first_step_label, "status": "Step 1/3 - review in progress..."})

        try:
            if review_first == "claude":
                first_review, first_session_id = _run_claude_manual_review_step(
                    target_repo=target_repo,
                    model=model,
                    effort=effort,
                    focus_desc=focus_desc,
                    file_hint=file_hint,
                    agent_label=first_step_label,
                    repo_key=repo_key,
                    send=send,
                    prior_session_id=first_session_id,
                    rate_limit_source="review_stream",
                )
            else:
                first_review = _run_codex_manual_review_step(
                    target_repo=target_repo,
                    focus_desc=focus_desc,
                    review_files=review_files,
                    agent_label=first_step_label,
                    step_name="Review step 1 (Codex)",
                    send=send,
                )
        except FileNotFoundError:
            send({"type": "task_error", "text": f"{first_step_label} is not installed, so step 1 could not start."})
            return
        except RuntimeError as exc:
            send({"type": "task_error", "text": str(exc)})
            return

        send({"type": "agent_status", "agent": first_step_label, "status": "Step 1/3 done"})

        if _task_cancelled:
            return

        # ── STEP 2: Reviewer 2 ────────────────────────────────────
        send({"type": "agent_status", "agent": second_step_label, "status": "Step 2/3 - review in progress..."})
        second_review = ""
        try:
            if review_second == "claude":
                second_review, _ = _run_claude_manual_review_step(
                    target_repo=target_repo,
                    model=model,
                    effort=effort,
                    focus_desc=focus_desc,
                    file_hint=file_hint,
                    agent_label=second_step_label,
                    repo_key=repo_key,
                    send=send,
                    prior_session_id=_get_chat_session(repo_key, scope="review"),
                    rate_limit_source="review_stream",
                )
            else:
                second_review = _run_codex_manual_review_step(
                    target_repo=target_repo,
                    focus_desc=focus_desc,
                    review_files=review_files,
                    agent_label=second_step_label,
                    step_name="Review step 2 (Codex)",
                    send=send,
                )
            send({"type": "agent_status", "agent": second_step_label, "status": "Step 2/3 done"})
        except FileNotFoundError:
            send({"type": "agent_status", "agent": second_step_label, "status": f"{second_step_label} is not installed - synthesis will continue without step 2 output"})
            second_review = f"({second_step_label} unavailable)"
        except RuntimeError as exc:
            send({"type": "agent_status", "agent": second_step_label, "status": f"Step 2 incomplete - continuing with limited data"})
            second_review = f"({second_step_label} incomplete: {str(exc)[:220]})"

        if _task_cancelled:
            return

        # ── STEP 3: Reviewer 1 synthesis ──────────────────────────
        send({"type": "agent_status", "agent": synthesis_label, "status": "Step 3/3 - synthesis: comparing both reviews..."})
        send({
            "type": "stream_text",
            "agent": "Review",
            "text": f"---\n**Step 3:** {_review_display_name(review_first)} is comparing both reviews and preparing the final report...",
        })

        try:
            if review_first == "claude":
                synthesis_text, has_fixes, _ = _run_claude_manual_synthesis_step(
                    target_repo=target_repo,
                    model=model,
                    effort=effort,
                    repo_key=repo_key,
                    send=send,
                    prior_session_id=_get_chat_session(repo_key, scope="review"),
                    first_label=_review_display_name(review_first),
                    second_label=_review_display_name(review_second),
                    first_review=first_review,
                    second_review=second_review,
                )
            else:
                synthesis_text, has_fixes = _run_codex_manual_synthesis_step(
                    target_repo=target_repo,
                    first_label=_review_display_name(review_first),
                    second_label=_review_display_name(review_second),
                    first_review=first_review,
                    second_review=second_review,
                    send=send,
                )
        except FileNotFoundError:
            send({"type": "task_error", "text": f"{synthesis_label} is not installed, so step 3 could not start."})
            return
        except RuntimeError as exc:
            send({"type": "task_error", "text": str(exc)})
            return

        send({"type": "agent_status", "agent": synthesis_label, "status": "Step 3/3 done"})
        send({
            "type": "stream_text",
            "agent": "Review",
            "text": f"---\n**Review completed** - Final flow: {pipeline_desc}",
        })

        if has_fixes:
            # Store fix suggestions for the later apply step (per project)
            _pending_review_fixes[repo_key] = synthesis_text
            send({"type": "review_fixes_available",
                  "text": "Fixes were identified. Should the suggested fixes be applied?"})
        else:
            _pending_review_fixes.pop(repo_key, None)

    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Review error: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": f"Review error: {str(e)[:200]}"})
    finally:
        with _process_lock:
            _active_process = None
        send({"type": "unlock"})


def _run_codex_auto_review(ws: WebSocket, target_repo: str, files_changed: list[str], send: callable) -> str | None:
    """Run Codex as the automatic reviewer for Claude-built file changes.

    Return the findings as a string, or None when everything looks good.
    """
    breaker_label = "Codex Review"
    settings_snapshot = _get_settings_snapshot()
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)

    # Only short file names for the prompt
    short_files = [f.rsplit("/", 1)[-1] if "/" in f else f for f in files_changed[:10]]
    file_list = ", ".join(short_files)

    send({"type": "agent_status", "agent": breaker_label, "status": f"Reviewing {len(files_changed)} changed file(s)..."})

    review_prompt = f"""You are a code reviewer. Review the following recently changed files for issues:

Changed files: {file_list}

Check for:
1. Security issues (injection, XSS, exposed secrets)
2. Logic bugs and regressions
3. Missing error handling
4. Obvious performance issues

Always respond in this format:

## Files reviewed
- filename: short summary of what the file does

## Analysis
Briefly describe what you checked (2-3 sentences).

## Result
If there are issues, use one line per issue:
- [HIGH/MEDIUM/LOW] file: description

If there are no issues, write exactly: "No issues found."
"""

    try:
        cmd = ["codex", "exec", "-s", "read-only", "--ephemeral", "--skip-git-repo-check",
               "--json", "-C", str(target_repo)]
        if codex_model:
            cmd.extend(["-m", codex_model])
        if codex_reasoning:
            cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
        if codex_fast:
            cmd.extend(["-c", 'service_tier="fast"'])
        cmd.append("-")

        import time as _time

        send({"type": "stream_text", "agent": breaker_label,
              "text": f"Starting review: {file_list}"})
        send({"type": "stream_text", "agent": breaker_label,
              "text": "Checking for: security, logic bugs, error handling, performance"})

        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=str(target_repo), env=_build_cli_env(),
        )

        # Register the auto-review process so stop() can kill it
        global _active_process
        with _process_lock:
            _active_process = process

        # Send the prompt through stdin and close it
        process.stdin.write(review_prompt)
        process.stdin.close()

        # Heartbeat thread: show progress every 10s while Codex is working
        import threading
        heartbeat_stop = threading.Event()
        start_time = _time.time()

        def _heartbeat():
            while not heartbeat_stop.is_set():
                heartbeat_stop.wait(10)
                if not heartbeat_stop.is_set():
                    elapsed = round(_time.time() - start_time)
                    send({"type": "agent_status", "agent": breaker_label,
                          "status": f"Codex is analyzing... ({elapsed}s)"})

        hb_thread = threading.Thread(target=_heartbeat, daemon=True)
        hb_thread.start()

        # Read stdout in JSONL format with an inactivity timeout as a safety net
        import select
        output_lines = []
        _INACTIVITY_LIMIT = 900  # Kill after 15 minutes without output
        last_output_time = _time.time()
        while True:
            if _task_cancelled:
                process.kill()
                process.wait()
                heartbeat_stop.set()
                return None
            if _time.time() - last_output_time > _INACTIVITY_LIMIT:
                process.kill()
                process.wait()
                heartbeat_stop.set()
                send({"type": "agent_status", "agent": breaker_label, "status": "Inactivity - review cancelled (15 min without output)"})
                return "\n".join(output_lines) if output_lines else None
            ready, _, _ = select.select([process.stdout], [], [], 5.0)
            if ready:
                line = process.stdout.readline()
                if not line:  # EOF - Codex finished
                    break
                last_output_time = _time.time()
                _forward_codex_stream_line(
                    line,
                    agent_label=breaker_label,
                    send=send,
                    text_buffer=output_lines,
                )

        heartbeat_stop.set()
        process.wait(timeout=10)
        with _process_lock:
            _active_process = None
        elapsed = round(_time.time() - start_time)
        output = "\n".join(output_lines).strip()

        if not output and process.returncode != 0:
            output = f"Review error: Codex exited with code {process.returncode}"
            send({"type": "stream_result", "agent": breaker_label, "text": output, "is_error": True})

        send({"type": "stream_text", "agent": breaker_label,
              "text": f"Review completed ({elapsed}s)"})

        # Evaluate the result; treat it as findings only on a successful exit
        if process.returncode != 0:
            send({"type": "agent_status", "agent": breaker_label,
                  "status": f"Codex error (exit {process.returncode}) - no review result"})
            return None
        output_lc = output.lower()
        if output and "no issues found" not in output_lc:
            send({"type": "agent_activity", "agent": breaker_label, "activity": "finding", "text": output})
            send({"type": "agent_status", "agent": breaker_label, "status": "Findings detected -> Claude Code is fixing"})
            return output  # Return findings for the Claude fix step
        else:
            send({"type": "agent_status", "agent": breaker_label, "status": "All clear"})
            return None

    except FileNotFoundError:
        send({"type": "agent_status", "agent": breaker_label,
              "status": "Codex CLI not installed - review skipped"})
        return None
    except Exception as e:
        send({"type": "agent_status", "agent": breaker_label, "status": f"Review error: {str(e)[:100]}"})
        return None
    finally:
        with _process_lock:
            _active_process = None


def _run_claude_auto_review(
    target_repo: str,
    files_changed: list[str],
    send: callable,
    *,
    model: str,
    effort: str,
) -> str | None:
    """Run Claude as the automatic reviewer and return findings when present."""
    breaker_label = "Claude Review"
    short_files = [f.rsplit("/", 1)[-1] if "/" in f else f for f in files_changed[:10]]
    file_list = ", ".join(short_files)

    send({"type": "agent_status", "agent": breaker_label, "status": f"Reviewing {len(files_changed)} changed file(s)..."})

    project_memory = _load_project_memory(target_repo)
    reviewer_context = (
        "You are reviewing code changes inside OMADS. "
        "Read and analyze the code, but do NOT edit anything. "
        "Respond in English.\n"
    )
    if project_memory:
        reviewer_context += f"\nProject context:\n{project_memory}\n"

    review_prompt = f"""You are a code reviewer. Review the following recently changed files for issues:

Changed files: {file_list}

Check for:
1. Security issues
2. Logic bugs and regressions
3. Missing error handling
4. Obvious performance issues

Always respond in this format:

## Files reviewed
- filename: short summary

## Analysis
Briefly describe what you checked (2-3 sentences).

## Findings
- [HIGH/MEDIUM/LOW] file:line: description

If you found no real issues, write exactly:
No issues found.
"""

    try:
        process = subprocess.Popen(
            [
                "claude", "-p", review_prompt,
                "--output-format", "stream-json",
                "--verbose",
                "--model", model,
                "--effort", effort,
                "--append-system-prompt", reviewer_context,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=target_repo,
            env=_build_cli_env(),
        )
        with _process_lock:
            _active_process = process

        output_lines: list[str] = []
        final_result = ""
        for line in process.stdout:
            if _task_cancelled:
                process.kill()
                process.wait()
                return None
            _, parsed_result = _forward_claude_stream_line(
                line,
                agent_label=breaker_label,
                send=send,
                text_buffer=output_lines,
                rate_limit_source="auto_review_stream",
            )
            if parsed_result:
                final_result = parsed_result

        process.wait(timeout=30)
        with _process_lock:
            _active_process = None

        review_text = final_result if final_result else "\n".join(output_lines).strip()
        if process.returncode != 0:
            send({"type": "agent_status", "agent": breaker_label, "status": f"Claude error (exit {process.returncode}) - no review result"})
            return None

        review_lc = review_text.lower()
        if review_text and "no issues found" not in review_lc:
            send({"type": "agent_activity", "agent": breaker_label, "activity": "finding", "text": review_text})
            send({"type": "agent_status", "agent": breaker_label, "status": "Findings detected -> Codex is fixing"})
            return review_text

        send({"type": "agent_status", "agent": breaker_label, "status": "All clear"})
        return None
    except FileNotFoundError:
        send({"type": "agent_status", "agent": breaker_label, "status": "Claude Code CLI not installed - review skipped"})
        return None
    except Exception as e:
        send({"type": "agent_status", "agent": breaker_label, "status": f"Review error: {str(e)[:100]}"})
        return None
    finally:
        with _process_lock:
            _active_process = None
