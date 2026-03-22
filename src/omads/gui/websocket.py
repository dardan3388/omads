"""WebSocket-Routen der OMADS GUI."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import runtime, state

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    
    # Security: Origin-Check — nur lokale Verbindungen erlauben (dynamischer Port)
    origin = ws.headers.get("origin", "")
    server_port = ws.scope.get("server", ("", 8080))[1]
    allowed_origins = set()
    for host in ("127.0.0.1", "localhost"):
        for port in (server_port, server_port + 1):
            allowed_origins.add(f"http://{host}:{port}")
    if origin and origin not in allowed_origins:
        await ws.close(code=1008, reason="Origin nicht erlaubt")
        return

    await ws.accept()
    with runtime._connections_lock:
        runtime._connections.append(ws)
    runtime._loop = asyncio.get_event_loop()

    _MAX_MESSAGE_LENGTH = 50000
    _last_message_time = 0.0
    _MIN_MESSAGE_INTERVAL = 1.0  # Sekunden zwischen Nachrichten

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat":
                user_text = data.get("text", "").strip()
                if not user_text:
                    continue

                # Security: Nachrichtenlänge begrenzen
                if len(user_text) > _MAX_MESSAGE_LENGTH:
                    await ws.send_json({"type": "error", "text": f"Nachricht zu lang (max {_MAX_MESSAGE_LENGTH} Zeichen)"})
                    continue

                # Security: Rate-Limiting — max 1 Nachricht pro Sekunde
                now = time.time()
                if now - _last_message_time < _MIN_MESSAGE_INTERVAL:
                    await ws.send_json({"type": "error", "text": "Zu schnell — bitte kurz warten"})
                    continue
                _last_message_time = now

                # Security: Nur einen Task gleichzeitig erlauben
                with runtime._process_lock:
                    busy = runtime._active_process and runtime._active_process.poll() is None
                if busy:
                    await ws.send_json({"type": "error", "text": "Es läuft bereits ein Task — bitte warten oder abbrechen"})
                    continue

                # Alles geht an Claude CLI — kein Chat/Task-Routing mehr
                thread = threading.Thread(
                    target=runtime._run_claude_session_thread,
                    args=(ws, user_text),
                    daemon=True,
                )
                thread.start()

            elif msg_type == "review":
                # Review-Modus: Claude + Codex parallel
                with runtime._process_lock:
                    busy = runtime._active_process and runtime._active_process.poll() is None
                if busy:
                    await ws.send_json({"type": "error", "text": "Es läuft bereits ein Task — bitte warten oder abbrechen"})
                    continue

                review_scope = data.get("scope", "project")  # project | last_task | custom
                review_focus = data.get("focus", "all")       # all | security | bugs | performance
                custom_scope = data.get("custom_scope", "")

                thread = threading.Thread(
                    target=runtime._run_review_thread,
                    args=(ws, review_scope, review_focus, custom_scope),
                    daemon=True,
                )
                thread.start()

            elif msg_type == "apply_fixes":
                # Fixes aus Review anwenden
                with runtime._process_lock:
                    busy = runtime._active_process and runtime._active_process.poll() is None
                if busy:
                    await ws.send_json({"type": "error", "text": "Es läuft bereits ein Task — bitte warten oder abbrechen"})
                    continue
                current_repo = str(Path(state._get_setting("target_repo", ".")).resolve())
                repo_fixes = runtime._pending_review_fixes.get(current_repo, "")
                if not repo_fixes:
                    await ws.send_json({"type": "error", "text": "Keine Review-Fixes vorhanden"})
                    continue

                fix_prompt = (
                    "Wende jetzt die folgenden Fixes aus dem Review an:\n\n"
                    + repo_fixes[:8000] + "\n\n"
                    "Regeln: Setze NUR die im Fix-Plan beschriebenen Änderungen um. Kein Scope Creep."
                )
                thread = threading.Thread(
                    target=runtime._run_claude_session_thread,
                    args=(ws, fix_prompt),
                    daemon=True,
                )
                thread.start()

            elif msg_type == "stop":
                # Session abbrechen
                with runtime._process_lock:
                    runtime._task_cancelled = True
                    if runtime._active_process and runtime._active_process.poll() is None:
                        runtime._active_process.kill()
                        runtime._active_process = None
                await ws.send_json({"type": "task_stopped", "text": "Abgebrochen."})

            elif msg_type == "set_repo":
                repo_path = data.get("path", "").strip()
                resolved = Path(repo_path).resolve() if repo_path else None
                home_dir = Path.home().resolve()
                if resolved and resolved.is_dir() and (resolved == home_dir or str(resolved).startswith(str(home_dir) + "/")):
                    snapshot = state._update_settings(lambda settings: settings.__setitem__("target_repo", str(resolved)))
                    await ws.send_json({
                        "type": "system",
                        "text": f"Projekt: {snapshot['target_repo']}",
                    })
                elif resolved and not resolved.is_dir():
                    await ws.send_json({
                        "type": "error",
                        "text": f"Kein Verzeichnis: {repo_path}",
                    })
                else:
                    await ws.send_json({
                        "type": "error",
                        "text": f"Verzeichnis nicht erlaubt oder existiert nicht: {repo_path}",
                    })

    except WebSocketDisconnect:
        with runtime._connections_lock:
            try:
                runtime._connections.remove(ws)
            except ValueError:
                pass
