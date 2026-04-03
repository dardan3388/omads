"""Shared persistence, status, and project helpers for the OMADS GUI."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

_file_locks_guard = threading.Lock()
_file_locks: dict[Path, threading.Lock] = {}


def _get_file_lock(path: Path) -> threading.Lock:
    """Return one stable lock per file."""
    normalized = path.expanduser().resolve(strict=False)
    with _file_locks_guard:
        lock = _file_locks.get(normalized)
        if lock is None:
            lock = threading.Lock()
            _file_locks[normalized] = lock
        return lock


def _write_text_file(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write one file atomically under a per-file lock."""
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with _get_file_lock(path):
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.replace(path)


def _append_jsonl_line(path: Path, entry: dict[str, Any]) -> None:
    """Append exactly one JSONL line in a thread-safe way."""
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _get_file_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_json_text(path: Path, *, encoding: str = "utf-8") -> str:
    """Read one file under the same per-file lock used by write paths."""
    path = path.expanduser().resolve(strict=False)
    with _get_file_lock(path):
        return path.read_text(encoding=encoding)


def _build_process_failure_text(
    context: str,
    returncode: int,
    *,
    result_text: str = "",
    output_lines: list[str] | None = None,
) -> str:
    """Build a user-readable error message for failed CLI processes."""
    detail = (result_text or "\n".join((output_lines or [])[-3:])).strip()
    text = f"{context} failed (exit code {returncode})."
    if detail:
        compact = " ".join(detail.split())[:280]
        text += f" Last output: {compact}"
    return text

# ─── LAN helpers ──────────────────────────────────────────────────

_RFC1918_RE = re.compile(
    r"^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})$"
)


def _detect_lan_ip() -> str:
    """Return the local LAN IP via a non-sending UDP connect trick."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if _RFC1918_RE.match(ip):
            return ip
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if _RFC1918_RE.match(ip):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def is_rfc1918_origin(origin: str) -> bool:
    """Return True if *origin* looks like http://<private-ip>:<port>."""
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(origin)
        host = parsed.hostname or ""
        return bool(_RFC1918_RE.match(host))
    except Exception:
        return False


def _resolve_user_path(path: str | Path) -> Path:
    """Resolve one user-supplied path without requiring it to exist."""
    return Path(path).expanduser().resolve(strict=False)


def is_path_inside_home(path: str | Path, *, allow_home: bool = True) -> bool:
    """Return True when *path* resolves to the home directory or one of its descendants."""
    target = _resolve_user_path(path)
    home_dir = _resolve_user_path(Path.home())
    if allow_home and target == home_dir:
        return True
    try:
        target.relative_to(home_dir)
        return True
    except ValueError:
        return False


# ─── Config file (persistent) ─────────────────────────────────────

_CONFIG_PATH = Path.home() / ".config" / "omads" / "gui_settings.json"
_GUI_STATUS_PATH = Path.home() / ".config" / "omads" / "gui_status.json"


def _default_target_repo() -> str:
    """Return the default repo path, overridable for container use."""
    return os.environ.get("OMADS_DEFAULT_TARGET_REPO", str(Path(".").resolve()))

_DEFAULT_SETTINGS: dict[str, Any] = {
    "target_repo": _default_target_repo(),
    "builder_agent": "claude",  # claude, codex
    "review_first_reviewer": "claude",  # claude, codex
    "review_second_reviewer": "codex",  # claude, codex
    # Claude Code CLI
    "claude_model": "sonnet",
    "claude_permission_mode": "default",  # default, auto, plan, bypassPermissions
    "claude_effort": "high",  # low, medium, high, max
    # Codex CLI automatic review
    "codex_model": "",  # Empty = Codex default (gpt-5.4)
    "codex_reasoning": "high",  # low, medium, high, xhigh
    "codex_fast": False,  # service_tier: fast vs default
    "codex_execution_mode": "default",  # default, auto, read-only, full-auto
    "auto_review": True,  # Run the current automatic breaker step after builder code changes
    "ui_theme": "dark",  # dark, light
    "lan_access": False,  # Bind to 0.0.0.0 so the GUI is reachable from LAN devices
}


