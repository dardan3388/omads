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
    └── state.py
        ↕
  Claude Code CLI + Codex CLI
```

OMADS is a local web GUI that orchestrates:

- **Claude Code CLI** as the builder agent
- **Codex CLI** as the review agent

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
- runtime status refresh
- health and status endpoints
- ledger and history reads
- frontend delivery at `/`

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
- Claude task runner
- review pipeline runner
- Codex auto-review runner

If behavior is tied to a running task, streaming, subprocess execution, or live GUI state, it probably belongs here.

### `src/omads/gui/launcher.py`

Owns local startup behavior:

- `uvicorn.run(...)`
- waiting for readiness
- opening the browser automatically

## Runtime Flow

### Chat Flow

1. The browser sends a `chat` message over WebSocket.
2. `websocket.py` validates the request and starts a background thread.
3. `runtime.py` starts `claude` as a subprocess with `stream-json`.
4. Claude events are converted into GUI stream events and broadcast back to the browser.
5. If Claude changed files and auto-review is enabled, Codex review may start automatically.

### Review Flow

1. The browser sends a `review` message.
2. `runtime.py` runs a three-step review:
- Claude review
- Codex review
- Claude synthesis of both results
3. If real fixes are identified, the review result is cached so the UI can offer an apply-fixes action.

## Persistence

Runtime data is stored below `~/.config/omads/`:

- `gui_settings.json`
- `gui_status.json`
- `projects.json`
- `chat_sessions.json`
- `history/`
- `memory/`

Project ledger data is stored under `data/ledger/`.

## Testing

The current smoke-test suite lives in `tests/test_gui_server.py`.

It covers:

- app startup and security headers
- settings and project validation
- log filtering and session persistence
- key Claude/review failure paths

The next highest-value test areas are:

- WebSocket behavior
- auto-review success and failure paths
- more UI reconnect and recovery scenarios

## Legacy Code

The `_legacy/` directory contains the earlier OMADS pipeline architecture. It is not the active source of truth for the GUI runtime.

## Documentation Rules

Use standard documentation files for standard jobs:

- `README.md` for onboarding
- `BACKLOG.md` for active work
- `CHANGELOG.md` for notable shipped changes
- `docs/architecture.md` for current structure and design intent

Do not reintroduce a separate narrative project diary that duplicates Git history.
