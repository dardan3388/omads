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

### Feature: Smartphone-Zugriff im Heimnetz (lokal umsetzen)

Ziel: OMADS vom Handy bedienen, solange der PC läuft und beide im selben WLAN sind.

**Gewünschter Flow:**
1. PC starten, OMADS starten
2. In der GUI auf "Auf Smartphone öffnen" klicken
3. Popup zeigt die URL (z.B. `http://192.168.1.42:8080`)
4. URL auf dem Handy eintippen → OMADS lädt vollständig und mobilfreundlich

**Was zu ändern ist:**

Backend (5 Dateien):
- `src/omads/cli/main.py` — Default-Host von `127.0.0.1` auf `0.0.0.0` ändern
- `src/omads/gui/launcher.py` — analog Default-Host anpassen
- `src/omads/gui/app.py` — CORS-Regex um private RFC-1918-IPs erweitern (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
- `src/omads/gui/websocket.py` — Origin-Check ebenfalls für private IPs öffnen
- `src/omads/gui/routes.py` — neuer Endpunkt `/api/network-info` der die lokale LAN-IP zurückgibt

Frontend:
- `src/omads/gui/frontend.html` — "Auf Smartphone öffnen"-Button + Modal das die URL anzeigt
- `src/omads/gui/frontend.html` — `@media (max-width: 768px)` Block: Sidebar als Toggle, Modals auf 95vw, Buttons min 44px, Font-Sizes bumpen, Live-Log-Panel Vollbreite

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

- Re-run the short live smoke test for `Codex -> Claude Code -> Codex` on a clean working tree to verify the tighter limited-data synthesis prompt under a real Claude rate-limit.
- Phase 2 (runtime module split) was completed on 2026-03-23 by extracting both `review_flow.py` and `builder_flow.py` out of `runtime.py`.
- Phase 3 (frontend module split) was completed on 2026-03-23 by moving the large inline GUI script into `src/omads/gui/static/js/` browser modules.
- Phase 4 (timeline paging / bounded loading) was completed on 2026-03-23 by adding paged timeline reads plus on-demand older-event loading in the GUI.
- The previously scheduled setup, GUI, docs, browser E2E, and configurable review-pipeline tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.
