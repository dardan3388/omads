from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omads.gui import runtime, server, state


class DummyProcess:
    def __init__(self, lines: list[str] | None = None, returncode: int = 0):
        self.stdout = iter(lines or [])
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True

    def poll(self):
        return None if not self.killed else self.returncode


@pytest.fixture()
def isolated_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    repo_dir = home_dir / "repo"
    repo_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    monkeypatch.setattr(state.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setattr(state, "_CONFIG_PATH", home_dir / ".config" / "omads" / "gui_settings.json")
    monkeypatch.setattr(state, "_GUI_STATUS_PATH", home_dir / ".config" / "omads" / "gui_status.json")
    monkeypatch.setattr(state, "_PROJECTS_PATH", home_dir / ".config" / "omads" / "projects.json")
    monkeypatch.setattr(state, "_HISTORY_DIR", home_dir / ".config" / "omads" / "history")
    monkeypatch.setattr(state, "_CHAT_SESSIONS_PATH", home_dir / ".config" / "omads" / "chat_sessions.json")
    monkeypatch.setattr(state, "_MEMORY_DIR", home_dir / ".config" / "omads" / "memory")
    monkeypatch.setattr(state, "_settings", copy.deepcopy(state._DEFAULT_SETTINGS))
    monkeypatch.setattr(state, "_gui_status", copy.deepcopy(state._GUI_STATUS_DEFAULTS))
    monkeypatch.setattr(state, "_chat_sessions", {})
    monkeypatch.setattr(runtime, "_connections", [])
    monkeypatch.setattr(runtime, "_active_process", None)
    monkeypatch.setattr(runtime, "_task_cancelled", False)
    monkeypatch.setattr(runtime, "_last_files_changed", [])
    monkeypatch.setattr(runtime, "_pending_review_fixes", {})

    state._settings["target_repo"] = str(repo_dir.resolve())

    return {
        "home_dir": home_dir,
        "repo_dir": repo_dir,
        "outside_dir": outside_dir,
    }


@pytest.fixture()
def client(isolated_server):
    with TestClient(server.app) as test_client:
        yield test_client


def test_index_and_settings_smoke(client: TestClient, isolated_server):
    response = client.get("/")
    assert response.status_code == 200
    assert "OMADS" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["target_repo"] == str(isolated_server["repo_dir"].resolve())


def test_update_settings_validates_target_repo_and_bounds(client: TestClient, isolated_server):
    outside_dir = isolated_server["outside_dir"]
    repo_dir = isolated_server["repo_dir"]

    response = client.post(
        "/api/settings",
        json={
            "target_repo": str(outside_dir),
            "claude_max_turns": 999,
            "codex_reasoning": "invalid",
            "codex_fast": True,
            "unknown_field": "ignored",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True

    settings = client.get("/api/settings").json()
    assert settings["target_repo"] == str(repo_dir.resolve())
    assert settings["claude_max_turns"] == 100
    assert settings["codex_reasoning"] == "high"
    assert settings["codex_fast"] is True
    assert "unknown_field" not in settings
    assert json.loads(state._CONFIG_PATH.read_text())["codex_fast"] is True

    valid_repo = isolated_server["home_dir"] / "other-repo"
    valid_repo.mkdir()
    response = client.post("/api/settings", json={"target_repo": str(valid_repo)})
    assert response.status_code == 200
    assert client.get("/api/settings").json()["target_repo"] == str(valid_repo.resolve())
    assert json.loads(state._CONFIG_PATH.read_text())["target_repo"] == str(valid_repo.resolve())


def test_project_endpoints_enforce_home_boundary_and_missing_paths(
    client: TestClient,
    isolated_server,
):
    outside_dir = isolated_server["outside_dir"]
    project_repo = isolated_server["home_dir"] / "project-a"
    project_repo.mkdir()

    response = client.post(
        "/api/projects",
        json={"name": "Extern", "path": str(outside_dir)},
    )
    assert response.status_code == 200
    assert "Only directories inside $HOME are allowed" in response.json()["error"]

    response = client.post(
        "/api/projects",
        json={"name": "Projekt A", "path": str(project_repo)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    project_id = payload["project"]["id"]

    projects = client.get("/api/projects").json()
    assert len(projects) == 1
    assert projects[0]["id"] == project_id
    assert client.get("/api/settings").json()["target_repo"] == str(project_repo.resolve())
    assert json.loads(state._PROJECTS_PATH.read_text())[0]["id"] == project_id
    assert json.loads(state._CONFIG_PATH.read_text())["target_repo"] == str(project_repo.resolve())

    project_repo.rmdir()
    response = client.post("/api/projects/switch", json={"id": project_id})
    assert response.status_code == 200
    assert "Directory no longer exists" in response.json()["error"]

    response = client.post("/api/projects", json={})
    assert response.status_code == 200
    assert response.json()["error"] == "Name and path are required"


def test_chat_session_persistence_roundtrip(isolated_server):
    repo_key = str(isolated_server["repo_dir"].resolve())
    state._set_chat_session(repo_key, "session-123")

    assert state._get_chat_session(repo_key) == "session-123"
    assert json.loads(state._CHAT_SESSIONS_PATH.read_text()) == {repo_key: "session-123"}
    assert state._load_chat_sessions() == {repo_key: "session-123"}


def test_append_log_filters_unknown_types_and_reads_valid_entries(isolated_server):
    project_id = "proj123"

    state._append_log(project_id, {"type": "stream_text", "agent": "Claude", "text": "Hallo"})
    state._append_log(project_id, {"type": "unknown_type", "text": "skip me"})

    entries = state._read_log(project_id)

    assert len(entries) == 1
    assert entries[0]["type"] == "stream_text"
    assert entries[0]["text"] == "Hallo"
    assert "timestamp" in entries[0]


def test_claude_task_failure_emits_task_error_and_unlock(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: DummyProcess(returncode=23))
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)

    runtime._run_claude_session_thread(None, "Please review")

    assert any(msg["type"] == "task_error" and "exit code 23" in msg["text"].lower() for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)
    assert not any(msg["type"] == "agent_status" and "Done" in msg.get("status", "") for msg in messages)


def test_review_step_one_failure_emits_task_error_and_unlock(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: DummyProcess(returncode=7))
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")

    runtime._run_review_thread(None, "project", "all", "")

    assert any(msg["type"] == "task_error" and "exit code 7" in msg["text"].lower() for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)
    assert not any(msg["type"] == "agent_status" and "Step 1/3 done" in msg.get("status", "") for msg in messages)
