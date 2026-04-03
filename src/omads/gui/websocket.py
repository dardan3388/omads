"""WebSocket routes for the OMADS GUI."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import runtime, state

router = APIRouter()


def _normalize_session_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one session-scoped settings patch from the WebSocket client."""
    normalized: dict[str, Any] = {}

    target_repo = payload.get("target_repo")
    if isinstance(target_repo, str):
        resolved = Path(target_repo).expanduser().resolve()
        if resolved.is_dir() and state.is_path_inside_home(resolved):
            normalized["target_repo"] = str(resolved)

    builder_agent = payload.get("builder_agent")
    if builder_agent in {"claude", "codex"}:
        normalized["builder_agent"] = builder_agent

    review_first = payload.get("review_first_reviewer")
    review_second = payload.get("review_second_reviewer")
    if review_first in {"claude", "codex"}:
        normalized["review_first_reviewer"] = review_first
    if review_second in {"claude", "codex"}:
        normalized["review_second_reviewer"] = review_second
    if (
        normalized.get("review_first_reviewer")
        and normalized.get("review_second_reviewer")
        and normalized["review_first_reviewer"] == normalized["review_second_reviewer"]
    ):
        normalized["review_second_reviewer"] = (
            "codex" if normalized["review_first_reviewer"] == "claude" else "claude"
        )

    if isinstance(payload.get("auto_review"), bool):
        normalized["auto_review"] = payload["auto_review"]

    claude_model = payload.get("claude_model")
    if isinstance(claude_model, str):
        normalized["claude_model"] = claude_model

    claude_effort = payload.get("claude_effort")
    if claude_effort in {"low", "medium", "high", "max"}:
        normalized["claude_effort"] = claude_effort

    claude_permission_mode = payload.get("claude_permission_mode")
    if claude_permission_mode in {"default", "auto", "plan", "bypassPermissions"}:
        normalized["claude_permission_mode"] = claude_permission_mode
    elif claude_permission_mode == "auto-accept":
        normalized["claude_permission_mode"] = "auto"
    elif claude_permission_mode == "bypass":
            normalized["claude_permission_mode"] = "bypassPermissions"

    codex_model = payload.get("codex_model")
    if isinstance(codex_model, str):
        normalized["codex_model"] = state._normalize_codex_model(codex_model, default="")

    codex_reasoning = payload.get("codex_reasoning")
    if codex_reasoning in {"low", "medium", "high", "xhigh"}:
        normalized["codex_reasoning"] = codex_reasoning

    codex_execution_mode = payload.get("codex_execution_mode")
    if isinstance(codex_execution_mode, str):
        normalized["codex_execution_mode"] = state._normalize_codex_execution_mode(
            codex_execution_mode,
            default="default",
        )

    if isinstance(payload.get("codex_fast"), bool):
        normalized["codex_fast"] = payload["codex_fast"]

    return normalized


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

    query_params = getattr(ws, "query_params", {}) or {}
    client_session_id = runtime.normalize_client_session_id(query_params.get("client_session_id"))
    await ws.accept()
    runtime.register_connection(ws, client_session_id)
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
                if not runtime._try_reserve_task_slot(ws):
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
                if not runtime._try_reserve_task_slot(ws):
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
                if not runtime._try_reserve_task_slot(ws):
                    await ws.send_json({"type": "error", "text": "A task is already running — please wait or stop it"})
                    continue
                current_settings = runtime.get_connection_settings_snapshot(ws)
                current_repo = str(Path(current_settings.get("target_repo", ".")).resolve())
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
                project = state._find_project_by_path(current_repo)
                project_id = project["id"] if project else None
                if project_id:
                    state._append_timeline_event(project_id, {"type": "user_input", "text": "Apply fixes"})

                fix_prompt = (
                    "Apply the following fixes from the review now:\n\n"
                    + repo_fixes[:8000] + "\n\n"
                    "Rules: Apply ONLY the changes described in the fix plan. No scope creep."
                )
                review_fix_runner = runtime._run_claude_session_thread
                if current_settings.get("review_first_reviewer", "claude") == "codex":
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
                # Stop only the task owned by this session.
                stop_result = runtime.stop_active_task_for_connection(ws)
                if stop_result == "stopped":
                    await ws.send_json({"type": "task_stopped", "text": "Stopped."})
                elif stop_result == "not_owner":
                    await ws.send_json({
                        "type": "error",
                        "text": "Another browser session owns the active task, so this stop request was ignored.",
                    })
                else:
                    await ws.send_json({"type": "error", "text": "No running task is available to stop."})

            elif msg_type == "set_repo":
                repo_path = data.get("path", "").strip()
                resolved = Path(repo_path).expanduser().resolve() if repo_path else None
                if resolved and resolved.is_dir() and state.is_path_inside_home(resolved):
                    snapshot = runtime.update_connection_settings(ws, {"target_repo": str(resolved)})
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

            elif msg_type == "set_session_settings":
                session_patch = _normalize_session_settings(data.get("settings", {}))
                if session_patch:
                    runtime.update_connection_settings(ws, session_patch)

    except WebSocketDisconnect:
        runtime.unregister_connection(ws)
