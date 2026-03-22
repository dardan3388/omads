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
  WebSocket guardrails, mocked auto-review, and mocked review-fix handoff flows are now covered. Remaining gaps are broader browser-side UI behavior and more end-to-end integration scenarios.

## Next

- [ ] Docker / cross-platform one-command setup
  `start-omads.sh` now covers Linux/macOS local startup, but Windows-friendly setup and containerized onboarding are still open.

- [ ] Diff viewer
  Improve code inspection directly in the GUI.

- [ ] Dark/light mode toggle
  Add a switchable visual theme.

- [ ] OpenAPI / Swagger docs
  Make the REST endpoints more visible and easier to inspect.

- [ ] GitHub issue templates and `CONTRIBUTING.md`
  Make collaboration on GitHub more structured.

## Technical Debt

- [ ] Extract stream parsing into helpers
  Reduce duplicated parsing logic between Claude and Codex flows.
