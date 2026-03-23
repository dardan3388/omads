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

- Re-run the short live smoke test for `Codex -> Claude Code -> Codex` on a clean working tree to verify the tighter limited-data synthesis prompt under a real Claude rate-limit.
- Phase 2 (runtime module split) was completed on 2026-03-23 by extracting both `review_flow.py` and `builder_flow.py` out of `runtime.py`.
- Phase 3 (frontend module split) was completed on 2026-03-23 by moving the large inline GUI script into `src/omads/gui/static/js/` browser modules.
- Phase 4 (timeline paging / bounded loading) was completed on 2026-03-23 by adding paged timeline reads plus on-demand older-event loading in the GUI.
- The previously scheduled setup, GUI, docs, browser E2E, and configurable review-pipeline tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.
