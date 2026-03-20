"""OMADS Web GUI — FastAPI Backend mit WebSocket für Live-Agent-Streaming."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from omads.utils.paths import get_data_dir, get_dna_dir

app = FastAPI(title="OMADS GUI")

# ─── Config-Datei (persistent) ────────────────────────────────────

_CONFIG_PATH = Path.home() / ".config" / "omads" / "gui_settings.json"
_GUI_STATUS_PATH = Path.home() / ".config" / "omads" / "gui_status.json"

_DEFAULT_SETTINGS: dict[str, Any] = {
    "target_repo": str(Path(".").resolve()),
    # Claude Code CLI
    "claude_model": "sonnet",
    "claude_permission_mode": "default",  # default, auto, plan, bypassPermissions
    "claude_max_turns": 25,
    "claude_effort": "high",  # low, medium, high, max
    # Codex CLI Auto-Review
    "codex_model": "",  # Leer = Codex-Default (gpt-5.4)
    "codex_reasoning": "high",  # low, medium, high, xhigh
    "codex_fast": False,  # service_tier: fast vs default
    "breaker_timeout": 120,
    "auto_review": True,  # Codex reviewt automatisch nach Code-Änderungen
}


def _load_config() -> dict[str, Any]:
    """Lädt Settings aus ~/.config/omads/gui_settings.json."""
    settings = dict(_DEFAULT_SETTINGS)
    if _CONFIG_PATH.exists():
        try:
            saved = json.loads(_CONFIG_PATH.read_text())
            settings.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def _save_config(settings: dict[str, Any]) -> None:
    """Speichert Settings persistent."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False))


# Globaler State — wird beim Start aus Config geladen
_settings: dict[str, Any] = _load_config()

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
    "codex_status": {
        "text": "",
        "last_checked": 0,
        "source": "",
        "error": "",
    },
}


def _load_gui_status() -> dict[str, Any]:
    """Lädt den letzten bekannten Claude-/Codex-Status."""
    status = {
        "claude_limit": dict(_GUI_STATUS_DEFAULTS["claude_limit"]),
        "codex_status": dict(_GUI_STATUS_DEFAULTS["codex_status"]),
    }
    if _GUI_STATUS_PATH.exists():
        try:
            saved = json.loads(_GUI_STATUS_PATH.read_text())
            if isinstance(saved.get("claude_limit"), dict):
                status["claude_limit"].update(saved["claude_limit"])
            if isinstance(saved.get("codex_status"), dict):
                status["codex_status"].update(saved["codex_status"])
        except (json.JSONDecodeError, OSError):
            pass
    return status


def _save_gui_status() -> None:
    """Speichert den letzten bekannten Claude-/Codex-Status."""
    _GUI_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GUI_STATUS_PATH.write_text(json.dumps(_gui_status, indent=2, ensure_ascii=False))


_CLI_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
    "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "OPENAI_API_KEY",
    "DISPLAY", "WAYLAND_DISPLAY", "SHELL", "EDITOR",
    "VISUAL", "SSH_AUTH_SOCK",
}


def _build_cli_env() -> dict[str, str]:
    """Gibt eine schlanke, sichere ENV für Claude/Codex zurück."""
    return {
        k: v for k, v in os.environ.items()
        if k in _CLI_ENV_ALLOWLIST and k != "CLAUDECODE"
    }

# ─── Projekt-Registry (persistent) ────────────────────────────────

_PROJECTS_PATH = Path.home() / ".config" / "omads" / "projects.json"
_HISTORY_DIR = Path.home() / ".config" / "omads" / "history"


