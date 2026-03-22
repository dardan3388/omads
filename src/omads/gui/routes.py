"""REST-Routen der OMADS GUI."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from omads.utils.paths import get_data_dir, get_dna_dir

from . import runtime
from .state import (
    CreateProjectRequest,
    SwitchProjectRequest,
    UpdateSettingsRequest,
    _find_project_by_path,
    _get_gui_status_snapshot,
    _get_setting,
    _get_settings_snapshot,
    _load_projects,
    _probe_claude_limit_status,
    _probe_codex_status,
    _read_history,
    _read_log,
    _save_projects,
    _update_settings,
    _validate_project_id,
)

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def index():
    """Liefert das Frontend."""
    gui_dir = Path(__file__).parent
    html_path = gui_dir / "frontend.html"
    return HTMLResponse(html_path.read_text())


# ─── REST Endpoints ──────────────────────────────────────────────

@router.get("/api/settings")
async def get_settings():
    return _get_settings_snapshot()


_ALLOWED_SETTINGS = {
    "target_repo": str,
    "claude_model": str,
    "claude_permission_mode": str,
    "claude_max_turns": int,
    "claude_effort": str,
    "codex_model": str,
    "codex_reasoning": str,
    "codex_fast": bool,
    "auto_review": bool,
}

@router.post("/api/settings")
async def update_settings(data: UpdateSettingsRequest):
    payload = data.model_dump(exclude_none=True)

    def apply_updates(settings: dict[str, Any]) -> None:
        # Security: Nur bekannte Keys mit korrekten Typen akzeptieren
        for key, value in payload.items():
            if key not in _ALLOWED_SETTINGS:
                continue
            expected_type = _ALLOWED_SETTINGS[key]
            if not isinstance(value, expected_type):
                continue
            # target_repo braucht Extra-Validierung (is_dir + Home-Check)
            if key == "target_repo":
                resolved = Path(value).resolve()
                home_dir = Path.home().resolve()
                if not resolved.is_dir() or (resolved != home_dir and not str(resolved).startswith(str(home_dir) + "/")):
                    continue  # Ungültigen Pfad still ignorieren
                settings[key] = str(resolved)
            else:
                settings[key] = value

        # Bounds erzwingen
        settings["claude_max_turns"] = max(1, min(int(settings.get("claude_max_turns", 25)), 100))
        if settings.get("claude_effort") not in ("low", "medium", "high", "max"):
            settings["claude_effort"] = "high"
        if settings.get("codex_reasoning") not in ("low", "medium", "high", "xhigh"):
            settings["codex_reasoning"] = "high"

    snapshot = _update_settings(apply_updates)
    await runtime.broadcast({"type": "settings_updated", "settings": snapshot})
    return {"ok": True}


@router.get("/api/browse")
async def browse_directory(path: str = "~"):
    """Listet Unterverzeichnisse eines Pfads auf (für den Ordner-Picker)."""
    try:
        target = Path(path).expanduser().resolve()

        # Security: Nur Home-Verzeichnis und Unterverzeichnisse erlauben
        allowed_base = Path.home().resolve()
        if not (target == allowed_base or str(target).startswith(str(allowed_base) + "/")):
            return {"error": "Access is allowed only inside the home directory", "path": str(target), "dirs": []}

        if not target.exists() or not target.is_dir():
            return {"error": "Directory does not exist", "path": str(target), "dirs": []}

        dirs = []
        try:
            for entry in sorted(target.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    dirs.append({
                        "name": entry.name,
                        "path": str(entry),
                    })
        except PermissionError:
            pass

        return {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "dirs": dirs,
        }
    except Exception as e:
        return {"error": str(e), "path": path, "dirs": []}


@router.get("/api/runtime-status")
async def get_runtime_status():
    """Gibt den letzten bekannten Claude-/Codex-Status zurück."""
    return _get_gui_status_snapshot()


@router.post("/api/runtime-status/claude/refresh")
async def refresh_claude_runtime_status():
    """Aktualisiert die echte Claude-Limitanzeige."""
    with runtime._process_lock:
        busy = runtime._active_process and runtime._active_process.poll() is None
    if busy:
        return {"error": "Please wait until the current task finishes"}
    target_repo = _get_setting("target_repo", str(Path(".").resolve()))
    try:
        limit = await asyncio.to_thread(_probe_claude_limit_status, target_repo)
    except Exception as exc:
        return {"error": str(exc)}
    await runtime.broadcast({"type": "claude_limit_update", "limit": limit})
    return {"ok": True, "limit": limit}


@router.post("/api/runtime-status/codex/refresh")
async def refresh_codex_runtime_status():
    """Fragt Codex /status ab und liefert den letzten Text zurück."""
    with runtime._process_lock:
        busy = runtime._active_process and runtime._active_process.poll() is None
    if busy:
        return {"error": "Please wait until the current task finishes"}
    target_repo = _get_setting("target_repo", str(Path(".").resolve()))
    try:
        codex_status = await asyncio.to_thread(_probe_codex_status, target_repo)
    except Exception as exc:
        return {"error": str(exc)}
    await runtime.broadcast({"type": "codex_status_update", "codex_status": codex_status})
    return {"ok": True, "codex_status": codex_status}


# ─── Projekt-Management Endpoints ─────────────────────────────────

@router.get("/api/projects")
async def list_projects():
    """Listet alle registrierten Projekte auf."""
    return _load_projects()


@router.post("/api/projects")
async def create_project(data: CreateProjectRequest):
    """Erstellt ein neues Projekt."""
    from datetime import datetime
    import hashlib

    name = data.name.strip()
    path = data.path.strip()
    if not name or not path:
        return {"error": "Name and path are required"}

    resolved = str(Path(path).expanduser().resolve())
    if not Path(resolved).is_dir():
        return {"error": f"Not a directory: {resolved}"}
    home_str = str(Path.home().resolve())
    if resolved != home_str and not resolved.startswith(home_str + "/"):
        return {"error": "Only directories inside $HOME are allowed"}

    # Prüfe ob Projekt mit diesem Pfad bereits existiert
    existing = _find_project_by_path(resolved)
    if existing:
        return {"error": f"Project '{existing['name']}' already exists for this path"}

    project_id = hashlib.sha256(resolved.encode()).hexdigest()[:12]
    project = {
        "id": project_id,
        "name": name,
        "path": resolved,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_used": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    projects = _load_projects()
    projects.append(project)
    _save_projects(projects)

    # Direkt zu diesem Projekt wechseln
    _update_settings(lambda settings: settings.__setitem__("target_repo", resolved))

    return {"ok": True, "project": project}


@router.post("/api/projects/switch")
async def switch_project(data: SwitchProjectRequest):
    """Wechselt zum angegebenen Projekt."""
    from datetime import datetime

    project_id = data.id
    projects = _load_projects()

    for p in projects:
        if p["id"] == project_id:
            # Pfad validieren (könnte gelöscht/verschoben worden sein)
            proj_path = Path(p["path"])
            if not proj_path.is_dir():
                return {"error": f"Directory no longer exists: {p['path']}"}
            _update_settings(lambda settings: settings.__setitem__("target_repo", p["path"]))
            p["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_projects(projects)
            await runtime.broadcast({"type": "system", "text": p["path"]})
            return {"ok": True, "project": p}

    return {"error": "Project not found"}


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Entfernt ein Projekt aus der Registry (Dateien bleiben erhalten)."""
    try:
        _validate_project_id(project_id)
    except ValueError:
        return {"error": "Invalid project ID"}
    projects = _load_projects()
    projects = [p for p in projects if p["id"] != project_id]
    _save_projects(projects)
    return {"ok": True}


