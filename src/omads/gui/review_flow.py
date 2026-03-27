"""Review-specific helpers and subprocess runners for the OMADS GUI."""

from __future__ import annotations

import select
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .streaming import strip_fixes_needed_marker


@dataclass(slots=True)
class ReviewRuntimeContext:
    """Small adapter surface that keeps review helpers decoupled from runtime.py."""

    build_cli_env: Callable[[], dict[str, str]]
    build_process_failure_text: Callable[..., str]
    forward_claude_stream_line: Callable[..., tuple[str | None, str]]
    forward_codex_stream_line: Callable[..., None]
    get_settings_snapshot: Callable[[], dict]
    is_task_cancelled: Callable[[], bool]
    load_project_memory: Callable[[str], str]
    process_started: Callable[[subprocess.Popen], None]
    process_finished: Callable[[], None]
    store_review_session: Callable[[str, str], None]


def review_display_name(agent: str) -> str:
    """Return one human-readable agent name for pipeline descriptions."""
    return "Codex" if agent == "codex" else "Claude Code"


def review_runtime_label(agent: str, *, synthesis: bool = False) -> str:
    """Return one runtime label for streamed review status updates."""
    if agent == "codex":
        return "Codex" if synthesis else "Codex Review"
    return "Claude Code" if synthesis else "Claude Review"


def review_focus_description(focus: str, custom_focus: str) -> str:
    """Return one readable review focus description."""
    focus_map = {
        "all": "Security, bugs, error handling, performance",
        "security": "Security issues (injection, XSS, secrets, auth)",
        "bugs": "Logic bugs, race conditions, edge cases",
        "performance": "Performance issues, memory leaks, inefficient algorithms",
    }
    if focus == "custom" and custom_focus.strip():
        return custom_focus.strip()
    return focus_map.get(focus, focus_map["all"])


def _review_output_is_limited(review_text: str) -> bool:
    text = review_text.strip().lower()
    return text.startswith("(") and ("incomplete:" in text or "unavailable" in text)


def build_manual_synthesis_prompt(
    *,
    first_label: str,
    second_label: str,
    first_review: str,
    second_review: str,
) -> str:
    """Build the final synthesis prompt for the manual three-step review flow."""
    limited_second_review = _review_output_is_limited(second_review)
    prompt_parts = [
        f"You were Reviewer 1 ({first_label}) in this manual review flow.",
        f"Reviewer 2 ({second_label}) independently reviewed the same scope.",
    ]
    if limited_second_review:
        prompt_parts.append(
            f"Reviewer 2 was only partially available, so treat its output as limited context instead of a full second review."
        )
    else:
        prompt_parts.append("Compare both reviews and produce a final report.")

    prompt_parts.append("")
    prompt_parts.append(f"=== REVIEWER 1 ({first_label}) ===\n{first_review[:6000]}")
    prompt_parts.append("")
    prompt_parts.append(f"=== REVIEWER 2 ({second_label}) ===\n{second_review[:6000]}")
    prompt_parts.append("")

    if limited_second_review:
        prompt_parts.extend(
            [
                "Task:",
                "1. Preserve any solid findings from Reviewer 1.",
                f"2. Mention that {second_label} was incomplete or unavailable where relevant.",
                "3. Keep the final report concise and do NOT start a fresh full-code review.",
                "4. Build a prioritized list of only the REAL findings that should still be fixed.",
                "   Ignore false positives, restatements, and style-only remarks.",
                "",
            ]
        )
    else:
        prompt_parts.extend(
            [
                "Task:",
                "1. What did both reviews find (overlap)?",
                f"2. What did only {second_label} find that {first_label} missed?",
                f"3. What did only {first_label} find?",
                "4. Build a prioritized list of all REAL findings that should be fixed.",
                "   Ignore false positives and overly minor style remarks.",
                "",
            ]
        )

    prompt_parts.extend(
        [
            "Respond with:",
            "## Overlap (found by both)" if not limited_second_review else "## Overlap / confirmed findings",
            f"## Found only by {second_label}",
            f"## Found only by {first_label}",
            "## Final fix plan",
            "For each fix: file, line, and exactly what should be changed.",
            "",
            "IMPORTANT: Do NOT make any changes, only analyze. Respond in the same language the user writes in.",
            "",
            "REQUIRED: As the VERY LAST line of your answer, write exactly one of these markers:",
            "FIXES_NEEDED: true",
            "or",
            "FIXES_NEEDED: false",
        ]
    )
    return "\n".join(prompt_parts)


