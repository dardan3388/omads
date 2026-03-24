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

- Re-run the short live smoke test for `Codex -> Claude Code -> Codex` on a clean working tree to verify the tighter limited-data synthesis prompt under a real Claude rate-limit.
- Phase 2 (runtime module split) was completed on 2026-03-23 by extracting both `review_flow.py` and `builder_flow.py` out of `runtime.py`.
- Phase 3 (frontend module split) was completed on 2026-03-23 by moving the large inline GUI script into `src/omads/gui/static/js/` browser modules.
- Phase 4 (timeline paging / bounded loading) was completed on 2026-03-23 by adding paged timeline reads plus on-demand older-event loading in the GUI.
- The previously scheduled setup, GUI, docs, browser E2E, and configurable review-pipeline tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.
