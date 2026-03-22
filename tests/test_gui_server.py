from __future__ import annotations

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
    def __init__(self, stdout: str = "", stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr


class NoopThread:
    def __init__(self, target=None, args=(), daemon=None, kwargs=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


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

    history = client.get(f"/api/projects/{project_id}/history")
    logs = client.get(f"/api/projects/{project_id}/logs")
    assert history.status_code == 200
    assert logs.status_code == 200
    assert history.json()[0]["text"] == "hello"
    assert logs.json()[0]["text"] == "world"

    invalid_history = client.get("/api/projects/bad.id/history")
    invalid_logs = client.get("/api/projects/bad.id/logs")
    invalid_delete = client.delete("/api/projects/bad.id")
    assert invalid_history.json()["error"] == "Invalid project ID"
    assert invalid_logs.json()["error"] == "Invalid project ID"
    assert invalid_delete.json()["error"] == "Invalid project ID"


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
    assert started_threads[0].target is runtime._run_claude_session_thread
    assert started_threads[0].args[1] == "first"


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


def test_websocket_rejects_new_work_while_task_is_running(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "_active_process", BusyProcess())

    with client.websocket_connect("/ws") as ws_client:
        ws_client.send_json({"type": "review", "scope": "project", "focus": "all"})
        message = ws_client.receive_json()
        assert message["type"] == "error"
        assert "A task is already running" in message["text"]


def test_runtime_status_refresh_and_busy_guards(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sent_events: list[dict] = []

    async def fake_broadcast(msg):
        sent_events.append(msg)

    monkeypatch.setattr(runtime, "broadcast", fake_broadcast)
    monkeypatch.setattr(routes, "_probe_claude_limit_status", lambda repo: {"status": "allowed", "last_checked": 1})
    monkeypatch.setattr(routes, "_probe_codex_status", lambda repo: {"text": "Codex OK", "last_checked": 2})

    claude = client.post("/api/runtime-status/claude/refresh")
    codex = client.post("/api/runtime-status/codex/refresh")
    assert claude.status_code == 200
    assert codex.status_code == 200
    assert claude.json()["limit"]["status"] == "allowed"
    assert codex.json()["codex_status"]["text"] == "Codex OK"
    assert any(msg["type"] == "claude_limit_update" for msg in sent_events)
    assert any(msg["type"] == "codex_status_update" for msg in sent_events)

    monkeypatch.setattr(runtime, "_active_process", BusyProcess())
    busy_claude = client.post("/api/runtime-status/claude/refresh")
    busy_codex = client.post("/api/runtime-status/codex/refresh")
    assert "Please wait until the current task finishes" in busy_claude.json()["error"]
    assert "Please wait until the current task finishes" in busy_codex.json()["error"]


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
    runtime_status = client.get("/api/runtime-status")

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

    assert runtime_status.status_code == 200
    assert runtime_status.json()["claude_limit"] == state._GUI_STATUS_DEFAULTS["claude_limit"]
    assert runtime_status.json()["codex_status"] == state._GUI_STATUS_DEFAULTS["codex_status"]


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
        return processes.pop(0)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime, "broadcast_sync", lambda msg, proj_id_override=None: messages.append(msg))
    monkeypatch.setattr(runtime, "_build_cli_env", lambda: {})
    monkeypatch.setattr(runtime, "_load_project_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr("select.select", lambda read, write, err, timeout: (read, write, err))

    runtime._run_review_thread(None, "custom", "bugs", "src/api.py")

    assert state._get_chat_session(repo_key) == "review-session-2"
    assert runtime._pending_review_fixes[repo_key].startswith("## Final fix plan")
    assert any(msg["type"] == "review_fixes_available" for msg in messages)
    assert any(msg["type"] == "agent_status" and msg.get("status") == "Step 3/3 done" for msg in messages)
    assert any(msg["type"] == "unlock" for msg in messages)


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
