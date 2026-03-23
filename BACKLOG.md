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

- Investigate and tighten the short-run behavior of the manual review flow `Codex -> Claude Code -> Codex` when Claude is rate-limited. The flow now continues into step 3 as intended, but it took longer than the current smoke-test timeout budget.
- The previously scheduled setup, GUI, docs, browser E2E, and configurable review-pipeline tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.
