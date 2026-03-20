# OMADS — Web-GUI für Claude Code + Codex Auto-Review

## Projekt-Überblick

OMADS ist eine Web-GUI (Port 8080), die zwei KI-Agenten orchestriert:
- **Claude Code CLI** — Builder-Agent (Chat + Code-Generierung)
- **Codex CLI** — Auto-Reviewer (read-only, prüft nach jeder Code-Änderung)

Keine API-Keys nötig — beide laufen über bestehende Abos (Claude Pro + ChatGPT Plus).

## Berechtigungen

Claude hat volle Berechtigungen in diesem Projekt:
- Dateien erstellen, bearbeiten und löschen ohne Rückfrage
- Dependencies installieren ohne Rückfrage
- Shell-Befehle ausführen ohne Rückfrage
- Eigenständig Entscheidungen treffen und umsetzen
- Nur bei echten Blockaden den Nutzer fragen

## Architektur

```
Browser (localhost:8080)
    ↕ WebSocket + REST
FastAPI Backend (server.py)
    ├── Claude CLI (subprocess, stream-json)
    └── Codex CLI (subprocess, --json, read-only)
```

- **server.py** — Gesamte Backend-Logik (FastAPI + WebSocket)
- **frontend.html** — Single-Page-App (vanilla HTML/CSS/JS, kein Framework)

## Tech Stack

- **Sprache:** Python 3.11+
- **Web:** FastAPI + Uvicorn + WebSockets
- **CLI:** Click (für `omads gui` Startbefehl)
- **Builder:** Claude CLI (`claude -p`, stream-json)
- **Reviewer:** Codex CLI (`codex exec`, --json, read-only)

## Dateistruktur

```
two agents/
├── CLAUDE.md                     ← Diese Datei
├── PROJEKTPROTOKOLL.md           ← Entwicklungshistorie
├── pyproject.toml                ← Python-Projekt (omads v0.2)
├── .venv/                        ← Virtual Environment
├── src/omads/
│   ├── __init__.py
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py               ← CLI-Einstiegspunkt (omads gui)
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── server.py             ← FastAPI Backend (WebSocket, REST, Agent-Steuerung)
│   │   └── frontend.html         ← Web-Frontend (SPA)
│   ├── dna/
│   │   ├── __init__.py
│   │   └── cold_start.py         ← Betriebsphasen (Status-Anzeige)
│   └── utils/
│       ├── __init__.py
│       └── paths.py              ← Pfad-Utilities (Projekt-Root, Data-Dir)
├── dna/
│   └── cold_start_state.json     ← Aktuelle Betriebsphase
├── data/
│   └── ledger/task_history.jsonl  ← Task-Historie (read-only Anzeige)
└── _legacy/                      ← Archivierter Pipeline-Code (nicht aktiv)
```

## Nutzung

```bash
# Setup
cd "two agents"
source .venv/bin/activate

# GUI starten (öffnet Browser)
omads gui
# oder: omads gui --port 9090

# Direkt per Uvicorn
uvicorn omads.gui.server:app --host 0.0.0.0 --port 8080 --reload
```

## GUI-Features

- Chat mit Claude Code (Live-Streaming via WebSocket)
- Codex Auto-Review nach jeder Code-Änderung
- Echtzeit-Token-Tracking (Input/Output/Cache/Kosten)
- Rate-Limit-Status + Reset-Countdown (aus Claude CLI stream-json)
- Projekt-Verwaltung (mehrere Repos)
- Einstellungen (Model, Effort, Permissions, Codex-Config)
- Task-Historie und Session-Management
- Live-Log (alle CLI-Events)

## Konfiguration

Persistent in `~/.config/omads/`:
- `gui_settings.json` — AI-Modell, Effort, Permissions, Codex-Config
- `projects.json` — Registrierte Projekte
- `usage.json` — Nutzungsstatistiken
- `chat_sessions.json` — Claude CLI Session-IDs
- `history/` — Projekt-spezifische Task-Historie
- `memory/` — Projekt-spezifische Kontext-Summaries

## Wichtige Regeln

- Strikt an die GUI-Architektur halten (server.py + frontend.html)
- Konservativ entscheiden, einfachste Lösung wählen
- Nach JEDER Änderung PROJEKTPROTOKOLL.md aktualisieren
- Nach JEDER Implementierung selbst testen
- OMADS Server nach JEDER Änderung neu starten

## Legacy

Der Ordner `_legacy/` enthält die ursprüngliche OMADS-Pipeline-Architektur
(Director, Builder, Breaker, Judge, Ledger, etc.). Diese Module sind archiviert
und werden von der aktiven GUI nicht verwendet.

---

> **Hinweis:** Die GUI (server.py + frontend.html) ist die Source of Truth.
> Alles andere dient nur der Status-Anzeige oder ist archiviert.
