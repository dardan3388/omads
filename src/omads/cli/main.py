"""OMADS CLI — Einstiegspunkt für die Web-GUI."""

from __future__ import annotations

import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """OMADS — Web-GUI für Claude Code + Codex Auto-Review.

    Starte mit: omads gui
    """
    if ctx.invoked_subcommand is None:
        # Ohne Subcommand: direkt GUI starten
        ctx.invoke(gui)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Server-Host")
@click.option("--port", "-p", default=8080, help="Server-Port")
def gui(host: str, port: int):
    """Startet die OMADS Web-GUI im Browser."""
    from omads.gui.server import start_gui
    start_gui(host=host, port=port)


def _format_tool_use(tool_name: str, tool_input: dict) -> str:
    """Formatiert einen Tool-Aufruf als lesbaren Text für die Live-Anzeige.

    Wird von server.py importiert für die WebSocket-Streaming-Anzeige.
    """
    if tool_name == "Read":
        path = tool_input.get("file_path", "?")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"Lese {short}"
    elif tool_name == "Write":
        path = tool_input.get("file_path", "?")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"Schreibe {short}"
    elif tool_name == "Edit":
        path = tool_input.get("file_path", "?")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"Bearbeite {short}"
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "?")
        return f"$ {cmd[:80]}"
    elif tool_name == "Glob":
        pattern = tool_input.get("pattern", "?")
        return f"Suche {pattern}"
    elif tool_name == "Grep":
        pattern = tool_input.get("pattern", "?")
        return f"Suche nach: {pattern}"
    else:
        return f"{tool_name}(...)"


if __name__ == "__main__":
    cli()