def _load_projects() -> list[dict]:
    """Lädt die Projekt-Registry."""
    if _PROJECTS_PATH.exists():
        try:
            return json.loads(_PROJECTS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_projects(projects: list[dict]) -> None:
    """Speichert die Projekt-Registry."""
    _PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROJECTS_PATH.write_text(json.dumps(projects, indent=2, ensure_ascii=False))


def _get_project_history_path(project_id: str) -> Path:
    """Pfad zur Historie-Datei eines Projekts."""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return _HISTORY_DIR / f"{project_id}.jsonl"


def _get_project_log_path(project_id: str) -> Path:
    """Pfad zur Log-Datei eines Projekts."""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return _HISTORY_DIR / f"{project_id}_log.jsonl"


def _append_history(project_id: str, entry: dict) -> None:
    """Fügt einen Eintrag zur Projekt-Historie hinzu."""
    from datetime import datetime
    entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = _get_project_history_path(project_id)
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_log(project_id: str, entry: dict) -> None:
    """Fügt einen Log-Eintrag zur Projekt-Log-Datei hinzu."""
    from datetime import datetime
    _LOG_TYPES = {"task_start", "stream_text", "stream_tool", "agent_status",
                  "agent_activity", "task_complete", "task_stopped", "task_error",
                  "chat_response", "stream_thinking", "stream_result"}
    if entry.get("type") not in _LOG_TYPES:
        return
    entry["timestamp"] = datetime.now().strftime("%d.%m. %H:%M:%S")
    path = _get_project_log_path(project_id)
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_history(project_id: str) -> list[dict]:
    """Liest die komplette Historie eines Projekts."""
    path = _get_project_history_path(project_id)
    entries = []
    if path.exists():
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _read_log(project_id: str) -> list[dict]:
    """Liest die Log-Einträge eines Projekts."""
    path = _get_project_log_path(project_id)
    entries = []
    if path.exists():
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    # Letzte 500 Einträge (ältere sind für die Anzeige irrelevant)
    return entries[-500:]


def _find_project_by_path(path: str) -> dict | None:
    """Findet ein Projekt anhand seines Pfads."""
    resolved = str(Path(path).resolve())
    for p in _load_projects():
        if p.get("path") == resolved:
            return p
    return None


def _get_active_project_id() -> str | None:
    """Gibt die ID des aktiven Projekts zurück."""
    target = _settings.get("target_repo", "")
    if not target:
        return None
    proj = _find_project_by_path(target)
    return proj["id"] if proj else None


_gui_status_lock = threading.Lock()
_gui_status: dict[str, Any] = _load_gui_status()


def _get_gui_status_snapshot() -> dict[str, Any]:
    """Gibt den zuletzt bekannten GUI-Status zurück."""
    with _gui_status_lock:
        return {
            "claude_limit": dict(_gui_status["claude_limit"]),
            "codex_status": dict(_gui_status["codex_status"]),
        }


def _sync_gui_status_from_disk_locked() -> None:
    """Zieht den letzten Stand aus der Datei nach, um Teil-Updates nicht zu überschreiben."""
    latest = _load_gui_status()
    _gui_status["claude_limit"].update(latest["claude_limit"])
    _gui_status["codex_status"].update(latest["codex_status"])


def _update_claude_limit_status(rl_info: dict[str, Any], source: str) -> dict[str, Any]:
    """Speichert echte Claude-Limitdaten aus einem rate_limit_event."""
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


def _set_codex_status(text: str, source: str, error: str = "") -> dict[str, Any]:
    """Speichert den letzten echten Codex-/status-Text."""
    with _gui_status_lock:
        _sync_gui_status_from_disk_locked()
        codex_status = _gui_status["codex_status"]
        codex_status["text"] = text.strip()
        codex_status["last_checked"] = int(time.time())
        codex_status["source"] = source
        codex_status["error"] = error
        _save_gui_status()
        return dict(codex_status)


def _probe_claude_limit_status(target_repo: str) -> dict[str, Any]:
    """Fragt Claude minimal ab, um echte Limitdaten zu erhalten."""
    model = _settings.get("claude_model", "sonnet")
    cmd = [
        "claude",
        "-p",
        "Antworte exakt mit OK.",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
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
        raise RuntimeError(stderr or "Claude-Limit konnte nicht abgefragt werden")

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
        raise RuntimeError("Claude hat keine Limitdaten zurückgegeben")
    return _update_claude_limit_status(rate_limit_info, source="manual_refresh")


def _probe_codex_status(target_repo: str) -> dict[str, Any]:
    """Fragt Codex über /status ab und speichert die Antwort."""
    cmd = [
        "codex",
        "exec",
        "/status",
        "--json",
        "-s",
        "read-only",
        "-C",
        str(target_repo),
        "--skip-git-repo-check",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=target_repo,
        env=_build_cli_env(),
        timeout=30,
    )

    texts: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            text = item.get("text", "")
            if text:
                texts.append(text.strip())

    status_text = "\n\n".join(t for t in texts if t).strip()
    if not status_text:
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(stderr or "Codex-Status konnte nicht abgefragt werden")
        status_text = "Codex hat keinen Status-Text geliefert."

    return _set_codex_status(status_text, source="manual_refresh")

# Aktive WebSocket-Verbindungen
_connections: list[WebSocket] = []

# Laufender Prozess (für Abbruch)
_active_process: subprocess.Popen | None = None
_task_cancelled: bool = False
_last_files_changed: list[str] = []  # Zuletzt geänderte Dateien (für Review "Letzter Task")
_pending_review_fixes: str = ""  # Letzte Review-Synthese mit Fix-Vorschlägen


async def broadcast(msg: dict) -> None:
    """Sendet eine Nachricht an alle verbundenen Clients."""
    dead = []
    for ws in _connections:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.remove(ws)


def broadcast_sync(msg: dict) -> None:
    """Synchroner Wrapper für broadcast (aus Threads heraus)."""
    # Log-Events pro Projekt persistieren
    proj_id = _get_active_project_id()
    if proj_id:
        try:
            _append_log(proj_id, dict(msg))
        except Exception:
            pass
    for ws in _connections:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(msg), _loop)
        except Exception:
            pass


_loop: asyncio.AbstractEventLoop | None = None


# ─── HTML Frontend ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Liefert das Frontend."""
    gui_dir = Path(__file__).parent
    html_path = gui_dir / "frontend.html"
    return HTMLResponse(html_path.read_text())


# ─── REST Endpoints ──────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    return _settings


_ALLOWED_SETTINGS = {
    "target_repo": str,
    "claude_model": str,
    "claude_permission_mode": str,
    "claude_max_turns": int,
    "claude_effort": str,
    "codex_model": str,
    "codex_reasoning": str,
    "codex_fast": bool,
    "breaker_timeout": int,
    "auto_review": bool,
}

@app.post("/api/settings")
async def update_settings(data: dict):
    # Security: Nur bekannte Keys mit korrekten Typen akzeptieren
    for key, value in data.items():
        if key not in _ALLOWED_SETTINGS:
            continue
        expected_type = _ALLOWED_SETTINGS[key]
        if not isinstance(value, expected_type):
            continue
        _settings[key] = value

    # Bounds erzwingen
    _settings["claude_max_turns"] = max(1, min(int(_settings.get("claude_max_turns", 25)), 100))
    _settings["breaker_timeout"] = max(10, min(int(_settings.get("breaker_timeout", 120)), 600))
    if _settings.get("claude_effort") not in ("low", "medium", "high", "max"):
        _settings["claude_effort"] = "high"
    if _settings.get("codex_reasoning") not in ("low", "medium", "high", "xhigh"):
        _settings["codex_reasoning"] = "high"

    _save_config(_settings)
    await broadcast({"type": "settings_updated", "settings": _settings})
    return {"ok": True}


@app.get("/api/browse")
async def browse_directory(path: str = "~"):
    """Listet Unterverzeichnisse eines Pfads auf (für den Ordner-Picker)."""
    try:
        target = Path(path).expanduser().resolve()

        # Security: Nur Home-Verzeichnis und Unterverzeichnisse erlauben
        allowed_base = Path.home().resolve()
        if not (target == allowed_base or str(target).startswith(str(allowed_base) + "/")):
            return {"error": "Zugriff nur innerhalb des Home-Verzeichnisses erlaubt", "path": str(target), "dirs": []}

        if not target.exists() or not target.is_dir():
            return {"error": "Verzeichnis existiert nicht", "path": str(target), "dirs": []}

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


@app.get("/api/runtime-status")
async def get_runtime_status():
    """Gibt den letzten bekannten Claude-/Codex-Status zurück."""
    return _get_gui_status_snapshot()


@app.post("/api/runtime-status/claude/refresh")
async def refresh_claude_runtime_status():
    """Aktualisiert die echte Claude-Limitanzeige."""
    if _active_process and _active_process.poll() is None:
        return {"error": "Während eines laufenden Tasks bitte kurz warten"}
    target_repo = _settings.get("target_repo", str(Path(".").resolve()))
    try:
        limit = await asyncio.to_thread(_probe_claude_limit_status, target_repo)
    except Exception as exc:
        return {"error": str(exc)}
    await broadcast({"type": "claude_limit_update", "limit": limit})
    return {"ok": True, "limit": limit}


@app.post("/api/runtime-status/codex/refresh")
async def refresh_codex_runtime_status():
    """Fragt Codex /status ab und liefert den letzten Text zurück."""
    if _active_process and _active_process.poll() is None:
        return {"error": "Während eines laufenden Tasks bitte kurz warten"}
    target_repo = _settings.get("target_repo", str(Path(".").resolve()))
    try:
        codex_status = await asyncio.to_thread(_probe_codex_status, target_repo)
    except Exception as exc:
        return {"error": str(exc)}
    await broadcast({"type": "codex_status_update", "codex_status": codex_status})
    return {"ok": True, "codex_status": codex_status}


# ─── Projekt-Management Endpoints ─────────────────────────────────

@app.get("/api/projects")
async def list_projects():
    """Listet alle registrierten Projekte auf."""
    return _load_projects()


@app.post("/api/projects")
async def create_project(data: dict):
    """Erstellt ein neues Projekt."""
    from datetime import datetime
    import hashlib

    name = data.get("name", "").strip()
    path = data.get("path", "").strip()
    if not name or not path:
        return {"error": "Name und Pfad sind Pflichtfelder"}

    resolved = str(Path(path).expanduser().resolve())
    if not Path(resolved).exists():
        return {"error": f"Verzeichnis existiert nicht: {resolved}"}

    # Prüfe ob Projekt mit diesem Pfad bereits existiert
    existing = _find_project_by_path(resolved)
    if existing:
        return {"error": f"Projekt '{existing['name']}' existiert bereits für diesen Pfad"}

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
    _settings["target_repo"] = resolved
    _save_config(_settings)

    return {"ok": True, "project": project}


@app.post("/api/projects/switch")
async def switch_project(data: dict):
    """Wechselt zum angegebenen Projekt."""
    from datetime import datetime

    project_id = data.get("id", "")
    projects = _load_projects()

    for p in projects:
        if p["id"] == project_id:
            _settings["target_repo"] = p["path"]
            _save_config(_settings)
            p["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_projects(projects)
            await broadcast({"type": "system", "text": p["path"]})
            return {"ok": True, "project": p}

    return {"error": "Projekt nicht gefunden"}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Entfernt ein Projekt aus der Registry (Dateien bleiben erhalten)."""
    projects = _load_projects()
    projects = [p for p in projects if p["id"] != project_id]
    _save_projects(projects)
    return {"ok": True}


@app.get("/api/projects/{project_id}/history")
async def get_project_history(project_id: str):
    """Gibt die komplette Historie eines Projekts zurück."""
    return _read_history(project_id)


@app.get("/api/projects/{project_id}/logs")
async def get_project_logs(project_id: str):
    """Gibt die Log-Einträge eines Projekts zurück."""
    return _read_log(project_id)


@app.get("/api/health")
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


@app.get("/api/status")
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
        "target_repo": _settings["target_repo"],
        "auto_review": _settings.get("auto_review", True),
    }


@app.get("/api/ledger")
async def get_ledger():
    """Gibt die letzten 20 Ledger-Einträge zurück."""
    ledger_path = get_data_dir() / "ledger" / "task_history.jsonl"
    entries = []
    if ledger_path.exists():
        for line in ledger_path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries[-20:]


# ─── WebSocket ────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global _loop, _task_cancelled, _active_process

    # Security: Origin-Check — nur lokale Verbindungen erlauben
    origin = ws.headers.get("origin", "")
    allowed_origins = [
        "http://127.0.0.1:8080", "http://localhost:8080",
        "http://127.0.0.1:8081", "http://localhost:8081",
    ]
    if origin and origin not in allowed_origins:
        await ws.close(code=1008, reason="Origin nicht erlaubt")
        return

    await ws.accept()
    _connections.append(ws)
    _loop = asyncio.get_event_loop()

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
                if _active_process and _active_process.poll() is None:
                    await ws.send_json({"type": "error", "text": "Es läuft bereits ein Task — bitte warten oder abbrechen"})
                    continue

                # Alles geht an Claude CLI — kein Chat/Task-Routing mehr
                thread = threading.Thread(
                    target=_run_claude_session_thread,
                    args=(ws, user_text),
                    daemon=True,
                )
                thread.start()

            elif msg_type == "review":
                # Review-Modus: Claude + Codex parallel
                if _active_process and _active_process.poll() is None:
                    await ws.send_json({"type": "error", "text": "Es läuft bereits ein Task — bitte warten oder abbrechen"})
                    continue

                review_scope = data.get("scope", "project")  # project | last_task | custom
                review_focus = data.get("focus", "all")       # all | security | bugs | performance
                custom_scope = data.get("custom_scope", "")

                thread = threading.Thread(
                    target=_run_review_thread,
                    args=(ws, review_scope, review_focus, custom_scope),
                    daemon=True,
                )
                thread.start()

            elif msg_type == "apply_fixes":
                # Fixes aus Review anwenden
                if _active_process and _active_process.poll() is None:
                    await ws.send_json({"type": "error", "text": "Es läuft bereits ein Task — bitte warten oder abbrechen"})
                    continue
                if not _pending_review_fixes:
                    await ws.send_json({"type": "error", "text": "Keine Review-Fixes vorhanden"})
                    continue

                fix_prompt = (
                    "Wende jetzt die folgenden Fixes aus dem Review an:\n\n"
                    + _pending_review_fixes[:8000] + "\n\n"
                    "Regeln: Setze NUR die im Fix-Plan beschriebenen Änderungen um. Kein Scope Creep."
                )
                thread = threading.Thread(
                    target=_run_claude_session_thread,
                    args=(ws, fix_prompt),
                    daemon=True,
                )
                thread.start()

            elif msg_type == "stop":
                # Session abbrechen
                _task_cancelled = True
                if _active_process and _active_process.poll() is None:
                    _active_process.kill()
                    _active_process = None
                await ws.send_json({"type": "task_stopped", "text": "Abgebrochen."})

            elif msg_type == "set_repo":
                repo_path = data.get("path", "").strip()
                if repo_path and Path(repo_path).exists():
                    _settings["target_repo"] = str(Path(repo_path).resolve())
                    await ws.send_json({
                        "type": "system",
                        "text": f"Projekt: {_settings['target_repo']}",
                    })
                else:
                    await ws.send_json({
                        "type": "error",
                        "text": f"Verzeichnis existiert nicht: {repo_path}",
                    })

    except WebSocketDisconnect:
        _connections.remove(ws)


# ─── Chat-Session-Persistenz (Claude CLI) ─────────────────────────
_CHAT_SESSIONS_PATH = Path.home() / ".config" / "omads" / "chat_sessions.json"

def _load_chat_sessions() -> dict[str, str]:
    if _CHAT_SESSIONS_PATH.exists():
        try:
            return json.loads(_CHAT_SESSIONS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def _save_chat_sessions(sessions: dict[str, str]) -> None:
    _CHAT_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CHAT_SESSIONS_PATH.write_text(json.dumps(sessions, indent=2))

_chat_sessions: dict[str, str] = _load_chat_sessions()


# ─── Projekt-Memory (persistenter Kontext über Sessions hinweg) ───
_MEMORY_DIR = Path.home() / ".config" / "omads" / "memory"


def _get_memory_path(repo_path: str) -> Path:
    """Gibt den Memory-Dateipfad für ein bestimmtes Repo zurück."""
    safe_name = Path(repo_path).resolve().name or "default"
    return _MEMORY_DIR / f"{safe_name}.md"


def _load_project_memory(repo_path: str) -> str:
    """Lädt den persistenten Projektkontext (Memory + CLAUDE.md)."""
    parts: list[str] = []

    # 1. CLAUDE.md des Zielprojekts lesen (wie Claude Code es tut)
    claude_md = Path(repo_path) / "CLAUDE.md"
    if claude_md.exists():
        try:
            content = claude_md.read_text(encoding="utf-8")[:8000]
            parts.append(f"=== CLAUDE.md (Projektinstruktionen) ===\n{content}")
        except OSError:
            pass

    # 2. Gespeicherte Projekt-Zusammenfassung laden
    mem_path = _get_memory_path(repo_path)
    if mem_path.exists():
        try:
            content = mem_path.read_text(encoding="utf-8")[:4000]
            parts.append(f"=== Letzte Session-Zusammenfassung ===\n{content}")
        except OSError:
            pass

    return "\n\n".join(parts)


def _save_project_memory(repo_path: str, summary: str) -> None:
    """Speichert eine Projekt-Zusammenfassung für die nächste Session."""
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    mem_path = _get_memory_path(repo_path)
    # Zusammenfassung mit Zeitstempel
    from datetime import datetime, timezone
    header = f"Letzte Aktualisierung: {datetime.now(timezone.utc).isoformat()}\n\n"
    mem_path.write_text(header + summary[:6000], encoding="utf-8")


def _run_claude_session_thread(ws: WebSocket, user_text: str) -> None:
    """Einziger Einstiegspunkt: Claude CLI bekommt alles — Fragen, Chat, Code-Aufträge.

    Claude CLI entscheidet selbst was zu tun ist. Nach Code-Änderungen läuft
    automatisch ein Codex-Review im Hintergrund.
    """
    import time as _time
    from omads.cli.main import _format_tool_use

    global _active_process, _task_cancelled
    _task_cancelled = False

    target_repo = _settings.get("target_repo", str(Path(".").resolve()))
    repo_key = str(Path(target_repo).resolve())
    model = _settings.get("claude_model", "sonnet")
    max_turns = str(max(1, min(int(_settings.get("claude_max_turns", 25)), 100)))
    perm_mode = _settings.get("claude_permission_mode", "default")
    effort = _settings.get("claude_effort", "high")
    agent_label = "Claude Code"

    def send(msg: dict):
        broadcast_sync(msg)

    send({"type": "agent_status", "agent": agent_label, "status": "Arbeitet..."})

    # Historie: User-Eingabe loggen
    proj_id = _get_active_project_id()
    if proj_id:
        _append_history(proj_id, {"type": "user_input", "text": user_text})

    try:
        env = _build_cli_env()

        # Projekt-Memory laden (CLAUDE.md + letzte Zusammenfassung)
        project_memory = _load_project_memory(target_repo)

        # OMADS-Kontext für Claude CLI
        omads_context = (
            "Du arbeitest innerhalb von OMADS (Orchestrated Multi-Agent Development System). "
            "Der User kommuniziert mit dir über eine Web-GUI. "
            "Nach jeder Code-Änderung prüft Codex CLI automatisch deinen Code im Hintergrund. "
            "Wenn Codex Probleme findet, bekommst du die Findings als nächste Nachricht und sollst sie fixen. "
            "Antworte auf Deutsch.\n\n"
        )
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
        session_id = _chat_sessions.get(repo_key)
        if session_id:
            cmd.extend(["--resume", session_id])

        # --permission-mode deaktiviert: triggert afk-mode Beta-Header Bug in CLI v2.1.74
        # Permission wird stattdessen über ~/.claude/settings.json gesteuert

        start_time = _time.time()
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=target_repo, env=env,
        )
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

        _active_process = None
        process.wait(timeout=300)
        elapsed = round(_time.time() - start_time)
        success = process.returncode == 0 and not _task_cancelled

        # Session-ID für Folge-Nachrichten speichern (Gesprächsgedächtnis)
        if captured_session_id and success:
            _chat_sessions[repo_key] = captured_session_id
            _save_chat_sessions(_chat_sessions)

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
        if files_changed and _settings.get("auto_review", True) and success:
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
                fix_session = _chat_sessions.get(repo_key)
                if fix_session:
                    fix_cmd.extend(["--resume", fix_session])

                fix_process = subprocess.Popen(
                    fix_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=target_repo, env=env,
                )
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

                _active_process = None
                fix_process.wait(timeout=300)

                if fix_session_id and fix_process.returncode == 0:
                    _chat_sessions[repo_key] = fix_session_id
                    _save_chat_sessions(_chat_sessions)

                send({"type": "agent_status", "agent": "Claude Code", "status": "Fixes angewendet"})

    except FileNotFoundError:
        send({"type": "chat_response", "agent": "System",
              "text": "Claude CLI nicht gefunden. Installiere mit: npm install -g @anthropic-ai/claude-code"})
    except subprocess.TimeoutExpired:
        if _active_process:
            _active_process.kill()
        send({"type": "chat_response", "agent": "System", "text": "Timeout — Claude CLI hat zu lange gebraucht."})
    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Claude-Session-Fehler: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": "Ein interner Fehler ist aufgetreten. Details im Server-Log."})
    finally:
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
    _task_cancelled = False

    target_repo = _settings.get("target_repo", str(Path(".").resolve()))
    model = _settings.get("claude_model", "sonnet")
    max_turns = str(max(1, min(int(_settings.get("claude_max_turns", 25)), 100)))

    def send(msg: dict):
        broadcast_sync(msg)

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
        session_id = _chat_sessions.get(repo_key)
        if session_id:
            cmd.extend(["--resume", session_id])

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=target_repo, env=env,
        )
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

        process.wait(timeout=300)
        _active_process = None
        claude_review = "\n".join(claude_output)

        if captured_session_id and process.returncode == 0:
            _chat_sessions[repo_key] = captured_session_id
            _save_chat_sessions(_chat_sessions)

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

        codex_model = _settings.get("codex_model", "")
        codex_timeout = int(_settings.get("breaker_timeout", 120))
        codex_reasoning = _settings.get("codex_reasoning", "high")
        codex_fast = _settings.get("codex_fast", False)
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
                codex_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=str(target_repo),
            )
            codex_process.stdin.write(codex_prompt)
            codex_process.stdin.close()

            import select
            codex_lines = []
            deadline = time.time() + codex_timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    codex_process.kill()
                    codex_process.wait()
                    send({"type": "agent_status", "agent": "Codex Review", "status": f"Timeout ({codex_timeout}s) — abgebrochen"})
                    codex_review = "\n".join(codex_lines) if codex_lines else "(Codex Timeout)"
                    break
                if _task_cancelled:
                    codex_process.kill()
                    codex_process.wait()
                    return
                ready, _, _ = select.select([codex_process.stdout], [], [], 5.0)
                if ready:
                    line = codex_process.stdout.readline()
                    if not line:  # EOF — Codex ist fertig
                        codex_process.wait(timeout=10)
                        codex_review = "\n".join(codex_lines)
                        send({"type": "agent_status", "agent": "Codex Review", "status": "Schritt 2/3 fertig"})
                        break
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
            synth_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=target_repo, env=env,
        )
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

        synth_process.wait(timeout=300)
        _active_process = None

        if synth_session_id and synth_process.returncode == 0:
            _chat_sessions[repo_key] = synth_session_id
            _save_chat_sessions(_chat_sessions)

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
            # Fix-Vorschläge als Kontext speichern für den Apply-Schritt
            global _pending_review_fixes
            _pending_review_fixes = synthesis_text
            send({"type": "review_fixes_available",
                  "text": "Fixes gefunden. Sollen die vorgeschlagenen Fixes angewendet werden?"})

    except Exception as e:
        import logging
        logging.getLogger("omads.gui").error("Review-Fehler: %s", e, exc_info=True)
        send({"type": "chat_response", "agent": "System", "text": f"Review-Fehler: {str(e)[:200]}"})
    finally:
        _active_process = None
        send({"type": "unlock"})


def _run_codex_auto_review(ws: WebSocket, target_repo: str, files_changed: list[str], send: callable) -> str | None:
    """Codex CLI Auto-Review: prüft geänderte Dateien automatisch nach Claude-Änderungen.

    Gibt die Findings als String zurück (oder None wenn alles OK).
    """
    breaker_label = "Codex Review"
    codex_model = _settings.get("codex_model", "")
    codex_reasoning = _settings.get("codex_reasoning", "high")
    codex_fast = _settings.get("codex_fast", False)
    timeout = int(_settings.get("breaker_timeout", 120))

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
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(target_repo),
        )

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

        # Stdout lesen mit echtem Timeout (select-basiert) — JSONL-Format
        import select
        output_lines = []
        deadline = _time.time() + timeout
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                process.kill()
                process.wait()
                heartbeat_stop.set()
                send({"type": "agent_status", "agent": breaker_label, "status": f"Timeout ({timeout}s) — Review abgebrochen"})
                return "\n".join(output_lines) if output_lines else None
            ready, _, _ = select.select([process.stdout], [], [], 5.0)
            if ready:
                line = process.stdout.readline()
                if not line:  # EOF — Codex fertig
                    break
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
        elapsed = round(_time.time() - start_time)
        output = "\n".join(output_lines).strip()

        if not output and process.returncode != 0:
            stderr = process.stderr.read().strip()[:200] if process.stderr else ""
            output = f"Review-Fehler: {stderr}" if stderr else ""
            if stderr:
                send({"type": "stream_result", "agent": breaker_label, "text": stderr, "is_error": True})

        send({"type": "stream_text", "agent": breaker_label,
              "text": f"Review abgeschlossen ({elapsed}s)"})

        # Ergebnis auswerten
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
    except subprocess.TimeoutExpired:
        if process:
            process.kill()
        send({"type": "agent_status", "agent": breaker_label, "status": f"Timeout ({timeout}s) — Review abgebrochen"})
        return None
    except Exception as e:
        send({"type": "agent_status", "agent": breaker_label, "status": f"Review-Fehler: {str(e)[:100]}"})
        return None


def start_gui(host: str = "127.0.0.1", port: int = 8080):
    """Startet den GUI-Server."""
    import webbrowser
    import threading
    import time
    import urllib.request
    import uvicorn

    url = f"http://{host}:{port}"
    print(f"\n  OMADS GUI startet auf {url} ...")

    def open_browser_when_ready():
        """Wartet bis der Server antwortet, dann öffnet den Browser."""
        for _ in range(30):  # max 15 Sekunden warten
            try:
                urllib.request.urlopen(url, timeout=1)
                print(f"  OMADS GUI: {url}\n")
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(0.5)
        # Fallback: trotzdem öffnen
        webbrowser.open(url)

    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
