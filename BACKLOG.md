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

- [ ] Expand test coverage
  Backend integration coverage now includes runtime status, health/status/ledger, WebSocket guardrails, mocked auto-review, and mocked review-fix handoff flows. Remaining gaps are broader browser-side UI behavior and true end-to-end browser scenarios.

## Next

- [ ] Dockerized AI CLI authentication and workspace mounts
  A basic Docker entrypoint can run OMADS headlessly, but a polished container story for authenticated Claude/Codex usage and project mounts is still open.

- [ ] Diff viewer
  Improve code inspection directly in the GUI.

- [ ] Dark/light mode toggle
  Add a switchable visual theme.

- [ ] OpenAPI / Swagger docs
  Make the REST endpoints more visible and easier to inspect.

- [ ] GitHub issue templates and `CONTRIBUTING.md`
  Make collaboration on GitHub more structured.

## Later

- [ ] Selectable builder agent / Codex fallback builder
  Let OMADS choose between Claude Code and Codex as the primary builder so work can continue when one builder is unavailable or out of quota.

## Technical Debt

- [ ] Extract stream parsing into helpers
  Reduce duplicated parsing logic between Claude and Codex flows.
