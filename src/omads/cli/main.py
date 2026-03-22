"""OMADS CLI - entry point for the web GUI."""

from __future__ import annotations

import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """OMADS - web GUI for Claude Code + Codex auto-review.

    Start with: omads gui
    """
    if ctx.invoked_subcommand is None:
        # Start the GUI directly when no subcommand is provided.
        ctx.invoke(gui)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Server host")
@click.option("--port", "-p", default=8080, help="Server port")
def gui(host: str, port: int):
    """Start the OMADS web GUI in the browser."""
    from omads.gui.server import start_gui
    start_gui(host=host, port=port)


def _format_tool_use(tool_name: str, tool_input: dict) -> str:
    """Format a tool call as readable text for the live UI.

    Imported by server.py for WebSocket streaming display.
    """
    if tool_name == "Read":
        path = tool_input.get("file_path", "?")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"Read {short}"
    elif tool_name == "Write":
        path = tool_input.get("file_path", "?")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"Write {short}"
    elif tool_name == "Edit":
        path = tool_input.get("file_path", "?")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"Edit {short}"
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "?")
        return f"$ {cmd[:80]}"
    elif tool_name == "Glob":
        pattern = tool_input.get("pattern", "?")
        return f"Search {pattern}"
    elif tool_name == "Grep":
        pattern = tool_input.get("pattern", "?")
        return f"Search for: {pattern}"
    else:
        return f"{tool_name}(...)"


if __name__ == "__main__":
    cli()
