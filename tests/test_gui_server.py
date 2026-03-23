from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omads.gui import routes, runtime, server, state, websocket


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
    def __init__(self, lines: list[str] | None = None, returncode: int = 0):
        self.stdout = DummyStream(lines)
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

    state._append_history(project_id, {"type": "user_input", "text": "hello"})
    state._append_log(project_id, {"type": "stream_text", "agent": "Claude", "text": "world"})
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
    assert timeline.json()[0]["type"] == "user_input"
    assert timeline.json()[1]["type"] == "stream_text"

    invalid_history = client.get("/api/projects/bad.id/history")
    invalid_logs = client.get("/api/projects/bad.id/logs")
    invalid_timeline = client.get("/api/projects/bad.id/timeline")
    invalid_delete = client.delete("/api/projects/bad.id")
    assert invalid_history.json()["error"] == "Invalid project ID"
    assert invalid_logs.json()["error"] == "Invalid project ID"
    assert invalid_timeline.json()["error"] == "Invalid project ID"
    assert invalid_delete.json()["error"] == "Invalid project ID"


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

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "apply_fixes"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert message["text"] == "No review fixes are available"

        ws_client.send_json({"type": "set_repo", "path": str(valid_repo)})
        message = ws_client.receive_json()
        assert message == {"type": "system", "text": f"Project: {valid_repo.resolve()}"}
        assert state._get_setting("target_repo") == str(valid_repo.resolve())

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


def test_builder_dispatch_uses_selected_primary_builder(isolated_server, monkeypatch: pytest.MonkeyPatch):
    called: list[tuple[str, str]] = []

    monkeypatch.setattr(runtime, "_run_claude_session_thread", lambda ws, text: called.append(("claude", text)))
    monkeypatch.setattr(runtime, "_run_codex_session_thread", lambda ws, text: called.append(("codex", text)))

    state._update_settings(lambda settings: settings.__setitem__("builder_agent", "claude"))
    runtime._run_builder_session_thread(None, "first task")

    state._update_settings(lambda settings: settings.__setitem__("builder_agent", "codex"))
    runtime._run_builder_session_thread(None, "second task")

    assert called == [("claude", "first task"), ("codex", "second task")]


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

    assert state._get_chat_session(repo_key) == "claude-session-2"
    assert runtime._last_files_changed == ["src/app.py"]
    assert any(
        msg["type"] == "agent_status" and msg.get("status") == "Fixing Codex findings..."
        for msg in messages
    )
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Fixes applied" for msg in messages)
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
