from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omads.gui import github, routes, runtime, server, state, streaming, websocket


class DummyStream:
    def __init__(self, lines: list[str] | None = None):
        self._lines = list(lines or [])
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._index >= len(self._lines):
            raise StopIteration
        line = self._lines[self._index]
        self._index += 1
        return line

    def readline(self):
        if self._index >= len(self._lines):
            return ""
        line = self._lines[self._index]
        self._index += 1
        return line


class DummyStdin:
    def __init__(self):
        self.buffer = ""
        self.closed = False

    def write(self, text: str):
        self.buffer += text
        return len(text)

    def close(self):
        self.closed = True


class DummyProcess:
    def __init__(
        self,
        lines: list[str] | None = None,
        returncode: int = 0,
        stderr_lines: list[str] | None = None,
    ):
        self.stdout = DummyStream(lines)
        self.stderr = DummyStream(stderr_lines)
        self.stdin = DummyStdin()
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True

    def poll(self):
        return None if not self.killed else self.returncode


class BusyProcess:
    def poll(self):
        return None


class KillableBusyProcess:
    def __init__(self):
        self.killed = False

    def poll(self):
        return None if not self.killed else 0

    def kill(self):
        self.killed = True


class DummyCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class NoopThread:
    def __init__(self, target=None, args=(), daemon=None, kwargs=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


class DummyAdmissionWebSocket:
    def __init__(self, *, origin: str | None, client_host: str = "127.0.0.1", server_port: int = 8080):
        self.headers = {"origin": origin} if origin is not None else {}
        self.scope = {
            "server": ("127.0.0.1", server_port),
            "client": (client_host, 12345),
        }
        self.accepted = False
        self.closed: tuple[int, str] | None = None

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = (code, reason)

    async def receive_json(self):
        raise websocket.WebSocketDisconnect()

    async def send_json(self, message):
        raise AssertionError(f"send_json should not be called: {message}")


class RecordingWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message):
        self.sent.append(message)


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
    monkeypatch.setattr(state, "_TIMELINE_DIR", home_dir / ".config" / "omads" / "timeline")
    monkeypatch.setattr(state, "_CHAT_SESSIONS_PATH", home_dir / ".config" / "omads" / "chat_sessions.json")
    monkeypatch.setattr(state, "_MEMORY_DIR", home_dir / ".config" / "omads" / "memory")
    monkeypatch.setattr(state, "_settings", copy.deepcopy(state._DEFAULT_SETTINGS))
    monkeypatch.setattr(state, "_gui_status", copy.deepcopy(state._GUI_STATUS_DEFAULTS))
    monkeypatch.setattr(state, "_chat_sessions", {})
    monkeypatch.setattr(runtime, "_connections", [])
    monkeypatch.setattr(runtime, "_connection_settings", {})
    monkeypatch.setattr(runtime, "_connection_session_ids", {})
    monkeypatch.setattr(runtime, "_session_settings_store", {})
    monkeypatch.setattr(runtime, "_connection_last_task_files", {})
    monkeypatch.setattr(runtime, "_session_last_task_files", {})
    monkeypatch.setattr(runtime, "_active_process", None)
    monkeypatch.setattr(runtime, "_active_task_owner", None)
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
            "builder_agent": "invalid",
            "review_first_reviewer": "claude",
            "review_second_reviewer": "claude",
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
    assert settings["builder_agent"] == "claude"
    assert settings["review_first_reviewer"] == "claude"
    assert settings["review_second_reviewer"] == "codex"
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

    response = client.post("/api/settings", json={"builder_agent": "codex"})
    assert response.status_code == 200
    assert client.get("/api/settings").json()["builder_agent"] == "codex"

    response = client.post(
        "/api/settings",
        json={"review_first_reviewer": "codex", "review_second_reviewer": "codex"},
    )
    assert response.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["review_first_reviewer"] == "codex"
    assert settings["review_second_reviewer"] == "claude"


def test_update_settings_persists_claude_permission_mode(client: TestClient, isolated_server):
    response = client.post(
        "/api/settings",
        json={"claude_permission_mode": "plan"},
    )
    assert response.status_code == 200
    assert response.json()["settings"]["claude_permission_mode"] == "plan"
    assert client.get("/api/settings").json()["claude_permission_mode"] == "plan"

    response = client.post(
        "/api/settings",
        json={"claude_permission_mode": "auto-accept"},
    )
    assert response.status_code == 200
    assert response.json()["settings"]["claude_permission_mode"] == "auto"
    assert client.get("/api/settings").json()["claude_permission_mode"] == "auto"


def test_update_settings_persists_codex_execution_mode(client: TestClient, isolated_server):
    response = client.post(
        "/api/settings",
        json={"codex_execution_mode": "full_auto"},
    )
    assert response.status_code == 200
    assert response.json()["settings"]["codex_execution_mode"] == "full-auto"
    assert client.get("/api/settings").json()["codex_execution_mode"] == "full-auto"


def test_update_settings_persists_codex_model_variants(client: TestClient, isolated_server):
    response = client.post(
        "/api/settings",
        json={"codex_model": " gpt-5.3-Codex "},
    )
    assert response.status_code == 200
    assert response.json()["settings"]["codex_model"] == "gpt-5.3-Codex"
    assert client.get("/api/settings").json()["codex_model"] == "gpt-5.3-Codex"


def test_session_settings_websocket_patch_keeps_claude_permission_mode(isolated_server):
    normalized = websocket._normalize_session_settings(
        {
            "claude_permission_mode": "plan",
        }
    )

    assert normalized["claude_permission_mode"] == "plan"


def test_session_settings_websocket_patch_keeps_codex_execution_mode(isolated_server):
    normalized = websocket._normalize_session_settings(
        {
            "codex_execution_mode": "read_only",
        }
    )

    assert normalized["codex_execution_mode"] == "read-only"


def test_session_settings_websocket_patch_keeps_codex_model_variants(isolated_server):
    normalized = websocket._normalize_session_settings(
        {
            "codex_model": " gpt-5.3-Codex ",
        }
    )

    assert normalized["codex_model"] == "gpt-5.3-Codex"


def test_load_config_coerces_legacy_boolean_strings(isolated_server):
    state._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    state._CONFIG_PATH.write_text(
        json.dumps(
            {
                "codex_fast": "false",
                "auto_review": "true",
                "lan_access": "1",
                "claude_permission_mode": "bypass",
                "codex_execution_mode": "full_auto",
            }
        ),
        encoding="utf-8",
    )

    loaded = state._load_config()

    assert loaded["codex_fast"] is False
    assert loaded["auto_review"] is True
    assert loaded["lan_access"] is True
    assert loaded["claude_permission_mode"] == "bypassPermissions"
    assert loaded["codex_execution_mode"] == "full-auto"


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
        json={"name": "Project A", "path": str(project_repo)},
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