def run_claude_manual_review_step(
    ctx: ReviewRuntimeContext,
    *,
    target_repo: str,
    model: str,
    effort: str,
    focus_desc: str,
    file_hint: str,
    agent_label: str,
    repo_key: str,
    send: Callable[[dict], None],
    prior_session_id: str | None = None,
    rate_limit_source: str = "review_stream",
) -> tuple[str, str | None]:
    """Run one Claude-based manual review step and return text plus session ID."""
    captured_session_id: str | None = None
    project_memory = ctx.load_project_memory(target_repo)
    review_context = (
        "You are performing a code review inside OMADS. "
        "Read and analyze the code, but do NOT edit anything. "
        "Respond in the same language the user writes in.\n\n"
    )
    if project_memory:
        review_context += f"Project context:\n{project_memory}\n\n"

    review_prompt = (
        f"Perform a thorough code review. Focus: {focus_desc}.{file_hint}\n\n"
        "Respond with a structured analysis:\n"
        "## Summary\n## Findings (sorted by severity: CRITICAL > HIGH > MEDIUM)\n"
        "## Positive notes\n\nRespond in the same language the user writes in."
    )

    cmd = [
        "claude",
        "-p",
        review_prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--effort",
        effort,
        "--append-system-prompt",
        review_context,
    ]
    if prior_session_id:
        cmd.extend(["--resume", prior_session_id])

    output_lines: list[str] = []
    final_result = ""

    if ctx.is_task_cancelled():
        return "", captured_session_id

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=target_repo,
        env=ctx.build_cli_env(),
    )
    ctx.process_started(process)

    for line in process.stdout:
        if ctx.is_task_cancelled():
            process.kill()
            process.wait()
            ctx.process_finished()
            return "", captured_session_id
        parsed_session_id, parsed_result = ctx.forward_claude_stream_line(
            line,
            agent_label=agent_label,
            send=send,
            text_buffer=output_lines,
            rate_limit_source=rate_limit_source,
        )
        if parsed_session_id and not captured_session_id:
            captured_session_id = parsed_session_id
        if parsed_result:
            final_result = parsed_result

    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    finally:
        ctx.process_finished()

    review_text = final_result if final_result else "\n".join(output_lines).strip()
    if process.returncode != 0 and not ctx.is_task_cancelled():
        raise RuntimeError(
            ctx.build_process_failure_text(
                agent_label,
                process.returncode,
                result_text=review_text,
                output_lines=output_lines,
            )
        )
    if captured_session_id and process.returncode == 0:
        ctx.store_review_session(repo_key, captured_session_id)
    return review_text, captured_session_id


def run_codex_manual_review_step(
    ctx: ReviewRuntimeContext,
    *,
    target_repo: str,
    focus_desc: str,
    review_files: list[str],
    agent_label: str,
    step_name: str,
    send: Callable[[dict], None],
) -> str:
    """Run one Codex-based manual review step and return its text."""
    settings_snapshot = ctx.get_settings_snapshot()
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)

    review_prompt = (
        "You are a code reviewer. Perform a thorough review.\n"
        f"Focus: {focus_desc}\n"
    )
    if review_files:
        review_prompt += f"Files: {', '.join(f.rsplit('/', 1)[-1] for f in review_files[:15])}\n"
    review_prompt += (
        "\nRespond with:\n## Files reviewed\n## Findings\n"
        "- [CRITICAL/HIGH/MEDIUM] file:line: description\n## Positive notes\n\n"
        "Respond in the same language the user writes in."
    )

    cmd = ["codex", "exec", "-s", "read-only", "--ephemeral", "--skip-git-repo-check", "--json", "-C", str(target_repo)]
    if codex_model:
        cmd.extend(["-m", codex_model])
    if codex_reasoning:
        cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
    if codex_fast:
        cmd.extend(["-c", 'service_tier="fast"'])
    cmd.append("-")

    if ctx.is_task_cancelled():
        return ""

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=str(target_repo),
        env=ctx.build_cli_env(),
    )
    ctx.process_started(process)

    process.stdin.write(review_prompt)
    process.stdin.close()

    output_lines: list[str] = []
    inactivity_limit = 900
    last_output_time = time.time()
    while True:
        if ctx.is_task_cancelled():
            process.kill()
            process.wait()
            ctx.process_finished()
            return ""
        if time.time() - last_output_time > inactivity_limit:
            process.kill()
            process.wait()
            ctx.process_finished()
            raise RuntimeError(f"{step_name} stopped after 15 minutes without output.")
        ready, _, _ = select.select([process.stdout], [], [], 5.0)
        if ready:
            line = process.stdout.readline()
            if not line:
                break
            last_output_time = time.time()
            ctx.forward_codex_stream_line(
                line,
                agent_label=agent_label,
                send=send,
                text_buffer=output_lines,
            )

    process.wait(timeout=10)
    ctx.process_finished()

    review_text = "\n".join(output_lines).strip()
    if process.returncode != 0 and not ctx.is_task_cancelled():
        raise RuntimeError(
            ctx.build_process_failure_text(
                step_name,
                process.returncode,
                result_text=review_text,
                output_lines=output_lines,
            )
        )
    return review_text


