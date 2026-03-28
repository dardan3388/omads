"""REST routes for the OMADS GUI."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from omads.utils.paths import get_data_dir, get_dna_dir

from . import runtime
from .state import (
    CreateProjectRequest,
    GitHubCloneRequest,
    GitHubGitRequest,
    SwitchProjectRequest,
    UpdateSettingsRequest,
    _detect_lan_ip,
    _find_project_by_path,
    _get_setting,
    _get_settings_snapshot,
    _load_projects,
    _read_history,
    _read_log,
    _read_timeline_page,
    _save_projects,
    _update_settings,
    _validate_project_id,
    is_path_inside_home,
)

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def index():
    """Serve the frontend."""
    gui_dir = Path(__file__).parent
    html_path = gui_dir / "frontend.html"
    return HTMLResponse(html_path.read_text())


# ─── REST endpoints ──────────────────────────────────────────────

@router.get("/api/settings")
async def get_settings():
    return _get_settings_snapshot()


_ALLOWED_SETTINGS = {
    "target_repo": str,
    "builder_agent": str,
    "review_first_reviewer": str,
    "review_second_reviewer": str,
    "claude_model": str,
    "claude_permission_mode": str,
    "claude_effort": str,
    "codex_model": str,
    "codex_reasoning": str,
    "codex_fast": bool,
    "auto_review": bool,
    "ui_theme": str,
    "lan_access": bool,
}

def _schedule_server_restart() -> None:
    """Restart the server process after a brief delay so the HTTP response can flush."""

    def _do_restart() -> None:
        time.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_do_restart, daemon=True).start()


@router.post("/api/settings")
async def update_settings(data: UpdateSettingsRequest):
    lan_before = _get_setting("lan_access", False)
    payload = data.model_dump(exclude_none=True)

    def apply_updates(settings: dict[str, Any]) -> None:
        # Security: accept only known keys with the correct types
        for key, value in payload.items():
            if key not in _ALLOWED_SETTINGS:
                continue
            expected_type = _ALLOWED_SETTINGS[key]
            if not isinstance(value, expected_type):
                continue
            # target_repo needs extra validation (is_dir + home boundary check)
            if key == "target_repo":
                resolved = Path(value).expanduser().resolve()
                if not resolved.is_dir() or not is_path_inside_home(resolved):
                    continue  # Silently ignore invalid paths
                settings[key] = str(resolved)
            else:
                settings[key] = value

        if settings.get("builder_agent") not in ("claude", "codex"):
            settings["builder_agent"] = "claude"
        if settings.get("review_first_reviewer") not in ("claude", "codex"):
            settings["review_first_reviewer"] = "claude"
        if settings.get("review_second_reviewer") not in ("claude", "codex"):
            settings["review_second_reviewer"] = "codex"
        if settings.get("review_second_reviewer") == settings.get("review_first_reviewer"):
            settings["review_second_reviewer"] = (
                "codex" if settings.get("review_first_reviewer") == "claude" else "claude"
            )
        if settings.get("claude_effort") not in ("low", "medium", "high", "max"):
            settings["claude_effort"] = "high"
        if settings.get("codex_reasoning") not in ("low", "medium", "high", "xhigh"):
            settings["codex_reasoning"] = "high"
        if settings.get("ui_theme") not in ("dark", "light"):
            settings["ui_theme"] = "dark"

    snapshot = _update_settings(apply_updates)
    await runtime.broadcast({"type": "settings_updated", "settings": snapshot})

    # Auto-restart when LAN access changes (CORS middleware is bound at startup)
    lan_after = snapshot.get("lan_access", False)
    if lan_before != lan_after:
        await runtime.broadcast({
            "type": "server_restart",
            "text": "Server restarts to apply LAN access change…",
        })
        _schedule_server_restart()

    return {"ok": True}


@router.get("/api/network-info")
async def get_network_info():
    """Return the detected LAN IP, port, QR code image, and current LAN access state."""
    import base64
    import io

    import segno

    lan_ip = _detect_lan_ip()
    port = int(os.environ.get("OMADS_PORT", "8080"))
    enabled = _get_setting("lan_access", False)
    lan_url = f"http://{lan_ip}:{port}"

    buf = io.BytesIO()
    segno.make(lan_url).save(buf, kind="png", scale=10, border=4)
    qr_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    return {
        "lan_ip": lan_ip,
        "port": port,
        "lan_url": lan_url,
        "lan_access_enabled": enabled,
        "qr_data_url": qr_data_url,
    }


def _run_git_command(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one git command inside the current target repository."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        timeout=20,
    )


@router.get("/api/diff")
async def get_repo_diff():
    """Return a unified Git diff for the active project."""
    repo_path = Path(_get_setting("target_repo", str(Path(".").resolve()))).expanduser().resolve()
    if not repo_path.is_dir():
        return {
            "error": f"Project directory does not exist: {repo_path}",
            "repo": str(repo_path),
            "status_lines": [],
            "changed_files": [],
            "diff": "",
            "has_changes": False,
        }

    try:
        inside = _run_git_command(repo_path, ["rev-parse", "--is-inside-work-tree"])
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {
                "error": "Current project is not a Git repository",
                "repo": str(repo_path),
                "status_lines": [],
                "changed_files": [],
                "diff": "",
                "has_changes": False,
            }

        status_result = _run_git_command(repo_path, ["status", "--short"])
        status_lines = [line.rstrip() for line in status_result.stdout.splitlines() if line.strip()]
        changed_files = [line[3:].strip() if len(line) > 3 else line.strip() for line in status_lines]

        has_head = _run_git_command(repo_path, ["rev-parse", "--verify", "HEAD"]).returncode == 0
        if has_head:
            diff_result = _run_git_command(repo_path, ["diff", "--no-ext-diff", "--submodule=diff", "HEAD", "--"])
            diff_text = diff_result.stdout.strip()
        else:
            diff_parts: list[str] = []
            staged = _run_git_command(repo_path, ["diff", "--cached", "--no-ext-diff", "--"]).stdout.strip()
            unstaged = _run_git_command(repo_path, ["diff", "--no-ext-diff", "--"]).stdout.strip()
            untracked = _run_git_command(repo_path, ["ls-files", "--others", "--exclude-standard"]).stdout.strip()
            if staged:
                diff_parts.append("### Staged changes\n\n" + staged)
            if unstaged:
                diff_parts.append("### Unstaged changes\n\n" + unstaged)
            if untracked:
                diff_parts.append("### Untracked files\n\n" + untracked)
            diff_text = "\n\n".join(diff_parts).strip()

        return {
            "repo": str(repo_path),
            "status_lines": status_lines,
            "changed_files": changed_files,
            "diff": diff_text,
            "has_changes": bool(status_lines or diff_text),
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "repo": str(repo_path),
            "status_lines": [],
            "changed_files": [],
            "diff": "",
            "has_changes": False,
        }


@router.get("/api/browse")
async def browse_directory(path: str = "~"):
    """List subdirectories for the folder picker."""
    try:
        target = Path(path).expanduser().resolve()

        # Security: allow only the home directory and its descendants
        if not is_path_inside_home(target):
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


# ─── Project management endpoints ─────────────────────────────────

@router.get("/api/projects")
async def list_projects():
    """List all registered projects."""
    return _load_projects()


@router.post("/api/projects")
async def create_project(data: CreateProjectRequest):
    """Create a new project."""
    from datetime import datetime
    import hashlib

    name = data.name.strip()
    path = data.path.strip()
    if not name or not path:
        return {"error": "Name and path are required"}

    resolved = str(Path(path).expanduser().resolve())
    if not Path(resolved).is_dir():
        return {"error": f"Not a directory: {resolved}"}
    if not is_path_inside_home(resolved):
        return {"error": "Only directories inside $HOME are allowed"}

    # Check whether a project with this path already exists
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

    # Switch to this project immediately
    _update_settings(lambda settings: settings.__setitem__("target_repo", resolved))

    return {"ok": True, "project": project}


@router.post("/api/projects/switch")
async def switch_project(data: SwitchProjectRequest):
    """Switch to the requested project."""
    from datetime import datetime

    project_id = data.id
    projects = _load_projects()

    for p in projects:
        if p["id"] == project_id:
            # Validate the path because the directory may have been moved or deleted
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
    """Remove a project from the registry while keeping its files."""
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
    """Return the full history for one project."""
    try:
        return _read_history(project_id)
    except ValueError:
        return {"error": "Invalid project ID"}


@router.get("/api/projects/{project_id}/logs")
async def get_project_logs(project_id: str):
    """Return the log entries for one project."""
    try:
        return _read_log(project_id)
    except ValueError:
        return {"error": "Invalid project ID"}


@router.get("/api/projects/{project_id}/timeline")
async def get_project_timeline(project_id: str, limit: int = 200, before: int | None = None):
    """Return one bounded page from the unified event timeline for one project."""
    try:
        return _read_timeline_page(project_id, limit=limit, before=before)
    except ValueError:
        return {"error": "Invalid project ID"}


@router.get("/api/health")
async def get_health():
    """Check whether Claude Code CLI and Codex CLI are available."""
    import shutil

    result: dict[str, Any] = {"claude": {"installed": False}, "codex": {"installed": False}}

    # Check Claude Code CLI
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
            result["claude"]["version"] = "unknown"
        # Check authentication: ~/.claude/.credentials.json must exist
        creds = Path.home() / ".claude" / ".credentials.json"
        result["claude"]["authenticated"] = creds.exists()
    else:
        result["claude"]["hint"] = "npm install -g @anthropic-ai/claude-code"

    # Check Codex CLI
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
            result["codex"]["version"] = "unknown"
    else:
        result["codex"]["hint"] = "npm install -g @openai/codex"

    # Python version
    import sys
    result["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    return result


@router.get("/api/status")
async def get_status():
    """Return OMADS system status."""
    from omads.dna.cold_start import get_current_phase
    phase = "unknown"
    try:
        phase = get_current_phase(get_dna_dir()).value
    except Exception:
        pass

    # Count ledger entries
    ledger_count = 0
    ledger_path = get_data_dir() / "ledger" / "task_history.jsonl"
    if ledger_path.exists():
        ledger_count = sum(1 for _ in ledger_path.open())

    return {
        "phase": phase,
        "total_tasks": ledger_count,
        "target_repo": _get_setting("target_repo", str(Path(".").resolve())),
        "builder_agent": _get_setting("builder_agent", "claude"),
        "auto_review": _get_setting("auto_review", True),
    }


@router.get("/api/ledger")
async def get_ledger():
    """Return the latest 20 ledger entries."""
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


# ─── GitHub integration endpoints ─────────────────────────────────

@router.post("/api/github/auth/connect")
async def github_auth_connect(data: dict[str, Any]):
    """Connect to GitHub with a Personal Access Token (fallback)."""
    from . import github

    token = (data.get("token") or "").strip()
    if not token:
        return {"error": "Token is required"}
    try:
        result = github.connect_with_token(token)
        await runtime.broadcast({"type": "github_connected", "username": result.get("username", "")})
        return result
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Connection failed: {exc}"}


@router.post("/api/github/auth/device/start")
async def github_auth_device_start():
    """Start the GitHub OAuth Device Flow."""
    from . import github

    try:
        result = github.start_device_flow()
        return {"ok": True, **result}
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Device flow failed: {exc}"}


@router.post("/api/github/auth/device/poll")
async def github_auth_device_poll(data: dict[str, Any]):
    """Poll for GitHub OAuth Device Flow completion."""
    from . import github

    device_code = (data.get("device_code") or "").strip()
    if not device_code:
        return {"error": "device_code is required"}

    try:
        result = github.poll_device_flow(device_code)
        if result.get("status") == "complete":
            await runtime.broadcast({"type": "github_connected", "username": result.get("username", "")})
        return result
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.delete("/api/github/auth")
async def github_auth_disconnect():
    """Disconnect from GitHub (delete stored token)."""
    from . import github

    github.disconnect()
    await runtime.broadcast({"type": "github_disconnected"})
    return {"ok": True}


@router.get("/api/github/auth/status")
async def github_auth_status():
    """Return current GitHub auth status (no token exposed)."""
    from . import github

    return {**github.get_auth_status(), "has_client_id": github.has_client_id()}


@router.get("/api/github/repos")
async def github_list_repos(page: int = 1, per_page: int = 30, search: str = ""):
    """List the authenticated user's GitHub repos with optional search."""
    from . import github

    try:
        repos = github.list_repos(page=page, per_page=per_page, search=search)
        return {"repos": repos}
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Failed to list repos: {exc}"}