def test_project_duplicate_invalid_id_history_and_log_endpoints(client: TestClient, isolated_server):
    project_repo = isolated_server["home_dir"] / "project-a"
    project_repo.mkdir()

    created = client.post("/api/projects", json={"name": "Project A", "path": str(project_repo)}).json()
    project_id = created["project"]["id"]

    duplicate = client.post("/api/projects", json={"name": "Project B", "path": str(project_repo)})
    assert duplicate.status_code == 200
    assert "already exists" in duplicate.json()["error"]

    state._append_timeline_event(project_id, {"type": "user_input", "text": "hello"})
    state._append_timeline_event(project_id, {"type": "stream_text", "agent": "Claude", "text": "world"})

    history = client.get(f"/api/projects/{project_id}/history")
    logs = client.get(f"/api/projects/{project_id}/logs")
    timeline = client.get(f"/api/projects/{project_id}/timeline")
    assert history.status_code == 200
    assert logs.status_code == 200
    assert timeline.status_code == 200
    assert history.json()[0]["text"] == "hello"
    assert logs.json()[0]["text"] == "world"
    assert timeline.json()["entries"][0]["type"] == "user_input"
    assert timeline.json()["entries"][1]["type"] == "stream_text"
    assert timeline.json()["entries"][0]["seq"] == 1
    assert timeline.json()["total_count"] == 2

    invalid_history = client.get("/api/projects/bad.id/history")
    invalid_logs = client.get("/api/projects/bad.id/logs")
    invalid_timeline = client.get("/api/projects/bad.id/timeline")
    invalid_delete = client.delete("/api/projects/bad.id")
    assert invalid_history.json()["error"] == "Invalid project ID"
    assert invalid_logs.json()["error"] == "Invalid project ID"
    assert invalid_timeline.json()["error"] == "Invalid project ID"
    assert invalid_delete.json()["error"] == "Invalid project ID"


def test_history_and_logs_are_derived_from_timeline_when_present(client: TestClient):
    project_id = "proj123"

    state._append_timeline_event(project_id, {"type": "user_input", "text": "build this"})
    state._append_timeline_event(project_id, {"type": "stream_text", "agent": "Codex", "text": "working"})
    state._append_timeline_event(project_id, {"type": "agent_status", "agent": "Codex", "status": "Done (3s)"})

    history = client.get(f"/api/projects/{project_id}/history")
    logs = client.get(f"/api/projects/{project_id}/logs")

    assert history.status_code == 200
    assert logs.status_code == 200
    assert [entry["type"] for entry in history.json()] == ["user_input"]
    assert [entry["type"] for entry in logs.json()] == ["stream_text", "agent_status"]


def test_timeline_endpoint_supports_bounded_loading_and_before_cursor(client: TestClient):
    project_id = "projpage"

    for idx in range(1, 8):
        state._append_timeline_event(project_id, {"type": "stream_text", "agent": "Codex", "text": f"entry-{idx}"})

    first_page = client.get(f"/api/projects/{project_id}/timeline?limit=3")
    assert first_page.status_code == 200
    payload = first_page.json()
    assert payload["total_count"] == 7
    assert payload["has_more"] is True
    assert payload["next_before"] == 5
    assert [entry["text"] for entry in payload["entries"]] == ["entry-5", "entry-6", "entry-7"]
    assert [entry["seq"] for entry in payload["entries"]] == [5, 6, 7]

    older_page = client.get(f"/api/projects/{project_id}/timeline?limit=3&before=5")
    assert older_page.status_code == 200
    older_payload = older_page.json()
    assert older_payload["has_more"] is True
    assert older_payload["next_before"] == 2
    assert [entry["text"] for entry in older_payload["entries"]] == ["entry-2", "entry-3", "entry-4"]

    earliest_page = client.get(f"/api/projects/{project_id}/timeline?limit=3&before=2")
    assert earliest_page.status_code == 200
    earliest_payload = earliest_page.json()
    assert earliest_payload["has_more"] is False
    assert earliest_payload["next_before"] is None
    assert [entry["text"] for entry in earliest_payload["entries"]] == ["entry-1"]


def test_chat_session_persistence_roundtrip(isolated_server):
    repo_key = str(isolated_server["repo_dir"].resolve())
    state._set_chat_session(repo_key, "session-123")
    assert state._get_chat_session(repo_key) == "session-123"
    assert json.loads(state._CHAT_SESSIONS_PATH.read_text()) == {repo_key: "session-123"}
    assert state._load_chat_sessions() == {repo_key: "session-123"}


def test_websocket_rejects_missing_origin_but_accepts_local_browser_origin(isolated_server):
    missing_origin_ws = DummyAdmissionWebSocket(origin=None, client_host="127.0.0.1")
    asyncio.run(websocket.websocket_endpoint(missing_origin_ws))
    assert missing_origin_ws.accepted is False
    assert missing_origin_ws.closed == (1008, "Origin not allowed")

    allowed_origin_ws = DummyAdmissionWebSocket(origin="http://localhost:8080", client_host="127.0.0.1")
    asyncio.run(websocket.websocket_endpoint(allowed_origin_ws))
    assert allowed_origin_ws.accepted is True
    assert allowed_origin_ws.closed is None


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
    repo_key = str(isolated_server["repo_dir"].resolve())
    runtime._pending_review_fixes[repo_key] = "## Final fix plan\n- stale"

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: DummyProcess(returncode=7))
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")

    runtime._run_review_thread(None, "project", "all", "")

    assert any(msg["type"] == "task_error" and "exit code 7" in msg["text"].lower() for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)
    assert not any(msg["type"] == "agent_status" and "Step 1/3 done" in msg.get("status", "") for msg in messages)
    assert repo_key not in runtime._pending_review_fixes


