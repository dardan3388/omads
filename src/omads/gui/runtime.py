"""Laufzeit-State, Broadcasts und Task-Runner fuer die OMADS GUI."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

from fastapi import WebSocket

from .streaming import (
    parse_claude_stream_line,
    parse_codex_jsonl_line,
    strip_fixes_needed_marker,
)
from .state import (
    _append_history,
    _append_log,
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

# Aktive WebSocket-Verbindungen (Lock schützt add/remove/iterate)
_connections_lock = threading.Lock()
_connections: list[WebSocket] = []

# Laufender Prozess (für Abbruch) — Lock schützt gegen Race Conditions
_process_lock = threading.Lock()
_active_process: subprocess.Popen | None = None
_task_cancelled: bool = False
_last_files_changed: list[str] = []  # Zuletzt geänderte Dateien (für Review "Letzter Task")
_pending_review_fixes: dict[str, str] = {}  # {repo_path: fixes_text} — pro Projekt


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
    """Sendet eine Nachricht an alle verbundenen Clients."""
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
    """Synchroner Wrapper für broadcast (aus Threads heraus)."""
    # Log-Events pro Projekt persistieren
    proj_id = proj_id_override or _get_active_project_id()
    if proj_id:
        try:
            _append_log(proj_id, dict(msg))
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
    """Einziger Einstiegspunkt: Claude CLI bekommt alles — Fragen, Chat, Code-Aufträge.

    Claude CLI entscheidet selbst was zu tun ist. Nach Code-Änderungen läuft
    automatisch ein Codex-Review im Hintergrund.
    """
    import time as _time

    global _active_process, _task_cancelled
    with _process_lock:
        _task_cancelled = False

    settings_snapshot = _get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    repo_key = str(Path(target_repo).resolve())
    model = settings_snapshot.get("claude_model", "sonnet")
    effort = settings_snapshot.get("claude_effort", "high")
    auto_review = settings_snapshot.get("auto_review", True)
    agent_label = "Claude Code"

    # Projekt-ID beim Task-Start einfrieren (bleibt korrekt auch bei Projektwechsel)
    _frozen_proj_id = _get_active_project_id()

    def send(msg: dict):
        broadcast_sync(msg, proj_id_override=_frozen_proj_id)

    send({"type": "agent_status", "agent": agent_label, "status": "Working..."})

    # Historie: User-Eingabe loggen
    proj_id = _frozen_proj_id
    if proj_id:
        _append_history(proj_id, {"type": "user_input", "text": user_text})

    try:
        env = _build_cli_env()
        before_snapshot = _capture_repo_change_snapshot(target_repo)

        # OMADS-Kontext für Claude CLI
        omads_context = (
            "You are working inside OMADS (Orchestrated Multi-Agent Development System). "
            "The user interacts with you through a web GUI. "
            "After every code change, Codex CLI automatically reviews your code in the background. "
            "If Codex finds issues, you will receive the findings as the next message and should fix them. "
            "Respond in English.\n\n"
        )

        # Projekt-Memory NUR bei neuer Session laden (spart Tokens bei --resume)
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

        # Claude CLI mit stream-json für Live-Output
        cmd = ["claude", "-p", user_text, "--output-format", "stream-json",
               "--verbose", "--model", model,
               "--effort", effort,
               "--append-system-prompt", omads_context]

        # Session fortsetzen wenn vorhanden (Gesprächsgedächtnis)
        if session_id:
            cmd.extend(["--resume", session_id])

        # --permission-mode deaktiviert: triggert afk-mode Beta-Header Bug in CLI v2.1.74
        # Permission wird stattdessen über ~/.claude/settings.json gesteuert

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
            if proj_id:
                _append_history(proj_id, {
                    "type": "task_error",
                    "text": f"Claude task exit code {process.returncode}",
                    "duration_s": elapsed,
                })
            return

        # Session-ID für Folge-Nachrichten speichern (Gesprächsgedächtnis)
        if captured_session_id and success:
            _set_chat_session(repo_key, captured_session_id)

        after_snapshot = _capture_repo_change_snapshot(target_repo) if success else before_snapshot
        snapshot_files = after_snapshot.get("changed_files", []) if _repo_snapshot_changed(before_snapshot, after_snapshot) else []
        files_changed = _merge_changed_files(files_changed, snapshot_files)

        # Letzte geänderte Dateien merken (für Review "Letzter Task")
        global _last_files_changed
        if files_changed:
            _last_files_changed = list(files_changed)

        # Ergebnis anzeigen — nur wenn nichts live gestreamt wurde
        result_text = final_result if final_result else "\n".join(output_lines)
        if result_text and not output_lines:
            send({"type": "chat_response", "agent": agent_label, "text": result_text})

        send({"type": "agent_status", "agent": agent_label, "status": f"Done ({elapsed}s)"})

        # Projekt-Memory aktualisieren (Zusammenfassung für nächste Session)
        if success and (output_lines or result_text):
            summary_parts = []
            if files_changed:
                summary_parts.append(f"Changed files: {', '.join(f[-60:] for f in files_changed[:20])}")
            # Letzte Ausgaben als Kontext-Zusammenfassung
            recent_output = "\n".join(output_lines[-10:]) if output_lines else result_text
            summary_parts.append(f"Latest task: {user_text[:200]}")
            summary_parts.append(f"Result: {recent_output[:2000]}")
            _save_project_memory(target_repo, "\n".join(summary_parts))

        # Historie: Ergebnis loggen
        if proj_id:
            _append_history(proj_id, {
                "type": "claude_response", "text": result_text[:500],
                "files_changed": len(files_changed), "duration_s": elapsed,
            })

        # === CODEX AUTO-REVIEW ===
        # Wenn Claude Dateien geändert hat und Auto-Review aktiviert ist → Codex reviewt
        if files_changed and auto_review and success:
            review_findings = _run_codex_auto_review(ws, target_repo, files_changed, send)

            # Wenn Codex Probleme gefunden hat → Findings an Claude CLI zurückgeben
            if review_findings:
                send({"type": "agent_status", "agent": "Claude Code",
                      "status": "Fixing Codex findings..."})

                fix_prompt = (
                    "The Codex auto-reviewer found the following issues in your code. "
                    "Please fix them:\n\n" + review_findings + "\n\n"
                    "Rules: Fix ONLY the reported issues. No scope creep."
                )

                # Claude CLI frische Session für Fix
                fix_cmd = ["claude", "-p", fix_prompt, "--output-format", "stream-json",
                           "--verbose", "--model", model,
                           "--effort", effort,
                           "--append-system-prompt", omads_context]
                fix_session = _get_chat_session(repo_key)
                if fix_session:
                    fix_cmd.extend(["--resume", fix_session])

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
        # Memory auch bei Crash/Rate-Limit sichern (was wir bisher haben)
        try:
            if output_lines:
                crash_summary = (
                    f"Latest task (interrupted): {user_text[:200]}\n"
                    f"Output so far: {chr(10).join(output_lines[-5:])}"
                )
                _save_project_memory(target_repo, crash_summary)
        except Exception:
            pass
        # Unlock im Frontend
        send({"type": "unlock"})


def _run_codex_session_thread(ws: WebSocket, user_text: str) -> None:
    """Run one chat/coding task through Codex as the selected builder."""
    import select
    import time as _time

    global _active_process, _task_cancelled
    with _process_lock:
        _task_cancelled = False

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
        _append_history(proj_id, {"type": "user_input", "text": user_text})

    output_lines: list[str] = []
    try:
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
            if proj_id:
                _append_history(proj_id, {
                    "type": "task_error",
                    "text": f"Codex task exit code {process.returncode}",
                    "duration_s": elapsed,
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

        if proj_id:
            _append_history(proj_id, {
                "type": "builder_response",
                "agent": agent_label,
                "text": result_text[:500],
                "files_changed": len(files_changed),
                "duration_s": elapsed,
            })

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


def _run_review_thread(ws: WebSocket, scope: str, focus: str, custom_scope: str) -> None:
    """Review-Thread: 3-Schritt-Review mit Transparenz.

    Schritt 1: Claude Code reviewt → Ergebnis sichtbar
    Schritt 2: Codex reviewt (parallel-fähig) → Ergebnis sichtbar
    Schritt 3: Claude Code bekommt Codex-Ergebnis → Synthese + Fix-Vorschläge → "Fixes anwenden?" Button

    scope: 'project' | 'last_task' | 'custom'
    focus: 'all' | 'security' | 'bugs' | 'performance'
    custom_scope: Pfad(e) bei scope='custom'
    """
    import time as _time

    global _active_process, _task_cancelled
    with _process_lock:
        _task_cancelled = False

    settings_snapshot = _get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    model = settings_snapshot.get("claude_model", "sonnet")

    # Projekt-ID beim Task-Start einfrieren
    _frozen_proj_id = _get_active_project_id()

    def send(msg: dict):
        broadcast_sync(msg, proj_id_override=_frozen_proj_id)

    # Scope bestimmen
    if scope == "last_task" and _last_files_changed:
        review_files = _last_files_changed
        scope_desc = f"Last task ({len(review_files)} files)"
    elif scope == "custom" and custom_scope.strip():
        review_files = [f.strip() for f in custom_scope.split(",") if f.strip()]
        scope_desc = f"Custom: {custom_scope[:100]}"
    else:
        review_files = []
        scope_desc = "Whole project"

    # Fokus-Beschreibung
    focus_map = {
        "all": "Security, bugs, error handling, performance",
        "security": "Security issues (injection, XSS, secrets, auth)",
        "bugs": "Logic bugs, race conditions, edge cases",
        "performance": "Performance issues, memory leaks, inefficient algorithms",
    }
    focus_desc = focus_map.get(focus, focus_map["all"])

    file_hint = ""
    if review_files:
        file_hint = f"\n\nPay special attention to these files:\n" + "\n".join(f"- {f}" for f in review_files[:30])

    env = _build_cli_env()

    send({"type": "stream_text", "agent": "Review",
          "text": f"**Review started**\nScope: {scope_desc}\nFocus: {focus_desc}\n\n"
                  "Current fixed flow: Step 1 (Claude Code) -> Step 2 (Codex) -> Step 3 (Claude Code synthesis + fix suggestions)"})

    try:
        # ── SCHRITT 1: Claude Code Review ─────────────────────────
        send({"type": "agent_status", "agent": "Claude Code", "status": "Step 1/3 - review in progress..."})

        project_memory = _load_project_memory(target_repo)
        claude_context = (
            "You are performing a code review (NO changes!). "
            "Read and analyze the code, but do NOT edit anything.\n\n"
        )
        if project_memory:
            claude_context += f"Project context:\n{project_memory}\n\n"

        review_prompt = (
            f"Perform a thorough code review. Focus: {focus_desc}.{file_hint}\n\n"
            "Respond with a structured analysis:\n"
            "## Summary\n## Findings (sorted by severity: CRITICAL > HIGH > MEDIUM)\n"
            "## Positive notes\n\nRespond in English."
        )

        cmd = ["claude", "-p", review_prompt, "--output-format", "stream-json",
               "--verbose", "--model", model,
               "--append-system-prompt", claude_context]

        repo_key = str(Path(target_repo).resolve())
        session_id = _get_chat_session(repo_key)
        if session_id:
            cmd.extend(["--resume", session_id])

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=target_repo, env=env,
        )
        with _process_lock:
            _active_process = process

        claude_output = []
        captured_session_id = None

        for line in process.stdout:
            if _task_cancelled:
                process.kill()
                return
            parsed_session_id, _ = _forward_claude_stream_line(
                line,
                agent_label="Claude Code",
                send=send,
                text_buffer=claude_output,
                rate_limit_source="review_stream",
            )
            if parsed_session_id and not captured_session_id:
                captured_session_id = parsed_session_id

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        with _process_lock:
            _active_process = None
        claude_review = "\n".join(claude_output)

        if process.returncode != 0 and not _task_cancelled:
            send({
                "type": "task_error",
                "text": _build_process_failure_text(
                    "Review step 1 (Claude Code)",
                    process.returncode,
                    output_lines=claude_output,
                ),
            })
            return

        if captured_session_id and process.returncode == 0:
            _set_chat_session(repo_key, captured_session_id)

        send({"type": "agent_status", "agent": "Claude Code", "status": "Step 1/3 done"})

        if _task_cancelled:
            return

        # ── SCHRITT 2: Codex Review ───────────────────────────────
        send({"type": "agent_status", "agent": "Codex Review", "status": "Step 2/3 - review in progress..."})

        codex_prompt = (
            "You are a code reviewer. Perform a thorough review.\n"
            f"Focus: {focus_desc}\n"
        )
        if review_files:
            codex_prompt += f"Files: {', '.join(f.rsplit('/', 1)[-1] for f in review_files[:15])}\n"
        codex_prompt += (
            "\nRespond with:\n## Files reviewed\n## Findings\n"
            "- [CRITICAL/HIGH/MEDIUM] file:line: description\n## Positive notes\n\n"
            "Respond in English."
        )

        codex_model = settings_snapshot.get("codex_model", "")
        codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
        codex_fast = settings_snapshot.get("codex_fast", False)
        codex_review = ""

        try:
            codex_cmd = ["codex", "exec", "-s", "read-only", "--ephemeral",
                         "--skip-git-repo-check", "--json", "-C", str(target_repo)]
            if codex_model:
                codex_cmd.extend(["-m", codex_model])
            if codex_reasoning:
                codex_cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
            if codex_fast:
                codex_cmd.extend(["-c", 'service_tier="fast"'])
            codex_cmd.append("-")

            codex_process = subprocess.Popen(
                codex_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, cwd=str(target_repo), env=_build_cli_env(),
            )
            codex_process.stdin.write(codex_prompt)
            codex_process.stdin.close()

            import select
            codex_lines = []
            _INACTIVITY_LIMIT = 900  # 15 Minuten ohne Output → kill
            last_output_time = time.time()
            while True:
                if _task_cancelled:
                    codex_process.kill()
                    codex_process.wait()
                    return
                if time.time() - last_output_time > _INACTIVITY_LIMIT:
                    codex_process.kill()
                    codex_process.wait()
                    send({"type": "agent_status", "agent": "Codex Review", "status": "Inactivity - Codex cancelled (15 min without output)"})
                    codex_review = "\n".join(codex_lines) if codex_lines else "(Codex inactive)"
                    break
                ready, _, _ = select.select([codex_process.stdout], [], [], 5.0)
                if ready:
                    line = codex_process.stdout.readline()
                    if not line:  # EOF — Codex ist fertig
                        codex_process.wait(timeout=10)
                        codex_review = "\n".join(codex_lines)
                        send({"type": "agent_status", "agent": "Codex Review", "status": "Step 2/3 done"})
                        break
                    last_output_time = time.time()
                    _forward_codex_stream_line(
                        line,
                        agent_label="Codex Review",
                        send=send,
                        text_buffer=codex_lines,
                    )

        except FileNotFoundError:
            send({"type": "agent_status", "agent": "Codex Review",
                  "status": "Codex CLI not installed - skipped"})
            codex_review = "(Codex unavailable)"
        except Exception as e:
            try:
                codex_process.kill()
                codex_process.wait()
            except Exception:
                pass
            send({"type": "agent_status", "agent": "Codex Review", "status": f"Error: {str(e)[:100]}"})
            codex_review = f"(Codex error: {str(e)[:200]})"

        if _task_cancelled:
            return

        # ── SCHRITT 3: Claude Code Synthese ───────────────────────
        send({"type": "agent_status", "agent": "Claude Code",
              "status": "Step 3/3 - synthesis: comparing both reviews..."})
        send({"type": "stream_text", "agent": "Review",
              "text": "---\n**Step 3: Claude Code is now analyzing the Codex review and preparing the final report...**"})

        synthesis_prompt = (
            "You just performed a code review (step 1). "
            "Now Codex has independently reviewed the same project (step 2). "
            "Compare both reviews and produce a final report.\n\n"
            f"=== YOUR REVIEW (Claude Code) ===\n{claude_review[:6000]}\n\n"
            f"=== CODEX REVIEW ===\n{codex_review[:6000]}\n\n"
            "Task:\n"
            "1. What did both reviews find (overlap)?\n"
            "2. What did only Codex find that you missed?\n"
            "3. What did only you find?\n"
            "4. Build a prioritized list of all REAL findings that should be fixed.\n"
            "   Ignore false positives and overly minor style remarks.\n\n"
            "Respond with:\n"
            "## Overlap (found by both)\n"
            "## Found only by Codex\n"
            "## Found only by Claude Code\n"
            "## Final fix plan\n"
            "For each fix: file, line, and exactly what should be changed.\n\n"
            "IMPORTANT: Do NOT make any changes, only analyze. Respond in English.\n\n"
            "REQUIRED: As the VERY LAST line of your answer, write exactly one of these markers:\n"
            "FIXES_NEEDED: true\n"
            "or\n"
            "FIXES_NEEDED: false\n"
            "Nothing else on that line. true = there are real fixes needed. false = everything is fine or only style remarks remain."
        )

        synthesis_context = (
            "You are comparing your own review with Codex's review. "
            "Be honest: if Codex found something important that you missed, say so. "
            "At the end, the user should decide whether the fixes should be applied. "
            "Do NOT make code changes.\n"
        )

        synth_cmd = ["claude", "-p", synthesis_prompt, "--output-format", "stream-json",
                     "--verbose", "--model", model,
                     "--append-system-prompt", synthesis_context]

        if captured_session_id:
            synth_cmd.extend(["--resume", captured_session_id])

        synth_process = subprocess.Popen(
            synth_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=target_repo, env=env,
        )
        with _process_lock:
            _active_process = synth_process

        synthesis_output = []
        synth_session_id = None

        for line in synth_process.stdout:
            if _task_cancelled:
                synth_process.kill()
                return
            parsed_session_id, _ = _forward_claude_stream_line(
                line,
                agent_label="Claude Code",
                send=send,
                text_buffer=synthesis_output,
                rate_limit_source="synthesis_stream",
            )
            if parsed_session_id and not synth_session_id:
                synth_session_id = parsed_session_id

        try:
            synth_process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            synth_process.kill()
            synth_process.wait()
        with _process_lock:
            _active_process = None

        if synth_process.returncode != 0 and not _task_cancelled:
            send({
                "type": "task_error",
                "text": _build_process_failure_text(
                    "Review step 3 (synthesis)",
                    synth_process.returncode,
                    output_lines=synthesis_output,
                ),
            })
            return

        if synth_session_id and synth_process.returncode == 0:
            _set_chat_session(repo_key, synth_session_id)

        synthesis_text, has_fixes = strip_fixes_needed_marker("\n".join(synthesis_output))

        send({"type": "agent_status", "agent": "Claude Code", "status": "Step 3/3 done"})
        send({"type": "stream_text", "agent": "Review",
              "text": "---\n**Review completed** - Current fixed flow: Claude Code review -> Codex review -> Claude Code synthesis"})

        if has_fixes:
            # Fix-Vorschläge als Kontext speichern für den Apply-Schritt (pro Projekt)
            global _pending_review_fixes
            _pending_review_fixes[str(Path(target_repo).resolve())] = synthesis_text
            send({"type": "review_fixes_available",
                  "text": "Fixes were identified. Should the suggested fixes be applied?"})

    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Review error: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": f"Review error: {str(e)[:200]}"})
    finally:
        with _process_lock:
            _active_process = None
        send({"type": "unlock"})


def _run_codex_auto_review(ws: WebSocket, target_repo: str, files_changed: list[str], send: callable) -> str | None:
    """Codex CLI Auto-Review: prüft geänderte Dateien automatisch nach Claude-Änderungen.

    Gibt die Findings als String zurück (oder None wenn alles OK).
    """
    breaker_label = "Codex Review"
    settings_snapshot = _get_settings_snapshot()
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)

    # Nur Dateinamen (kurz) für den Prompt
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

        # Auto-Review-Prozess registrieren damit stop() ihn killen kann
        global _active_process
        with _process_lock:
            _active_process = process

        # Prompt an stdin senden und schließen
        process.stdin.write(review_prompt)
        process.stdin.close()

        # Heartbeat-Thread: zeigt alle 10s Fortschritt während Codex arbeitet
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

        # Stdout lesen — JSONL-Format, Inactivity-Timeout als Safety-Net
        import select
        output_lines = []
        _INACTIVITY_LIMIT = 900  # 15 Minuten ohne Output → kill
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
                if not line:  # EOF — Codex fertig
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

        # Ergebnis auswerten — nur bei erfolgreichem Exit als Finding behandeln
        if process.returncode != 0:
            send({"type": "agent_status", "agent": breaker_label,
                  "status": f"Codex error (exit {process.returncode}) - no review result"})
            return None
        output_lc = output.lower()
        if output and "no issues found" not in output_lc:
            send({"type": "agent_activity", "agent": breaker_label, "activity": "finding", "text": output})
            send({"type": "agent_status", "agent": breaker_label, "status": "Findings detected -> Claude Code is fixing"})
            return output  # Findings zurückgeben für Claude-Fix
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
