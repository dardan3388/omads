# BACKLOG.md

Visible task list for this repository.

This file is the source of truth for active work, priorities, and next steps.
Use `CHANGELOG.md` for shipped changes and `docs/architecture.md` for durable technical context.

## Usage

- New agents should read `AGENTS.md`, `PROJECT_RULES.md`, and then this file.
- Open work lives here, not hidden in narrative history files.
- Work from highest priority downward unless the user explicitly changes direction.
- When something is finished, remove it from here or mark it done and document the important outcome in `CHANGELOG.md` if it is noteworthy.

## Now

- No active backlog items right now.
- The previously scheduled setup, GUI, docs, and stream-parsing tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.

## Later

- [ ] Selectable builder agent / Codex fallback builder
  Let OMADS choose between Claude Code and Codex as the primary builder so work can continue when one builder is unavailable or out of quota.

- [ ] Full browser E2E coverage
  The automated suite now covers backend flows, docs visibility, diff inspection, and mocked CLI orchestration. A future Playwright-style browser suite would still add value for reconnect and UI interaction paths.