def test_websocket_chat_validates_length_and_rate_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    started_threads: list[NoopThread] = []

    def build_thread(*args, **kwargs):
        thread = NoopThread(*args, **kwargs)
        started_threads.append(thread)
        return thread

    monkeypatch.setattr(websocket.threading, "Thread", build_thread)

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "chat", "text": "x" * 50001})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "Message too long" in message["text"]

        ws_client.send_json({"type": "chat", "text": "first"})
        ws_client.send_json({"type": "chat", "text": "second"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "Too fast" in message["text"]

    assert len(started_threads) == 1
    assert started_threads[0].started is True
    assert started_threads[0].target is runtime._run_builder_session_thread
    assert started_threads[0].args[1] == "first"


def test_websocket_reserves_slot_before_worker_thread_starts(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    started_threads: list[NoopThread] = []

    def build_thread(*args, **kwargs):
        thread = NoopThread(*args, **kwargs)
        started_threads.append(thread)
        return thread

    monkeypatch.setattr(websocket.threading, "Thread", build_thread)

    try:
        with client.websocket_connect("/ws") as ws_client:
            ws_client.send_json({"type": "chat", "text": "first"})
            ws_client.send_json({"type": "review", "scope": "project", "focus": "all"})
            message = ws_client.receive_json()
            assert message["type"] == "error"
            assert "A task is already running" in message["text"]
    finally:
        with runtime._process_lock:
            runtime._active_process = None
            runtime._task_cancelled = False

    assert len(started_threads) == 1
    assert started_threads[0].started is True
    assert started_threads[0].target is runtime._run_builder_session_thread
    assert started_threads[0].args[1] == "first"


def test_websocket_rejects_new_work_while_task_slot_is_reserved(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    started_threads: list[NoopThread] = []

    def build_thread(*args, **kwargs):
        thread = NoopThread(*args, **kwargs)
        started_threads.append(thread)
        return thread

    monkeypatch.setattr(websocket.threading, "Thread", build_thread)

    assert runtime._try_reserve_task_slot() is True
    try:
        with client.websocket_connect("/ws") as ws_client:
            ws_client.send_json({"type": "chat", "text": "hello"})
            message = ws_client.receive_json()
            assert message["type"] == "error"
            assert "A task is already running" in message["text"]
    finally:
        runtime._release_reserved_task_slot()

    assert started_threads == []


def test_websocket_apply_fixes_and_set_repo_errors(client: TestClient, isolated_server):
    valid_repo = isolated_server["home_dir"] / "project-b"
    valid_repo.mkdir()
    missing_dir = isolated_server["home_dir"] / "missing-dir"
    session_id = "browser-session-set-repo"

    with client.websocket_connect(f"/ws?client_session_id={session_id}") as ws_client:
        ws_client.send_json({"type": "apply_fixes"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert message["text"] == "No review fixes are available"

        ws_client.send_json({"type": "set_repo", "path": str(valid_repo)})
        message = ws_client.receive_json()
        assert message == {"type": "system", "text": f"Project: {valid_repo.resolve()}"}
        assert state._get_setting("target_repo") == str(isolated_server["repo_dir"].resolve())
        session_settings = client.get("/api/session-settings", params={"client_session_id": session_id}).json()
        assert session_settings["target_repo"] == str(valid_repo.resolve())

        ws_client.send_json({"type": "set_repo", "path": str(missing_dir)})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "Not a directory" in message["text"]


def test_websocket_apply_fixes_uses_selected_first_reviewer(
    client: TestClient,
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    started_threads: list[NoopThread] = []
    runtime._pending_review_fixes[str(isolated_server["repo_dir"].resolve())] = "## Final fix plan\n- Do the thing"

    def build_thread(*args, **kwargs):
        thread = NoopThread(*args, **kwargs)
        started_threads.append(thread)
        return thread

    monkeypatch.setattr(websocket.threading, "Thread", build_thread)
    state._update_settings(lambda settings: settings.update({"review_first_reviewer": "codex"}))

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "apply_fixes"})

    assert len(started_threads) == 1
    assert started_threads[0].started is True
    assert started_threads[0].target is runtime._run_codex_session_thread
    assert "Apply the following fixes from the review now" in started_threads[0].args[1]
    assert str(isolated_server["repo_dir"].resolve()) not in runtime._pending_review_fixes


def test_websocket_rejects_new_work_while_task_is_running(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "_active_process", BusyProcess())

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "review", "scope": "project", "focus": "all"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "A task is already running" in message["text"]


def test_stop_active_task_for_connection_enforces_owner(isolated_server):
    owner = object()
    other = object()
    process = KillableBusyProcess()

    assert runtime._try_reserve_task_slot(owner) is True
    runtime._active_process = process

    assert runtime.stop_active_task_for_connection(other) == "not_owner"
    assert runtime._task_cancelled is False
    assert process.killed is False

    assert runtime.stop_active_task_for_connection(owner) == "stopped"
    assert runtime._task_cancelled is True
    assert process.killed is True
    assert runtime._active_process is None
    assert runtime._active_task_owner is None


def test_websocket_stop_rejects_non_owner(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "stop_active_task_for_connection", lambda ws: "not_owner")

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "stop"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "owns the active task" in message["text"]


def test_websocket_stop_reports_when_no_task_is_running(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "stop_active_task_for_connection", lambda ws: "idle")

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "stop"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "No running task" in message["text"]


def test_builder_dispatch_uses_selected_primary_builder(isolated_server, monkeypatch: pytest.MonkeyPatch):
    called: list[tuple[str, str]] = []

    monkeypatch.setattr(runtime, "_run_claude_session_thread", lambda ws, text: called.append(("claude", text)))
    monkeypatch.setattr(runtime, "_run_codex_session_thread", lambda ws, text: called.append(("codex", text)))

    state._update_settings(lambda settings: settings.__setitem__("builder_agent", "claude"))
    runtime._run_builder_session_thread(None, "first task")

    state._update_settings(lambda settings: settings.__setitem__("builder_agent", "codex"))
    runtime._run_builder_session_thread(None, "second task")

    assert called == [("claude", "first task"), ("codex", "second task")]


def test_builder_dispatch_uses_connection_scoped_settings(isolated_server, monkeypatch: pytest.MonkeyPatch):
    repo_a = isolated_server["home_dir"] / "repo-a"
    repo_b = isolated_server["home_dir"] / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    ws_a = object()
    ws_b = object()
    runtime.register_connection(ws_a)
    runtime.register_connection(ws_b)
    runtime.update_connection_settings(
        ws_a,
        {
            "builder_agent": "claude",
            "target_repo": str(repo_a.resolve()),
            "claude_model": "sonnet",
        },
    )
    runtime.update_connection_settings(
        ws_b,
        {
            "builder_agent": "codex",
            "target_repo": str(repo_b.resolve()),
            "codex_reasoning": "low",
            "codex_fast": True,
        },
    )

    called: list[tuple[str, str, str, dict]] = []
    monkeypatch.setattr(
        runtime,
        "_run_claude_session_thread",
        lambda ws, text: called.append(
            ("claude", text, runtime.get_connection_settings_snapshot(ws)["target_repo"], runtime.get_connection_settings_snapshot(ws))
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_run_codex_session_thread",
        lambda ws, text: called.append(
            ("codex", text, runtime.get_connection_settings_snapshot(ws)["target_repo"], runtime.get_connection_settings_snapshot(ws))
        ),
    )

    try:
        runtime._run_builder_session_thread(ws_a, "task A")
        runtime._run_builder_session_thread(ws_b, "task B")
    finally:
        runtime.unregister_connection(ws_a)
        runtime.unregister_connection(ws_b)

    assert called[0][0:3] == ("claude", "task A", str(repo_a.resolve()))
    assert called[0][3]["claude_model"] == "sonnet"
    assert called[1][0:3] == ("codex", "task B", str(repo_b.resolve()))
    assert called[1][3]["codex_reasoning"] == "low"
    assert called[1][3]["codex_fast"] is True


def test_session_settings_survive_disconnect_for_same_browser_session(isolated_server):
    repo_a = isolated_server["home_dir"] / "repo-a"
    repo_a.mkdir()
    session_id = "browser-session-12345"
    ws_a = object()
    ws_b = object()

    runtime.register_connection(ws_a, session_id)
    runtime.update_connection_settings(
        ws_a,
        {
            "builder_agent": "codex",
            "target_repo": str(repo_a.resolve()),
            "codex_reasoning": "low",
        },
    )
    runtime.unregister_connection(ws_a)

    runtime.register_connection(ws_b, session_id)
    try:
        restored = runtime.get_connection_settings_snapshot(ws_b)
    finally:
        runtime.unregister_connection(ws_b)

    assert restored["builder_agent"] == "codex"
    assert restored["target_repo"] == str(repo_a.resolve())
    assert restored["codex_reasoning"] == "low"


def test_session_settings_endpoint_prefers_runtime_session_snapshot(client: TestClient, isolated_server):
    repo_a = isolated_server["home_dir"] / "repo-a"
    repo_a.mkdir()
    session_id = "browser-session-12345"
    ws = object()

    runtime.register_connection(ws, session_id)
    runtime.update_connection_settings(
        ws,
        {
            "builder_agent": "codex",
            "target_repo": str(repo_a.resolve()),
            "codex_fast": True,
        },
    )
    runtime.unregister_connection(ws)

    response = client.get("/api/session-settings", params={"client_session_id": session_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["builder_agent"] == "codex"
    assert payload["target_repo"] == str(repo_a.resolve())
    assert payload["codex_fast"] is True


def test_project_create_and_switch_can_update_one_session_without_mutating_global_defaults(
    client: TestClient,
    isolated_server,
):
    session_id = "browser-session-projects"
    repo_a = isolated_server["home_dir"] / "repo-a"
    repo_b = isolated_server["home_dir"] / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    created = client.post(
        "/api/projects",
        params={"client_session_id": session_id},
        json={"name": "Repo A", "path": str(repo_a)},
    )
    assert created.status_code == 200
    assert created.json()["ok"] is True
    assert state._get_setting("target_repo") == str(isolated_server["repo_dir"].resolve())
    assert client.get("/api/session-settings", params={"client_session_id": session_id}).json()["target_repo"] == str(repo_a.resolve())

    created_b = client.post(
        "/api/projects",
        json={"name": "Repo B", "path": str(repo_b)},
    )
    assert created_b.status_code == 200
    project_b_id = created_b.json()["project"]["id"]

    switched = client.post(
        "/api/projects/switch",
        params={"client_session_id": session_id},
        json={"id": project_b_id},
    )
    assert switched.status_code == 200
    assert switched.json()["ok"] is True
    assert state._get_setting("target_repo") == str(repo_b.resolve())
    assert client.get("/api/session-settings", params={"client_session_id": session_id}).json()["target_repo"] == str(repo_b.resolve())


def test_last_task_files_survive_disconnect_for_same_browser_session(isolated_server):
    session_id = "browser-session-12345"
    ws_a = object()
    ws_b = object()

    runtime.register_connection(ws_a, session_id)
    runtime.record_last_task_files(ws_a, ["repo-a/file_a.py"])
    runtime.unregister_connection(ws_a)

    runtime.register_connection(ws_b, session_id)
    try:
        restored = runtime.get_last_task_files_snapshot(ws_b)
    finally:
        runtime.unregister_connection(ws_b)

    assert restored == ["repo-a/file_a.py"]


def test_last_task_review_scope_uses_requesting_session_files(isolated_server, monkeypatch: pytest.MonkeyPatch):
    repo_dir = isolated_server["repo_dir"]
    session_a = "browser-session-a"
    session_b = "browser-session-b"
    ws_a = RecordingWebSocket()
    ws_b = RecordingWebSocket()
    codex_review_calls: list[list[str]] = []

    monkeypatch.setattr(runtime.asyncio, "run_coroutine_threadsafe", lambda coro, loop: asyncio.run(coro))
    monkeypatch.setattr(runtime, "_append_timeline_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_get_chat_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_run_codex_manual_review_step", lambda **kwargs: codex_review_calls.append(list(kwargs["review_files"])) or "Step review")
    monkeypatch.setattr(runtime, "_run_claude_manual_review_step", lambda **kwargs: ("Second review", "review-session-1"))
    monkeypatch.setattr(runtime, "_run_codex_manual_synthesis_step", lambda **kwargs: ("All clear", False))

    runtime.register_connection(ws_a, session_a)
    runtime.register_connection(ws_b, session_b)
    runtime.update_connection_settings(
        ws_a,
        {
            "target_repo": str(repo_dir.resolve()),
            "review_first_reviewer": "codex",
            "review_second_reviewer": "claude",
        },
    )
    runtime.update_connection_settings(
        ws_b,
        {
            "target_repo": str(repo_dir.resolve()),
            "review_first_reviewer": "codex",
            "review_second_reviewer": "claude",
        },
    )
    runtime.record_last_task_files(ws_a, ["repo-a/file_a.py"])
    runtime.record_last_task_files(ws_b, ["repo-b/file_b.py"])

    try:
        runtime._run_review_thread(ws_a, "last_task", "all", "")
    finally:
        runtime.unregister_connection(ws_a)
        runtime.unregister_connection(ws_b)

    assert codex_review_calls == [["repo-a/file_a.py"]]
    assert any(
        msg["type"] == "stream_text" and "Scope: Last task (1 files)" in msg["text"]
        for msg in ws_a.sent
    )
    assert all("repo-b/file_b.py" not in msg.get("text", "") for msg in ws_a.sent)


def test_send_to_ws_sync_targets_only_the_initiating_connection(isolated_server, monkeypatch: pytest.MonkeyPatch):
    ws_a = RecordingWebSocket()
    ws_b = RecordingWebSocket()
    runtime.register_connection(ws_a)
    runtime.register_connection(ws_b)

    timeline_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(runtime, "_append_timeline_event", lambda proj_id, msg: timeline_events.append((proj_id, msg)))
    monkeypatch.setattr(runtime.asyncio, "run_coroutine_threadsafe", lambda coro, loop: asyncio.run(coro))

    try:
        runtime.send_to_ws_sync(ws_a, {"type": "stream_text", "agent": "Codex", "text": "hello"}, proj_id_override="proj-a")
    finally:
        runtime.unregister_connection(ws_a)
        runtime.unregister_connection(ws_b)

    assert ws_a.sent == [{"type": "stream_text", "agent": "Codex", "text": "hello"}]
    assert ws_b.sent == []
    assert timeline_events == [("proj-a", {"type": "stream_text", "agent": "Codex", "text": "hello"})]


def test_browse_health_status_and_ledger_endpoints(client: TestClient, isolated_server, monkeypatch: pytest.MonkeyPatch):
    home_dir = isolated_server["home_dir"]
    repo_dir = isolated_server["repo_dir"]
    ledger_dir = home_dir / "data" / "ledger"
    ledger_dir.mkdir(parents=True)
    ledger_file = ledger_dir / "task_history.jsonl"
    ledger_file.write_text('{"task":"one"}\n{"task":"two"}\n', encoding="utf-8")

    creds_dir = home_dir / ".claude"
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / ".credentials.json").write_text("{}", encoding="utf-8")

    def fake_which(name: str):
        return f"/usr/bin/{name}" if name in {"claude", "codex"} else None

    def fake_run(cmd, capture_output=True, text=True, timeout=5):
        if cmd[0] == "claude":
            return DummyCompletedProcess(stdout="claude 1.2.3")
        return DummyCompletedProcess(stdout="codex 4.5.6")

    class DummyPhase:
        value = "builder"

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(routes.subprocess, "run", fake_run)
    monkeypatch.setattr(routes.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setattr(routes, "get_data_dir", lambda: home_dir / "data")
    monkeypatch.setattr(routes, "get_dna_dir", lambda: home_dir / "dna")
    monkeypatch.setattr("omads.dna.cold_start.get_current_phase", lambda path: DummyPhase())

    outside_response = client.get("/api/browse", params={"path": str(isolated_server["outside_dir"])})
    assert outside_response.status_code == 200
    assert "Access is allowed only inside the home directory" in outside_response.json()["error"]

    browse_response = client.get("/api/browse", params={"path": str(home_dir)})
    assert browse_response.status_code == 200
    assert any(entry["name"] == "repo" for entry in browse_response.json()["dirs"])

    health = client.get("/api/health")
    status = client.get("/api/status")
    ledger = client.get("/api/ledger")
    assert health.status_code == 200
    assert health.json()["claude"]["installed"] is True
    assert health.json()["claude"]["authenticated"] is True
    assert health.json()["claude"]["version"] == "claude 1.2.3"
    assert health.json()["codex"]["version"] == "codex 4.5.6"

    assert status.status_code == 200
    assert status.json()["phase"] == "builder"
    assert status.json()["total_tasks"] == 2
    assert status.json()["target_repo"] == str(repo_dir.resolve())

    assert ledger.status_code == 200
    assert len(ledger.json()) == 2
    assert ledger.json()[-1]["task"] == "two"


def test_diff_and_status_endpoints_prefer_session_snapshot_over_global_settings(
    client: TestClient,
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_a = isolated_server["home_dir"] / "repo-a"
    repo_b = isolated_server["home_dir"] / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    session_id = "browser-session-diff"

    state._update_settings(lambda settings: settings.__setitem__("target_repo", str(repo_a.resolve())))
    runtime.update_session_settings_for_session_id(
        session_id,
        {"target_repo": str(repo_b.resolve()), "builder_agent": "codex", "auto_review": False},
    )

    seen_cwds: list[str] = []

    def fake_run(cmd, capture_output=True, text=True, cwd=None, timeout=20):
        if cwd is not None:
            seen_cwds.append(cwd)
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return DummyCompletedProcess(stdout="true\n")
        if cmd[:4] == ["git", "rev-parse", "--verify", "HEAD"]:
            return DummyCompletedProcess(stdout="deadbeef\n")
        if cmd[:3] == ["git", "status", "--short"]:
            return DummyCompletedProcess(stdout=" M session.txt\n")
        if cmd[:6] == ["git", "diff", "--no-ext-diff", "--submodule=diff", "HEAD", "--"]:
            return DummyCompletedProcess(stdout="diff --git a/session.txt b/session.txt\n+session\n")
        raise AssertionError(f"Unexpected git command: {cmd}")

    class DummyPhase:
        value = "builder"

    monkeypatch.setattr(routes.subprocess, "run", fake_run)
    monkeypatch.setattr(routes, "get_data_dir", lambda: isolated_server["home_dir"] / "data")
    monkeypatch.setattr(routes, "get_dna_dir", lambda: isolated_server["home_dir"] / "dna")
    monkeypatch.setattr("omads.dna.cold_start.get_current_phase", lambda path: DummyPhase())

    diff = client.get("/api/diff", params={"client_session_id": session_id})
    status = client.get("/api/status", params={"client_session_id": session_id})
    fallback_status = client.get("/api/status")

    assert diff.status_code == 200
    assert diff.json()["repo"] == str(repo_b.resolve())
    assert status.status_code == 200
    assert status.json()["target_repo"] == str(repo_b.resolve())
    assert status.json()["builder_agent"] == "codex"
    assert status.json()["auto_review"] is False
    assert fallback_status.json()["target_repo"] == str(repo_a.resolve())
    assert str(repo_b.resolve()) in seen_cwds


def test_github_clone_endpoint_rejects_targets_outside_home(client: TestClient, isolated_server):
    response = client.post(
        "/api/github/clone",
        json={
            "full_name": "owner/repo",
            "target_dir": str(isolated_server["outside_dir"] / "clone-target"),
        },
    )

    assert response.status_code == 200
    assert "home directory" in response.json()["error"]


def test_github_clone_with_session_id_updates_only_the_requesting_session(
    client: TestClient,
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    session_id = "browser-session-clone"
    clone_target = isolated_server["home_dir"] / "clones"
    cloned_repo = clone_target / "owner-repo"
    clone_target.mkdir()
    cloned_repo.mkdir()

    monkeypatch.setattr(github, "clone_repo", lambda full_name, target_dir: {"path": str(cloned_repo.resolve())})
    monkeypatch.setattr(github, "get_auth_status", lambda: {"username": "octocat"})
    monkeypatch.setattr(runtime, "broadcast", lambda msg: asyncio.sleep(0))

    response = client.post(
        "/api/github/clone",
        params={"client_session_id": session_id},
        json={"full_name": "owner/repo", "target_dir": str(clone_target)},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert state._get_setting("target_repo") == str(isolated_server["repo_dir"].resolve())
    assert client.get("/api/session-settings", params={"client_session_id": session_id}).json()["target_repo"] == str(cloned_repo.resolve())



def test_theme_settings_diff_endpoint_and_openapi_docs(
    client: TestClient,
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_dir = isolated_server["repo_dir"].resolve()

    def fake_run(cmd, capture_output=True, text=True, cwd=None, timeout=20):
        assert cwd == str(repo_dir)
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return DummyCompletedProcess(stdout="true\n")
        if cmd[:4] == ["git", "rev-parse", "--verify", "HEAD"]:
            return DummyCompletedProcess(stdout="deadbeef\n")
        if cmd[:3] == ["git", "status", "--short"]:
            return DummyCompletedProcess(stdout=" M src/app.py\n?? notes.txt\n")
        if cmd[:6] == ["git", "diff", "--no-ext-diff", "--submodule=diff", "HEAD", "--"]:
            return DummyCompletedProcess(stdout="diff --git a/src/app.py b/src/app.py\n+print('hi')\n")
        raise AssertionError(f"Unexpected git command: {cmd}")

    monkeypatch.setattr(routes.subprocess, "run", fake_run)

    update = client.post("/api/settings", json={"ui_theme": "light"})
    assert update.status_code == 200
    assert update.json()["ok"] is True

    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["ui_theme"] == "light"
    assert settings.json()["builder_agent"] == "claude"

    diff = client.get("/api/diff")
    assert diff.status_code == 200
    payload = diff.json()
    assert payload["repo"] == str(repo_dir)
    assert payload["has_changes"] is True
    assert payload["changed_files"] == ["src/app.py", "notes.txt"]
    assert "diff --git a/src/app.py b/src/app.py" in payload["diff"]

    docs = client.get("/docs")
    redoc = client.get("/redoc")
    openapi = client.get("/openapi.json")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text
    assert redoc.status_code == 200
    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == "OMADS GUI"


def test_codex_builder_task_emits_output_and_unlock(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []

    process = DummyProcess(lines=[
        json.dumps({
            "type": "item.completed",
            "item": {"text": "Implemented the requested change."},
        }) + "\n"
    ])

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_codex_session_thread(None, "Implement a small feature")

    assert any(msg["type"] == "agent_status" and msg.get("agent") == "Codex" for msg in messages)
    assert any(msg["type"] == "stream_text" and msg.get("text") == "Implemented the requested change." for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_codex_builder_uses_fast_service_tier_when_enabled(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    commands: list[list[str]] = []
    process = DummyProcess(lines=[
        json.dumps({
            "type": "item.completed",
            "item": {"text": "Done."},
        }) + "\n"
    ])

    def popen(*args, **kwargs):
        commands.append(list(args[0]))
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))
    state._update_settings(lambda settings: settings.update({"codex_fast": True, "auto_review": False}))

    runtime._run_codex_session_thread(None, "Implement a small feature")

    assert commands
    codex_commands = [cmd for cmd in commands if cmd and cmd[0] == "codex"]
    assert codex_commands
    assert 'service_tier="fast"' in codex_commands[0]
    assert 'service_tier="flex"' not in codex_commands[0]
    assert any(msg["type"] == "unlock" for msg in messages)


def test_codex_builder_uses_selected_execution_mode_preset(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    commands: list[list[str]] = []
    process = DummyProcess(lines=[
        json.dumps({
            "type": "item.completed",
            "item": {"text": "Done."},
        }) + "\n"
    ])

    def popen(*args, **kwargs):
        commands.append(list(args[0]))
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))
    state._update_settings(lambda settings: settings.update({"codex_execution_mode": "auto", "auto_review": False}))

    runtime._run_codex_session_thread(None, "Implement a small feature")

    assert commands
    codex_commands = [cmd for cmd in commands if cmd and cmd[0] == "codex"]
    assert codex_commands
    assert "-s" in codex_commands[0]
    assert "workspace-write" in codex_commands[0]
    assert "-a" in codex_commands[0]
    assert "never" in codex_commands[0]
    assert "--dangerously-bypass-approvals-and-sandbox" not in codex_commands[0]
    assert any(msg["type"] == "unlock" for msg in messages)


def test_codex_builder_uses_flex_service_tier_when_fast_mode_disabled(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    commands: list[list[str]] = []
    process = DummyProcess(lines=[
        json.dumps({
            "type": "item.completed",
            "item": {"text": "Done."},
        }) + "\n"
    ])

    def popen(*args, **kwargs):
        commands.append(list(args[0]))
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))
    # Simulate a legacy string value to ensure command generation still normalizes correctly.
    state._update_settings(lambda settings: settings.update({"codex_fast": "false", "auto_review": False}))

    runtime._run_codex_session_thread(None, "Implement a small feature")

    assert commands
    codex_commands = [cmd for cmd in commands if cmd and cmd[0] == "codex"]
    assert codex_commands
    assert 'service_tier="flex"' in codex_commands[0]
    assert 'service_tier="fast"' not in codex_commands[0]
    assert any(msg["type"] == "unlock" for msg in messages)


def test_parse_codex_jsonl_line_handles_real_error_shapes():
    item_error_line = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "error",
            "message": "Model metadata for `definitely-not-a-real-model` not found.",
        },
    })
    top_level_error_line = json.dumps({
        "type": "error",
        "message": '{"detail":"The model is not supported when using Codex with a ChatGPT account."}',
    })

    assert streaming.parse_codex_jsonl_line(item_error_line) == [
        "Model metadata for `definitely-not-a-real-model` not found."
    ]
    assert streaming.parse_codex_jsonl_line(top_level_error_line) == [
        "The model is not supported when using Codex with a ChatGPT account."
    ]


def test_extract_codex_changed_files_reads_file_change_events():
    changed_file = "/tmp/example/schema_probe.txt"
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "abc"}) + "\n",
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "file_change",
                "changes": [
                    {"path": changed_file, "kind": "add"},
                    {"path": changed_file, "kind": "modify"},
                ],
                "status": "completed",
            },
        }) + "\n",
    ]

    assert streaming.extract_codex_changed_files(lines) == [changed_file]


def test_codex_builder_task_surfaces_scrubbed_stderr_failure(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    process = DummyProcess(
        returncode=1,
        stderr_lines=[
            "Error loading config.toml: token=sk-secret-1234567890\n",
        ],
    )

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_codex_session_thread(None, "Implement a small feature")

    task_errors = [msg for msg in messages if msg["type"] == "task_error"]
    assert task_errors
    assert "***" in task_errors[0]["text"]
    assert "sk-secret-1234567890" not in task_errors[0]["text"]
    assert any(msg["type"] == "unlock" for msg in messages)


def test_codex_builder_task_warns_when_json_events_have_no_visible_text(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    process = DummyProcess(lines=[
        json.dumps({"type": "thread.started", "thread_id": "abc"}) + "\n",
        json.dumps({"type": "turn.started"}) + "\n",
        json.dumps({"type": "turn.completed", "usage": {"output_tokens": 1}}) + "\n",
    ])

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_codex_session_thread(None, "Implement a small feature")

    warning_messages = [
        msg for msg in messages
        if msg["type"] == "chat_response" and "without any user-visible response" in msg.get("text", "")
    ]
    assert warning_messages
    assert "emitted JSON events" in warning_messages[0]["text"]
    assert any(
        msg["type"] == "agent_status" and msg.get("status") == "Finished without response (0s)"
        for msg in messages
    )
    assert any(msg["type"] == "unlock" for msg in messages)


def test_review_step_one_codex_failure_surfaces_scrubbed_stderr(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    state._settings["review_first_reviewer"] = "codex"
    state._settings["review_second_reviewer"] = "claude"

    process = DummyProcess(
        returncode=1,
        stderr_lines=["fatal: token=sk-secret-1234567890\n"],
    )

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_review_thread(None, "project", "all", "")

    task_errors = [msg for msg in messages if msg["type"] == "task_error"]
    assert task_errors
    assert "***" in task_errors[0]["text"]
    assert "sk-secret-1234567890" not in task_errors[0]["text"]
    assert any(msg["type"] == "unlock" for msg in messages)


def test_review_step_one_codex_warns_on_empty_output(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    state._settings["review_first_reviewer"] = "codex"
    state._settings["review_second_reviewer"] = "claude"

    process = DummyProcess(lines=[
        json.dumps({"type": "thread.started", "thread_id": "abc"}) + "\n",
        json.dumps({"type": "turn.completed", "usage": {"output_tokens": 1}}) + "\n",
    ])

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_review_thread(None, "project", "all", "")

    task_errors = [msg for msg in messages if msg["type"] == "task_error"]
    assert task_errors
    assert "without any user-visible response" in task_errors[0]["text"]
    assert any(msg["type"] == "unlock" for msg in messages)


def test_github_clone_repo_removes_origin_when_plain_url_restore_fails(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    target_dir = isolated_server["home_dir"] / "clone-target"
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "clone"]:
            target_dir.mkdir(parents=True, exist_ok=True)
            return DummyCompletedProcess(returncode=0)
        if args[:4] == ["git", "remote", "set-url", "origin"]:
            return DummyCompletedProcess(returncode=1, stderr="fatal: token=sk-secret-1234567890")
        if args[:3] == ["git", "remote", "remove"]:
            return DummyCompletedProcess(returncode=0)
        raise AssertionError(f"Unexpected git call: {args}")

    monkeypatch.setattr(github.Path, "home", classmethod(lambda cls: isolated_server["home_dir"]))
    monkeypatch.setattr(github, "_get_token", lambda: "sk-secret-1234567890")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    monkeypatch.setattr(github, "_build_cli_env", lambda: {})

    with pytest.raises(RuntimeError, match="origin remote was removed"):
        github.clone_repo("owner/repo", str(target_dir))

    assert calls[0][0:2] == ["git", "clone"]
    assert calls[1][0:4] == ["git", "remote", "set-url", "origin"]
    assert calls[2][0:3] == ["git", "remote", "remove"]


def test_codex_auto_review_returns_none_when_no_issues_found(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    process = DummyProcess(lines=[
        json.dumps({
            "type": "item.completed",
            "item": {"text": "## Files reviewed\n- app.py: Handles requests\n\n## Result\nNo issues found."},
        }) + "\n"
    ])

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))
    monkeypatch.setattr("threading.Thread", NoopThread)

    result = runtime._run_codex_auto_review(
        None,
        str(isolated_server["repo_dir"].resolve()),
        ["src/app.py"],
        messages.append,
    )

    assert result is None
    assert "Changed files: app.py" in process.stdin.buffer
    assert any(msg["type"] == "agent_status" and msg.get("status") == "All clear" for msg in messages)


def test_codex_auto_review_returns_findings_without_live_codex(isolated_server, monkeypatch: pytest.MonkeyPatch):
    messages: list[dict] = []
    process = DummyProcess(lines=[
        json.dumps({
            "type": "item.completed",
            "item": {"text": "## Findings\n- [HIGH] app.py: missing validation"},
        }) + "\n"
    ])

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))
    monkeypatch.setattr("threading.Thread", NoopThread)

    result = runtime._run_codex_auto_review(
        None,
        str(isolated_server["repo_dir"].resolve()),
        ["src/app.py"],
        messages.append,
    )

    assert result == "## Findings\n- [HIGH] app.py: missing validation"
    assert any(msg["type"] == "agent_activity" and msg.get("activity") == "finding" for msg in messages)
    assert any(
        msg["type"] == "agent_status" and "Findings detected -> Claude Code is fixing" in msg.get("status", "")
        for msg in messages
    )


def test_review_thread_emits_fix_suggestion_flow_without_live_clis(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    claude_commands: list[list[str]] = []
    state._set_chat_session(repo_key, "builder-session-before-review")
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-1",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Claude review output"}]},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "type": "item.completed",
                "item": {"text": "## Findings\n- [HIGH] api.py: race condition"},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-2",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "## Final fix plan\n- api.py:10: add a lock\nFIXES_NEEDED: true"}]},
            }) + "\n"
        ]),
    ]

    def popen(*args, **kwargs):
        claude_commands.append(list(args[0]))
        return processes.pop(0)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_review_thread(None, "custom", "bugs", "src/api.py")

    assert state._get_chat_session(repo_key) == "builder-session-before-review"
    assert runtime._pending_review_fixes[repo_key].startswith("## Final fix plan")
    assert "--resume" not in claude_commands[0]
    synthesis_cmd = claude_commands[-1]
    assert "--resume" in synthesis_cmd
    assert synthesis_cmd[synthesis_cmd.index("--resume") + 1] == "review-session-1"
    assert any(msg["type"] == "review_fixes_available" for msg in messages)
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Step 3/3 done" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_review_thread_clears_stale_fix_plan_when_review_finishes_cleanly(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    review_key = f"{repo_key}::manual_review"
    runtime._pending_review_fixes[repo_key] = "## Final fix plan\n- stale"
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-1",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "## Summary\nNo issues found."}]},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "type": "item.completed",
                "item": {"text": "## Findings\nNo issues found."},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-2",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "## Final fix plan\nNo fixes needed\nFIXES_NEEDED: false"}]},
            }) + "\n"
        ]),
    ]

    def popen(*args, **kwargs):
        return processes.pop(0)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_review_thread(None, "project", "all", "")

    assert repo_key not in runtime._pending_review_fixes
    assert state._get_chat_session(review_key) is None
    assert not any(msg["type"] == "review_fixes_available" for msg in messages)
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Step 3/3 done" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_review_thread_supports_reversed_pipeline_and_custom_focus(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    state._set_chat_session(repo_key, "builder-session-before-review")
    codex_processes: list[DummyProcess] = []
    claude_commands: list[list[str]] = []
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "type": "item.completed",
                "item": {"text": "## Findings\n- [HIGH] api.py: missing lock"},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-claude",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Claude second review output"}]},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "type": "item.completed",
                "item": {"text": "## Final fix plan\n- api.py:10: add a lock\nFIXES_NEEDED: true"},
            }) + "\n"
        ]),
    ]

    def popen(*args, **kwargs):
        process = processes.pop(0)
        command = list(args[0])
        if command[0] == "codex":
            codex_processes.append(process)
        else:
            claude_commands.append(command)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))
    state._update_settings(
        lambda settings: settings.update(
            {
                "review_first_reviewer": "codex",
                "review_second_reviewer": "claude",
            }
        )
    )

    runtime._run_review_thread(
        None,
        "custom",
        "custom",
        "src/api.py",
        "Race conditions in the websocket flow",
    )

    assert runtime._pending_review_fixes[repo_key].startswith("## Final fix plan")
    assert state._get_chat_session(repo_key) == "builder-session-before-review"
    assert any("Flow: Codex -> Claude Code -> Codex" in msg.get("text", "") for msg in messages if msg["type"] == "stream_text")
    assert any(msg["type"] == "review_fixes_available" for msg in messages)
    assert "Race conditions in the websocket flow" in codex_processes[0].stdin.buffer
    assert claude_commands and claude_commands[0][0] == "claude"


