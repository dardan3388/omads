# Contributing

Thanks for contributing to OMADS.

## Before You Start

- Read `README.md` for setup and product overview.
- Read `AGENTS.md`, `PROJECT_RULES.md`, and `BACKLOG.md` before changing code.
- Use `CHANGELOG.md` for notable shipped changes and `docs/architecture.md` for module boundaries.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the local GUI:

```bash
./start-omads.sh
```

Or use the CLI directly:

```bash
omads gui
```

## Tests

Run the automated test suite before opening a pull request:

```bash
pytest
```

The test suite is intentionally mock-heavy so it can validate OMADS behavior without consuming live Claude Code or Codex quota.

## Workflow Expectations

- Prefer small, focused changes.
- Keep project-facing documentation in English.
- Do not introduce hidden project diaries or alternative sources of truth.
- Update `BACKLOG.md` when priorities change.
- Update `CHANGELOG.md` when a user-visible or structurally important change ships.
- If you touch architecture or module boundaries, update `docs/architecture.md`.

## Pull Requests

When opening a pull request:

- explain the user-facing goal
- summarize the main implementation choices
- list validation steps you ran
- mention any limits or follow-up work still open

## Issues

Use the provided GitHub issue templates for bugs and feature requests so maintainers get the right context quickly.