def _coerce_bool_setting(value: Any, *, default: bool) -> bool:
    """Convert legacy string/int bool-like values into strict booleans."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, int):
        return value != 0
    return default


def _normalize_claude_permission_mode(value: Any, *, default: str = "default") -> str:
    """Normalize legacy Claude permission values into the CLI-supported modes."""
    if not isinstance(value, str):
        return default
    normalized = value.strip()
    if normalized in {"default", "auto", "plan", "bypassPermissions"}:
        return normalized
    if normalized == "auto-accept":
        return "auto"
    if normalized == "bypass":
        return "bypassPermissions"
    return default


def _normalize_codex_execution_mode(value: Any, *, default: str = "default") -> str:
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


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UpdateSettingsRequest(_RequestModel):
    target_repo: str | None = None
    builder_agent: str | None = None
    review_first_reviewer: str | None = None
    review_second_reviewer: str | None = None
    claude_model: str | None = None
    claude_permission_mode: str | None = None
    claude_effort: str | None = None
    codex_model: str | None = None
    codex_reasoning: str | None = None
    codex_fast: bool | None = None
    codex_execution_mode: str | None = None
    auto_review: bool | None = None
    ui_theme: str | None = None
    lan_access: bool | None = None


class CreateProjectRequest(_RequestModel):
    name: str = ""
    path: str = ""


class SwitchProjectRequest(_RequestModel):
    id: str = ""


class GitHubCloneRequest(_RequestModel):
    full_name: str = ""
    target_dir: str = ""


class GitHubGitRequest(_RequestModel):
    repo_path: str = ""
    operation: str = ""  # status, commit, push, pull
    message: str = ""


def _load_config() -> dict[str, Any]:
    """Load settings from ~/.config/omads/gui_settings.json."""
    settings = dict(_DEFAULT_SETTINGS)
    if _CONFIG_PATH.exists():
        try:
            saved = json.loads(_read_json_text(_CONFIG_PATH))
            settings.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    settings["codex_fast"] = _coerce_bool_setting(
        settings.get("codex_fast"),
        default=_DEFAULT_SETTINGS["codex_fast"],
    )
    settings["auto_review"] = _coerce_bool_setting(
        settings.get("auto_review"),
        default=_DEFAULT_SETTINGS["auto_review"],
    )
    settings["lan_access"] = _coerce_bool_setting(
        settings.get("lan_access"),
        default=_DEFAULT_SETTINGS["lan_access"],
    )
    settings["claude_permission_mode"] = _normalize_claude_permission_mode(
        settings.get("claude_permission_mode"),
        default=_DEFAULT_SETTINGS["claude_permission_mode"],
    )
    if isinstance(settings.get("codex_model"), str):
        settings["codex_model"] = settings["codex_model"].strip()
    settings["codex_execution_mode"] = _normalize_codex_execution_mode(
        settings.get("codex_execution_mode"),
        default=_DEFAULT_SETTINGS["codex_execution_mode"],
    )
    return settings


def _save_config(settings: dict[str, Any]) -> None:
    """Persist settings."""
    _write_text_file(_CONFIG_PATH, json.dumps(settings, indent=2, ensure_ascii=False))


# Global state loaded from config at startup
_settings_lock = threading.RLock()
_settings: dict[str, Any] = _load_config()


def _get_settings_snapshot() -> dict[str, Any]:
    """Return a consistent copy of the current settings."""
    with _settings_lock:
        return dict(_settings)


def _get_setting(key: str, default: Any = None) -> Any:
    """Read exactly one setting value under lock."""
    with _settings_lock:
        return _settings.get(key, default)


def _update_settings(update_fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Update settings atomically, persist them, and return a snapshot."""
    with _settings_lock:
        mutable = dict(_settings)
        update_fn(mutable)
        _settings.clear()
        _settings.update(mutable)
        snapshot = dict(_settings)
        _save_config(snapshot)
        return snapshot

_GUI_STATUS_DEFAULTS: dict[str, Any] = {
    "claude_limit": {
        "status": "",
        "resets_at": 0,
        "rate_limit_type": "",
        "is_using_overage": False,
        "overage_status": "",
        "overage_disabled_reason": "",
        "last_checked": 0,
        "source": "",
        "error": "",
    },
}


def _load_gui_status() -> dict[str, Any]:
    """Load the last known GUI limit status."""
    status = {
        "claude_limit": dict(_GUI_STATUS_DEFAULTS["claude_limit"]),
    }
    if _GUI_STATUS_PATH.exists():
        try:
            saved = json.loads(_read_json_text(_GUI_STATUS_PATH))
            if isinstance(saved.get("claude_limit"), dict):
                status["claude_limit"].update(saved["claude_limit"])
        except (json.JSONDecodeError, OSError):
            pass
    return status


