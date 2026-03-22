"""Helpers for parsing Claude Code and Codex streaming output."""

from __future__ import annotations

import json
from typing import Any

from omads.cli.main import _format_tool_use


def _build_tool_detail(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Return a compact tool detail string for the live UI."""
    if tool_name == "Edit":
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        if old and new:
            return f"--- old ---\n{old}\n--- new ---\n{new}"
    if tool_name == "Write":
        return tool_input.get("content", "")
    if tool_name == "Bash":
        return tool_input.get("command", "")
    if tool_name == "Read":
        return tool_input.get("file_path", "")
    if tool_name in {"Glob", "Grep"}:
        return tool_input.get("pattern", "")
    return ""


def parse_claude_stream_line(line: str) -> list[dict[str, Any]]:
    """Normalize one Claude stream-json line into UI-friendly events."""
    stripped = line.strip()
    if not stripped:
        return []

    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    parsed: list[dict[str, Any]] = []
    if event.get("session_id"):
        parsed.append({"kind": "session_id", "session_id": event["session_id"]})

    event_type = event.get("type", "")
    if event_type == "assistant":
        message = event.get("message", {})
        content_blocks = message.get("content", []) if isinstance(message, dict) else []
        for block in content_blocks:
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_name = block.get("name", "?")
                tool_input = block.get("input", {})
                parsed.append({
                    "kind": "tool",
                    "tool": tool_name,
                    "file_path": tool_input.get("file_path", ""),
                    "description": _format_tool_use(tool_name, tool_input),
                    "detail": _build_tool_detail(tool_name, tool_input),
                })
            elif block_type == "text":
                text = block.get("text", "").strip()
                if text:
                    parsed.append({"kind": "text", "text": text})
            elif block_type == "thinking":
                thinking = block.get("thinking", "").strip()
                if thinking:
                    parsed.append({"kind": "thinking", "text": f"[Thinking: {len(thinking)} chars]"})
    elif event_type == "user":
        message = event.get("message", {})
        content_blocks = message.get("content", []) if isinstance(message, dict) else []
        for block in content_blocks:
            if block.get("type") != "tool_result":
                continue
            result_content = block.get("content", "")
            if isinstance(result_content, str) and result_content.strip():
                parsed.append({
                    "kind": "tool_result",
                    "text": result_content,
                    "is_error": bool(block.get("is_error", False)),
                })
    elif event_type == "result":
        parsed.append({"kind": "result", "text": event.get("result", "")})
    elif event_type == "rate_limit_event":
        rate_limit_info = event.get("rate_limit_info", {})
        if rate_limit_info:
            parsed.append({"kind": "rate_limit", "rate_limit_info": rate_limit_info})

    return parsed


def parse_codex_jsonl_line(line: str) -> list[str]:
    """Extract human-readable text payloads from one Codex JSONL line."""
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return []

    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [stripped] if stripped.strip() else []

    if event.get("type") != "item.completed":
        return []

    text = event.get("item", {}).get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    return [text] if text else []


def strip_fixes_needed_marker(text: str) -> tuple[str, bool]:
    """Remove the trailing FIXES_NEEDED marker and return whether fixes exist."""
    cleaned_lines: list[str] = []
    has_fixes = False
    for line in text.splitlines():
        normalized = line.strip().lower()
        if normalized == "fixes_needed: true":
            has_fixes = True
            continue
        if normalized == "fixes_needed: false":
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip(), has_fixes
