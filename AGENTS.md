# AGENTS.md

Central rules for all coding agents working in this repository. This file is binding for Claude Code, Codex, and any other agent.

## Core Principle

- Git is the single source of truth for changes.
- All agents work on the same project state.
- Existing or parallel changes must never be overwritten accidentally.
- The current project state must be checked before starting work.

## Required Startup Checklist

Before any substantive work, always do the following in this order:

1. Read `AGENTS.md`.
2. Read `PROJECT_RULES.md` if it exists.
3. Read `BACKLOG.md` to understand active priorities.
4. Check `git status`.
5. Check `git log --oneline --decorate -5` to understand recent changes.
6. Check `git diff` before editing files.

If the task touches architecture, backend flow, or module boundaries, also read `docs/architecture.md`.

If you need recent shipped context, use `CHANGELOG.md` and Git history instead of inventing an internal project diary.

## Git Setup

- The repository must be initialized.
- The default branch is `main`.
- If no remote is configured yet, create a private GitHub repository and connect it.
- Never commit sensitive data.

## Parallel Agent Workflow

- Always assume that other agents may be working in the same repository at the same time.
- Never revert or overwrite someone else's changes without explicit approval.
- Use Git actively before editing:
- `git status`
- `git diff`
- `git log`

## Sync Rule

- After meaningful code changes, automatically sync without asking:
- `git add .`
- `git commit -m "<clear, meaningful description>"`
- `git push`
- Do not wait for explicit confirmation — push immediately after important changes.

## .gitignore / Security

The following patterns must exist or be kept in `.gitignore`:

- Python: `__pycache__/`, `*.pyc`, `venv/`, `.env`
- Node: `node_modules/`
- OS: `.DS_Store`, `Thumbs.db`
- Logs: `*.log`
- Secrets: `.env*`, `secrets.*`

Never commit:

- Passwords
- API keys
- Any other sensitive data

## Documentation Rules

- `BACKLOG.md` is the single source of truth for open work.
- `CHANGELOG.md` records notable shipped changes.
- `docs/architecture.md` explains the current structure and technical rationale.
- `CLAUDE.md` may exist as a Claude-native entrypoint, but it must stay short and defer to the standard repository docs.
- Do not reintroduce `PROJEKTPROTOKOLL.md` or any hidden project diary.
- Git explains who changed what and when; docs explain why and how the system is meant to work.

## Working Style

- Never break or restructure the existing architecture. New features must fit into the current module structure as documented in `docs/architecture.md`. If a task would require architectural changes, stop and discuss with the user first.
- Prefer small, targeted changes over broad rewrites.
- Understand the current diff before editing.
- Touch only files required for the current task.
- Surface unclear conflicts instead of guessing.
- Keep project-facing documentation in English unless the user explicitly requests otherwise.

## Goal

- Minimum effort for the user
- One consistent state for all agents
- Clean, secure, and understandable history
