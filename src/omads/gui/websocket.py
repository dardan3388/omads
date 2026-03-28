"""WebSocket routes for the OMADS GUI."""

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
    
    # Security: allow only local origins (dynamic port) plus LAN when enabled
    origin = ws.headers.get("origin", "")
    server_port = ws.scope.get("server", ("", 8080))[1]
    client_host = (ws.scope.get("client") or ("", 0))[0]
    allowed_origins = set()
    for host in ("127.0.0.1", "localhost"):
        for port in (server_port, server_port + 1):
            allowed_origins.add(f"http://{host}:{port}")
    if origin:
        if origin not in allowed_origins:
            # When LAN access is on, also accept private-network origins
            lan_ok = state._get_setting("lan_access", False) and state.is_rfc1918_origin(origin)
            if not lan_ok:
                await ws.close(code=1008, reason="Origin not allowed")
                return
    elif client_host != "testclient":
        await ws.close(code=1008, reason="Origin not allowed")
        return

    await ws.accept()
    with runtime._connections_lock:
        runtime._connections.append(ws)
    runtime._loop = asyncio.get_event_loop()

    _MAX_MESSAGE_LENGTH = 50000
    _last_message_time = 0.0
    _MIN_MESSAGE_INTERVAL = 1.0  # Seconds between messages

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat":
                user_text = data.get("text", "").strip()
                if not user_text:
                    continue

                # Security: limit message length
                if len(user_text) > _MAX_MESSAGE_LENGTH:
                    await ws.send_json({"type": "error", "text": f"Message too long (max {_MAX_MESSAGE_LENGTH} characters)"})
                    continue

                # Security: rate limiting — max 1 message per second
                now = time.time()
                if now - _last_message_time < _MIN_MESSAGE_INTERVAL:
                    await ws.send_json({"type": "error", "text": "Too fast — please wait a moment"})
                    continue
                _last_message_time = now

                # Security: allow only one task at a time
                if not runtime._try_reserve_task_slot():
                    await ws.send_json({"type": "error", "text": "A task is already running — please wait or stop it"})
                    continue

                # Normal chat tasks always go to the currently selected builder
                thread = threading.Thread(
                    target=runtime._run_builder_session_thread,
                    args=(ws, user_text),
                    daemon=True,
                )
                try:
                    thread.start()
                except Exception:
                    runtime._release_reserved_task_slot()
                    await ws.send_json({"type": "error", "text": "Could not start the task"})
                    continue

            elif msg_type == "review":
                # Manual review mode: configurable reviewer order
                if not runtime._try_reserve_task_slot():
                    await ws.send_json({"type": "error", "text": "A task is already running — please wait or stop it"})
                    continue

                review_scope = data.get("scope", "project")  # project | last_task | custom
                review_focus = data.get("focus", "all")      # all | security | bugs | performance | custom
                custom_scope = data.get("custom_scope", "")
                custom_focus = data.get("custom_focus", "")

                thread = threading.Thread(
                    target=runtime._run_review_thread,
                    args=(ws, review_scope, review_focus, custom_scope, custom_focus),
                    daemon=True,
                )
                try:
                    thread.start()
                except Exception:
                    runtime._release_reserved_task_slot()
                    await ws.send_json({"type": "error", "text": "Could not start the task"})
                    continue

            elif msg_type == "apply_fixes":
                # Apply fixes from the stored review result
                if not runtime._try_reserve_task_slot():
                    await ws.send_json({"type": "error", "text": "A task is already running — please wait or stop it"})
                    continue
                current_repo = str(Path(state._get_setting("target_repo", ".")).resolve())
                repo_fixes = runtime._pending_review_fixes.get(current_repo, "")
                if not repo_fixes:
                    runtime._release_reserved_task_slot()
                    await ws.send_json({"type": "error", "text": "No review fixes are available"})
                    continue
                repo_fixes = runtime._pending_review_fixes.pop(current_repo, "")
                if not repo_fixes:
                    runtime._release_reserved_task_slot()
                    await ws.send_json({"type": "error", "text": "No review fixes are available"})
                    continue
                project_id = state._get_active_project_id()
                if project_id:
                    state._append_timeline_event(project_id, {"type": "user_input", "text": "Apply fixes"})

                fix_prompt = (
                    "Apply the following fixes from the review now:\n\n"
                    + repo_fixes[:8000] + "\n\n"
                    "Rules: Apply ONLY the changes described in the fix plan. No scope creep."
                )
                review_fix_runner = runtime._run_claude_session_thread
                if state._get_setting("review_first_reviewer", "claude") == "codex":
                    review_fix_runner = runtime._run_codex_session_thread
                thread = threading.Thread(
                    target=review_fix_runner,
                    args=(ws, fix_prompt),
                    daemon=True,
                )
                try:
                    thread.start()
                except Exception:
                    runtime._pending_review_fixes[current_repo] = repo_fixes
                    runtime._release_reserved_task_slot()
                    await ws.send_json({"type": "error", "text": "Could not start the task"})
                    continue

            elif msg_type == "stop":
                # Stop the current session
                with runtime._process_lock:
                    runtime._task_cancelled = True
                    if (
                        runtime._active_process
                        and runtime._active_process is not runtime._RESERVED_PROCESS_SLOT
                        and runtime._active_process.poll() is None
                    ):
                        runtime._active_process.kill()
                        runtime._active_process = None
                await ws.send_json({"type": "task_stopped", "text": "Stopped."})

            elif msg_type == "set_repo":
                repo_path = data.get("path", "").strip()
                resolved = Path(repo_path).resolve() if repo_path else None
                if resolved and resolved.is_dir() and state.is_path_inside_home(resolved):
                    snapshot = state._update_settings(lambda settings: settings.__setitem__("target_repo", str(resolved)))
                    await ws.send_json({
                        "type": "system",
                        "text": f"Project: {snapshot['target_repo']}",
                    })
                elif resolved and not resolved.is_dir():
                    await ws.send_json({
                        "type": "error",
                        "text": f"Not a directory: {repo_path}",
                    })
                else:
                    await ws.send_json({
                        "type": "error",
                        "text": f"Directory is not allowed or does not exist: {repo_path}",
                    })

    except WebSocketDisconnect:
        with runtime._connections_lock:
            try:
                runtime._connections.remove(ws)
            except ValueError:
                pass
