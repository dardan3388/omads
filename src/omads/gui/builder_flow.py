"""Builder and automatic breaker task runners for the OMADS GUI."""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import WebSocket

from .streaming import extract_codex_changed_files, parse_claude_stream_line

_REAL_THREAD = threading.Thread


def _coerce_bool(value: object, default: bool = False) -> bool:
    """Parse one potentially legacy bool-like setting value."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _codex_service_tier_arg(codex_fast: object) -> str:
    """Return an explicit service-tier override so local config cannot shadow GUI settings."""
    return 'service_tier="fast"' if _coerce_bool(codex_fast, default=False) else 'service_tier="flex"'


def _normalize_codex_execution_mode(value: object, default: str = "default") -> str:
    """Normalize the simplified Codex execution preset into a stable value."""
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower().replace(" ", "-").replace("_", "-")
    if normalized in {"default", "auto", "read-only", "full-auto"}:
        return normalized
    if normalized in {"readonly", "read-only"}:
        return "read-only"
    if normalized in {"fullauto", "full-auto"}:
        return "full-auto"
    return default


def _codex_execution_mode_args(codex_execution_mode: object) -> list[str]:
    """Return explicit Codex sandbox/approval flags for the selected preset."""
    mode = _normalize_codex_execution_mode(codex_execution_mode)
    if mode == "auto":
        return ["-s", "workspace-write", "-a", "never"]
    if mode == "read-only":
        return ["-s", "read-only", "-a", "untrusted"]
    if mode == "full-auto":
        return ["--dangerously-bypass-approvals-and-sandbox"]
    return ["-s", "workspace-write", "-a", "on-request"]


def _claude_permission_mode_arg(permission_mode: object) -> str:
    """Return the explicit Claude permission mode for one run."""
    if not isinstance(permission_mode, str):
        return "default"
    normalized = permission_mode.strip()
    if normalized in {"default", "auto", "plan", "bypassPermissions"}:
        return normalized
    return "default"


def _validate_target_repo(target_repo: str) -> None:
    """Raise early if the target repository directory does not exist."""
    if not Path(target_repo).is_dir():
        raise FileNotFoundError(
            f"Project directory not found: {target_repo}"
        )


_CODEX_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[^\s]+"), r"\1***"),
    (re.compile(r"(?i)(x-access-token:)[^@\s]+"), r"\1***"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|sess-[A-Za-z0-9_-]{8,}|github_pat_[A-Za-z0-9_]{20,})\b"), "***"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)(\s*[:=]\s*)([^\s,;\"']{6,})"), r"\1\2***"),
)


def _scrub_token_errors(text: str) -> str:
    """Redact likely credentials before stderr is surfaced to the GUI."""
    scrubbed = text
    for pattern, replacement in _CODEX_SECRET_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def _summarize_codex_stderr(stderr_lines: list[str], *, limit: int = 6) -> str:
    """Return one compact, already-scrubbed stderr summary."""
    cleaned = [line.strip() for line in stderr_lines if line and line.strip()]
    if not cleaned:
        return ""
    return " ".join(cleaned[-limit:])[:400]


def _build_codex_failure_detail(*, stdout_text: str, stderr_lines: list[str]) -> str:
    """Combine stdout/stderr into one compact failure detail string."""
    detail_parts: list[str] = []
    stderr_summary = _summarize_codex_stderr(stderr_lines)
    if stderr_summary:
        detail_parts.append(f"stderr: {stderr_summary}")
    stdout_summary = " ".join(stdout_text.split()).strip()
    if stdout_summary:
        detail_parts.append(f"stdout: {stdout_summary[:220]}")
    return " | ".join(detail_parts)


def _build_codex_empty_output_warning(
    *,
    run_label: str,
    raw_stdout_lines: int,
    stderr_lines: list[str],
) -> str:
    """Explain why a Codex run ended without any user-visible text."""
    text = f"{run_label} finished without any user-visible response."
    if raw_stdout_lines:
        text += " Codex emitted JSON events, but none contained assistant text that OMADS could forward."
    stderr_summary = _summarize_codex_stderr(stderr_lines)
    if stderr_summary:
        text += f" Last stderr: {stderr_summary}"
    return text


def _start_stream_reader(
    stream,
    *,
    stream_kind: str,
    event_queue: "queue.Queue[tuple[str, object | None]]",
) -> None:
    """Read one text stream in the background and forward lines into a queue."""

    def _reader() -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    event_queue.put((stream_kind, None))
                    return
                event_queue.put((stream_kind, line))
        except Exception as exc:  # pragma: no cover - defensive fallback
            event_queue.put((stream_kind, exc))
            event_queue.put((stream_kind, None))

    _REAL_THREAD(target=_reader, daemon=True).start()


def _stream_codex_process(
    process: subprocess.Popen,
    *,
    ctx: "BuilderRuntimeContext",
    send: Callable[[dict], None],
    agent_label: str,
    text_buffer: list[str],
    raw_stdout_buffer: list[str] | None = None,
    inactivity_error_text: str,
    emit_stop_message: bool = False,
    inactivity_limit: int = 900,
) -> tuple[bool, int, list[str]]:
    """Drain one Codex process until completion while reading stdout and stderr."""
    raw_stdout_lines = 0
    stderr_lines: list[str] = []
    event_queue: "queue.Queue[tuple[str, object | None]]" = queue.Queue()
    open_streams: set[str] = set()
    if process.stdout is not None:
        open_streams.add("stdout")
        _start_stream_reader(process.stdout, stream_kind="stdout", event_queue=event_queue)
    if process.stderr is not None:
        open_streams.add("stderr")
        _start_stream_reader(process.stderr, stream_kind="stderr", event_queue=event_queue)

    last_activity_time = time.monotonic()
    while open_streams:
        if ctx.is_task_cancelled():
            process.kill()
            process.wait()
            if emit_stop_message:
                send({"type": "task_stopped", "text": "Stopped."})
            return False, raw_stdout_lines, stderr_lines

        if time.monotonic() - last_activity_time > inactivity_limit:
            process.kill()
            process.wait()
            timeout_text = inactivity_error_text
            stderr_summary = _summarize_codex_stderr(stderr_lines)
            if stderr_summary:
                timeout_text += f" Last stderr: {stderr_summary}"
            send({"type": "task_error", "text": timeout_text})
            return False, raw_stdout_lines, stderr_lines

        try:
            stream_kind, payload = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if payload is None:
            open_streams.discard(stream_kind)
            continue
        if isinstance(payload, Exception):  # pragma: no cover - defensive fallback
            open_streams.discard(stream_kind)
            continue

        line = payload
        last_activity_time = time.monotonic()
        if stream_kind == "stdout":
            raw_stdout_lines += 1
            if raw_stdout_buffer is not None:
                raw_stdout_buffer.append(line)
            ctx.forward_codex_stream_line(
                line,
                agent_label=agent_label,
                send=send,
                text_buffer=text_buffer,
            )
        else:
            cleaned = _scrub_token_errors(line.strip())
            if cleaned:
                stderr_lines.append(cleaned)

    return True, raw_stdout_lines, stderr_lines


@dataclass(slots=True)
class BuilderRuntimeContext:
    """Small adapter surface that keeps builder helpers decoupled from runtime.py."""

    append_timeline_event: Callable[[str, dict], None]
    build_cli_env: Callable[[], dict[str, str]]
    build_process_failure_text: Callable[..., str]
    capture_repo_change_snapshot: Callable[[str], dict[str, object]]
    forward_codex_stream_line: Callable[..., None]
    build_chat_handover_context: Callable[[str], str]
    get_active_project_id: Callable[[], str | None]
    get_chat_session: Callable[..., str | None]
    get_settings_snapshot: Callable[[], dict]
    is_task_cancelled: Callable[[], bool]
    load_project_memory: Callable[[str], str]
    merge_changed_files: Callable[..., list[str]]
    parse_claude_stream_line: Callable[[str], list[dict]]
    process_started: Callable[[subprocess.Popen], None]
    process_finished: Callable[[], None]
    repo_snapshot_changed: Callable[[dict[str, object], dict[str, object]], bool]
    run_claude_auto_review: Callable[..., str | None]
    run_codex_auto_review: Callable[..., str | None]
    save_project_memory: Callable[[str, str], None]
    set_builder_session: Callable[..., None]
    set_last_files_changed: Callable[[list[str]], None]
    update_claude_limit_status: Callable[[dict, str], dict]


def run_claude_session_thread(
    ctx: BuilderRuntimeContext,
    ws: WebSocket,
    user_text: str,
    send: Callable[[dict], None],
) -> None:
    """Run one task through Claude Code and trigger the automatic breaker step."""
    settings_snapshot = ctx.get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    repo_key = str(Path(target_repo).resolve())
    model = settings_snapshot.get("claude_model", "sonnet")
    effort = settings_snapshot.get("claude_effort", "high")
    permission_mode = _claude_permission_mode_arg(settings_snapshot.get("claude_permission_mode", "default"))
    auto_review = settings_snapshot.get("auto_review", True)
    agent_label = "Claude Code"

    proj_id = ctx.get_active_project_id()
    send({"type": "agent_status", "agent": agent_label, "status": "Working..."})
    if proj_id:
        ctx.append_timeline_event(proj_id, {"type": "user_input", "text": user_text})

    output_lines: list[str] = []
    try:
        _validate_target_repo(target_repo)
        if ctx.is_task_cancelled():
            return

        env = ctx.build_cli_env()
        before_snapshot = ctx.capture_repo_change_snapshot(target_repo)

        omads_context = (
            "You are working inside OMADS (Orchestrated Multi-Agent Development System). "
            "The user interacts with you through a web GUI. "
            "After every code change, Codex CLI automatically reviews your code in the background. "
            "If Codex finds issues, you will receive the findings as the next message and should fix them. "
            "Respond in the same language the user writes in.\n\n"
        )

        session_id = ctx.get_chat_session(repo_key, scope="builder:claude")
        if not session_id:
            project_memory = ctx.load_project_memory(target_repo)
            if project_memory:
                omads_context += (
                    "You have the following context from previous sessions and the project:\n\n"
                    + project_memory
                    + "\n\nUse this context to continue seamlessly without requiring the user "
                    "to explain where the work left off.\n\n"
                )
            # Provide recent chat history so the builder knows the conversation context
            # even when switching from another builder (e.g. Codex -> Claude Code)
            handover = ctx.build_chat_handover_context(proj_id or "")
            if handover:
                omads_context += (
                    "Recent conversation in this project (may include messages from another builder):\n\n"
                    + handover
                    + "\n\nContinue naturally from this context. Do not repeat or summarize it.\n\n"
                )

        cmd = [
            "claude",
            "-p",
            user_text,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--effort",
            effort,
            "--permission-mode",
            permission_mode,
            "--append-system-prompt",
            omads_context,
        ]
        if session_id:
            cmd.extend(["--resume", session_id])

        if ctx.is_task_cancelled():
            return
        start_time = time.time()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=target_repo,
            env=env,
        )
        ctx.process_started(process)

        final_result = ""
        captured_session_id = None
        files_changed: list[str] = []

        for line in process.stdout:
            if ctx.is_task_cancelled():
                process.kill()
                send({"type": "task_stopped", "text": "Stopped."})
                break

            for event in ctx.parse_claude_stream_line(line):
                kind = event["kind"]
                if kind == "session_id" and not captured_session_id:
                    captured_session_id = event["session_id"]
                elif kind == "tool":
                    if event["tool"] in ("Write", "Edit"):
                        file_path = event.get("file_path", "")
                        if file_path and file_path not in files_changed:
                            files_changed.append(file_path)
                    send(
                        {
                            "type": "stream_tool",
                            "agent": agent_label,
                            "tool": event["tool"],
                            "description": event["description"],
                            "detail": event["detail"],
                        }
                    )
                elif kind == "text":
                    send({"type": "stream_text", "agent": agent_label, "text": event["text"]})
                    output_lines.append(event["text"])
                elif kind == "thinking":
                    send({"type": "stream_thinking", "agent": agent_label, "text": event["text"]})
                elif kind == "tool_result":
                    send(
                        {
                            "type": "stream_result",
                            "agent": agent_label,
                            "text": event["text"],
                            "is_error": event["is_error"],
                        }
                    )
                elif kind == "result":
                    final_result = event["text"]
                elif kind == "rate_limit":
                    limit = ctx.update_claude_limit_status(event["rate_limit_info"], source="task_stream")
                    send({"type": "claude_limit_update", "limit": limit})

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        finally:
            ctx.process_finished()

        elapsed = round(time.time() - start_time)
        success = process.returncode == 0 and not ctx.is_task_cancelled()

        if not ctx.is_task_cancelled() and process.returncode != 0:
            result_text = final_result if final_result else "\n".join(output_lines)
            send(
                {
                    "type": "task_error",
                    "text": ctx.build_process_failure_text(
                        "Claude Code Task",
                        process.returncode,
                        result_text=result_text,
                        output_lines=output_lines,
                    ),
                }
            )
            return

        if captured_session_id and success:
            ctx.set_builder_session(repo_key, captured_session_id, scope="builder:claude")

        after_snapshot = ctx.capture_repo_change_snapshot(target_repo) if success else before_snapshot
        snapshot_files = (
            after_snapshot.get("changed_files", [])
            if ctx.repo_snapshot_changed(before_snapshot, after_snapshot)
            else []
        )
        files_changed = ctx.merge_changed_files(files_changed, snapshot_files)
        if files_changed:
            ctx.set_last_files_changed(list(files_changed))

        result_text = final_result if final_result else "\n".join(output_lines)
        if result_text and not output_lines:
            send({"type": "chat_response", "agent": agent_label, "text": result_text})

        send({"type": "agent_status", "agent": agent_label, "status": f"Done ({elapsed}s)"})

        if success and (output_lines or result_text):
            summary_parts = []
            if files_changed:
                summary_parts.append(f"Changed files: {', '.join(f[-60:] for f in files_changed[:20])}")
            recent_output = "\n".join(output_lines[-10:]) if output_lines else result_text
            summary_parts.append(f"Latest task: {user_text[:200]}")
            summary_parts.append(f"Result: {recent_output[:2000]}")
            ctx.save_project_memory(target_repo, "\n".join(summary_parts))

        if files_changed and auto_review and success:
            review_findings = ctx.run_codex_auto_review(ws, target_repo, files_changed, send)
            if review_findings:
                send({"type": "agent_status", "agent": "Claude Code", "status": "Fixing Codex findings..."})
                fix_prompt = (
                    "The Codex auto-reviewer found the following issues in your code. "
                    "Please fix them:\n\n"
                    + review_findings
                    + "\n\nRules: Fix ONLY the reported issues. No scope creep."
                )

                fix_cmd = [
                    "claude",
                    "-p",
                    fix_prompt,
                    "--output-format",
                    "stream-json",
                    "--verbose",
                    "--model",
                    model,
                    "--effort",
                    effort,
                    "--permission-mode",
                    permission_mode,
                    "--append-system-prompt",
                    omads_context,
                ]
                fix_session = ctx.get_chat_session(repo_key, scope="builder:claude")
                if fix_session:
                    fix_cmd.extend(["--resume", fix_session])

                if ctx.is_task_cancelled():
                    return
                fix_process = subprocess.Popen(
                    fix_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=target_repo,
                    env=env,
                )
                ctx.process_started(fix_process)

                fix_output_lines: list[str] = []
                fix_session_id = None

                for fline in fix_process.stdout:
                    if ctx.is_task_cancelled():
                        fix_process.kill()
                        break
                    parsed_session_id = None
                    parsed_result = ""
                    for event in ctx.parse_claude_stream_line(fline):
                        kind = event["kind"]
                        if kind == "session_id" and not parsed_session_id:
                            parsed_session_id = event["session_id"]
                        elif kind == "tool":
                            send(
                                {
                                    "type": "stream_tool",
                                    "agent": "Claude Code",
                                    "tool": event["tool"],
                                    "description": event["description"],
                                    "detail": event["detail"],
                                }
                            )
                        elif kind == "text":
                            send({"type": "stream_text", "agent": "Claude Code", "text": event["text"]})
                            fix_output_lines.append(event["text"])
                        elif kind == "thinking":
                            send({"type": "stream_thinking", "agent": "Claude Code", "text": event["text"]})
                        elif kind == "tool_result":
                            send(
                                {
                                    "type": "stream_result",
                                    "agent": "Claude Code",
                                    "text": event["text"],
                                    "is_error": event["is_error"],
                                }
                            )
                        elif kind == "result":
                            parsed_result = event["text"]
                        elif kind == "rate_limit":
                            limit = ctx.update_claude_limit_status(event["rate_limit_info"], source="fix_stream")
                            send({"type": "claude_limit_update", "limit": limit})
                    if parsed_session_id and not fix_session_id:
                        fix_session_id = parsed_session_id
                    if parsed_result and not fix_output_lines:
                        send({"type": "chat_response", "agent": "Claude Code", "text": parsed_result})

                try:
                    fix_process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    fix_process.kill()
                    fix_process.wait()
                finally:
                    ctx.process_finished()

                if fix_process.returncode != 0 and not ctx.is_task_cancelled():
                    send(
                        {
                            "type": "task_error",
                            "text": ctx.build_process_failure_text(
                                "Claude Code fix run",
                                fix_process.returncode,
                                output_lines=fix_output_lines,
                            ),
                        }
                    )
                else:
                    if fix_session_id and fix_process.returncode == 0:
                        ctx.set_builder_session(repo_key, fix_session_id, scope="builder:claude")
                    send({"type": "agent_status", "agent": "Claude Code", "status": "Fixes applied"})

    except FileNotFoundError:
        send(
            {
                "type": "chat_response",
                "agent": "System",
                "text": "Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code",
            }
        )
    except subprocess.TimeoutExpired:
        send({"type": "chat_response", "agent": "System", "text": "Timeout — Claude CLI took too long."})
    except Exception:
        import logging

        logging.getLogger("omads.gui").error("Claude session error", exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": "An internal error occurred. See the server log for details."})
    finally:
        ctx.process_finished()
        try:
            if output_lines:
                crash_summary = (
                    f"Latest task (interrupted): {user_text[:200]}\n"
                    f"Output so far: {chr(10).join(output_lines[-5:])}"
                )
                ctx.save_project_memory(target_repo, crash_summary)
        except Exception:
            pass
        send({"type": "unlock"})


def run_codex_session_thread(
    ctx: BuilderRuntimeContext,
    ws: WebSocket,
    user_text: str,
    send: Callable[[dict], None],
) -> None:
    """Run one chat/coding task through Codex as the selected builder."""
    settings_snapshot = ctx.get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)
    codex_execution_mode = settings_snapshot.get("codex_execution_mode", "default")
    claude_model = settings_snapshot.get("claude_model", "sonnet")
    claude_effort = settings_snapshot.get("claude_effort", "high")
    auto_review = settings_snapshot.get("auto_review", True)
    agent_label = "Codex"

    proj_id = ctx.get_active_project_id()
    send({"type": "agent_status", "agent": agent_label, "status": "Working..."})
    if proj_id:
        ctx.append_timeline_event(proj_id, {"type": "user_input", "text": user_text})

    output_lines: list[str] = []
    try:
        _validate_target_repo(target_repo)
        if ctx.is_task_cancelled():
            return

        env = ctx.build_cli_env()
        before_snapshot = ctx.capture_repo_change_snapshot(target_repo)
        project_memory = ctx.load_project_memory(target_repo)
        prompt_parts = [
            "You are working inside OMADS (Orchestrated Multi-Agent Development System).",
            "The user interacts with you through a web GUI.",
            "You are the currently selected primary builder for this task.",
            "After your code changes, the reviewer agent will check the result and you may receive findings to address.",
            "Respond in the same language the user writes in.",
        ]
        if project_memory:
            prompt_parts.append("\nProject context:\n" + project_memory)
        # Provide recent chat history for builder handover context
        handover = ctx.build_chat_handover_context(proj_id or "")
        if handover:
            prompt_parts.append(
                "\nRecent conversation in this project (may include messages from another builder):\n"
                + handover
                + "\n\nContinue naturally from this context. Do not repeat or summarize it."
            )
        prompt_parts.append("\nUser request:\n" + user_text)
        prompt = "\n".join(prompt_parts)

        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(target_repo),
        ]
        cmd.extend(_codex_execution_mode_args(codex_execution_mode))
        if codex_model:
            cmd.extend(["-m", codex_model])
        if codex_reasoning:
            cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
        cmd.extend(["-c", _codex_service_tier_arg(codex_fast)])
        cmd.append("-")

        if ctx.is_task_cancelled():
            return
        start_time = time.time()
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(target_repo),
            env=env,
        )
        ctx.process_started(process)

        process.stdin.write(prompt)
        process.stdin.close()

        raw_stdout_buffer: list[str] = []
        completed, raw_stdout_lines, stderr_lines = _stream_codex_process(
            process,
            ctx=ctx,
            send=send,
            agent_label=agent_label,
            text_buffer=output_lines,
            raw_stdout_buffer=raw_stdout_buffer,
            inactivity_error_text="Codex task timed out after 15 minutes without output.",
            emit_stop_message=True,
        )
        if not completed:
            return

        process.wait(timeout=10)
        ctx.process_finished()

        elapsed = round(time.time() - start_time)
        result_text = "\n".join(output_lines).strip()
        if process.returncode != 0 and not ctx.is_task_cancelled():
            failure_detail = _build_codex_failure_detail(
                stdout_text=result_text,
                stderr_lines=stderr_lines,
            )
            send(
                {
                    "type": "task_error",
                    "text": ctx.build_process_failure_text(
                        "Codex Task",
                        process.returncode,
                        result_text=failure_detail,
                        output_lines=output_lines,
                    ),
                }
            )
            return

        after_snapshot = ctx.capture_repo_change_snapshot(target_repo)
        stream_files_changed = extract_codex_changed_files(raw_stdout_buffer)
        snapshot_files_changed = (
            after_snapshot.get("changed_files", [])
            if ctx.repo_snapshot_changed(before_snapshot, after_snapshot)
            else []
        )
        files_changed = ctx.merge_changed_files(stream_files_changed, snapshot_files_changed)
        if files_changed:
            ctx.set_last_files_changed(list(files_changed))

        status_text = f"Done ({elapsed}s)"
        if not output_lines:
            status_text = f"Finished without response ({elapsed}s)"
            send(
                {
                    "type": "chat_response",
                    "agent": "System",
                    "text": _build_codex_empty_output_warning(
                        run_label="Codex task",
                        raw_stdout_lines=raw_stdout_lines,
                        stderr_lines=stderr_lines,
                    ),
                }
            )

        send({"type": "agent_status", "agent": agent_label, "status": status_text})

        if result_text:
            summary_parts = []
            if files_changed:
                summary_parts.append(f"Changed files: {', '.join(f[-60:] for f in files_changed[:20])}")
            summary_parts.append(f"Latest task: {user_text[:200]}")
            summary_parts.append(f"Result: {result_text[:2000]}")
            ctx.save_project_memory(target_repo, "\n".join(summary_parts))

        if files_changed and auto_review:
            review_findings = ctx.run_claude_auto_review(
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
                    + "\n\nRules: Fix ONLY the reported issues. No scope creep. Respond in the same language the user writes in."
                )

                fix_cmd = [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "--json",
                    "-C",
                    str(target_repo),
                ]
                fix_cmd.extend(_codex_execution_mode_args(codex_execution_mode))
                if codex_model:
                    fix_cmd.extend(["-m", codex_model])
                if codex_reasoning:
                    fix_cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
                fix_cmd.extend(["-c", _codex_service_tier_arg(codex_fast)])
                fix_cmd.append("-")

                if ctx.is_task_cancelled():
                    return
                fix_process = subprocess.Popen(
                    fix_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(target_repo),
                    env=env,
                )
                ctx.process_started(fix_process)

                fix_output_lines: list[str] = []
                fix_process.stdin.write(fix_prompt)
                fix_process.stdin.close()

                fix_completed, fix_raw_stdout_lines, fix_stderr_lines = _stream_codex_process(
                    fix_process,
                    ctx=ctx,
                    send=send,
                    agent_label=agent_label,
                    text_buffer=fix_output_lines,
                    inactivity_error_text="Codex fix run timed out after 15 minutes without output.",
                )
                if not fix_completed:
                    return

                fix_process.wait(timeout=10)
                ctx.process_finished()

                if fix_process.returncode != 0 and not ctx.is_task_cancelled():
                    fix_failure_detail = _build_codex_failure_detail(
                        stdout_text="\n".join(fix_output_lines).strip(),
                        stderr_lines=fix_stderr_lines,
                    )
                    send(
                        {
                            "type": "task_error",
                            "text": ctx.build_process_failure_text(
                                "Codex fix run",
                                fix_process.returncode,
                                result_text=fix_failure_detail,
                                output_lines=fix_output_lines,
                            ),
                        }
                    )
                else:
                    if not fix_output_lines:
                        send(
                            {
                                "type": "chat_response",
                                "agent": "System",
                                "text": _build_codex_empty_output_warning(
                                    run_label="Codex fix run",
                                    raw_stdout_lines=fix_raw_stdout_lines,
                                    stderr_lines=fix_stderr_lines,
                                ),
                            }
                        )
                        send({"type": "agent_status", "agent": "Codex", "status": "Fix run finished (no response)"})
                    else:
                        send({"type": "agent_status", "agent": "Codex", "status": "Fixes applied"})

    except FileNotFoundError:
        send(
            {
                "type": "chat_response",
                "agent": "System",
                "text": "Codex CLI not found. Install it with: npm install -g @openai/codex",
            }
        )
    except subprocess.TimeoutExpired:
        send({"type": "chat_response", "agent": "System", "text": "Timeout — Codex CLI took too long."})
    except Exception:
        import logging

        logging.getLogger("omads.gui").error("Codex session error", exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": "An internal error occurred. See the server log for details."})
    finally:
        ctx.process_finished()
        if output_lines:
            summary = f"Latest task (interrupted): {user_text[:200]}\nOutput so far: {' '.join(output_lines[-5:])}"
            ctx.save_project_memory(target_repo, summary)
        send({"type": "unlock"})


def run_codex_auto_review(
    ctx: BuilderRuntimeContext,
    ws: WebSocket,
    target_repo: str,
    files_changed: list[str],
    send: Callable[[dict], None],
) -> str | None:
    """Run Codex as the automatic reviewer for Claude-built file changes."""
    breaker_label = "Codex Review"
    settings_snapshot = ctx.get_settings_snapshot()
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)
    codex_execution_mode = settings_snapshot.get("codex_execution_mode", "default")

    short_files = [f.rsplit("/", 1)[-1] if "/" in f else f for f in files_changed[:10]]
    file_list = ", ".join(short_files)

    send({"type": "agent_status", "agent": breaker_label, "status": f"Reviewing {len(files_changed)} changed file(s)..."})
    _validate_target_repo(target_repo)

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
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--json", "-C", str(target_repo)]
        cmd.extend(_codex_execution_mode_args(codex_execution_mode))
        if codex_model:
            cmd.extend(["-m", codex_model])
        if codex_reasoning:
            cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
        cmd.extend(["-c", _codex_service_tier_arg(codex_fast)])
        cmd.append("-")

        send({"type": "stream_text", "agent": breaker_label, "text": f"Starting review: {file_list}"})
        send({"type": "stream_text", "agent": breaker_label, "text": "Checking for: security, logic bugs, error handling, performance"})

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(target_repo),
            env=ctx.build_cli_env(),
        )
        ctx.process_started(process)

        process.stdin.write(review_prompt)
        process.stdin.close()

        heartbeat_stop = threading.Event()
        start_time = time.time()

        def heartbeat():
            while not heartbeat_stop.is_set():
                heartbeat_stop.wait(10)
                if not heartbeat_stop.is_set():
                    elapsed = round(time.time() - start_time)
                    send({"type": "agent_status", "agent": breaker_label, "status": f"Codex is analyzing... ({elapsed}s)"})

        threading.Thread(target=heartbeat, daemon=True).start()

        output_lines: list[str] = []
        completed, raw_stdout_lines, stderr_lines = _stream_codex_process(
            process,
            ctx=ctx,
            send=send,
            agent_label=breaker_label,
            text_buffer=output_lines,
            inactivity_error_text="Codex auto-review timed out after 15 minutes without output.",
        )
        heartbeat_stop.set()
        if not completed:
            ctx.process_finished()
            return "\n".join(output_lines).strip() if output_lines else None

        process.wait(timeout=10)
        ctx.process_finished()

        elapsed = round(time.time() - start_time)
        output = "\n".join(output_lines).strip()
        if not output and process.returncode != 0:
            output = _build_codex_failure_detail(stdout_text="", stderr_lines=stderr_lines)
            if not output:
                output = f"Review error: Codex exited with code {process.returncode}"
            send({"type": "stream_result", "agent": breaker_label, "text": output, "is_error": True})

        if not output and process.returncode == 0:
            send(
                {
                    "type": "stream_text",
                    "agent": breaker_label,
                    "text": _build_codex_empty_output_warning(
                        run_label="Codex auto-review",
                        raw_stdout_lines=raw_stdout_lines,
                        stderr_lines=stderr_lines,
                    ),
                }
            )
            send({"type": "agent_status", "agent": breaker_label, "status": "Review finished without response"})
            return None

        send({"type": "stream_text", "agent": breaker_label, "text": f"Review completed ({elapsed}s)"})

        if process.returncode != 0:
            send({"type": "agent_status", "agent": breaker_label, "status": f"Codex error (exit {process.returncode}) - no review result"})
            return None
        output_lc = output.lower()
        if output and "no issues found" not in output_lc:
            send({"type": "agent_activity", "agent": breaker_label, "activity": "finding", "text": output})
            send({"type": "agent_status", "agent": breaker_label, "status": "Findings detected -> Claude Code is fixing"})
            return output

        send({"type": "agent_status", "agent": breaker_label, "status": "All clear"})
        return None
    except FileNotFoundError:
        send({"type": "agent_status", "agent": breaker_label, "status": "Codex CLI not installed - review skipped"})
        return None
    except Exception as exc:
        send({"type": "agent_status", "agent": breaker_label, "status": f"Review error: {str(exc)[:100]}"})
        return None
    finally:
        ctx.process_finished()


def run_claude_auto_review(
    ctx: BuilderRuntimeContext,
    target_repo: str,
    files_changed: list[str],
    send: Callable[[dict], None],
    *,
    model: str,
    effort: str,
) -> str | None:
    """Run Claude as the automatic reviewer and return findings when present."""
    breaker_label = "Claude Review"
    short_files = [f.rsplit("/", 1)[-1] if "/" in f else f for f in files_changed[:10]]
    file_list = ", ".join(short_files)

    send({"type": "agent_status", "agent": breaker_label, "status": f"Reviewing {len(files_changed)} changed file(s)..."})
    _validate_target_repo(target_repo)

    project_memory = ctx.load_project_memory(target_repo)
    reviewer_context = (
        "You are reviewing code changes inside OMADS. "
        "Read and analyze the code, but do NOT edit anything. "
        "Respond in the same language the user writes in.\n"
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
                "claude",
                "-p",
                review_prompt,
                "--output-format",
                "stream-json",
                "--verbose",
                "--model",
                model,
                "--effort",
                effort,
                "--append-system-prompt",
                reviewer_context,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=target_repo,
            env=ctx.build_cli_env(),
        )
        ctx.process_started(process)

        output_lines: list[str] = []
        final_result = ""
        for line in process.stdout:
            if ctx.is_task_cancelled():
                process.kill()
                process.wait()
                return None
            for event in ctx.parse_claude_stream_line(line):
                kind = event["kind"]
                if kind == "tool":
                    send(
                        {
                            "type": "stream_tool",
                            "agent": breaker_label,
                            "tool": event["tool"],
                            "description": event["description"],
                            "detail": event["detail"],
                        }
                    )
                elif kind == "text":
                    send({"type": "stream_text", "agent": breaker_label, "text": event["text"]})
                    output_lines.append(event["text"])
                elif kind == "thinking":
                    send({"type": "stream_thinking", "agent": breaker_label, "text": event["text"]})
                elif kind == "tool_result":
                    send(
                        {
                            "type": "stream_result",
                            "agent": breaker_label,
                            "text": event["text"],
                            "is_error": event["is_error"],
                        }
                    )
                elif kind == "result":
                    final_result = event["text"]
                elif kind == "rate_limit":
                    limit = ctx.update_claude_limit_status(event["rate_limit_info"], source="auto_review_stream")
                    send({"type": "claude_limit_update", "limit": limit})

        process.wait(timeout=30)
        ctx.process_finished()

        review_text = final_result if final_result else "\n".join(output_lines).strip()
        if process.returncode != 0:
            send({"type": "agent_status", "agent": breaker_label, "status": f"Claude error (exit {process.returncode}) - no review result"})
            return None

        if review_text and "no issues found" not in review_text.lower():
            send({"type": "agent_activity", "agent": breaker_label, "activity": "finding", "text": review_text})
            send({"type": "agent_status", "agent": breaker_label, "status": "Findings detected -> Codex is fixing"})
            return review_text

        send({"type": "agent_status", "agent": breaker_label, "status": "All clear"})
        return None
    except FileNotFoundError:
        send({"type": "agent_status", "agent": breaker_label, "status": "Claude CLI not installed - review skipped"})
        return None
    except Exception as exc:
        send({"type": "agent_status", "agent": breaker_label, "status": f"Review error: {str(exc)[:100]}"})
        return None
    finally:
        ctx.process_finished()
