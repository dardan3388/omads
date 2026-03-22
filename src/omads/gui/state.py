"""Shared persistence, status, and project helpers for the OMADS GUI."""

from __future__ import annotations

import json
import os
import re
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
    "auto_review": True,  # Run the current automatic breaker step after builder code changes
    "ui_theme": "dark",  # dark, light
}


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
    auto_review: bool | None = None
    ui_theme: str | None = None


class CreateProjectRequest(_RequestModel):
    name: str = ""
    path: str = ""


class SwitchProjectRequest(_RequestModel):
    id: str = ""


def _load_config() -> dict[str, Any]:
    """Load settings from ~/.config/omads/gui_settings.json."""
    settings = dict(_DEFAULT_SETTINGS)
    if _CONFIG_PATH.exists():
        try:
            saved = json.loads(_read_json_text(_CONFIG_PATH))
            settings.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
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


def _append_history(project_id: str, entry: dict) -> None:
    """Append one entry to the project history."""
    from datetime import datetime
    entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = _get_project_history_path(project_id)
    _append_jsonl_line(path, entry)


def _append_log(project_id: str, entry: dict) -> None:
    """Append one entry to the project log file."""
    from datetime import datetime
    _LOG_TYPES = {"task_start", "stream_text", "stream_tool", "agent_status",
                  "agent_activity", "task_complete", "task_stopped", "task_error",
                  "chat_response", "stream_thinking", "stream_result"}
    if entry.get("type") not in _LOG_TYPES:
        return
    entry["timestamp"] = datetime.now().strftime("%d.%m. %H:%M:%S")
    path = _get_project_log_path(project_id)
    _append_jsonl_line(path, entry)


def _read_history(project_id: str) -> list[dict]:
    """Read the last 200 history entries for one project (tail read)."""
    from collections import deque
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
    """Read the last 500 log entries for one project (tail read)."""
    from collections import deque
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

def _load_chat_sessions() -> dict[str, str]:
    if _CHAT_SESSIONS_PATH.exists():
        try:
            return json.loads(_read_json_text(_CHAT_SESSIONS_PATH))
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def _save_chat_sessions(sessions: dict[str, str]) -> None:
    _write_text_file(_CHAT_SESSIONS_PATH, json.dumps(sessions, indent=2))

_chat_sessions_lock = threading.RLock()
_chat_sessions: dict[str, str] = _load_chat_sessions()


def _get_chat_session(repo_key: str) -> str | None:
    """Read one stored Claude session consistently under lock."""
    with _chat_sessions_lock:
        return _chat_sessions.get(repo_key)


def _set_chat_session(repo_key: str, session_id: str) -> None:
    """Update one Claude session atomically and persist it."""
    with _chat_sessions_lock:
        _chat_sessions[repo_key] = session_id
        _save_chat_sessions(dict(_chat_sessions))


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