def run_claude_manual_synthesis_step(
    ctx: ReviewRuntimeContext,
    *,
    target_repo: str,
    model: str,
    effort: str,
    repo_key: str,
    send: Callable[[dict], None],
    prior_session_id: str | None,
    first_label: str,
    second_label: str,
    first_review: str,
    second_review: str,
) -> tuple[str, bool, str | None]:
    """Run one Claude synthesis step for manual review."""
    synthesis_prompt = build_manual_synthesis_prompt(
        first_label=first_label,
        second_label=second_label,
        first_review=first_review,
        second_review=second_review,
    )
    synthesis_context = (
        "You are the final reviewer in OMADS. "
        "Compare both reviews honestly, keep only real findings, and prepare a user-facing fix plan. "
        "Do NOT make code changes.\n"
    )

    cmd = [
        "claude",
        "-p",
        synthesis_prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--effort",
        effort,
        "--append-system-prompt",
        synthesis_context,
    ]
    if prior_session_id:
        cmd.extend(["--resume", prior_session_id])

    output_lines: list[str] = []
    captured_session_id: str | None = None
    final_result = ""

    if ctx.is_task_cancelled():
        return "", False, None

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=target_repo,
        env=ctx.build_cli_env(),
    )
    ctx.process_started(process)

    for line in process.stdout:
        if ctx.is_task_cancelled():
            process.kill()
            process.wait()
            ctx.process_finished()
            return "", False, captured_session_id
        parsed_session_id, parsed_result = ctx.forward_claude_stream_line(
            line,
            agent_label="Claude Code",
            send=send,
            text_buffer=output_lines,
            rate_limit_source="synthesis_stream",
        )
        if parsed_session_id and not captured_session_id:
            captured_session_id = parsed_session_id
        if parsed_result:
            final_result = parsed_result

    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    finally:
        ctx.process_finished()

    synthesis_text = final_result if final_result else "\n".join(output_lines).strip()
    if process.returncode != 0 and not ctx.is_task_cancelled():
        raise RuntimeError(
            ctx.build_process_failure_text(
                "Review step 3 (synthesis)",
                process.returncode,
                result_text=synthesis_text,
                output_lines=output_lines,
            )
        )
    if captured_session_id and process.returncode == 0:
        ctx.store_review_session(repo_key, captured_session_id)
    cleaned_text, has_fixes = strip_fixes_needed_marker(synthesis_text)
    return cleaned_text, has_fixes, captured_session_id


def run_codex_manual_synthesis_step(
    ctx: ReviewRuntimeContext,
    *,
    target_repo: str,
    first_label: str,
    second_label: str,
    first_review: str,
    second_review: str,
    send: Callable[[dict], None],
) -> tuple[str, bool]:
    """Run one Codex synthesis step for manual review."""
    settings_snapshot = ctx.get_settings_snapshot()
    codex_model = settings_snapshot.get("codex_model", "")
    codex_reasoning = settings_snapshot.get("codex_reasoning", "high")
    codex_fast = settings_snapshot.get("codex_fast", False)

    synthesis_prompt = build_manual_synthesis_prompt(
        first_label=first_label,
        second_label=second_label,
        first_review=first_review,
        second_review=second_review,
    )

    cmd = ["codex", "exec", "-s", "read-only", "--ephemeral", "--skip-git-repo-check", "--json", "-C", str(target_repo)]
    if codex_model:
        cmd.extend(["-m", codex_model])
    if codex_reasoning:
        cmd.extend(["-c", f'model_reasoning_effort="{codex_reasoning}"'])
    if codex_fast:
        cmd.extend(["-c", 'service_tier="fast"'])
    cmd.append("-")

    if ctx.is_task_cancelled():
        return "", False

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=str(target_repo),
        env=ctx.build_cli_env(),
    )
    ctx.process_started(process)

    process.stdin.write(synthesis_prompt)
    process.stdin.close()

    output_lines: list[str] = []
    inactivity_limit = 900
    last_output_time = time.time()
    while True:
        if ctx.is_task_cancelled():
            process.kill()
            process.wait()
            ctx.process_finished()
            return "", False
        if time.time() - last_output_time > inactivity_limit:
            process.kill()
            process.wait()
            ctx.process_finished()
            raise RuntimeError("Review step 3 (synthesis) stopped after 15 minutes without output.")
        ready, _, _ = select.select([process.stdout], [], [], 5.0)
        if ready:
            line = process.stdout.readline()
            if not line:
                break
            last_output_time = time.time()
            ctx.forward_codex_stream_line(
                line,
                agent_label="Codex",
                send=send,
                text_buffer=output_lines,
            )

    process.wait(timeout=10)
    ctx.process_finished()

    synthesis_text = "\n".join(output_lines).strip()
    if process.returncode != 0 and not ctx.is_task_cancelled():
        raise RuntimeError(
            ctx.build_process_failure_text(
                "Review step 3 (synthesis)",
                process.returncode,
                result_text=synthesis_text,
                output_lines=output_lines,
            )
        )
    cleaned_text, has_fixes = strip_fixes_needed_marker(synthesis_text)
    return cleaned_text, has_fixes
