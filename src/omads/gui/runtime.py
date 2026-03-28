"""Runtime state, broadcasts, and task runners for the OMADS GUI."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .builder_flow import (
    BuilderRuntimeContext,
    run_claude_auto_review as _builder_run_claude_auto_review,
    run_claude_session_thread as _builder_run_claude_session_thread,
    run_codex_auto_review as _builder_run_codex_auto_review,
    run_codex_session_thread as _builder_run_codex_session_thread,
)
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
    _build_chat_handover_context,
    _build_cli_env,
    _build_process_failure_text,
    _clear_chat_session,
    _get_active_project_id,
    _get_chat_session,
    _find_project_by_path,
    _get_last_builder,
    _get_setting,
    _get_settings_snapshot,
    _load_project_memory,
    _save_project_memory,
    _set_chat_session,
    _set_last_builder,
    _update_claude_limit_status,
)

# Active WebSocket connections (lock protects add/remove/iterate)
_connections_lock = threading.Lock()
_connections: list[WebSocket] = []
_connection_settings: dict[WebSocket, dict[str, Any]] = {}
_connection_session_ids: dict[WebSocket, str] = {}
_session_settings_store: dict[str, dict[str, Any]] = {}
_connection_last_task_files: dict[WebSocket, list[str]] = {}
_session_last_task_files: dict[str, list[str]] = {}

# Running process used for stop handling; lock protects against race conditions
_process_lock = threading.Lock()
_active_process: subprocess.Popen | None = None
_active_task_owner: WebSocket | None = None
_task_cancelled: bool = False
_last_files_changed: list[str] = []  # Legacy/global fallback for ws-less task runners
_pending_review_fixes: dict[str, str] = {}  # {repo_path: fixes_text} per project


class _ReservedProcessSlot:
    """Sentinel used while a worker thread is still starting a subprocess."""

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        return None


_RESERVED_PROCESS_SLOT = _ReservedProcessSlot()
_CLIENT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

def normalize_client_session_id(raw: str | None) -> str | None:
    """Validate one browser-provided session identifier."""
    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip()
    if not _CLIENT_SESSION_ID_RE.match(value):
        return None
    return value


def register_connection(ws: WebSocket, client_session_id: str | None = None) -> None:
    """Register one live WebSocket and seed its session-scoped settings."""
    normalized_session_id = normalize_client_session_id(client_session_id)
    snapshot = dict(_session_settings_store.get(normalized_session_id) or _get_settings_snapshot())
    with _connections_lock:
        _connections.append(ws)
        _connection_settings[ws] = snapshot
        if normalized_session_id:
            _connection_session_ids[ws] = normalized_session_id
            _session_settings_store[normalized_session_id] = dict(snapshot)
            session_last_task_files = _session_last_task_files.get(normalized_session_id)
            if session_last_task_files is not None:
                _connection_last_task_files[ws] = list(session_last_task_files)


def unregister_connection(ws: WebSocket) -> None:
    """Remove one WebSocket and its session-scoped settings."""
    with _connections_lock:
        try:
            _connections.remove(ws)
        except ValueError:
            pass
        _connection_settings.pop(ws, None)
        _connection_session_ids.pop(ws, None)
        _connection_last_task_files.pop(ws, None)


def get_connection_settings_snapshot(ws: WebSocket | None) -> dict[str, Any]:
    """Return the current session-scoped settings for one WebSocket."""
    if ws is None:
        return _get_settings_snapshot()
    with _connections_lock:
        snapshot = _connection_settings.get(ws)
        return dict(snapshot) if snapshot is not None else _get_settings_snapshot()


def get_session_settings_snapshot(client_session_id: str | None) -> dict[str, Any]:
    """Return the runtime settings snapshot for one browser session if known."""
    normalized_session_id = normalize_client_session_id(client_session_id)
    if not normalized_session_id:
        return _get_settings_snapshot()
    with _connections_lock:
        snapshot = _session_settings_store.get(normalized_session_id)
        return dict(snapshot) if snapshot is not None else _get_settings_snapshot()


def get_last_task_files_snapshot(ws: WebSocket | None) -> list[str]:
    """Return the last completed task file list for one browser session."""
    if ws is None:
        return list(_last_files_changed)
    with _connections_lock:
        files = _connection_last_task_files.get(ws)
        if files is not None:
            return list(files)
        session_id = _connection_session_ids.get(ws)
        if session_id and session_id in _session_last_task_files:
            return list(_session_last_task_files[session_id])
    return []


def record_last_task_files(ws: WebSocket | None, files: list[str]) -> None:
    """Store the latest changed-file set for one browser session."""
    global _last_files_changed
    file_list = list(files)
    _last_files_changed = file_list
    if ws is None:
        return
    with _connections_lock:
        _connection_last_task_files[ws] = list(file_list)
        session_id = _connection_session_ids.get(ws)
        if session_id:
            _session_last_task_files[session_id] = list(file_list)


def update_connection_settings(ws: WebSocket | None, patch: dict[str, Any]) -> dict[str, Any]:
    """Update the session-scoped settings for one WebSocket and return the snapshot."""
    if ws is None:
        return _get_settings_snapshot()
    with _connections_lock:
        snapshot = dict(_connection_settings.get(ws) or _get_settings_snapshot())
        snapshot.update(patch)
        _connection_settings[ws] = snapshot
        session_id = _connection_session_ids.get(ws)
        if session_id:
            _session_settings_store[session_id] = dict(snapshot)
        return dict(snapshot)


def _project_id_from_settings_snapshot(settings_snapshot: dict[str, Any]) -> str | None:
    """Resolve the project ID for one task-local settings snapshot."""
    target_repo = settings_snapshot.get("target_repo", "")
    if not target_repo:
        return None
    project = _find_project_by_path(str(Path(target_repo).resolve()))
    return project["id"] if project else None


def _try_reserve_task_slot(owner_ws: WebSocket | None = None) -> bool:
    """Reserve the global task slot before handing work to a background thread."""
    global _active_process, _active_task_owner, _task_cancelled
    with _process_lock:
        if _active_process and _active_process.poll() is None:
            return False
        _task_cancelled = False
        _active_process = _RESERVED_PROCESS_SLOT
        _active_task_owner = owner_ws
        return True


def _release_reserved_task_slot() -> None:
    """Release a reservation that never reached the subprocess start stage."""
    global _active_process, _active_task_owner, _task_cancelled
    with _process_lock:
        if _active_process is _RESERVED_PROCESS_SLOT:
            _active_process = None
            _active_task_owner = None
            _task_cancelled = False


def stop_active_task_for_connection(ws: WebSocket | None) -> str:
    """Stop the active task only when the caller owns the current task slot."""
    global _active_process, _active_task_owner, _task_cancelled
    with _process_lock:
        if not _active_process or _active_process.poll() is not None:
            _active_process = None
            _active_task_owner = None
            _task_cancelled = False
            return "idle"

        if _active_task_owner is not None and ws is not None and _active_task_owner is not ws:
            return "not_owner"

        _task_cancelled = True
        if _active_process is _RESERVED_PROCESS_SLOT:
            _active_process = None
            _active_task_owner = None
            return "stopped"

        try:
            _active_process.kill()
        finally:
            _active_process = None
            _active_task_owner = None
        return "stopped"


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
                _connection_settings.pop(ws, None)
                _connection_session_ids.pop(ws, None)
                _connection_last_task_files.pop(ws, None)


def broadcast_sync(msg: dict, *, proj_id_override: str | None = None) -> None:
    """Synchronous wrapper for `broadcast` used from worker threads."""
    # Persist all live runtime events through the unified per-project timeline.
    proj_id = proj_id_override or _get_active_project_id()
    if proj_id:
        try:
            _append_timeline_event(proj_id, dict(msg))
        except Exception:
            logging.getLogger("omads").debug("Timeline write failed for %s", proj_id, exc_info=True)
    with _connections_lock:
        snapshot = list(_connections)
    for ws in snapshot:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(msg), _loop)
        except Exception:
            pass


def send_to_ws_sync(
    ws: WebSocket | None,
    msg: dict,
    *,
    proj_id_override: str | None = None,
) -> None:
    """Send one task-scoped message only to the initiating WebSocket."""
    if ws is None:
        broadcast_sync(msg, proj_id_override=proj_id_override)
        return

    proj_id = proj_id_override or _project_id_from_settings_snapshot(get_connection_settings_snapshot(ws))
    if proj_id:
        try:
            _append_timeline_event(proj_id, dict(msg))
        except Exception:
            logging.getLogger("omads").debug("Timeline write failed for %s", proj_id, exc_info=True)

    try:
        asyncio.run_coroutine_threadsafe(ws.send_json(msg), _loop)
    except Exception:
        pass


_loop: asyncio.AbstractEventLoop | None = None


def _run_builder_session_thread(ws: WebSocket, user_text: str) -> None:
    """Route one chat task to the currently selected primary builder."""
    settings_snapshot = get_connection_settings_snapshot(ws)
    builder_agent = settings_snapshot.get("builder_agent", "claude")
    proj_id = _project_id_from_settings_snapshot(settings_snapshot)
    target_repo = settings_snapshot.get("target_repo", "")

    # Detect builder switch and invalidate the Claude session so the next
    # Claude run starts fresh with the full chat handover context instead
    # of resuming a stale session that knows nothing about the other builder.
    if proj_id:
        last_builder = _get_last_builder(proj_id)
        if last_builder and last_builder != builder_agent:
            if target_repo:
                _clear_chat_session(target_repo, scope="builder:claude")
        _set_last_builder(proj_id, builder_agent)

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


def _builder_runtime_context(
    frozen_proj_id: str | None,
    settings_snapshot: dict[str, Any],
    owner_ws: WebSocket | None,
) -> BuilderRuntimeContext:
    """Build the dependency bundle used by builder-specific runtime helpers."""

    def process_started(process: subprocess.Popen) -> None:
        global _active_process
        with _process_lock:
            _active_process = process

    def process_finished() -> None:
        global _active_process, _active_task_owner
        with _process_lock:
            _active_process = None
            _active_task_owner = None

    def is_task_cancelled() -> bool:
        return _task_cancelled

    def get_active_project_id() -> str | None:
        return frozen_proj_id

    def set_builder_session(repo_key: str, session_id: str, *, scope: str = "builder") -> None:
        _set_chat_session(repo_key, session_id, scope=scope)

    def set_last_files_changed(files: list[str]) -> None:
        record_last_task_files(owner_ws, files)

    return BuilderRuntimeContext(
        append_timeline_event=_append_timeline_event,
        build_chat_handover_context=_build_chat_handover_context,
        build_cli_env=_build_cli_env,
        build_process_failure_text=_build_process_failure_text,
        capture_repo_change_snapshot=_capture_repo_change_snapshot,
        forward_codex_stream_line=_forward_codex_stream_line,
        get_active_project_id=get_active_project_id,
        get_chat_session=_get_chat_session,
        get_settings_snapshot=lambda: dict(settings_snapshot),
        is_task_cancelled=is_task_cancelled,
        load_project_memory=_load_project_memory,
        merge_changed_files=_merge_changed_files,
        parse_claude_stream_line=parse_claude_stream_line,
        process_started=process_started,
        process_finished=process_finished,
        repo_snapshot_changed=_repo_snapshot_changed,
        run_claude_auto_review=_run_claude_auto_review,
        run_codex_auto_review=_run_codex_auto_review,
        save_project_memory=_save_project_memory,
        set_builder_session=set_builder_session,
        set_last_files_changed=set_last_files_changed,
        update_claude_limit_status=_update_claude_limit_status,
    )


def _run_claude_session_thread(ws: WebSocket, user_text: str) -> None:
    """Delegate one Claude builder task to the dedicated builder-flow module."""
    settings_snapshot = get_connection_settings_snapshot(ws)
    frozen_proj_id = _project_id_from_settings_snapshot(settings_snapshot)

    def send(msg: dict) -> None:
        send_to_ws_sync(ws, msg, proj_id_override=frozen_proj_id)

    return _builder_run_claude_session_thread(
        _builder_runtime_context(frozen_proj_id, settings_snapshot, ws),
        ws,
        user_text,
        send,
    )


def _run_codex_session_thread(ws: WebSocket, user_text: str) -> None:
    """Delegate one Codex builder task to the dedicated builder-flow module."""
    settings_snapshot = get_connection_settings_snapshot(ws)
    frozen_proj_id = _project_id_from_settings_snapshot(settings_snapshot)

    def send(msg: dict) -> None:
        send_to_ws_sync(ws, msg, proj_id_override=frozen_proj_id)

    return _builder_run_codex_session_thread(
        _builder_runtime_context(frozen_proj_id, settings_snapshot, ws),
        ws,
        user_text,
        send,
    )


def _review_runtime_context(settings_snapshot: dict[str, Any]) -> ReviewRuntimeContext:
    """Build the small dependency bundle used by review-specific helpers."""

    def process_started(process: subprocess.Popen) -> None:
        global _active_process
        with _process_lock:
            _active_process = process

    def process_finished() -> None:
        global _active_process, _active_task_owner
        with _process_lock:
            _active_process = None
            _active_task_owner = None

    def is_task_cancelled() -> bool:
        return _task_cancelled

    def store_review_session(repo_key: str, session_id: str) -> None:
        _set_chat_session(repo_key, session_id, scope="review")

    return ReviewRuntimeContext(
        build_cli_env=_build_cli_env,
        build_process_failure_text=_build_process_failure_text,
        forward_claude_stream_line=_forward_claude_stream_line,
        forward_codex_stream_line=_forward_codex_stream_line,
        get_settings_snapshot=lambda: dict(settings_snapshot),
        is_task_cancelled=is_task_cancelled,
        load_project_memory=_load_project_memory,
        process_started=process_started,
        process_finished=process_finished,
        store_review_session=store_review_session,
    )


def _run_claude_manual_review_step(**kwargs):
    """Delegate one Claude review step to the review-specific runtime module."""
    settings_snapshot = kwargs.pop("settings_snapshot")
    return _review_run_claude_manual_review_step(_review_runtime_context(settings_snapshot), **kwargs)


def _run_codex_manual_review_step(**kwargs):
    """Delegate one Codex review step to the review-specific runtime module."""
    settings_snapshot = kwargs.pop("settings_snapshot")
    return _review_run_codex_manual_review_step(_review_runtime_context(settings_snapshot), **kwargs)


def _run_claude_manual_synthesis_step(**kwargs):
    """Delegate one Claude synthesis step to the review-specific runtime module."""
    settings_snapshot = kwargs.pop("settings_snapshot")
    return _review_run_claude_manual_synthesis_step(_review_runtime_context(settings_snapshot), **kwargs)


def _run_codex_manual_synthesis_step(**kwargs):
    """Delegate one Codex synthesis step to the review-specific runtime module."""
    settings_snapshot = kwargs.pop("settings_snapshot")
    return _review_run_codex_manual_synthesis_step(_review_runtime_context(settings_snapshot), **kwargs)


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

    global _active_process, _active_task_owner, _task_cancelled, _pending_review_fixes
    with _process_lock:
        if _task_cancelled:
            if _active_process is _RESERVED_PROCESS_SLOT:
                _active_process = None
            return

    settings_snapshot = get_connection_settings_snapshot(ws)
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
    _frozen_proj_id = _project_id_from_settings_snapshot(settings_snapshot)

    def send(msg: dict):
        send_to_ws_sync(ws, msg, proj_id_override=_frozen_proj_id)

    # Determine scope
    last_task_files = get_last_task_files_snapshot(ws)
    if scope == "last_task" and last_task_files:
        review_files = last_task_files
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
                    settings_snapshot=settings_snapshot,
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
                    settings_snapshot=settings_snapshot,
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
                    settings_snapshot=settings_snapshot,
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
                    settings_snapshot=settings_snapshot,
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
                    settings_snapshot=settings_snapshot,
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
                    settings_snapshot=settings_snapshot,
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
            _active_task_owner = None
        send({"type": "unlock"})


def _run_codex_auto_review(ws: WebSocket, target_repo: str, files_changed: list[str], send: callable) -> str | None:
    """Delegate one Codex automatic breaker run to the builder-flow module."""
    settings_snapshot = get_connection_settings_snapshot(ws)
    return _builder_run_codex_auto_review(
        _builder_runtime_context(_project_id_from_settings_snapshot(settings_snapshot), settings_snapshot, ws),
        ws,
        target_repo,
        files_changed,
        send,
    )


def _run_claude_auto_review(
    target_repo: str,
    files_changed: list[str],
    send: callable,
    *,
    model: str,
    effort: str,
) -> str | None:
    """Delegate one Claude automatic breaker run to the builder-flow module."""
    settings_snapshot = _get_settings_snapshot()
    settings_snapshot["target_repo"] = target_repo
    return _builder_run_claude_auto_review(
        _builder_runtime_context(_project_id_from_settings_snapshot(settings_snapshot), settings_snapshot, None),
        target_repo,
        files_changed,
        send,
        model=model,
        effort=effort,
    )
