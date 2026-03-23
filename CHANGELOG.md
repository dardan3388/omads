# Changelog

All notable changes to this project are documented in this file.

The format is loosely based on Keep a Changelog.

## Unreleased

### Added

- Added a public-ready README gallery with real OMADS screenshots under `docs/assets/`.
- Added a persistent primary-builder setting so normal chat tasks can be routed to either Claude Code or Codex from the GUI until the user changes the selection.
- Added a dedicated Review settings tab so users can choose Reviewer 1 and Reviewer 2 for the manual review button while step 3 automatically returns to Reviewer 1.
- Added custom free-text focus instructions to the manual review dialog.
- Added a unified per-project event timeline so the chat view and live log can rebuild from the same persisted source after reloads.
- Added `start-omads.sh` as a one-command local launcher for Linux/macOS.
- Added `start-omads.ps1` as a Windows-friendly one-command launcher.
- Added `python -m omads` support through `src/omads/__main__.py`.
- Added mock-based tests for WebSocket guardrails, Codex auto-review outcomes, review fix suggestions, and Claude/Codex handoff flows without requiring live CLI access.
- Added backend integration tests for health/status/ledger routes, runtime status refresh, and project history/log/error paths.
- Added backend integration tests for theme settings, the diff endpoint, and local OpenAPI docs.
- Added Playwright-based browser E2E tests for theme switching, builder switching, diff viewing, and the WebSocket chat flow.
- Added a built-in diff viewer for the active Git working tree in the OMADS GUI.
- Added a switchable dark/light theme stored in GUI settings.
- Added `compose.yaml` plus `.env.docker.example` for Docker-based workspace/auth mounting.
- Added `CONTRIBUTING.md` and GitHub issue templates for bug reports and feature requests.
- Added `src/omads/gui/streaming.py` to centralize Claude/Codex stream parsing helpers.

### Changed

- New runtime activity now persists only through the unified per-project timeline; the legacy `history` and `log` endpoints are compatibility views derived from that timeline instead of a second live write path.
- Tightened the manual review synthesis prompt when Reviewer 2 is incomplete or unavailable, so step 3 stays focused on consolidating the available findings instead of drifting into an open-ended fresh review.
- Reworked the README so the repository is easier to understand for future public GitHub readers, with a clearer product pitch, faster quick-start path, and example OMADS workflows.
- Normal chat tasks now follow the persistent GUI builder selection instead of being hardwired to Claude Code.
- Automatic post-change review now runs after builder-created code changes for both builder paths instead of disappearing when Codex is selected as the builder.
- Manual review is no longer hardcoded to `Claude Code -> Codex -> Claude Code`; it now follows the configured reviewer order and routes apply-fixes back to Reviewer 1.
- The chat view and live log no longer depend on separate persistence models; manual review activity and other streamed events now survive reloads from the shared timeline without truncating the log replay.
- Replaced the custom `PROJEKTPROTOKOLL.md` project diary with standard English documentation.
- Added `docs/architecture.md` as the durable reference for backend structure and module boundaries.
- Standardized the main repository documentation in English for broader external use.
- Standardized the GUI labels, onboarding copy, review flow messages, and API-facing project errors in English.
- Clarified GUI wording around builders, reviewers, and automatic breaker behavior so the UI matches the actual OMADS flow again.
- Clarified the README start flow with explicit quick-start and troubleshooting guidance.
- Reduced `CLAUDE.md` to a minimal Claude-specific bridge file that delegates to the standard repository docs.
- Added an explicit browserless startup mode for headless and container use.
- Upgraded the Docker image from a basic headless shell to an authenticated OMADS runtime with Node.js, Git, Claude Code CLI, Codex CLI, and `/workspace` as the default target repo.
- Surfaced FastAPI docs through visible GUI and README entry points.
- Documented the one-time Playwright Chromium install step for browser E2E coverage.

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
