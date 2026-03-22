# CLAUDE.md

This file exists as the Claude Code specific entrypoint for this repository.

It is intentionally short. It is not a second source of truth and it must not
drift into a parallel project manual.

## Priority Order

When working in this repository, use these files in this order:

1. `AGENTS.md` — binding workflow rules for all coding agents
2. `PROJECT_RULES.md` — repository-specific collaboration rules
3. `BACKLOG.md` — active priorities and next tasks
4. `docs/architecture.md` — current structure and module boundaries
5. `CHANGELOG.md` — notable shipped changes
6. `README.md` — onboarding and local startup

If anything in this file appears to conflict with the files above, the files
above win.

## Why This File Exists

- Claude Code reads `CLAUDE.md` natively as project context.
- OMADS also loads this file into project memory for Claude sessions.
- Keeping this file small avoids duplicated instructions and reduces drift.

## Repository Guidance For Claude

- Stay within the active GUI architecture under `src/omads/gui/`.
- Prefer small, targeted changes over broad rewrites.
- Do not reintroduce `PROJEKTPROTOKOLL.md` or any hidden project diary.
- Keep project-facing documentation in English.
- After meaningful changes, update the standard docs instead of expanding this file.

## Practical Pointers

- Start OMADS with `./start-omads.sh` or `omads gui` inside the project venv.
- The backend compatibility facade is `src/omads/gui/server.py`.
- Core behavior lives in `app.py`, `routes.py`, `websocket.py`, `runtime.py`,
  `state.py`, and `launcher.py`.