@router.post("/api/github/repos/create")
async def github_create_repo(data: dict[str, Any]):
    """Create a new GitHub repository."""
    from . import github

    name = (data.get("name") or "").strip()
    if not name:
        return {"error": "Repository name is required"}

    try:
        result = github.create_repo(
            name,
            private=data.get("private", False),
            description=data.get("description", ""),
            auto_init=data.get("auto_init", True),
            gitignore_template=data.get("gitignore_template", ""),
            license_template=data.get("license_template", ""),
        )
        return {"ok": True, **result}
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Failed to create repo: {exc}"}


@router.get("/api/github/repos/info")
async def github_repo_info(full_name: str):
    """Get info about any GitHub repo (for review of foreign repos)."""
    from . import github

    try:
        info = github.get_repo_info(full_name)
        return {"ok": True, **info}
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Failed to get repo info: {exc}"}


@router.post("/api/github/clone")
async def github_clone_repo(data: GitHubCloneRequest):
    """Clone a GitHub repo and register it as an OMADS project."""
    from datetime import datetime
    import hashlib
    from . import github

    full_name = data.full_name.strip()
    target_dir = data.target_dir.strip()
    if not full_name or not target_dir:
        return {"error": "full_name and target_dir are required"}
    if not is_path_inside_home(target_dir):
        return {"error": "Only clone targets inside the home directory are allowed"}

    try:
        clone_result = github.clone_repo(full_name, target_dir)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}

    cloned_path = clone_result["path"]

    # Auto-register as OMADS project
    project_name = full_name.split("/")[-1]
    existing = _find_project_by_path(cloned_path)
    if existing:
        return {"ok": True, "project": existing, "cloned": True}

    project_id = hashlib.sha256(cloned_path.encode()).hexdigest()[:12]
    project = {
        "id": project_id,
        "name": project_name,
        "path": cloned_path,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_used": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "github_repo": full_name,
    }
    projects = _load_projects()
    projects.append(project)
    _save_projects(projects)

    # Switch to the newly cloned project
    _update_settings(lambda settings: settings.__setitem__("target_repo", cloned_path))
    await runtime.broadcast({"type": "github_connected", "username": github.get_auth_status().get("username", "")})

    return {"ok": True, "project": project, "cloned": True}


@router.post("/api/github/git")
async def github_git_operation(data: GitHubGitRequest):
    """Run a Git operation (status, commit, push, pull) on a repo."""
    from . import github

    repo_path = data.repo_path.strip()
    operation = data.operation.strip()
    if not repo_path or not operation:
        return {"error": "repo_path and operation are required"}

    if operation not in ("status", "commit", "push", "pull"):
        return {"error": f"Unknown operation: {operation}"}

    # Security: only allow repos inside $HOME
    resolved = str(Path(repo_path).expanduser().resolve())
    if not is_path_inside_home(resolved):
        return {"error": "Only repositories inside $HOME are allowed"}

    try:
        kwargs: dict[str, Any] = {}
        if operation == "commit":
            kwargs["message"] = data.message
        result = github.git_operation(resolved, operation, **kwargs)
        return {"ok": True, **result}
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