@router.get("/api/projects/{project_id}/history")
async def get_project_history(project_id: str):
    """Gibt die komplette Historie eines Projekts zurück."""
    try:
        return _read_history(project_id)
    except ValueError:
        return {"error": "Invalid project ID"}


@router.get("/api/projects/{project_id}/logs")
async def get_project_logs(project_id: str):
    """Gibt die Log-Einträge eines Projekts zurück."""
    try:
        return _read_log(project_id)
    except ValueError:
        return {"error": "Invalid project ID"}


@router.get("/api/health")
async def get_health():
    """Prüft ob Claude Code CLI und Codex CLI verfügbar sind."""
    import shutil

    result: dict[str, Any] = {"claude": {"installed": False}, "codex": {"installed": False}}

    # Claude Code CLI prüfen
    claude_path = shutil.which("claude")
    if claude_path:
        result["claude"]["installed"] = True
        result["claude"]["path"] = claude_path
        try:
            ver = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=5,
            )
            result["claude"]["version"] = ver.stdout.strip() or ver.stderr.strip()
        except Exception:
            result["claude"]["version"] = "unbekannt"
        # Auth prüfen: ~/.claude/.credentials.json muss existieren
        creds = Path.home() / ".claude" / ".credentials.json"
        result["claude"]["authenticated"] = creds.exists()
    else:
        result["claude"]["hint"] = "npm install -g @anthropic-ai/claude-code"

    # Codex CLI prüfen
    codex_path = shutil.which("codex")
    if codex_path:
        result["codex"]["installed"] = True
        result["codex"]["path"] = codex_path
        try:
            ver = subprocess.run(
                ["codex", "--version"], capture_output=True, text=True, timeout=5,
            )
            result["codex"]["version"] = ver.stdout.strip() or ver.stderr.strip()
        except Exception:
            result["codex"]["version"] = "unbekannt"
    else:
        result["codex"]["hint"] = "npm install -g @openai/codex"

    # Python-Version
    import sys
    result["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    return result


@router.get("/api/status")
async def get_status():
    """Gibt OMADS-Systemstatus zurück."""
    from omads.dna.cold_start import get_current_phase
    phase = "unbekannt"
    try:
        phase = get_current_phase(get_dna_dir()).value
    except Exception:
        pass

    # Ledger-Einträge zählen
    ledger_count = 0
    ledger_path = get_data_dir() / "ledger" / "task_history.jsonl"
    if ledger_path.exists():
        ledger_count = sum(1 for _ in ledger_path.open())

    return {
        "phase": phase,
        "total_tasks": ledger_count,
        "target_repo": _get_setting("target_repo", str(Path(".").resolve())),
        "auto_review": _get_setting("auto_review", True),
    }


@router.get("/api/ledger")
async def get_ledger():
    """Gibt die letzten 20 Ledger-Einträge zurück."""
    from collections import deque
    ledger_path = get_data_dir() / "ledger" / "task_history.jsonl"
    entries = []
    if ledger_path.exists():
        try:
            with open(ledger_path, encoding="utf-8") as f:
                tail = deque(f, maxlen=20)
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

