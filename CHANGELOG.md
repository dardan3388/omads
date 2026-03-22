# Changelog

All notable changes to this project are documented in this file.

The format is loosely based on Keep a Changelog.

## Unreleased

### Added

- Added `start-omads.sh` as a one-command local launcher for Linux/macOS.
- Added `start-omads.ps1` as a Windows-friendly one-command launcher.
- Added `python -m omads` support through `src/omads/__main__.py`.
- Added mock-based tests for WebSocket guardrails, Codex auto-review outcomes, review fix suggestions, and Claude/Codex handoff flows without requiring live CLI access.
- Added a basic Docker image and `.dockerignore` for headless/container startup.

### Changed

- Replaced the custom `PROJEKTPROTOKOLL.md` project diary with standard English documentation.
- Added `docs/architecture.md` as the durable reference for backend structure and module boundaries.
- Standardized the main repository documentation in English for broader external use.
- Standardized the GUI labels, onboarding copy, review flow messages, and API-facing project errors in English.
- Clarified the README start flow with explicit quick-start and troubleshooting guidance.
- Reduced `CLAUDE.md` to a minimal Claude-specific bridge file that delegates to the standard repository docs.
- Added an explicit browserless startup mode for headless and container use.

## 2026-03-22

### Changed

- Split the GUI backend monolith into focused modules:
- `server.py` is now a compatibility facade
- `app.py` owns FastAPI assembly
- `routes.py` owns REST endpoints
- `websocket.py` owns WebSocket handling
- `state.py` owns persistent settings and project state
- `runtime.py` owns runtime state and task runners
- `launcher.py` owns local startup behavior

### Added

- Smoke tests for server startup, security headers, settings validation, project validation, log filtering, and chat-session persistence.
- Failure-path tests for Claude task execution and review step errors.

### Fixed

- Shared state is now protected more consistently with locks and atomic file writes.
- Claude and review failures now emit visible task errors and reliably unlock the UI.
- REST request bodies now use Pydantic models instead of raw dictionaries.

## 2026-03-21

### Added

- Initial OMADS GUI foundation with Claude Code integration and Codex auto-review support.
- Project backlog, agent workflow rules, and private GitHub repository setup.

### Fixed

- Early self-review findings around path handling, logging behavior, and frontend safety.