def test_review_thread_falls_back_to_second_reviewer_for_synthesis(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())

    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_append_timeline_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_get_chat_session", lambda *args, **kwargs: "review-session-1")
    monkeypatch.setattr(runtime, "_run_codex_manual_review_step", lambda **kwargs: "## Findings\n- [HIGH] api.py: missing lock")
    monkeypatch.setattr(runtime, "_run_claude_manual_review_step", lambda **kwargs: ("## Findings\n- [HIGH] api.py: missing lock", "review-session-2"))

    def fail_primary_synthesis(**kwargs):
        raise RuntimeError(
            "Review step 3 (synthesis) failed (exit code 1). Last output: stdout: You've hit your usage limit."
        )

    monkeypatch.setattr(runtime, "_run_codex_manual_synthesis_step", fail_primary_synthesis)
    monkeypatch.setattr(
        runtime,
        "_run_claude_manual_synthesis_step",
        lambda **kwargs: ("## Final fix plan\n- api.py:10: add a lock\nFIXES_NEEDED: true", True, "review-session-3"),
    )
    state._update_settings(
        lambda settings: settings.update(
            {
                "review_first_reviewer": "codex",
                "review_second_reviewer": "claude",
            }
        )
    )

    runtime._run_review_thread(None, "project", "bugs", "")

    assert runtime._pending_review_fixes[repo_key].startswith("## Final fix plan")
    assert any(msg["type"] == "review_fixes_available" for msg in messages)
    assert any(
        msg["type"] == "stream_text"
        and "Step 3 fallback" in msg.get("text", "")
        and "Claude Code will prepare the final report instead." in msg.get("text", "")
        for msg in messages
    )
    assert any(
        msg["type"] == "agent_status"
        and msg.get("agent") == "Claude Code"
        and msg.get("status") == "Step 3/3 - fallback synthesis in progress..."
        for msg in messages
    )
    assert not any(msg["type"] == "task_error" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_claude_manual_review_step_retries_once_after_nonzero_exit(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    processes = [
        DummyProcess(returncode=1, stderr_lines=["review stream crashed\n"]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-2",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "## Findings\n- [HIGH] api.py: missing lock"}]},
            }) + "\n"
        ]),
    ]

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: processes.pop(0))
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_append_timeline_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_run_codex_manual_review_step", lambda **kwargs: "## Findings\n- [HIGH] api.py: missing lock")
    monkeypatch.setattr(
        runtime,
        "_run_claude_manual_synthesis_step",
        lambda **kwargs: ("## Final fix plan\n- api.py:10: add a lock\nFIXES_NEEDED: true", True, "review-session-3"),
    )
    state._update_settings(
        lambda settings: settings.update(
            {
                "review_first_reviewer": "claude",
                "review_second_reviewer": "codex",
            }
        )
    )

    runtime._run_review_thread(None, "project", "bugs", "")

    assert runtime._pending_review_fixes[repo_key].startswith("## Final fix plan")
    assert any(
        msg["type"] == "stream_text"
        and "Claude Review stopped unexpectedly. OMADS retries once" in msg.get("text", "")
        for msg in messages
    )
    assert not any(msg["type"] == "task_error" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_claude_manual_synthesis_retries_once_after_nonzero_exit(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    processes = [
        DummyProcess(returncode=1, stderr_lines=["synthesis stream crashed\n"]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "review-session-3",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "## Final fix plan\n- api.py:10: add a lock\nFIXES_NEEDED: true"}]},
            }) + "\n"
        ]),
    ]

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: processes.pop(0))
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_append_timeline_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        runtime,
        "_run_claude_manual_review_step",
        lambda **kwargs: ("## Findings\n- [HIGH] api.py: missing lock", "review-session-2"),
    )
    monkeypatch.setattr(runtime, "_run_codex_manual_review_step", lambda **kwargs: "## Findings\n- [HIGH] api.py: missing lock")
    state._update_settings(
        lambda settings: settings.update(
            {
                "review_first_reviewer": "claude",
                "review_second_reviewer": "codex",
            }
        )
    )

    runtime._run_review_thread(None, "project", "bugs", "")

    assert runtime._pending_review_fixes[repo_key].startswith("## Final fix plan")
    assert any(
        msg["type"] == "stream_text"
        and "Review step 3 (synthesis) stopped unexpectedly. OMADS retries once" in msg.get("text", "")
        for msg in messages
    )
    assert not any(msg["type"] == "task_error" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_manual_synthesis_prompt_switches_to_limited_mode_for_incomplete_second_review():
    prompt = runtime._build_manual_synthesis_prompt(
        first_label="Codex",
        second_label="Claude Code",
        first_review="## Findings\n- [HIGH] websocket.py:61: race condition",
        second_review="(Claude Review incomplete: You've hit your limit · resets tomorrow)",
    )

    assert "Reviewer 2 was only partially available" in prompt
    assert "do NOT start a fresh full-code review" in prompt
    assert "Compare both reviews and produce a final report." not in prompt


def test_manual_synthesis_prompt_keeps_full_compare_mode_for_complete_second_review():
    prompt = runtime._build_manual_synthesis_prompt(
        first_label="Claude Code",
        second_label="Codex",
        first_review="## Findings\n- [HIGH] websocket.py:61: race condition",
        second_review="## Findings\n- [HIGH] websocket.py:61: race condition",
    )

    assert "Compare both reviews and produce a final report." in prompt
    assert "Reviewer 2 was only partially available" not in prompt


def test_claude_task_runs_fix_pass_after_auto_review_findings(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "session_id": "claude-session-1",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/app.py", "content": "print('hi')"}},
                        {"type": "text", "text": "Initial implementation done"},
                    ]
                },
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "claude-session-2",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Applied the requested fixes"}]},
            }) + "\n"
        ]),
    ]

    def popen(*args, **kwargs):
        return processes.pop(0)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_capture_repo_change_snapshot",
        lambda *args, **kwargs: {"status_lines": [], "changed_files": [], "diff_text": ""},
    )
    monkeypatch.setattr(runtime, "_run_codex_auto_review", lambda ws, target_repo, files_changed, send: "Fix this issue")

    runtime._run_claude_session_thread(None, "Implement a feature")

    assert state._get_chat_session(repo_key, purpose="builder:claude") == "claude-session-2"
    assert runtime._last_files_changed == ["src/app.py"]
    assert any(
        msg["type"] == "agent_status" and msg.get("status") == "Fixing Codex findings..."
        for msg in messages
    )
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Fixes applied" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_claude_fix_run_retries_once_after_nonzero_exit(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    repo_key = str(isolated_server["repo_dir"].resolve())
    popen_calls: list[list[str]] = []
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "session_id": "claude-session-1",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/app.py", "content": "print('hi')"}},
                        {"type": "text", "text": "Initial implementation done"},
                    ]
                },
            }) + "\n"
        ]),
        DummyProcess(returncode=1, stderr_lines=["network glitch while starting stream\n"]),
        DummyProcess(lines=[
            json.dumps({
                "session_id": "claude-session-2",
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Applied fixes after retry"}]},
            }) + "\n"
        ]),
    ]

    def popen(*args, **kwargs):
        popen_calls.append(list(args[0]))
        return processes.pop(0)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_capture_repo_change_snapshot",
        lambda *args, **kwargs: {"status_lines": [], "changed_files": [], "diff_text": ""},
    )
    monkeypatch.setattr(runtime, "_run_codex_auto_review", lambda ws, target_repo, files_changed, send: "Fix this issue")

    runtime._run_claude_session_thread(None, "Implement a feature")

    assert len(popen_calls) == 3
    assert state._get_chat_session(repo_key, purpose="builder:claude") == "claude-session-2"
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Retrying fix run..." for msg in messages)
    assert any(
        msg["type"] == "stream_text"
        and "retries once" in msg.get("text", "")
        for msg in messages
    )
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Fixes applied" for msg in messages)
    assert not any(msg["type"] == "task_error" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_claude_fix_run_final_failure_is_nonfatal_and_unlocks(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "session_id": "claude-session-1",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/app.py", "content": "print('hi')"}},
                        {"type": "text", "text": "Initial implementation done"},
                    ]
                },
            }) + "\n"
        ]),
        DummyProcess(returncode=1, stderr_lines=["transport disconnected\n"]),
        DummyProcess(returncode=1, stderr_lines=["transport disconnected again\n"]),
    ]

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: processes.pop(0))
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_capture_repo_change_snapshot",
        lambda *args, **kwargs: {"status_lines": [], "changed_files": [], "diff_text": ""},
    )
    monkeypatch.setattr(runtime, "_run_codex_auto_review", lambda ws, target_repo, files_changed, send: "Fix this issue")

    runtime._run_claude_session_thread(None, "Implement a feature")

    assert any(
        msg["type"] == "stream_text"
        and "could not finish" in msg.get("text", "")
        for msg in messages
    )
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Fix run incomplete" for msg in messages)
    assert not any(msg["type"] == "task_error" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_claude_task_uses_selected_permission_mode(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    commands: list[list[str]] = []
    process = DummyProcess(lines=[
        json.dumps({
            "session_id": "claude-session-permissions",
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Done"}]},
        }) + "\n"
    ])

    def popen(*args, **kwargs):
        commands.append(list(args[0]))
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_capture_repo_change_snapshot", lambda *args, **kwargs: {"status_lines": [], "changed_files": [], "diff_text": ""})
    state._update_settings(
        lambda settings: settings.update(
            {
                "claude_permission_mode": "plan",
                "auto_review": False,
            }
        )
    )

    runtime._run_claude_session_thread(None, "Implement a feature")

    claude_commands = [cmd for cmd in commands if cmd and cmd[0] == "claude"]
    assert claude_commands
    assert "--permission-mode" in claude_commands[0]
    assert claude_commands[0][claude_commands[0].index("--permission-mode") + 1] == "plan"
    assert any(msg["type"] == "unlock" for msg in messages)


def test_codex_task_runs_claude_breaker_and_fix_pass(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    processes = [
        DummyProcess(lines=[
            json.dumps({
                "type": "item.completed",
                "item": {"text": "Implemented the requested change."},
            }) + "\n"
        ]),
        DummyProcess(lines=[
            json.dumps({
                "type": "item.completed",
                "item": {"text": "Applied the requested fixes."},
            }) + "\n"
        ]),
    ]
    snapshots = iter([
        {"status_lines": [], "changed_files": [], "diff_text": ""},
        {
            "status_lines": [" M src/app.py"],
            "changed_files": ["src/app.py"],
            "diff_text": "diff --git a/src/app.py b/src/app.py\n+print('hi')\n",
        },
    ])

    def popen(*args, **kwargs):
        return processes.pop(0)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_capture_repo_change_snapshot", lambda *args, **kwargs: next(snapshots))
    monkeypatch.setattr(
        runtime,
        "_run_claude_auto_review",
        lambda target_repo, files_changed, send, model, effort: "Fix this issue",
    )
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_codex_session_thread(None, "Implement a Codex feature")

    assert runtime._last_files_changed == ["src/app.py"]
    assert any(
        msg["type"] == "agent_status" and msg.get("status") == "Fixing Claude findings..."
        for msg in messages
    )
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Fixes applied" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


def test_codex_task_runs_claude_breaker_from_file_change_events_without_git_repo(
    isolated_server,
    monkeypatch: pytest.MonkeyPatch,
):
    messages: list[dict] = []
    changed_file = str((isolated_server["repo_dir"] / "standalone.html").resolve())
    process = DummyProcess(lines=[
        json.dumps({"type": "thread.started", "thread_id": "abc"}) + "\n",
        json.dumps({
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": "Implemented the requested change."},
        }) + "\n",
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "file_change",
                "changes": [{"path": changed_file, "kind": "add"}],
                "status": "completed",
            },
        }) + "\n",
        json.dumps({"type": "turn.completed", "usage": {"output_tokens": 42}}) + "\n",
    ])
    review_calls: list[list[str]] = []

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(runtime, "_save_project_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_capture_repo_change_snapshot",
        lambda *args, **kwargs: {"status_lines": [], "changed_files": [], "diff_text": ""},
    )
    monkeypatch.setattr(
        runtime,
        "_run_claude_auto_review",
        lambda target_repo, files_changed, send, model, effort: review_calls.append(list(files_changed)) or None,
    )

    runtime._run_codex_session_thread(None, "Implement a Codex feature")

    assert review_calls == [[changed_file]]
    assert runtime._last_files_changed == [changed_file]
    assert any(msg["type"] == "unlock" for msg in messages)
