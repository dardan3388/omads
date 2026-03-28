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

### Feature: GitHub-Integration v2 (OAuth Device Flow + neue GUI)

Kompletter Umbau der GitHub-Integration. PAT-basierte Auth wird durch OAuth Device Flow ersetzt.
Ergebnis einer Architektur-Diskussion zwischen Claude Code und Codex (2026-03-28).

#### Warum der Umbau?
- PATs sind nicht userfreundlich (manuell erstellen, Scopes pflegen, neue Repos manuell hinzufügen)
- GitHub empfiehlt PATs nicht für Third-Party-Apps
- `-c remote.origin.url=` Override wurde von Git ignoriert (Bug)
- Fehlermeldungen kamen auf Deutsch statt Englisch

#### Auth: OAuth App + Device Flow

- Eine zentrale OMADS OAuth App registrieren (`client_id` im Code, kein `client_secret` nötig)
- Device Flow: User sieht Code → öffnet `github.com/login/device` → autorisiert → fertig
- Token läuft nicht regelmäßig ab; Re-Auth nur bei Widerruf
- Bei 401/403: automatisch Re-Auth anbieten
- Für Git push/pull: zuerst native Git-Credentials probieren, OAuth-Token als Fallback

#### GUI-Architektur (3 Integrationspunkte, kein Mega-Tab)

**1. Header:** Kleiner Status-Chip (verbunden/nicht verbunden)
**2. Startscreen / Projekt-Sidebar:** Aktionen (`Neues Repo` | `Repo öffnen` | `Repo reviewen`)
**3. Offenes Projekt:** Branch, Remote-Status, Pull/Push in der Sidebar

#### Neue Features

**Repo-Picker mit Suche:**
- Suchbare Liste der eigenen Repos (mit Org-Filter)
- Direkte Eingabe von `owner/repo` oder GitHub-URL
- Tabs: `Meine` | `Organisationen` | `Zuletzt` | `URL`

**Neues Repo erstellen:**
- Kompaktes Modal: Name, Public/Private, README, .gitignore
- Nach Erstellen: automatisch klonen und als OMADS-Projekt öffnen

**Fremdes Repo reviewen:**
- User gibt `owner/repo` ein → OMADS validiert per API
- Quick-Look: README, Metadaten (nur API)
- Deep Review: temporärer Clone → vollständiges Review

#### Git-Ops-Modal UX-Fixes
- Auto-Refresh nach Push/Pull (aktuell fehlt)
- Leere Repos: Buttons disabled + Hinweis statt kryptischer Fehler
- Commit-Message-Input nach Commit leeren

#### Implementierungs-Phasen
1. OAuth Device Flow Backend (`github.py` umbauen)
2. Auth-Routes anpassen (`routes.py`)
3. Auth-UI umbauen — Device Flow Modal (`github_ui.js`)
4. Repo-Picker mit Suche + Create Repo
5. Git-Ops-Modal UX-Fixes
6. Fremdes Repo reviewen (temp clone)

#### Sicherheit (bleibt bestehen)
- Token nie an den Browser weitergeben — nur Auth-Status
- Token nie in `.git/config` schreiben
- Alle Subprocess-Aufrufe ohne `shell=True`
- `full_name` gegen `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$` validieren
- Token-Fehler-Scrubbing vor jedem Log/Error-Output

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
