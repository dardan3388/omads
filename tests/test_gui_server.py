from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omads.gui import server


@pytest.fixture()
def isolated_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    repo_dir = home_dir / "repo"
    repo_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setattr(server, "_CONFIG_PATH", home_dir / ".config" / "omads" / "gui_settings.json")
    monkeypatch.setattr(server, "_GUI_STATUS_PATH", home_dir / ".config" / "omads" / "gui_status.json")
    monkeypatch.setattr(server, "_PROJECTS_PATH", home_dir / ".config" / "omads" / "projects.json")
    monkeypatch.setattr(server, "_HISTORY_DIR", home_dir / ".config" / "omads" / "history")
    monkeypatch.setattr(server, "_CHAT_SESSIONS_PATH", home_dir / ".config" / "omads" / "chat_sessions.json")
    monkeypatch.setattr(server, "_MEMORY_DIR", home_dir / ".config" / "omads" / "memory")
    monkeypatch.setattr(server, "_settings", copy.deepcopy(server._DEFAULT_SETTINGS))
    monkeypatch.setattr(server, "_gui_status", copy.deepcopy(server._GUI_STATUS_DEFAULTS))
    monkeypatch.setattr(server, "_chat_sessions", {})
    monkeypatch.setattr(server, "_connections", [])
    monkeypatch.setattr(server, "_active_process", None)
    monkeypatch.setattr(server, "_task_cancelled", False)
    monkeypatch.setattr(server, "_last_files_changed", [])
    monkeypatch.setattr(server, "_pending_review_fixes", {})

    server._settings["target_repo"] = str(repo_dir.resolve())

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
    assert json.loads(server._CONFIG_PATH.read_text())["codex_fast"] is True

    valid_repo = isolated_server["home_dir"] / "other-repo"
    valid_repo.mkdir()
    response = client.post("/api/settings", json={"target_repo": str(valid_repo)})
    assert response.status_code == 200
    assert client.get("/api/settings").json()["target_repo"] == str(valid_repo.resolve())
    assert json.loads(server._CONFIG_PATH.read_text())["target_repo"] == str(valid_repo.resolve())


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
    assert "Nur Verzeichnisse innerhalb von $HOME erlaubt" in response.json()["error"]

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
    assert json.loads(server._PROJECTS_PATH.read_text())[0]["id"] == project_id
    assert json.loads(server._CONFIG_PATH.read_text())["target_repo"] == str(project_repo.resolve())

    project_repo.rmdir()
    response = client.post("/api/projects/switch", json={"id": project_id})
    assert response.status_code == 200
    assert "Verzeichnis existiert nicht mehr" in response.json()["error"]


def test_chat_session_persistence_roundtrip(isolated_server):
    repo_key = str(isolated_server["repo_dir"].resolve())
    server._set_chat_session(repo_key, "session-123")

    assert server._get_chat_session(repo_key) == "session-123"
    assert json.loads(server._CHAT_SESSIONS_PATH.read_text()) == {repo_key: "session-123"}
    assert server._load_chat_sessions() == {repo_key: "session-123"}


def test_append_log_filters_unknown_types_and_reads_valid_entries(isolated_server):
    project_id = "proj123"

    server._append_log(project_id, {"type": "stream_text", "agent": "Claude", "text": "Hallo"})
    server._append_log(project_id, {"type": "unknown_type", "text": "skip me"})

    entries = server._read_log(project_id)

    assert len(entries) == 1
    assert entries[0]["type"] == "stream_text"
    assert entries[0]["text"] == "Hallo"
    assert "timestamp" in entries[0]
