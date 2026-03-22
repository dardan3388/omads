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

    send({"type": "agent_status", "agent": agent_label, "status": "Arbeitet..."})

    # Historie: User-Eingabe loggen
    proj_id = _frozen_proj_id
    if proj_id:
        _append_history(proj_id, {"type": "user_input", "text": user_text})

    try:
        env = _build_cli_env()

        # OMADS-Kontext für Claude CLI
        omads_context = (
            "Du arbeitest innerhalb von OMADS (Orchestrated Multi-Agent Development System). "
            "Der User kommuniziert mit dir über eine Web-GUI. "
            "Nach jeder Code-Änderung prüft Codex CLI automatisch deinen Code im Hintergrund. "
            "Wenn Codex Probleme findet, bekommst du die Findings als nächste Nachricht und sollst sie fixen. "
            "Antworte auf Deutsch.\n\n"
        )

        # Projekt-Memory NUR bei neuer Session laden (spart Tokens bei --resume)
        session_id = _get_chat_session(repo_key)
        if not session_id:
            project_memory = _load_project_memory(target_repo)
            if project_memory:
                omads_context += (
                    "Du hast folgenden Kontext aus vorherigen Sessions und dem Projekt:\n\n"
                    + project_memory + "\n\n"
                    "Nutze diesen Kontext um nahtlos weiterzuarbeiten, ohne dass der User "
                    "dir erklären muss wo ihr stehen geblieben seid."
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
                send({"type": "task_stopped", "text": "Abgebrochen."})
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
                                      "text": f"[Denkprozess: {len(thinking)} Zeichen]"})

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
                    "text": f"Claude Task Exit-Code {process.returncode}",
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

        send({"type": "agent_status", "agent": agent_label, "status": f"Fertig ({elapsed}s)"})

        # Projekt-Memory aktualisieren (Zusammenfassung für nächste Session)
        if success and (output_lines or result_text):
            summary_parts = []
            if files_changed:
                summary_parts.append(f"Geänderte Dateien: {', '.join(f[-60:] for f in files_changed[:20])}")
            # Letzte Ausgaben als Kontext-Zusammenfassung
            recent_output = "\n".join(output_lines[-10:]) if output_lines else result_text
            summary_parts.append(f"Letzte Aufgabe: {user_text[:200]}")
            summary_parts.append(f"Ergebnis: {recent_output[:2000]}")
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
                      "status": "Behebt Codex-Findings..."})

                fix_prompt = (
                    "Der Codex Auto-Reviewer hat folgende Probleme in deinem Code gefunden. "
                    "Bitte behebe diese:\n\n" + review_findings + "\n\n"
                    "Regeln: Behebe NUR die gemeldeten Probleme. Kein Scope Creep."
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
                    send({"type": "agent_status", "agent": "Claude Code", "status": "Fixes angewendet"})

    except FileNotFoundError:
        send({"type": "chat_response", "agent": "System",
              "text": "Claude CLI nicht gefunden. Installiere mit: npm install -g @anthropic-ai/claude-code"})
    except subprocess.TimeoutExpired:
        with _process_lock:
            if _active_process:
                _active_process.kill()
        send({"type": "chat_response", "agent": "System", "text": "Timeout — Claude CLI hat zu lange gebraucht."})
    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Claude-Session-Fehler: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": "Ein interner Fehler ist aufgetreten. Details im Server-Log."})
    finally:
        with _process_lock:
            _active_process = None
        # Memory auch bei Crash/Rate-Limit sichern (was wir bisher haben)
        try:
            if output_lines:
                crash_summary = (
                    f"Letzte Aufgabe (unterbrochen): {user_text[:200]}\n"
                    f"Bisheriger Output: {chr(10).join(output_lines[-5:])}"
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
        scope_desc = f"Letzter Task ({len(review_files)} Dateien)"
    elif scope == "custom" and custom_scope.strip():
        review_files = [f.strip() for f in custom_scope.split(",") if f.strip()]
        scope_desc = f"Auswahl: {custom_scope[:100]}"
    else:
        review_files = []
        scope_desc = "Ganzes Projekt"

    # Fokus-Beschreibung
    focus_map = {
        "all": "Sicherheit, Bugs, Fehlerbehandlung, Performance",
        "security": "Sicherheitsprobleme (Injection, XSS, Secrets, Auth)",
        "bugs": "Logikfehler, Bugs, Race Conditions, Edge Cases",
        "performance": "Performance-Probleme, Memory Leaks, ineffiziente Algorithmen",
    }
    focus_desc = focus_map.get(focus, focus_map["all"])

    file_hint = ""
    if review_files:
        file_hint = f"\n\nPrüfe speziell diese Dateien:\n" + "\n".join(f"- {f}" for f in review_files[:30])

    env = _build_cli_env()

    send({"type": "stream_text", "agent": "Review",
          "text": f"**Review gestartet**\nScope: {scope_desc}\nFokus: {focus_desc}\n\n"
                  "Ablauf: Schritt 1 (Claude Code) → Schritt 2 (Codex) → Schritt 3 (Synthese + Fix-Vorschläge)"})

    try:
        # ── SCHRITT 1: Claude Code Review ─────────────────────────
        send({"type": "agent_status", "agent": "Claude Code", "status": "Schritt 1/3 — Review läuft..."})

        project_memory = _load_project_memory(target_repo)
        claude_context = (
            "Du führst ein Code-Review durch (KEINE Änderungen!). "
            "Lies und analysiere den Code, mach aber KEINE Edits.\n\n"
        )
        if project_memory:
            claude_context += f"Projektkontext:\n{project_memory}\n\n"

        review_prompt = (
            f"Führe ein gründliches Code-Review durch. Fokus: {focus_desc}.{file_hint}\n\n"
            "Antworte mit einer strukturierten Analyse:\n"
            "## Zusammenfassung\n## Findings (nach Schweregrad: KRITISCH > HOCH > MITTEL)\n"
            "## Positive Befunde\n\nAntworte auf Deutsch."
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
                    "Review Schritt 1 (Claude Code)",
                    process.returncode,
                    output_lines=claude_output,
                ),
            })
            return

        if captured_session_id and process.returncode == 0:
            _set_chat_session(repo_key, captured_session_id)

        send({"type": "agent_status", "agent": "Claude Code", "status": "Schritt 1/3 fertig"})

        if _task_cancelled:
            return

        # ── SCHRITT 2: Codex Review ───────────────────────────────
        send({"type": "agent_status", "agent": "Codex Review", "status": "Schritt 2/3 — Review läuft..."})

        codex_prompt = (
            f"Du bist ein Code-Reviewer. Führe ein gründliches Review durch.\n"
            f"Fokus: {focus_desc}\n"
        )
        if review_files:
            codex_prompt += f"Dateien: {', '.join(f.rsplit('/', 1)[-1] for f in review_files[:15])}\n"
        codex_prompt += (
            "\nAntworte mit:\n## Geprüfte Dateien\n## Findings\n"
            "- [KRITISCH/HOCH/MITTEL] Datei:Zeile: Beschreibung\n## Positive Befunde\n"
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
                    send({"type": "agent_status", "agent": "Codex Review", "status": "Inaktivität — Codex abgebrochen (15min ohne Output)"})
                    codex_review = "\n".join(codex_lines) if codex_lines else "(Codex inaktiv)"
                    break
                ready, _, _ = select.select([codex_process.stdout], [], [], 5.0)
                if ready:
                    line = codex_process.stdout.readline()
                    if not line:  # EOF — Codex ist fertig
                        codex_process.wait(timeout=10)
                        codex_review = "\n".join(codex_lines)
                        send({"type": "agent_status", "agent": "Codex Review", "status": "Schritt 2/3 fertig"})
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
                  "status": "Codex CLI nicht installiert — übersprungen"})
            codex_review = "(Codex nicht verfügbar)"
        except Exception as e:
            try:
                codex_process.kill()
                codex_process.wait()
            except Exception:
                pass
            send({"type": "agent_status", "agent": "Codex Review", "status": f"Fehler: {str(e)[:100]}"})
            codex_review = f"(Codex Fehler: {str(e)[:200]})"

        if _task_cancelled:
            return

        # ── SCHRITT 3: Claude Code Synthese ───────────────────────
        send({"type": "agent_status", "agent": "Claude Code",
              "status": "Schritt 3/3 — Synthese: vergleicht beide Reviews..."})
        send({"type": "stream_text", "agent": "Review",
              "text": "---\n**Schritt 3: Claude Code analysiert jetzt das Codex-Review und erstellt den finalen Bericht...**"})

        synthesis_prompt = (
            "Du hast gerade ein Code-Review durchgeführt (Schritt 1). "
            "Jetzt hat Codex unabhängig dasselbe Projekt geprüft (Schritt 2). "
            "Vergleiche beide Reviews und erstelle einen finalen Bericht.\n\n"
            f"=== DEIN REVIEW (Claude Code) ===\n{claude_review[:6000]}\n\n"
            f"=== CODEX REVIEW ===\n{codex_review[:6000]}\n\n"
            "Aufgabe:\n"
            "1. Was haben beide gefunden (Übereinstimmungen)?\n"
            "2. Was hat nur Codex gefunden, das du übersehen hast?\n"
            "3. Was hast nur du gefunden?\n"
            "4. Erstelle eine priorisierte Liste aller ECHTEN Findings die gefixt werden sollten.\n"
            "   Ignoriere False Positives und zu kleinteilige Style-Hinweise.\n\n"
            "Antworte mit:\n"
            "## Übereinstimmungen (beide gefunden)\n"
            "## Nur von Codex gefunden\n"
            "## Nur von Claude Code gefunden\n"
            "## Finaler Fix-Plan\n"
            "Für jeden Fix: Datei, Zeile, was genau gefixt werden soll.\n\n"
            "WICHTIG: Mach KEINE Änderungen, nur analysieren! Antworte auf Deutsch.\n\n"
            "PFLICHT: Schreibe als ALLERLETZTE Zeile deiner Antwort genau einen dieser Marker:\n"
            "FIXES_NEEDED: true\n"
            "oder\n"
            "FIXES_NEEDED: false\n"
            "Nichts anderes in dieser Zeile. true = es gibt echte Fixes. false = alles OK oder nur Style-Hinweise."
        )

        synthesis_context = (
            "Du vergleichst dein eigenes Review mit dem von Codex. "
            "Sei ehrlich — wenn Codex etwas Wichtiges gefunden hat, das du übersehen hast, sag das. "
            "Am Ende soll der User entscheiden ob die Fixes angewendet werden. "
            "KEINE Code-Änderungen durchführen!\n"
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
                    "Review Schritt 3 (Synthese)",
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

        send({"type": "agent_status", "agent": "Claude Code", "status": "Schritt 3/3 fertig"})
        send({"type": "stream_text", "agent": "Review",
              "text": "---\n**Review abgeschlossen** — 3 Schritte: Claude Code Review → Codex Review → Synthese"})

        if has_fixes:
            # Fix-Vorschläge als Kontext speichern für den Apply-Schritt (pro Projekt)
            global _pending_review_fixes
            _pending_review_fixes[str(Path(target_repo).resolve())] = synthesis_text
            send({"type": "review_fixes_available",
                  "text": "Fixes gefunden. Sollen die vorgeschlagenen Fixes angewendet werden?"})

    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Review-Fehler: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": f"Review-Fehler: {str(e)[:200]}"})
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

    send({"type": "agent_status", "agent": breaker_label, "status": f"Prüft {len(files_changed)} geänderte Datei(en)..."})

    review_prompt = f"""Du bist ein Code-Reviewer. Prüfe die folgenden kürzlich geänderten Dateien auf Probleme:

Geänderte Dateien: {file_list}

Prüfe auf:
1. Sicherheitsprobleme (Injection, XSS, offene Secrets)
2. Logikfehler und Bugs
3. Fehlende Fehlerbehandlung
4. Offensichtliche Performance-Probleme

Antworte IMMER mit diesem Format:

## Geprüfte Dateien
- Dateiname: kurze Zusammenfassung was die Datei macht

## Analyse
Beschreibe kurz was du geprüft hast (2-3 Sätze).

## Ergebnis
Falls Probleme: Pro Problem eine Zeile:
- [HIGH/MEDIUM/LOW] Datei: Beschreibung

Falls keine Probleme: "Keine Probleme gefunden."
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
              "text": f"Starte Review: {file_list}"})
        send({"type": "stream_text", "agent": breaker_label,
              "text": f"Prüfe auf: Sicherheit, Logikfehler, Fehlerbehandlung, Performance"})

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
                          "status": f"Codex analysiert... ({elapsed}s)"})

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
                send({"type": "agent_status", "agent": breaker_label, "status": "Inaktivität — Review abgebrochen (15min ohne Output)"})
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
            output = f"Review-Fehler: Codex beendete mit Exit-Code {process.returncode}"
            send({"type": "stream_result", "agent": breaker_label, "text": output, "is_error": True})

        send({"type": "stream_text", "agent": breaker_label,
              "text": f"Review abgeschlossen ({elapsed}s)"})

        # Ergebnis auswerten — nur bei erfolgreichem Exit als Finding behandeln
        if process.returncode != 0:
            send({"type": "agent_status", "agent": breaker_label,
                  "status": f"Codex-Fehler (Exit {process.returncode}) — kein Review-Ergebnis"})
            return None
        if output and "keine probleme" not in output.lower():
            send({"type": "agent_activity", "agent": breaker_label, "activity": "finding", "text": output})
            send({"type": "agent_status", "agent": breaker_label, "status": "Hinweise gefunden → Claude Code fixt"})
            return output  # Findings zurückgeben für Claude-Fix
        else:
            send({"type": "agent_status", "agent": breaker_label, "status": "Alles OK"})
            return None

    except FileNotFoundError:
        send({"type": "agent_status", "agent": breaker_label,
              "status": "Codex CLI nicht installiert — Review übersprungen"})
        return None
    except Exception as e:
        send({"type": "agent_status", "agent": breaker_label, "status": f"Review-Fehler: {str(e)[:100]}"})
        return None
    finally:
        with _process_lock:
            _active_process = None


