# PROJECT_RULES.md

Additional repository-specific rules for this project.

## Repo Context

- Project name: `omads`
- Main branch: `main`
- The working tree may already contain changes at any time. Read the current diff before editing.
- `BACKLOG.md` is the visible source of truth for active work.
- `CHANGELOG.md` records notable shipped changes.
- `docs/architecture.md` describes the current architecture and intended module boundaries.

## Collaboration

- When extending files that already changed, inspect the diff first and build on top of it instead of replacing it blindly.
- Meta files such as `AGENTS.md`, `PROJECT_RULES.md`, `.gitignore`, `README.md`, `CHANGELOG.md`, and `docs/` may be maintained without interfering with ongoing feature work in `src/`.
- After meaningful code changes, automatically sync (add, commit, push) without asking.

## Documentation

- Project-facing documentation should be kept in English.
- Avoid custom history documents that duplicate Git.
- `CLAUDE.md` may remain for Claude-specific bootstrapping, but it must not become a parallel source of truth.
- Use standard files for standard purposes:
- `README.md` for onboarding
- `BACKLOG.md` for open work
- `CHANGELOG.md` for notable changes
- `docs/architecture.md` for structural explanation

## Git and Security

- The remote should point to a private GitHub repository.
- Secrets, local environments, log files, and generated artifacts stay out of commits.
