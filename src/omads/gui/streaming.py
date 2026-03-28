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

    etype = event.get("type", "")

    def _normalize_codex_message(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text:
            return ""
        if text[:1] not in {"{", "["}:
            return text
        try:
            embedded = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return text
        if isinstance(embedded, dict):
            for key in ("detail", "message", "error"):
                detail = embedded.get(key)
                if isinstance(detail, str) and detail.strip():
                    return detail.strip()
        if isinstance(embedded, str) and embedded.strip():
            return embedded.strip()
        return text

    # Surface error messages (e.g. rate-limit, auth failures).
    # Codex emits both "error" and "turn.failed" with the same text; only use "error".
    if etype == "error":
        msg = _normalize_codex_message(event.get("message", ""))
        return [msg] if msg else []

    if etype != "item.completed":
        return []

    item = event.get("item", {})
    if item.get("type") == "error":
        msg = _normalize_codex_message(item.get("message", ""))
        return [msg] if msg else []

    text = item.get("text", "")
    text = text.strip() if isinstance(text, str) else ""
    return [text] if text else []


def extract_codex_changed_files(lines: list[str]) -> list[str]:
    """Extract changed file paths from Codex JSONL output while preserving order."""
    changed_files: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

        if event.get("type") != "item.completed":
            continue

        item = event.get("item", {})
        if item.get("type") != "file_change":
            continue

        changes = item.get("changes", [])
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue
            path = change.get("path", "")
            if isinstance(path, str) and path and path not in changed_files:
                changed_files.append(path)

    return changed_files


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
