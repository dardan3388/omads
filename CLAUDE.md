# OMADS — Web GUI for Claude Code + Codex Auto-Review

## Project Overview

OMADS is a web GUI on port `8080` that orchestrates two AI agents:

- **Claude Code CLI** — builder agent for chat and code changes
- **Codex CLI** — read-only auto-reviewer after code changes

No API keys are required. Both CLIs run on top of existing subscriptions.

## Permissions

Claude has full working permissions in this repository:

- Create, edit, and delete files without asking first
- Install dependencies without asking first
- Run shell commands without asking first
- Make and implement reasonable decisions independently
- Ask the user only when there is a real blocker or a risky product decision

## Architecture

```text
Browser (localhost:8080)
    ↕ WebSocket + REST
FastAPI app
    ├── routes.py
    ├── websocket.py
    ├── runtime.py
    └── state.py
```

- `src/omads/gui/server.py` — stable compatibility facade for the traditional import path
- `src/omads/gui/app.py` — FastAPI app, middleware, and router assembly
- `src/omads/gui/routes.py` — REST endpoints
- `src/omads/gui/websocket.py` — WebSocket endpoint and GUI command handling
- `src/omads/gui/runtime.py` — broadcast helpers and Claude/Codex task runners
- `src/omads/gui/state.py` — persistent settings, project registry, GUI status, logs, sessions, memory
- `src/omads/gui/launcher.py` — local `uvicorn` startup and browser opening
- `src/omads/gui/frontend.html` — single-page UI without a frontend framework

For a fuller explanation, see `docs/architecture.md`.

## Tech Stack

- **Language:** Python 3.11+
- **Web:** FastAPI + Uvicorn + WebSockets
- **CLI:** Click for `omads gui`
- **Builder:** Claude CLI (`claude -p`, `stream-json`)
- **Reviewer:** Codex CLI (`codex exec`, `--json`, read-only)

## Repository Layout

```text
two agents/
├── AGENTS.md
├── BACKLOG.md
├── CHANGELOG.md
├── CLAUDE.md
├── README.md
├── PROJECT_RULES.md
├── docs/
│   └── architecture.md
├── src/omads/
│   ├── cli/main.py
│   ├── gui/
│   │   ├── app.py
│   │   ├── launcher.py
│   │   ├── routes.py
│   │   ├── runtime.py
│   │   ├── server.py
│   │   ├── state.py
│   │   ├── websocket.py
│   │   └── frontend.html
│   ├── dna/
│   └── utils/
├── data/
├── dna/
├── tests/
└── _legacy/
```

## Usage

```bash
cd "two agents"
source .venv/bin/activate

omads gui
# or: omads gui --port 9090

uvicorn omads.gui.server:app --host 0.0.0.0 --port 8080 --reload
```

## GUI Features

- Chat with Claude Code via live WebSocket streaming
- Automatic Codex review after code changes
- Real-time token tracking and activity streaming
- Claude rate-limit status and reset countdown
- Multi-project management
- Persistent settings and chat sessions
- Project history, logs, and memory summaries

## Persistent Data

Stored in `~/.config/omads/`:

- `gui_settings.json` — model, effort, permissions, Codex config
- `projects.json` — registered projects
- `chat_sessions.json` — Claude session IDs
- `history/` — project-specific task history and logs
- `memory/` — project-specific context summaries

## Important Rules

- Stay within the active GUI architecture.
- Prefer the simplest correct solution.
- After each meaningful change, update the relevant standard docs:
- `BACKLOG.md` for open work
- `CHANGELOG.md` for notable shipped changes
- `docs/architecture.md` for durable structural changes
- Do not bring back `PROJEKTPROTOKOLL.md`.
- Test your implementation after each meaningful change.

## Legacy

The `_legacy/` directory contains the earlier OMADS pipeline architecture
(director, builder, breaker, judge, ledger, and related modules). Those files
are archived and are not the active source of truth for the GUI.