def _save_gui_status() -> None:
    """Persist the last known GUI limit status."""
    _write_text_file(_GUI_STATUS_PATH, json.dumps(_gui_status, indent=2, ensure_ascii=False))


_CLI_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
    "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "OPENAI_API_KEY",
    "DISPLAY", "WAYLAND_DISPLAY", "SHELL", "EDITOR",
    "VISUAL", "SSH_AUTH_SOCK",
}


def _build_cli_env() -> dict[str, str]:
    """Return a minimal, safe environment for Claude/Codex."""
    return {
        k: v for k, v in os.environ.items()
        if k in _CLI_ENV_ALLOWLIST and k != "CLAUDECODE"
    }

# ─── Project registry (persistent) ────────────────────────────────

_PROJECTS_PATH = Path.home() / ".config" / "omads" / "projects.json"
_HISTORY_DIR = Path.home() / ".config" / "omads" / "history"
_TIMELINE_DIR = Path.home() / ".config" / "omads" / "timeline"
_LOG_TYPES = {
    "task_start",
    "stream_text",
    "stream_tool",
    "agent_status",
    "agent_activity",
    "task_complete",
    "task_stopped",
    "task_error",
    "chat_response",
    "stream_thinking",
    "stream_result",
}
_HISTORY_COMPAT_TYPES = {
    "user_input",
    "builder_response",
    "claude_response",
    "task_result",
    "chat",
    "task_error",
    "chat_response",
}


def _load_projects() -> list[dict]:
    """Load the project registry."""
    if _PROJECTS_PATH.exists():
        try:
            return json.loads(_read_json_text(_PROJECTS_PATH))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_projects(projects: list[dict]) -> None:
    """Save the project registry."""
    _write_text_file(_PROJECTS_PATH, json.dumps(projects, indent=2, ensure_ascii=False))


_SAFE_PROJECT_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_project_id(project_id: str) -> str:
    """Validate project_id against path traversal (alphanumeric, -, _ only)."""
    if not project_id or not _SAFE_PROJECT_ID.match(project_id):
        raise ValueError(f"Invalid project ID: {project_id!r}")
    return project_id


def _get_project_history_path(project_id: str) -> Path:
    """Return the path to one project's history file."""
    _validate_project_id(project_id)
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return _HISTORY_DIR / f"{project_id}.jsonl"


def _get_project_log_path(project_id: str) -> Path:
    """Return the path to one project's log file."""
    _validate_project_id(project_id)
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return _HISTORY_DIR / f"{project_id}_log.jsonl"


def _get_project_timeline_path(project_id: str) -> Path:
    """Return the path to one project's unified event timeline."""
    _validate_project_id(project_id)
    _TIMELINE_DIR.mkdir(parents=True, exist_ok=True)
    return _TIMELINE_DIR / f"{project_id}.jsonl"


def _append_history(project_id: str, entry: dict) -> None:
    """Append one entry to the project history."""
    from datetime import datetime
    entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = _get_project_history_path(project_id)
    _append_jsonl_line(path, entry)


def _append_log(project_id: str, entry: dict) -> None:
    """Append one entry to the project log file."""
    from datetime import datetime
    if entry.get("type") not in _LOG_TYPES:
        return
    entry["timestamp"] = datetime.now().strftime("%d.%m. %H:%M:%S")
    path = _get_project_log_path(project_id)
    _append_jsonl_line(path, entry)


def _append_timeline_event(project_id: str, entry: dict) -> None:
    """Append one event to the unified project timeline."""
    from datetime import datetime, timezone

    event = dict(entry)
    now = datetime.now(timezone.utc)
    event.setdefault("timestamp", now.isoformat())
    event.setdefault("timestamp_display", now.strftime("%Y-%m-%d %H:%M:%S"))
    path = _get_project_timeline_path(project_id)
    _append_jsonl_line(path, event)


