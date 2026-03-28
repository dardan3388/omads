"""GitHub integration for the OMADS GUI.

Simple Personal Access Token (PAT) authentication, GitHub API access,
and Git credential injection for clone/push/pull operations.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .state import (
    _build_cli_env,
    _read_json_text,
    _write_text_file,
)

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────

_TOKEN_PATH = Path.home() / ".config" / "omads" / "github_token.json"

# ─── Validation ───────────────────────────────────────────────────

_SAFE_REPO_NAME = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")


def _validate_full_name(full_name: str) -> str:
    """Validate a GitHub repo full_name against injection attacks."""
    if not full_name or not _SAFE_REPO_NAME.match(full_name):
        raise ValueError(f"Invalid repository name: {full_name!r}")
    return full_name


# ─── Token persistence ────────────────────────────────────────────

_token_lock = threading.Lock()


def _load_token() -> dict[str, Any] | None:
    """Load the stored GitHub token, or None if not authenticated."""
    if not _TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(_read_json_text(_TOKEN_PATH))
        if data.get("access_token"):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_token(data: dict[str, Any]) -> None:
    """Persist a GitHub token to disk."""
    with _token_lock:
        _write_text_file(_TOKEN_PATH, json.dumps(data, indent=2))


def _delete_token() -> None:
    """Remove the stored GitHub token."""
    with _token_lock:
        if _TOKEN_PATH.exists():
            _TOKEN_PATH.unlink(missing_ok=True)


def _scrub_token(text: str) -> str:
    """Remove any GitHub token from log/error output."""
    token_data = _load_token()
    if token_data and token_data.get("access_token"):
        text = text.replace(token_data["access_token"], "***")
    return text


# ─── Auth: simple PAT login ──────────────────────────────────────

def connect_with_token(token: str) -> dict[str, Any]:
    """Validate a GitHub PAT and store it if valid.

    Returns {"ok": True, "username": "..."} on success.
    """
    token = token.strip()
    if not token:
        raise ValueError("Token is empty")

    # Verify the token by calling /user
    resp = httpx.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    if resp.status_code == 401:
        raise ValueError("Invalid token — GitHub rejected it")
    resp.raise_for_status()

    user = resp.json()
    username = user.get("login", "")

    token_data = {
        "access_token": token,
        "token_type": "bearer",
        "username": username,
        "authenticated_at": int(time.time()),
    }
    _save_token(token_data)

    return {"ok": True, "username": username}


def get_auth_status() -> dict[str, Any]:
    """Return auth status without exposing the token."""
    token_data = _load_token()
    if not token_data:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": token_data.get("username", ""),
    }


def disconnect() -> None:
    """Remove the stored GitHub token (log out)."""
    _delete_token()


# ─── GitHub API ───────────────────────────────────────────────────

def _get_token() -> str:
    """Return the stored access token, raising if not authenticated."""
    token_data = _load_token()
    if not token_data or not token_data.get("access_token"):
        raise RuntimeError("Not authenticated with GitHub")
    return token_data["access_token"]


def _github_headers() -> dict[str, str]:
    """Return authenticated GitHub API headers."""
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def list_repos(page: int = 1, per_page: int = 30, sort: str = "updated") -> list[dict[str, Any]]:
    """List repositories for the authenticated user."""
    resp = httpx.get(
        "https://api.github.com/user/repos",
        params={
            "sort": sort,
            "direction": "desc",
            "per_page": per_page,
            "page": page,
            "type": "all",
        },
        headers=_github_headers(),
        timeout=15,
    )
    resp.raise_for_status()

    repos = []
    for r in resp.json():
        repos.append({
            "full_name": r["full_name"],
            "description": r.get("description") or "",
            "private": r.get("private", False),
            "language": r.get("language") or "",
            "updated_at": r.get("updated_at", ""),
            "default_branch": r.get("default_branch", "main"),
            "html_url": r.get("html_url", ""),
        })
    return repos


# ─── Git operations ───────────────────────────────────────────────

def _auth_remote_url(full_name: str) -> str:
    """Build an authenticated HTTPS remote URL (never written to .git/config)."""
    _validate_full_name(full_name)
    token = _get_token()
    return f"https://x-access-token:{token}@github.com/{full_name}.git"


def clone_repo(full_name: str, target_dir: str) -> dict[str, Any]:
    """Clone a GitHub repo into target_dir using token-based auth.

    The token is passed via the clone URL and is NOT persisted in .git/config.
    After cloning, the remote URL is reset to the plain HTTPS URL.
    """
    _validate_full_name(full_name)
    target = Path(target_dir).expanduser().resolve()

    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Target directory is not empty: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)

    auth_url = _auth_remote_url(full_name)
    plain_url = f"https://github.com/{full_name}.git"

    result = subprocess.run(
        ["git", "clone", auth_url, str(target)],
        capture_output=True,
        text=True,
        timeout=120,
        env=_build_cli_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(_scrub_token(result.stderr.strip() or "git clone failed"))

    # Immediately reset the remote to the plain URL (remove token from config)
    subprocess.run(
        ["git", "remote", "set-url", "origin", plain_url],
        capture_output=True,
        text=True,
        cwd=str(target),
        timeout=10,
    )

    return {"path": str(target), "full_name": full_name}


def git_operation(repo_path: str, operation: str, **kwargs: Any) -> dict[str, Any]:
    """Run a Git operation (commit, push, pull, status) with token-based auth.

    For push/pull, the token is injected via -c remote.origin.url=...
    so it never touches .git/config.
    """
    repo = Path(repo_path).expanduser().resolve()
    if not (repo / ".git").is_dir():
        raise ValueError(f"Not a Git repository: {repo}")

    env = {**_build_cli_env(), "LC_ALL": "C"}

    def _has_commits() -> bool:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(repo),
            timeout=5, env=env,
        )
        return r.returncode == 0

    if operation == "status":
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=str(repo),
            timeout=15, env=env,
        )
        lines = [l.rstrip() for l in result.stdout.splitlines() if l.strip()]
        return {"status_lines": lines, "clean": len(lines) == 0}

    if operation == "commit":
        message = kwargs.get("message", "").strip()
        if not message:
            raise ValueError("Commit message is required")
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, text=True, cwd=str(repo),
            timeout=15, env=env,
        )
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=str(repo),
            timeout=30, env=env,
        )
        if result.returncode != 0:
            stderr = _scrub_token(result.stderr.strip())
            if "nothing to commit" in (result.stdout + result.stderr).lower():
                return {"committed": False, "message": "Nothing to commit"}
            raise RuntimeError(stderr or "git commit failed")
        return {"committed": True, "output": result.stdout.strip()}

    def _resolve_remote() -> tuple[str, str]:
        """Return (auth_url, branch) for the current repo."""
        origin_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=str(repo),
            timeout=10, env=env,
        )
        origin_url = origin_result.stdout.strip()
        full_name = _extract_full_name(origin_url)
        auth_url = _auth_remote_url(full_name)
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=str(repo),
            timeout=5, env=env,
        )
        branch = branch_result.stdout.strip() or "main"
        return auth_url, branch

    def _friendly_git_error(stderr: str, op: str) -> str:
        """Turn common git errors into actionable user messages."""
        cleaned = _scrub_token(stderr.strip())
        if "403" in cleaned or "Permission" in cleaned.lower() or "denied" in cleaned.lower():
            return (
                f"Permission denied — your GitHub token doesn't have access to this repo. "
                f"Go to github.com/settings/tokens, edit your token, "
                f"and add this repository to the allowed list."
            )
        if "could not read Username" in cleaned:
            return "GitHub authentication failed — reconnect your account in the GitHub menu."
        return cleaned or f"git {op} failed"

    if operation == "push":
        if not _has_commits():
            raise RuntimeError("Nothing to push — create a commit first.")
        auth_url, branch = _resolve_remote()

        result = subprocess.run(
            ["git", "push", auth_url, branch],
            capture_output=True, text=True, cwd=str(repo),
            timeout=60, env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(_friendly_git_error(result.stderr, "push"))
        return {"pushed": True, "output": _scrub_token(result.stdout.strip() + "\n" + result.stderr.strip()).strip()}

    if operation == "pull":
        if not _has_commits():
            raise RuntimeError("Nothing to pull — the repository has no commits yet.")
        auth_url, branch = _resolve_remote()

        result = subprocess.run(
            ["git", "pull", auth_url, branch],
            capture_output=True, text=True, cwd=str(repo),
            timeout=60, env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(_friendly_git_error(result.stderr, "pull"))
        return {"pulled": True, "output": _scrub_token(result.stdout.strip())}

    raise ValueError(f"Unknown Git operation: {operation}")


def _extract_full_name(remote_url: str) -> str:
    """Extract owner/repo from a GitHub remote URL."""
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", remote_url)
    if not m:
        raise ValueError(f"Cannot extract repo name from remote URL: {remote_url}")
    full_name = m.group(1)
    _validate_full_name(full_name)
    return full_name


# ─── Quick Edit (GitHub Contents API) ─────────────────────────────

def get_file_content(full_name: str, file_path: str, ref: str = "") -> dict[str, Any]:
    """Read a single file from a GitHub repo via the Contents API."""
    import base64

    _validate_full_name(full_name)
    params: dict[str, str] = {}
    if ref:
        params["ref"] = ref

    resp = httpx.get(
        f"https://api.github.com/repos/{full_name}/contents/{file_path}",
        params=params,
        headers=_github_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("type") != "file":
        raise ValueError(f"Path is not a file: {file_path}")

    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    return {
        "path": data["path"],
        "sha": data["sha"],
        "size": data.get("size", 0),
        "content": content,
    }


def update_file_content(
    full_name: str,
    file_path: str,
    content: str,
    message: str,
    sha: str,
    branch: str = "",
) -> dict[str, Any]:
    """Update a single file on GitHub via the Contents API (Quick Edit)."""
    import base64

    _validate_full_name(full_name)
    if not message.strip():
        raise ValueError("Commit message is required")

    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": sha,
    }
    if branch:
        body["branch"] = branch

    resp = httpx.put(
        f"https://api.github.com/repos/{full_name}/contents/{file_path}",
        json=body,
        headers=_github_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "path": file_path,
        "sha": data.get("content", {}).get("sha", ""),
        "commit_sha": data.get("commit", {}).get("sha", ""),
        "commit_url": data.get("commit", {}).get("html_url", ""),
    }


def list_repo_contents(full_name: str, path: str = "", ref: str = "") -> list[dict[str, Any]]:
    """List files/dirs in a GitHub repo path via the Contents API."""
    _validate_full_name(full_name)
    params: dict[str, str] = {}
    if ref:
        params["ref"] = ref

    resp = httpx.get(
        f"https://api.github.com/repos/{full_name}/contents/{path}",
        params=params,
        headers=_github_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict):
        return [{"name": data["name"], "path": data["path"], "type": data["type"], "size": data.get("size", 0)}]

    return [
        {
            "name": item["name"],
            "path": item["path"],
            "type": item["type"],
            "size": item.get("size", 0),
        }
        for item in data
    ]
