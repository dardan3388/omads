"""Runtime state, broadcasts, and task runners for the OMADS GUI."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path

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


def _builder_runtime_context(frozen_proj_id: str | None) -> BuilderRuntimeContext:
    """Build the dependency bundle used by builder-specific runtime helpers."""

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

    def get_active_project_id() -> str | None:
        return frozen_proj_id

    def set_builder_session(repo_key: str, session_id: str) -> None:
        _set_chat_session(repo_key, session_id)

    def set_last_files_changed(files: list[str]) -> None:
        global _last_files_changed
        _last_files_changed = list(files)

    return BuilderRuntimeContext(
        append_timeline_event=_append_timeline_event,
        build_cli_env=_build_cli_env,
        build_process_failure_text=_build_process_failure_text,
        capture_repo_change_snapshot=_capture_repo_change_snapshot,
        forward_codex_stream_line=_forward_codex_stream_line,
        get_active_project_id=get_active_project_id,
        get_chat_session=_get_chat_session,
        get_settings_snapshot=_get_settings_snapshot,
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
    frozen_proj_id = _get_active_project_id()

    def send(msg: dict) -> None:
        broadcast_sync(msg, proj_id_override=frozen_proj_id)

    return _builder_run_claude_session_thread(
        _builder_runtime_context(frozen_proj_id),
        ws,
        user_text,
        send,
    )


def _run_codex_session_thread(ws: WebSocket, user_text: str) -> None:
    """Delegate one Codex builder task to the dedicated builder-flow module."""
    frozen_proj_id = _get_active_project_id()

    def send(msg: dict) -> None:
        broadcast_sync(msg, proj_id_override=frozen_proj_id)

    return _builder_run_codex_session_thread(
        _builder_runtime_context(frozen_proj_id),
        ws,
        user_text,
        send,
    )


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
    """Delegate one Codex automatic breaker run to the builder-flow module."""
    return _builder_run_codex_auto_review(
        _builder_runtime_context(_get_active_project_id()),
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
    return _builder_run_claude_auto_review(
        _builder_runtime_context(_get_active_project_id()),
        target_repo,
        files_changed,
        send,
        model=model,
        effort=effort,
    )