def _read_history(project_id: str) -> list[dict]:
    """Return a history-compatible view for one project.

    Prefer the unified timeline when available, then fall back to the legacy
    history file for older runs.
    """
    from collections import deque

    timeline_entries: deque[dict] = deque(maxlen=200)
    for entry in _iter_timeline(project_id):
        if entry.get("type") in _HISTORY_COMPAT_TYPES:
            timeline_entries.append(entry)
    if timeline_entries:
        return list(timeline_entries)

    # Legacy fallback for older runs that predate the unified timeline.
    path = _get_project_history_path(project_id)
    entries = []
    if path.exists():
        try:
            with _get_file_lock(path):
                with open(path, encoding="utf-8") as f:
                    tail = deque(f, maxlen=200)
            for line in tail:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
    return entries


def _read_log(project_id: str) -> list[dict]:
    """Return a log-compatible view for one project.

    Prefer the unified timeline when available, then fall back to the legacy
    log file for older runs.
    """
    from collections import deque

    timeline_entries: deque[dict] = deque(maxlen=500)
    for entry in _iter_timeline(project_id):
        if entry.get("type") in _LOG_TYPES:
            timeline_entries.append(entry)
    if timeline_entries:
        return list(timeline_entries)

    # Legacy fallback for older runs that predate the unified timeline.
    path = _get_project_log_path(project_id)
    entries = []
    if path.exists():
        try:
            with _get_file_lock(path):
                with open(path, encoding="utf-8") as f:
                    tail = deque(f, maxlen=500)
            for line in tail:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
    return entries


