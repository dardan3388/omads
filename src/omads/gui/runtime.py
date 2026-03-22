"""Laufzeit-State, Broadcasts und Task-Runner fuer die OMADS GUI."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

from fastapi import WebSocket

from .state import (
    _append_history,
    _append_log,
    _build_cli_env,
    _build_process_failure_text,
    _get_active_project_id,
    _get_chat_session,
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

def _run_claude_session_thread(ws: WebSocket, user_text: str) -> None:
    """Einziger Einstiegspunkt: Claude CLI bekommt alles — Fragen, Chat, Code-Aufträge.

    Claude CLI entscheidet selbst was zu tun ist. Nach Code-Änderungen läuft
    automatisch ein Codex-Review im Hintergrund.
    """
    import time as _time
    from omads.cli.main import _format_tool_use

    global _active_process, _task_cancelled
    with _process_lock:
        _task_cancelled = False

    settings_snapshot = _get_settings_snapshot()
    target_repo = settings_snapshot.get("target_repo", str(Path(".").resolve()))
    repo_key = str(Path(target_repo).resolve())
    model = settings_snapshot.get("claude_model", "sonnet")
    max_turns = str(max(1, min(int(settings_snapshot.get("claude_max_turns", 25)), 100)))
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
               "--verbose", "--max-turns", max_turns, "--model", model,
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

            line = line.strip()
            if not line:
                continue

            try:
                ev = json.loads(line)
                ev_type = ev.get("type", "")

                # Session-ID extrahieren
                if "session_id" in ev and not captured_session_id:
                    captured_session_id = ev["session_id"]

                if ev_type == "assistant":
                    msg = ev.get("message", {})
                    for block in msg.get("content", []):
                        bt = block.get("type")
                        if bt == "tool_use":
                            tool_name = block.get("name", "?")
                            tool_input = block.get("input", {})
                            desc = _format_tool_use(tool_name, tool_input)

                            # Datei-Änderungen tracken
                            if tool_name in ("Write", "Edit"):
                                fpath = tool_input.get("file_path", "")
                                if fpath and fpath not in files_changed:
                                    files_changed.append(fpath)

                            detail = ""
                            if tool_name == "Edit":
                                old = tool_input.get("old_string", "")
                                new = tool_input.get("new_string", "")
                                if old and new:
                                    detail = f"--- alt ---\n{old}\n--- neu ---\n{new}"
                            elif tool_name == "Write":
                                content = tool_input.get("content", "")
                                if content:
                                    detail = content
                            elif tool_name == "Bash":
                                detail = tool_input.get("command", "")
                            elif tool_name == "Read":
                                detail = tool_input.get("file_path", "")
                            elif tool_name in ("Glob", "Grep"):
                                detail = tool_input.get("pattern", "")

                            send({"type": "stream_tool", "agent": agent_label, "tool": tool_name,
                                  "description": desc, "detail": detail})
                        elif bt == "text":
                            text = block.get("text", "").strip()
                            if text:
                                send({"type": "stream_text", "agent": agent_label, "text": text})
                                output_lines.append(text)
                        elif bt == "thinking":
                            # Claude's Denkprozess (Extended Thinking) — immer auf Englisch
                            thinking = block.get("thinking", "").strip()
                            if thinking:
                                send({"type": "stream_thinking", "agent": agent_label,
                                      "text": f"[Thinking: {len(thinking)} chars]"})

                elif ev_type == "user":
                    # Tool-Ergebnisse — was Claude nach Read/Bash/etc. zurückbekommt
                    msg_content = ev.get("message", {})
                    content_blocks = msg_content.get("content", []) if isinstance(msg_content, dict) else []
                    for block in content_blocks:
                        if block.get("type") == "tool_result":
                            is_error = block.get("is_error", False)
                            result_content = block.get("content", "")
                            if isinstance(result_content, str) and result_content.strip():
                                send({"type": "stream_result", "agent": agent_label,
                                      "text": result_content, "is_error": is_error})

                elif ev_type == "result":
                    final_result = ev.get("result", "")
                elif ev_type == "rate_limit_event":
                    rl_info = ev.get("rate_limit_info", {})
                    if rl_info:
                        limit = _update_claude_limit_status(rl_info, source="task_stream")
                        send({"type": "claude_limit_update", "limit": limit})

            except (ValueError, KeyError, TypeError):
                continue

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
                           "--verbose", "--max-turns", max_turns, "--model", model,
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
                    fline = fline.strip()
                    if not fline:
                        continue
                    try:
                        fev = json.loads(fline)
                        fev_type = fev.get("type", "")
                        if "session_id" in fev and not fix_session_id:
                            fix_session_id = fev["session_id"]
                        if fev_type == "assistant":
                            for block in fev.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    tool_name = block.get("name", "?")
                                    tool_input = block.get("input", {})
                                    desc = _format_tool_use(tool_name, tool_input)
                                    detail = ""
                                    if tool_name == "Edit":
                                        old = tool_input.get("old_string", "")
                                        new = tool_input.get("new_string", "")
                                        if old and new:
                                            detail = f"--- alt ---\n{old}\n--- neu ---\n{new}"
                                    elif tool_name == "Write":
                                        content = tool_input.get("content", "")
                                        if content:
                                            detail = content
                                    elif tool_name == "Bash":
                                        detail = tool_input.get("command", "")
                                    elif tool_name == "Read":
                                        detail = tool_input.get("file_path", "")
                                    elif tool_name in ("Glob", "Grep"):
                                        detail = tool_input.get("pattern", "")
                                    send({"type": "stream_tool", "agent": "Claude Code",
                                          "tool": tool_name, "description": desc, "detail": detail})
                                elif block.get("type") == "text":
                                    txt = block.get("text", "").strip()
                                    if txt:
                                        send({"type": "stream_text", "agent": "Claude Code", "text": txt})
                                        fix_output_lines.append(txt)
                        elif fev_type == "rate_limit_event":
                            rl_info = fev.get("rate_limit_info", {})
                            if rl_info:
                                limit = _update_claude_limit_status(rl_info, source="fix_stream")
                                send({"type": "claude_limit_update", "limit": limit})
                        elif fev_type == "result":
                            fix_result = fev.get("result", "")
                            if fix_result and not fix_output_lines:
                                send({"type": "chat_response", "agent": "Claude Code", "text": fix_result})
                    except (ValueError, KeyError, TypeError):
                        continue

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
                            "Claude Code Fix-Lauf",
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
    max_turns = str(max(1, min(int(settings_snapshot.get("claude_max_turns", 25)), 100)))

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
                  "Flow: Step 1 (Claude Code) -> Step 2 (Codex) -> Step 3 (synthesis + fix suggestions)"})

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
               "--verbose", "--max-turns", max_turns, "--model", model,
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
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                ev_type = ev.get("type", "")
                if "session_id" in ev and not captured_session_id:
                    captured_session_id = ev["session_id"]
                if ev_type == "assistant":
                    for block in ev.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                send({"type": "stream_text", "agent": "Claude Code", "text": text})
                                claude_output.append(text)
                        elif block.get("type") == "tool_use":
                            from omads.cli.main import _format_tool_use
                            tool_name = block.get("name", "?")
                            desc = _format_tool_use(tool_name, block.get("input", {}))
                            send({"type": "stream_tool", "agent": "Claude Code",
                                  "tool": tool_name, "description": desc, "detail": ""})
                elif ev_type == "rate_limit_event":
                    rl_info = ev.get("rate_limit_info", {})
                    if rl_info:
                        limit = _update_claude_limit_status(rl_info, source="review_stream")
                        send({"type": "claude_limit_update", "limit": limit})
            except (ValueError, KeyError, TypeError):
                continue

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
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    # JSONL parsen: Text aus item.completed
                    try:
                        cev = json.loads(line)
                        cev_type = cev.get("type", "")
                        if cev_type == "item.completed":
                            item_text = cev.get("item", {}).get("text", "")
                            if item_text:
                                codex_lines.append(item_text)
                                send({"type": "stream_text", "agent": "Codex Review", "text": item_text})
                    except (json.JSONDecodeError, ValueError):
                        if line.strip():
                            codex_lines.append(line)
                            send({"type": "stream_text", "agent": "Codex Review", "text": line})

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
                     "--verbose", "--max-turns", max_turns, "--model", model,
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
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                ev_type = ev.get("type", "")
                if "session_id" in ev and not synth_session_id:
                    synth_session_id = ev["session_id"]
                if ev_type == "assistant":
                    for block in ev.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                send({"type": "stream_text", "agent": "Claude Code", "text": text})
                                synthesis_output.append(text)
                        elif block.get("type") == "tool_use":
                            from omads.cli.main import _format_tool_use
                            tool_name = block.get("name", "?")
                            desc = _format_tool_use(tool_name, block.get("input", {}))
                            send({"type": "stream_tool", "agent": "Claude Code",
                                  "tool": tool_name, "description": desc, "detail": ""})
                elif ev_type == "rate_limit_event":
                    rl_info = ev.get("rate_limit_info", {})
                    if rl_info:
                        limit = _update_claude_limit_status(rl_info, source="synthesis_stream")
                        send({"type": "claude_limit_update", "limit": limit})
            except (ValueError, KeyError, TypeError):
                continue

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

        synthesis_text = "\n".join(synthesis_output)

        # Fix-Plan vorhanden? → Marker-basierte Erkennung (kein Keyword-Guessing)
        has_fixes = "fixes_needed: true" in synthesis_text.lower()
        # Marker aus der sichtbaren Ausgabe entfernen
        for marker in ["FIXES_NEEDED: true", "FIXES_NEEDED: false",
                        "fixes_needed: true", "fixes_needed: false"]:
            synthesis_text = synthesis_text.replace(marker, "").strip()

        send({"type": "agent_status", "agent": "Claude Code", "status": "Step 3/3 done"})
        send({"type": "stream_text", "agent": "Review",
              "text": "---\n**Review completed** - 3 steps: Claude Code review -> Codex review -> synthesis"})

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
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                # JSONL parsen: Text aus item.completed
                try:
                    cev = json.loads(line)
                    cev_type = cev.get("type", "")
                    if cev_type == "item.completed":
                        item_text = cev.get("item", {}).get("text", "")
                        if item_text:
                            output_lines.append(item_text)
                            send({"type": "stream_text", "agent": breaker_label, "text": item_text})
                except (json.JSONDecodeError, ValueError):
                    # Fallback: rohe Zeile als Text
                    if line.strip():
                        output_lines.append(line)
                        send({"type": "stream_text", "agent": breaker_label, "text": line})

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
