from __future__ import annotations

from click.testing import CliRunner

from omads.cli.main import cli
from omads.gui import launcher


def test_cli_gui_passes_no_browser_flag(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(
        "omads.gui.server.start_gui",
        lambda host="127.0.0.1", port=8080, open_browser=True: calls.append(
            {"host": host, "port": port, "open_browser": open_browser}
        ),
    )

    result = CliRunner().invoke(cli, ["gui", "--host", "0.0.0.0", "--port", "9090", "--no-browser"])

    assert result.exit_code == 0
    assert calls == [{"host": "0.0.0.0", "port": 9090, "open_browser": False}]


def test_should_open_browser_respects_env(monkeypatch):
    monkeypatch.setenv("OMADS_OPEN_BROWSER", "0")
    assert launcher.should_open_browser() is False
    assert launcher.should_open_browser(open_browser=False) is False

    monkeypatch.setenv("OMADS_OPEN_BROWSER", "true")
    assert launcher.should_open_browser() is True