def _iter_timeline(project_id: str):
    """Yield parsed timeline events in chronological order.

    The file is read in one pass under the lock so that callers never
    hold the lock across yields — preventing potential deadlocks when
    a write to the same file is attempted between iterations.
    """
    path = _get_project_timeline_path(project_id)
    if not path.exists():
        return
    entries: list[dict] = []
    try:
        with _get_file_lock(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return
    yield from entries


def _read_timeline(project_id: str) -> list[dict]:
    """Read the full unified event timeline for one project."""
    return list(_iter_timeline(project_id))


def _read_timeline_page(project_id: str, *, limit: int = 200, before: int | None = None) -> dict[str, Any]:
    """Read one bounded page from the unified timeline without truncating the source data."""
    from collections import deque

    safe_limit = max(1, min(limit, 500))
    total_count = 0
    window: deque[tuple[int, dict[str, Any]]] = deque(maxlen=safe_limit)

    for seq, entry in enumerate(_iter_timeline(project_id), start=1):
        total_count = seq
        if before is not None and seq >= before:
            continue
        window.append((seq, entry))

    entries = []
    for seq, entry in window:
        event = dict(entry)
        event["seq"] = seq
        entries.append(event)

    has_more = bool(entries) and entries[0]["seq"] > 1
    next_before = entries[0]["seq"] if has_more else None

    return {
        "entries": entries,
        "limit": safe_limit,
        "has_more": has_more,
        "next_before": next_before,
        "total_count": total_count,
    }


def _build_chat_handover_context(project_id: str) -> str:
    """Build a conversation summary from recent timeline events for builder handover."""
    if not project_id:
        return ""
    relevant_types = {"user_input", "stream_text", "chat_response", "task_error"}
    entries: list[str] = []
    for event in _iter_timeline(project_id):
        etype = event.get("type", "")
        if etype not in relevant_types:
            continue
        if etype == "user_input":
            line = f"User: {event.get('text', '')}"
        elif etype == "task_error":
            line = f"System: {event.get('text', '')}"
        else:
            agent = event.get("agent", "Agent")
            line = f"{agent}: {event.get('text', '')}"
        entries.append(line)
    if not entries:
        return ""
    return "\n".join(entries)


def _delete_project_data(project_id: str) -> None:
    """Remove timeline, history, and log files for one project."""
    _validate_project_id(project_id)
    for path in (
        _get_project_timeline_path(project_id),
        _get_project_history_path(project_id),
        _get_project_log_path(project_id),
    ):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _clear_project_timeline(project_id: str) -> None:
    """Clear the timeline for one project (user-initiated context reset)."""
    _validate_project_id(project_id)
    for path in (
        _get_project_timeline_path(project_id),
        _get_project_history_path(project_id),
        _get_project_log_path(project_id),
    ):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _find_project_by_path(path: str) -> dict | None:
    """Find one project by its path."""
    resolved = str(Path(path).resolve())
    for p in _load_projects():
        if p.get("path") == resolved:
            return p
    return None


def _get_active_project_id() -> str | None:
    """Return the active project ID."""
    target = _get_setting("target_repo", "")
    if not target:
        return None
    proj = _find_project_by_path(target)
    return proj["id"] if proj else None


_gui_status_lock = threading.Lock()
_gui_status: dict[str, Any] = _load_gui_status()


def _get_gui_status_snapshot() -> dict[str, Any]:
    """Return the last known GUI status."""
    with _gui_status_lock:
        return {
            "claude_limit": dict(_gui_status["claude_limit"]),
        }


def _sync_gui_status_from_disk_locked() -> None:
    """Refresh from disk to avoid overwriting partial updates."""
    latest = _load_gui_status()
    _gui_status["claude_limit"].update(latest["claude_limit"])


def _update_claude_limit_status(rl_info: dict[str, Any], source: str) -> dict[str, Any]:
    """Store real Claude limit data from one rate_limit_event."""
    with _gui_status_lock:
        _sync_gui_status_from_disk_locked()
        limit = _gui_status["claude_limit"]
        limit["status"] = rl_info.get("status", "")
        limit["resets_at"] = int(rl_info.get("resetsAt", 0) or 0)
        limit["rate_limit_type"] = rl_info.get("rateLimitType", "")
        limit["is_using_overage"] = bool(rl_info.get("isUsingOverage", False))
        limit["overage_status"] = rl_info.get("overageStatus", "")
        limit["overage_disabled_reason"] = rl_info.get("overageDisabledReason", "")
        limit["last_checked"] = int(time.time())
        limit["source"] = source
        limit["error"] = ""
        _save_gui_status()
        return dict(limit)


def _probe_claude_limit_status(target_repo: str) -> dict[str, Any]:
    """Query Claude minimally to retrieve real limit data."""
    model = _get_setting("claude_model", "sonnet")
    cmd = [
        "claude",
        "-p",
        "Reply with exactly OK.",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--effort",
        "low",
        "--tools",
        "",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=target_repo,
        env=_build_cli_env(),
        timeout=30,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or "Claude limit could not be fetched")

    rate_limit_info: dict[str, Any] | None = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "rate_limit_event":
            rate_limit_info = event.get("rate_limit_info", {})
            break

    if not rate_limit_info:
        raise RuntimeError("Claude did not return any limit data")
    return _update_claude_limit_status(rate_limit_info, source="manual_refresh")

_CHAT_SESSIONS_PATH = Path.home() / ".config" / "omads" / "chat_sessions.json"


def _repo_instance_id(repo_path: str | None) -> str | None:
    """Return a stable token for the current repo instance at one path."""
    if not repo_path:
        return None

    repo_root = Path(repo_path).expanduser().resolve(strict=False)
    marker = repo_root / ".git"
    identity_path = repo_root

    if marker.is_dir():
        identity_path = marker.resolve(strict=False)
    elif marker.is_file():
        try:
            raw = marker.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw.startswith("gitdir:"):
            rel = raw.split(":", 1)[1].strip()
            identity_path = (marker.parent / rel).resolve(strict=False)

    try:
        st = identity_path.stat()
    except OSError:
        return None

    import hashlib

    payload = f"{identity_path}|{st.st_dev}|{st.st_ino}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chat_session_key(repo_key: str, scope: str = "builder") -> str:
    """Return one stable session key per repository and conversation scope."""
    return repo_key if scope == "builder" else f"{repo_key}::{scope}"


def _load_chat_sessions() -> dict[str, Any]:
    if _CHAT_SESSIONS_PATH.exists():
        try:
            return json.loads(_read_json_text(_CHAT_SESSIONS_PATH))
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def _save_chat_sessions(sessions: dict[str, Any]) -> None:
    _write_text_file(_CHAT_SESSIONS_PATH, json.dumps(sessions, indent=2))

_chat_sessions_lock = threading.RLock()
_chat_sessions: dict[str, Any] = _load_chat_sessions()


def _get_chat_session(
    repo_key: str,
    *,
    repo_path: str | None = None,
    scope: str = "builder",
    purpose: str | None = None,
) -> str | None:
    """Read one stored Claude session consistently under lock."""
    if purpose is not None:
        scope = purpose
    key = _chat_session_key(repo_key, scope)
    with _chat_sessions_lock:
        entry = _chat_sessions.get(key)
        if isinstance(entry, str):
            # Legacy entries predate repo-instance tracking. Drop them when
            # a repo path is available so recreated repos do not resume stale sessions.
            if repo_path:
                del _chat_sessions[key]
                _save_chat_sessions(dict(_chat_sessions))
                return None
            return entry
        if not isinstance(entry, dict):
            return None

        session_id = entry.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            _chat_sessions.pop(key, None)
            _save_chat_sessions(dict(_chat_sessions))
            return None

        if repo_path:
            current_repo_instance = _repo_instance_id(repo_path)
            stored_repo_instance = entry.get("repo_instance_id")
            if (
                not isinstance(stored_repo_instance, str)
                or not current_repo_instance
                or stored_repo_instance != current_repo_instance
            ):
                _chat_sessions.pop(key, None)
                _save_chat_sessions(dict(_chat_sessions))
                return None

        return session_id


def _set_chat_session(
    repo_key: str,
    session_id: str,
    *,
    repo_path: str | None = None,
    scope: str = "builder",
    purpose: str | None = None,
) -> None:
    """Update one Claude session atomically and persist it."""
    if purpose is not None:
        scope = purpose
    entry: dict[str, str] | str = session_id
    repo_instance = _repo_instance_id(repo_path)
    if repo_instance:
        entry = {
            "session_id": session_id,
            "repo_instance_id": repo_instance,
        }
    with _chat_sessions_lock:
        _chat_sessions[_chat_session_key(repo_key, scope)] = entry
        _save_chat_sessions(dict(_chat_sessions))


def _clear_chat_session(repo_key: str, *, scope: str = "builder") -> None:
    """Remove a stored session so the next run starts fresh with handover context."""
    key = _chat_session_key(repo_key, scope)
    with _chat_sessions_lock:
        if key in _chat_sessions:
            del _chat_sessions[key]
            _save_chat_sessions(dict(_chat_sessions))


# ─── Last builder tracking (detect builder switches for handover) ──
_LAST_BUILDER_PATH = Path.home() / ".config" / "omads" / "last_builder.json"


def _get_last_builder(project_id: str) -> str | None:
    """Return which builder was last used for a project."""
    if not project_id:
        return None
    try:
        if _LAST_BUILDER_PATH.exists():
            data = json.loads(_read_json_text(_LAST_BUILDER_PATH))
            return data.get(project_id)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _set_last_builder(project_id: str, builder: str) -> None:
    """Record which builder was just used for a project."""
    if not project_id:
        return
    try:
        data = {}
        if _LAST_BUILDER_PATH.exists():
            data = json.loads(_read_json_text(_LAST_BUILDER_PATH))
    except (json.JSONDecodeError, OSError):
        data = {}
    data[project_id] = builder
    _write_text_file(_LAST_BUILDER_PATH, json.dumps(data, indent=2))


# ─── Project memory (persistent context across sessions) ──────────
_MEMORY_DIR = Path.home() / ".config" / "omads" / "memory"


def _get_memory_path(repo_path: str) -> Path:
    """Return the memory file path for one repository."""
    import hashlib
    resolved = str(Path(repo_path).resolve())
    short_hash = hashlib.sha256(resolved.encode()).hexdigest()[:12]
    friendly_name = Path(resolved).name or "default"
    return _MEMORY_DIR / f"{friendly_name}_{short_hash}.md"


def _load_project_memory(repo_path: str) -> str:
    """Load persistent project context (memory + CLAUDE.md)."""
    parts: list[str] = []

    # 1. Read the target project's CLAUDE.md (the way Claude Code does)
    claude_md = Path(repo_path) / "CLAUDE.md"
    if claude_md.exists():
        try:
            content = claude_md.read_text(encoding="utf-8")[:8000]
            parts.append(f"=== CLAUDE.md (Project instructions) ===\n{content}")
        except OSError:
            pass

    # 2. Load the stored project summary
    mem_path = _get_memory_path(repo_path)
    if mem_path.exists():
        try:
            content = mem_path.read_text(encoding="utf-8")[:4000]
            parts.append(f"=== Previous session summary ===\n{content}")
        except OSError:
            pass

    return "\n\n".join(parts)


def _save_project_memory(repo_path: str, summary: str) -> None:
    """Save one project summary for the next session."""
    mem_path = _get_memory_path(repo_path)
    # Prefix the summary with a timestamp
    from datetime import datetime, timezone
    header = f"Last updated: {datetime.now(timezone.utc).isoformat()}\n\n"
    _write_text_file(mem_path, header + summary[:6000], encoding="utf-8")
