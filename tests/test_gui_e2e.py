from __future__ import annotations

import copy
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omads.gui import routes, runtime, state, websocket
from omads.gui.app import app

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def e2e_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    repo_dir = home_dir / "repo"
    repo_dir.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "OMADS Tests"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo_dir, check=True, capture_output=True, text=True)
    tracked = repo_dir / "tracked.txt"
    tracked.write_text("line one\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
    tracked.write_text("line one\nline two\n", encoding="utf-8")
    (repo_dir / "notes.txt").write_text("temporary note\n", encoding="utf-8")

    monkeypatch.setattr(state.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setattr(routes.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setattr(websocket.Path, "home", classmethod(lambda cls: home_dir))
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

    return {"home_dir": home_dir, "repo_dir": repo_dir}


@pytest.fixture()
def live_gui(e2e_env):
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base_url, timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("OMADS test server did not become ready in time")

    try:
        yield {"base_url": base_url, **e2e_env}
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture()
def page():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-dependent skip
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        context = browser.new_context()
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


@pytest.mark.e2e
def test_e2e_theme_toggle_and_diff_viewer(page, live_gui):
    page.goto(live_gui["base_url"])
    page.wait_for_function("() => document.getElementById('connBadge')?.textContent === 'Connected'")

    page.get_by_role("button", name="Settings").click()
    page.get_by_role("button", name="Interface").click()
    page.locator("#sTheme").select_option("light")
    page.get_by_role("button", name="Save").click()

    page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
    assert page.locator("#btnThemeToggle").text_content() == "Theme: Light"

    settings_data = state._load_config()
    assert settings_data["ui_theme"] == "light"

    page.get_by_role("button", name="Diff").click()
    page.wait_for_function("() => document.getElementById('diffModal')?.classList.contains('open')")
    page.wait_for_function("() => document.getElementById('diffContent')?.textContent.includes('diff --git')")

    assert "repo" in page.locator("#diffSubtitle").text_content()
    assert "tracked.txt" in page.locator("#diffStatus").text_content()
    assert "diff --git" in page.locator("#diffContent").text_content()


@pytest.mark.e2e
def test_e2e_builder_selection_persists_and_updates_badge(page, live_gui):
    page.goto(live_gui["base_url"])
    page.wait_for_function("() => document.getElementById('connBadge')?.textContent === 'Connected'")

    assert page.locator("#builderBadge").text_content() == "Builder: Claude"

    page.get_by_role("button", name="Settings").click()
    page.locator("#sBuilder").select_option("codex")
    page.get_by_role("button", name="Save").click()

    page.wait_for_function("() => document.getElementById('builderBadge')?.textContent === 'Builder: Codex'")

    settings_data = state._load_config()
    assert settings_data["builder_agent"] == "codex"
    assert page.locator("#builderBadge").text_content() == "Builder: Codex"


@pytest.mark.e2e
def test_e2e_chat_flow_updates_browser_via_websocket(page, live_gui, monkeypatch: pytest.MonkeyPatch):
    received_inputs: list[str] = []

    def fake_runner(ws, user_text: str):
        received_inputs.append(user_text)
        runtime.broadcast_sync({"type": "agent_status", "agent": "Claude Code", "status": "Working..."})
        runtime.broadcast_sync({"type": "stream_text", "agent": "Claude Code", "text": f"Echo: {user_text}"})
        runtime.broadcast_sync({"type": "unlock"})

    monkeypatch.setattr(runtime, "_run_claude_session_thread", fake_runner)

    page.goto(live_gui["base_url"])
    page.wait_for_function("() => document.getElementById('connBadge')?.textContent === 'Connected'")

    page.locator("#input").fill("Hello from browser test")
    page.get_by_role("button", name="Send").click()

    page.wait_for_function("() => !document.getElementById('btnReview')?.disabled")
    page.wait_for_function("() => document.body.innerText.includes('Echo: Hello from browser test')")

    assert received_inputs == ["Hello from browser test"]
    assert "Hello from browser test" in page.locator("#stream").text_content()
    assert "Echo: Hello from browser test" in page.locator("#stream").text_content()
    assert page.locator("#btnSend").is_visible()
