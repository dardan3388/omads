# Architecture

This document describes the current OMADS runtime architecture and where future work should go.

## Overview

```text
Browser (localhost:8080)
    ↕ REST + WebSocket
FastAPI App
    ├── routes.py
    ├── websocket.py
    ├── runtime.py
    ├── streaming.py
    └── state.py
        ↕
  Claude Code CLI + Codex CLI
```

OMADS is a local web GUI that orchestrates:

- **Claude Code CLI** or **Codex CLI** as the user-selected primary builder
- an automatic breaker step after builder-created code changes
- a separate configurable manual review pipeline where Reviewer 1 runs step 1 and step 3, while Reviewer 2 runs step 2

The browser interacts with the backend over REST and WebSocket. The backend starts CLI subprocesses, streams progress back to the UI, persists project-specific state, and records logs/history.

## Module Responsibilities

### `src/omads/gui/server.py`

Compatibility facade for the historic import path `omads.gui.server`.

Use this when:

- external code imports `app`
- the CLI starts the GUI with `from omads.gui.server import start_gui`

Do not add new business logic here unless it is explicitly compatibility-related.

### `src/omads/gui/app.py`

Owns:

- FastAPI app creation
- CORS configuration
- security headers
- router registration

### `src/omads/gui/routes.py`

Owns REST endpoints for:

- settings
- project registration and switching
- Git diff inspection for the active repository
- runtime status refresh
- health and status endpoints
- ledger and history reads
- frontend delivery at `/`

FastAPI's built-in docs also stay enabled at `/docs`, `/redoc`, and `/openapi.json`.

### `src/omads/gui/websocket.py`

Owns the main `/ws` socket:

- chat requests
- review requests
- fix application requests
- stop requests
- repo switching from the UI

This module should stay focused on GUI command handling and transport concerns.

### `src/omads/gui/state.py`

Owns durable and shared data helpers:

- settings load/save
- GUI status load/save
- project registry
- history and log persistence
- chat session persistence
- project memory persistence
- CLI environment building
- file locking and atomic writes

If data must survive process restarts or be shared through files, it probably belongs here.

### `src/omads/gui/runtime.py`

Owns runtime-only state and process orchestration:

- active connections
- active subprocess tracking
- broadcast helpers
- builder-task dispatch
- Claude task runner
- Codex builder task runner
- review pipeline runner
- automatic breaker runners for both builder paths

This module should call shared parsing helpers instead of duplicating stream-json or JSONL parsing logic inline.

### `src/omads/gui/streaming.py`

Owns reusable parsing helpers for:

- Claude Code `stream-json` lines
- Codex JSONL review output
- synthesis markers such as `FIXES_NEEDED`

If behavior is tied to a running task, streaming, subprocess execution, or live GUI state, it probably belongs here.

### `src/omads/gui/launcher.py`

Owns local startup behavior:

- `uvicorn.run(...)`
- waiting for readiness
- opening the browser automatically
- browserless startup for headless or container use

## Runtime Flow

### Chat Flow

1. The browser sends a `chat` message over WebSocket.
2. `websocket.py` validates the request and starts a background thread.
3. `runtime.py` routes the task to the selected primary builder from GUI settings.
4. The chosen builder subprocess streams readable events back to the browser.
5. If the builder changed files and auto-review is enabled, OMADS starts the current automatic breaker step.
6. Today that means `Codex` reviews Claude-built changes, while `Claude Review` checks Codex-built changes before findings are handed back to the builder.
7. If the breaker reports real findings, the active builder receives them for a follow-up fix decision.

### Review Flow

The `Review` button is an additional manual feature. It is not the default builder loop.

Use it when the user wants to:

- review an existing local project without starting a coding task
- manually trigger a full-project review
- review only the last task or a custom scope

Flow:

1. The browser sends a `review` message.
2. `runtime.py` runs a three-step review:
- Reviewer 1 review
- Reviewer 2 cross-check review
- Reviewer 1 synthesis of both results and the optional fix plan
3. If real fixes are identified, the review result is cached so the UI can offer an apply-fixes action.

## Persistence

Runtime data is stored below `~/.config/omads/`:

- `gui_settings.json`
- `gui_status.json`
- `projects.json`
- `chat_sessions.json`
- `timeline/` for the unified per-project event stream used by the chat view, live log, and compatibility history/log reads
- `history/` only as a legacy fallback for older local data
- `memory/`

Project ledger data is stored under `data/ledger/`.

## Testing

The current smoke-test suite lives in `tests/test_gui_server.py`.

It covers:

- app startup and security headers
- settings and project validation
- diff and OpenAPI route visibility
- CLI and launcher startup flags
- log filtering and session persistence
- builder selection and dispatch
- key Claude/review failure paths
- browser-driven E2E coverage for theme switching, builder switching, diff inspection, and the WebSocket chat flow

The next highest-value test areas are deeper browser-driven scenarios such as reconnect recovery, review dialogs, and multi-project switching.

## Legacy Code

The `_legacy/` directory contains the earlier OMADS pipeline architecture. It is not the active source of truth for the GUI runtime.

## Documentation Rules

Use standard documentation files for standard jobs:

- `README.md` for onboarding
- `BACKLOG.md` for active work
- `CHANGELOG.md` for notable shipped changes
- `docs/architecture.md` for current structure and design intent

Do not reintroduce a separate narrative project diary that duplicates Git history.
