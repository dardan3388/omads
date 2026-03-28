# Changelog

All notable changes to this project are documented in this file.

The format is loosely based on Keep a Changelog.

## Unreleased

### Added

- Added smartphone access via LAN — OMADS binds to `0.0.0.0`, CORS allows private RFC-1918 IPs, a new `/api/network-info` endpoint, a smartphone-open button with URL modal, and a fully responsive mobile layout.
- Added chat context handover when switching builders — OMADS passes the recent conversation history to the new builder so it can continue naturally without losing context, even across different providers (Claude Code ↔ Codex).
- Added a public-ready README gallery with real OMADS screenshots under `docs/assets/`.
- Added `docs/live-smoke-tests.md` plus an animated demo GIF for the live Claude builder WebSocket smoke test.
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

- Builder sessions are now scoped per builder (`builder:claude`) instead of per repo, preventing stale session resumption when switching builders.
- Builder and manual-review runs now freeze their active repo/builder/reviewer settings from the initiating WebSocket session, so live tasks no longer follow whichever project another browser tab selected last.
- Browser reconnects now reuse a stable per-tab session ID plus runtime session snapshots, so a reconnect restores that tab's last repo/builder context instead of inheriting the latest persisted global GUI settings.
- Added `SUPPORT.md`, a pull request template, and direct commercial/support contact paths for the public launch.
- Added the direct support email to the README help section so public visitors can find contact details faster.
- Added `SECURITY.md`, `CODE_OF_CONDUCT.md`, and a security-policy contact link in the GitHub issue template config for the upcoming public release.
- Added a public release checklist and tightened the GitHub repository description/topics for the upcoming public launch.
- Reworked the README into a shorter public-facing landing page and moved the long setup instructions into `docs/getting-started.md`.
- Replaced the old static README screenshots with a current animated UI tour and removed outdated GUI stills from `docs/assets/`.
- Replaced the smoke-test GIF with a real English demo that starts from recorded Codex CLI launch context and then shows the actual OMADS GUI run.
- Standardized the current backlog and the new smoke-test documentation in English.
- Clarified the startup documentation so the quick start explains that OMADS must be started manually before opening `localhost`, and added the simplest auto-start-after-login guidance.

### Fixed

- Fixed the most visible multi-tab/LAN session bleed by unicast-delivering task stream events to the initiating client instead of broadcasting them to every connected browser.
- Fixed settings saves so the active browser session now mirrors the server-sanitized settings snapshot, including builder/reviewer/task-model choices that affect the next run immediately.
- Fixed `stop` ownership so one browser session can no longer cancel another session's active task just because OMADS still uses one shared task slot.
- Fixed reconnect drift so one browser tab no longer comes back on another tab's repo/builder after a disconnect or page reload while the server stays up.
- Fixed manual `last task` review scope leakage so a browser tab now reviews its own most recent changed files, including after reconnect, instead of reusing another session's global file list.
- Hardened home-directory path validation across settings, project switching, browsing, and GitHub clone/git endpoints so public users cannot steer OMADS at arbitrary filesystem locations through brittle string-prefix checks.
- Hardened GitHub clone cleanup so a failed `origin` reset now removes the temporary authenticated remote instead of silently leaving credentials behind in `.git/config`, and saved GitHub tokens now get private file permissions where supported.
- Manual Codex review and synthesis steps now surface scrubbed stderr-backed failures and treat empty successful exits as visible errors instead of silently continuing with missing review content.
- Cleaned up the CORS allowlist to use valid explicit localhost origins plus the existing regex-based origin validation instead of invalid wildcard-port origin strings.
- Codex CLI error messages (e.g. rate-limit with retry time) are now surfaced in the chat instead of showing only a generic "exit code 1" failure.
- Fixed duplicate Codex error display caused by both `error` and `turn.failed` JSONL events being forwarded.
- Fixed Codex builder runs that previously hung silently when `stdout` stayed empty: OMADS now reads and scrubs `stderr`, surfaces stderr-backed task failures, and warns when Codex exits without any user-visible response.
- Fixed Codex builder auto-review in normal local folders without Git metadata by extracting changed files directly from Codex `file_change` JSON events and keeping Git snapshotting only as a fallback.

### Changed

- Changed the project timeline API and frontend history/log loaders to use bounded pages with an older-events cursor, so long project histories stay responsive without truncating the stored timeline data.
- Split the old inline frontend script out of `src/omads/gui/frontend.html` into browser modules under `src/omads/gui/static/js/`, and mounted `/static` from FastAPI so the GUI logic now ships as maintainable frontend files instead of one large HTML-embedded script block.
- Extracted the Claude/Codex builder task runners and automatic breaker subprocess helpers into `src/omads/gui/builder_flow.py`, so `runtime.py` now stays focused on runtime state, routing, and orchestration.
- Extracted the manual review subprocess helpers and synthesis prompt builder into `src/omads/gui/review_flow.py`, so `runtime.py` keeps the orchestration role while review-specific mechanics live in a dedicated module.
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
