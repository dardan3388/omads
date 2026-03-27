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

### Feature: GitHub-Integration (lokal umsetzen)

Ziel: GitHub-Repos direkt aus der OMADS-GUI verbinden, klonen und daran arbeiten — inkl. Commit, Push, Pull.

**Gewünschter Flow:**
1. OMADS starten → GitHub-Badge im Header klicken
2. Code auf `github.com/activate` eingeben (einmalig) → verbunden
3. Repo aus der Liste wählen → wird lokal geklont und als OMADS-Projekt registriert
4. Mit Claude/Codex daran arbeiten, dann über Git-Button committen/pushen/pullen

**Voraussetzung (einmalig, ~2 Min):**
GitHub OAuth App anlegen unter `github.com/settings/developers` → `client_id` in `OMADS_GITHUB_CLIENT_ID` env var setzen.

**Neue Dateien:**
- `src/omads/gui/github.py` — Device Flow, Token-Speicherung (`~/.config/omads/github_token.json`), GitHub API, Git-Credential-Injection
- `src/omads/gui/static/js/github_ui.js` — Auth-Modal (3 Zustände), Repo-Browser, Clone-Flow, Git-Ops-Modal

**Zu erweiternde Dateien:**
- `src/omads/gui/routes.py` — 6 neue Endpunkte: `/api/github/auth/start`, `/api/github/auth/poll`, `DELETE /api/github/auth`, `/api/github/repos`, `/api/github/clone`, `/api/github/git`
- `src/omads/gui/state.py` — 2 neue Pydantic-Modelle (`GitHubCloneRequest`, `GitHubGitRequest`)
- `src/omads/gui/frontend.html` — GitHub-Badge im Header, 2 neue Modals, CSS
- `src/omads/gui/static/js/app.js` — Import + Globals + WebSocket-Handler für `github_connected`/`github_disconnected`
- `src/omads/gui/static/js/projects_ui.js` — Git-Button pro Projekt-Karte

**Sicherheit:**
- Token nie an den Browser weitergeben — nur Auth-Status
- Git-Credentials via `-c remote.origin.url=` (kein Schreiben in `.git/config`)
- Alle Subprocess-Aufrufe ohne `shell=True`
- `full_name` gegen `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$` validieren
- Token-Fehler-Scrubbing vor jedem Log/Error-Output

**Erweiterung: Hybrid-Modus (Quick Edit ohne Clone)**

Ziel: Kleine Änderungen direkt via GitHub API — ohne lokalen Clone, ohne Overhead.

Zwei Modi im GitHub-Modal:
- **Quick Edit** — Einzelne Datei über GitHub Contents API lesen/bearbeiten/committen, kein lokales Klonen nötig
- **Mit OMADS öffnen** — vollständiger Clone, Claude Code / Codex können auf dem Filesystem arbeiten

Wann welcher Modus sinnvoll ist:
- Quick Edit: einzelne Konfigdatei, Tippfehler-Fix, README-Änderung
- Mit OMADS öffnen: Code ausführen, testen, großer Refactor über viele Dateien

Technisch: GitHub Contents API (`GET/PUT /repos/{owner}/{repo}/contents/{path}`) — kein `git` nötig, kein lokales Filesystem.

### Hardening: Copilot-Audit Quick-Fixes

Aus einem Copilot-Audit abgeleitete, validierte Verbesserungen:

1. ~~**`Math.random()`-ID → Counter** (`chat_ui.js`) — Detail-Toggle-IDs per Counter statt `Math.random()` generieren.~~
2. ~~**CWD-Existenz-Check vor Popen** (`builder_flow.py`, `review_flow.py`) — Vor jedem `subprocess.Popen` prüfen ob `target_repo` noch existiert, statt kryptische OS-Fehler zu bekommen.~~
3. **Tests ausbauen** — Leeres `tests/`-Verzeichnis füllen, Edge-Cases für Builder-Flow und Review-Flow abdecken. CI/CD erst danach.

### Smoke-Tests

- Re-run the short live smoke test for `Codex -> Claude Code -> Codex` on a clean working tree to verify the tighter limited-data synthesis prompt under a real Claude rate-limit.
- Phase 2 (runtime module split) was completed on 2026-03-23 by extracting both `review_flow.py` and `builder_flow.py` out of `runtime.py`.
- Phase 3 (frontend module split) was completed on 2026-03-23 by moving the large inline GUI script into `src/omads/gui/static/js/` browser modules.
- Phase 4 (timeline paging / bounded loading) was completed on 2026-03-23 by adding paged timeline reads plus on-demand older-event loading in the GUI.
- The previously scheduled setup, GUI, docs, browser E2E, and configurable review-pipeline tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.
