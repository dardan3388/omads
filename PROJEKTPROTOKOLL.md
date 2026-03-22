# OMADS — Projektprotokoll

Dieses Dokument beschreibt den gesamten Entwicklungsprozess von OMADS (Orchestrated Multi-Agent Development System) Schritt für Schritt, damit eine dritte Person nachvollziehen kann, wie das Projekt entstanden ist.

**Entwicklungszeitraum:** März 2026
**Entwickelt von:** Dani (Projektleiter) + Claude Code (KI-Entwickler)
**Tech Stack:** Python 3.11+, Click, Pydantic v2, FastAPI, OpenAI API, Claude CLI

---

## Phase 1: Fundament (v1-Kern) — ~0–25%

### Schritt 1: Spezifikation lesen und verstehen
Als allererstes wurde die vollständige Architektur-Spezifikation (`OMADS_Specification.md`) gelesen. Diese Spec definiert das gesamte System: ein deterministisch orchestriertes Multi-Agent-System, bei dem zwei KI-Agenten zusammenarbeiten — einer baut Code, der andere prüft ihn.

### Schritt 2: Projektstruktur aufsetzen
- Python-Projekt mit `pyproject.toml` konfiguriert
- Virtual Environment (`.venv`) erstellt
- Dependencies: `click` (CLI), `pydantic` (Datenverträge), `openai` (Breaker-Agent)
- Ordnerstruktur angelegt: `src/omads/` mit Untermodulen für jede Architekturkomponente
- `pip install -e .` für lokale Entwicklung

### Schritt 3: Datenverträge definieren
Alle 9 Pydantic-Modelle in `src/omads/contracts/models.py` implementiert:
- `TaskInput` — Was der Nutzer eingeben will (Intent, Kriterien, Scope)
- `TaskPacket` — Internes Arbeitspaket (vom Director erstellt)
- `AgentResult` — Ergebnis eines Agenten (Status, Artefakte, Metriken)
- `BreakerFinding` / `BreakerFindings` — Prüfergebnisse des Breakers
- `ConformanceResult` — Ergebnis der Regelprüfung
- `EvaluationResult` — Judge-Entscheidung (accept/reject/escalate)
- `LedgerEntry` — Protokolleintrag
- Plus diverse Enums: `RiskLevel`, `TaskType`, `OperationalMode`, etc.

### Schritt 4: DNA Store Grundstruktur
Der `dna/`-Ordner enthält die "DNA" des Projekts — versionierte Konfiguration:
- `policy.json` — Run + Decision Policy (Budget-Limits, Schwellenwerte)
- `repo_constraints.json` — Repo-Einschränkungen
- `agent_scorecard.json` — Agent-Performance-Tracking
- `architecture_decisions.md` — ADRs (Architekturentscheidungen)
- `cold_start_state.json` — Cold-Start-Phasenverwaltung
- `golden_tasks/` — Referenz-Aufgaben für Self-Validation

### Schritt 5: CLI-Grundgerüst
Mit Click das CLI gebaut (`src/omads/cli/main.py`):
- `omads run <repo> --intent "..." -c "Kriterium" -s "scope/"` — Voller Task-Lauf
- `omads do "Aufgabe"` — Vereinfachter Einstieg
- `omads status` — Systemstatus
- `omads ledger` — Task-Historie
- `omads validate` — Self-Validation

### Schritt 6: Task Input Validation
`src/omads/cli/task_input.py` — Validiert Nutzereingaben:
- Fast Path (einfache Tasks) vs. Strict Path (kritische Tasks)
- Automatisches Upgrade von Fast auf Strict bei Risiko
- Prüft: Intent vorhanden, Kriterien definiert, Scope gültig

### Schritt 7: Director
`src/omads/director/director.py` — Das "Gehirn" des Systems:
- Klassifiziert Tasks nach Typ (feature, bugfix, refactor, etc.)
- Bewertet Risikostufe (low/medium/high/critical) anhand von Pfaden, Dateien, Keywords
- Wählt Betriebsmodus: `builder_only` oder `builder_plus_breaker`
- Erstellt das `TaskPacket` mit Budget, Timeout, Scope

### Schritt 8: Builder Agent
`src/omads/builder/agent.py` — Der "Bauarbeiter":
- Nutzt Claude CLI (`claude -p`) als Subprocess
- Baut einen detaillierten Prompt aus dem TaskPacket
- Arbeitet in einem Git-Worktree (isolierte Kopie des Repos)
- Retry-Logik bei Fehlern
- Fix: `CLAUDECODE`-Umgebungsvariable entfernen (sonst erkennt Claude CLI, dass es in sich selbst läuft)

### Schritt 9: Breaker Agent
`src/omads/breaker/agent.py` — Der "Prüfer":
- Nutzt OpenAI API mit Modell `o3` (Reasoning-Modell)
- Bekommt: Intent, Builder-Output, Diff/Patch
- Prüft auf: Security-Probleme, fehlende Edge Cases, Spezifikations-Verstöße
- Gibt strukturierte Findings als JSON zurück
- Prompt Caching: System-Prompt wird von OpenAI automatisch gecacht (50% Rabatt)
- Timeout: 120 Sekunden

### Schritt 10: Conformance Layer
`src/omads/conformance/checker.py` — Automatische Regelprüfung:
- Protocol-Check: Hat der Agent das richtige Format geliefert?
- Budget-Check: Budget eingehalten?
- MaxFiles-Check: Nicht zu viele Dateien geändert?
- Scope-Check: Nur erlaubte Dateien geändert?
- Quality-Check: Self-Assessment vorhanden?

### Schritt 11: Judge Layer
`src/omads/judge/judge.py` — Trifft die Entscheidung:
- Hard Gates: Sofortiges Reject bei Verstößen (z.B. Security-Finding mit Severity "critical")
- Policy Gates: Warnungen bei Policy-Verstößen
- Weighted Aggregation: Gewichteter Score aus allen Prüfungen
- Entscheidung: `accept` (>= 0.7), `reject` (< 0.4), `escalate` (dazwischen)

### Schritt 12: Task Ledger
`src/omads/ledger/ledger.py` — Append-only Protokoll:
- Jeder Task wird in `data/ledger/task_history.jsonl` festgehalten
- Enthält: Routing-Rationale, Evaluation, Artefakt-Referenzen
- Kann nie geändert oder gelöscht werden (append-only)

### Schritt 13: Dry-Run-Modus
Für Tests ohne echte API-Aufrufe:
- `--dry-run` Flag simuliert Builder und Breaker
- Generiert realistische Fake-Ergebnisse
- Durchläuft trotzdem den kompletten Flow (Director, Conformance, Judge, Ledger)

### Schritt 14: Task Decomposition
`src/omads/director/decomposition.py`:
- Erkennt komplexe Tasks anhand von Triggerwörtern ("und", "sowie", "mehrere")
- Zerlegt sie in sequentielle Subtasks
- Jeder Subtask durchläuft den kompletten Flow einzeln

### Schritt 15: Fehlerbehandlung
Robuste Error-Handler für alle kritischen Pfade:
- CLI: Try/Catch um den gesamten Flow
- JSON-Parsing: Toleriert verschiedene Breaker-Output-Formate
- Subprocess: Timeouts, FileNotFoundError, Retry
- API: OpenAI-Fehler werden gefangen und als Findings weitergegeben

---

## Phase 2: Erweiterte Features (v2) — ~25–50%

### Schritt 16: Self-Validation
`src/omads/validation/self_validation.py`:
- Automatische Trigger: Policy-Änderung, Agent-Version-Break, Drift, wöchentlich
- Führt Golden Tasks erneut aus und vergleicht Ergebnisse
- Erkennt Regression, Kalibrierungsprobleme, Drift

### Schritt 17: Evidence Batching
- Critical/High Findings → sofortige Eskalation
- Medium/Low → werden zu wöchentlichen Review-Paketen zusammengefasst

### Schritt 18: Risk Monitor
`src/omads/monitoring/risk_monitor.py`:
- Interrupt-Aktionen bei zu hohem Risiko
- `--force` Override für den Nutzer
- Events werden in `data/risk_events.jsonl` gespeichert

### Schritt 19: Derived Golden Tasks
- Tasks mit Score >= 0.85 werden automatisch zu "Golden Tasks" promotet
- Diese dienen als Referenz für zukünftige Self-Validation

### Schritt 20: Context Bundle Builder
`src/omads/context/bundle.py`:
- Baut rollenspezifische Kontextpakete für Builder und Breaker
- Enthält: ADRs, Constraints, bekannte Pitfalls, relevante Policy-Regeln
- Builder bekommt anderen Kontext als der Breaker

### Schritt 21: Temporal Governance
`src/omads/governance/temporal.py`:
- Scorecard Decay: Alte Scores verlieren exponentiell an Gewicht (Halbwertszeit 60 Tage)
- Boden bei 0.15 (Score verschwindet nie ganz)
- Revalidierungs-Trigger für abgelaufene Scorecards

---

## Phase 3: Fortgeschrittene Features (v3) — ~50–70%

### Schritt 22: Diff-Signature Policy (Layer 2)
`src/omads/policies/diff_signature.py`:
- Analysiert Diffs auf kritische Muster
- Erkennt: neue Dependencies, ENV-Zugriffe, SQL-Migrationen, entfernte Auth-Decorators
- Jedes Pattern hat eine Severity-Stufe

### Schritt 23: Scorecard-Hierarchien
`src/omads/governance/scorecard.py`:
- Hierarchisches Fallback-Routing für Scorecards
- Suche: exact match → type match → language match → global
- Beispiel: "python_feature_lowrisk" → "feature" → "python" → "global"

### Schritt 24: Scope Creep Score
- Mehrdimensionale Messung ob ein Task seinen Scope überschritten hat
- Dimensionen: file_overflow, criteria_miss, confidence_gap, diff_spillover
- Wird in die Judge-Entscheidung einbezogen

### Schritt 25: Observability Event-Bus
`src/omads/observability/event_bus.py`:
- Events: task.created, task.completed, risk_monitor.interrupt, arbitration.finished
- Persistiert in `data/observability/events.jsonl`
- Vorbereitet für Dashboard/UI (Events sind strukturiert und querybar)

### Schritt 26: Director Learning
`src/omads/director/learning.py`:
- Adaptives Routing basierend auf historischen Ergebnissen
- Lernt: Breaker-Empfehlungen, Budget-Anpassung, Decomposition-Bias
- Aktuell `learning_enabled: false` (braucht genug Daten)

### Schritt 27: Confidence Calibration
`src/omads/governance/confidence.py`:
- Bucket-basierte Kalibrierung der Agent-Confidence-Werte
- Judge passt Entscheidung an wenn Agent systematisch zu hoch/niedrig schätzt
- Routingwirksam: Director nutzt kalibrierte Werte

### Schritt 28: AST-Semantic Policy (Layer 3)
`src/omads/policies/ast_semantic.py`:
- Python AST-Analyse des geänderten Codes
- Erkennt: entfernte Permission-Checks, gelöschte Input-Validierung
- Semantisch tiefer als Diff-Signature (versteht Code-Struktur)

### Schritt 29: Container-Isolation
`src/omads/isolation/container.py`:
- Risikoklassen-Mapping: high risk → Container-Pflicht
- Erkennt: Docker, bubblewrap, firejail
- Compliance-Check: Block wenn high risk + kein Container

### Schritt 30: Adversarial Gold Tasks
5 bewusst böse Testfälle in `dna/golden_tasks/`:
- scope_creep: Task der absichtlich zu viel ändert
- auth_removal: Entfernt Authentifizierung
- sql_injection: Fügt SQL-Injection ein
- env_leak: Leakt Umgebungsvariablen
- test_failure: Bricht Tests

### Schritt 31: Post-Merge Causality
`src/omads/monitoring/post_merge.py`:
- Nach dem Merge: Überwachung ob Probleme auftreten
- Menschliche Kausalitätsbestätigung: "War der Bug durch diesen Task verursacht?"
- CLI: `omads confirm-finding TASK-ID 0` → Scorecard-Penalty

---

## Phase 4: Spec-Compliance-Härtung — ~70–80%

### Schritt 32: Gap-Analyse
Systematischer Abgleich Code ↔ Spezifikation. 31 Gaps gefunden, die wichtigsten gefixt:

### Schritt 33: Fehlende DNA Store Files
- `dna/risk_overrides.json` — Pfadbasierte Risiko-Overrides
- `dna/task_entry_thresholds.json` — Fast/Strict-Path Schwellenwerte
- `dna/rejected_approaches.md` — 5 dokumentierte verworfene Ansätze

### Schritt 34: Risk-Overrides im Director
Director liest jetzt `risk_overrides.json` und passt Risikoklassifikation an:
- Pfade wie `*/auth/*` oder `*/security/*` → automatisch higher risk

### Schritt 35: HumanApproval im Ledger
- Neues Pydantic-Modell `HumanApproval` (required, status, approved_by, approved_at, reason)
- Automatisch `required=True` wenn Judge "escalate" oder "human_review_required" entscheidet
- Spec-Pflicht: Normative Statusänderungen brauchen menschliche Bestätigung

### Schritt 36: TemporalMetadata überall
- Neues Pydantic-Modell `TemporalMetadata` (temporal_model_type, created_at, last_validated_at, revalidation_state)
- Auf ALLEN Datenverträgen: TaskPacket, AgentResult, ConformanceResult, EvaluationResult, LedgerEntry
- Auch auf allen DNA Store Artefakten

### Schritt 37: Version-Break Confirmation Workflow
`src/omads/governance/version_breaks.py`:
- Agent-Version-Break: Claude oder OpenAI veröffentlicht neues Modell → Scorecards müssen zurückgesetzt werden
- Context-Version-Break: Repo-Struktur ändert sich fundamental → Artefakte revalidieren
- Workflow: automatisch erkannt → Status "suggested" → Mensch bestätigt/verwirft
- CLI: `omads version-breaks` + `omads confirm-version-break <id> [--reject]`

### Schritt 38: Container-Isolation Enforcement
- Vorher: nur Warning wenn high risk ohne Container
- Nachher: Block (Conformance-Verletzung) — kein Task-Durchlauf ohne Container bei high risk

---

## Phase 5: Integrationstests — ~80–85%

### Schritt 39: Test-Infrastruktur
- pytest + pytest-timeout
- `tests/conftest.py` mit shared Fixtures (tmp-Dirs, Sample-Objekte)

### Schritt 40: 92 Unit-Tests geschrieben
Alle Tests erfolgreich:
- `test_models.py` (22 Tests) — Datenverträge, Enums, Validierung
- `test_director.py` (12 Tests) — Routing, Risiko, Decomposition
- `test_conformance.py` (10 Tests) — Alle Prüfgruppen
- `test_judge.py` (8 Tests) — Accept/Reject/Escalate, Security Gates
- `test_governance.py` (18 Tests) — Decay, Version-Breaks, Isolation
- `test_ledger.py` (8 Tests) — Append-only, HumanApproval
- `test_e2e_dryrun.py` (6 Tests) — CLI-Befehle End-to-End

### Schritt 41: Erster echter E2E-Run
- Erstes Mal echte Claude CLI + OpenAI API aufgerufen
- Task: "Erstelle einen einfachen Taschenrechner"
- Builder (Claude Code) hat in ~30s einen tkinter-GUI-Rechner gebaut
- Breaker (OpenAI o3) hat den Code geprüft, ~1.143 Tokens verbraucht (~$0.003)
- Judge: accept mit Score 0.95

---

## Phase 6: UX-Revolution — ~85–92%

### Schritt 42: Erste UX-Kritik
User-Feedback: "Das CLI ist viel zu komplex! Niemand versteht das."
Problem: Die CLI zeigte interne Details wie Task-IDs, Conformance-Layer-Ergebnisse, Judge-Scores, Ledger-Einträge, Policy Gates — alles auf einmal. 30+ Zeilen Output für einen einfachen Task.

### Schritt 43: Radikale CLI-Vereinfachung
`omads do` komplett umgebaut:
- Vorher: 30+ Zeilen technischer Output
- Nachher: ~8 Zeilen: "Ich baue..." → "Fertig!" → "Datei: /pfad" → "Starten mit: python3 /pfad"
- Interne Details nur noch bei `--verbose`

### Schritt 44: Builder Git-Fallback
Problem: `omads do` funktionierte nur in Git-Repos (wegen Worktree-Isolation).
Fix: `_is_git_repo()` Check — nutzt Worktree nur wenn Git vorhanden, arbeitet sonst direkt.

### Schritt 45: Interaktive Session
User-Feedback: "OMADS soll wie Claude Code funktionieren — ich tippe `omads` und kann chatten!"
- `omads` (ohne Argumente) startet jetzt eine interaktive REPL-Session
- Welcome-Screen mit ASCII-Box
- Repo-Auswahl (Pfad oder Enter für aktuelles Verzeichnis)
- Session-Loop: Eingabe → Verarbeitung → "Was soll ich als nächstes tun?"
- `exit`/`quit`/Ctrl+C zum Beenden

### Schritt 46: Chat vs. Task Erkennung
Problem: User tippte "was sollen wir heute programmieren?" und OMADS schickte es an den Builder als Programmier-Auftrag (Ergebnis: `intent_mismatch`).

Lösung — `_is_question_or_chat()` Funktion:
- Erkennt Fragezeichen, deutsche/englische Fragewörter
- Erkennt Grüße und Smalltalk
- Erkennt kurze Eingaben ohne Aktionsverben
- Routing: Frage → Chat-Antwort, Auftrag → Builder

### Schritt 47: Chat via OpenAI API
Problem: Chat-Antworten liefen zunächst über Claude CLI — langsam und teuer.
User-Feedback: "Nimm die OpenAI API, da habe ich Guthaben."
- `_handle_chat()` nutzt jetzt OpenAI API mit `gpt-4o-mini` (schnell, ~$0.00015 pro Antwort)
- Einfache Grüße ("hallo", "danke") werden ohne API-Call beantwortet (kostet null)

### Schritt 48: Agent-Labels in der CLI
Problem: User wusste nicht, welcher Agent gerade was macht.
- `[Claude Code]` (blau) — Builder arbeitet
- `[OpenAI o3]` (magenta) — Breaker prüft
- `[GPT-4o-mini]` (magenta) — Chat-Antworten
- Jede Zeile trägt ein Label

### Schritt 49: Live Builder-Streaming
Problem: Builder lief "blind" — man sah nichts bis zum Endergebnis.
- Umstellung von `--output-format text` auf `--output-format stream-json`
- Jedes Event wird live geparst und angezeigt:
  - `[Claude] Lese rechner.py` (Tool-Aufruf: Read)
  - `[Claude] Bearbeite rechner.py` (Tool-Aufruf: Edit)
  - `[Claude] $ python3 -m pytest` (Tool-Aufruf: Bash)
- `_format_tool_use()` macht Tool-Aufrufe menschenlesbar

### Schritt 50: Breaker-Output sichtbar
- `run_breaker()` erhält `on_output` Callback
- Roh-Antwort des Breakers wird an die CLI/GUI weitergereicht
- Man sieht jetzt was OpenAI o3 analysiert hat

---

## Phase 7: Web GUI — ~92–95% (aktuell)

### Schritt 51: Entscheidung für Web GUI
User-Wunsch: "Ich will alles über eine GUI steuern — Chat, Einstellungen, Agent-Aktivität."
4 Optionen evaluiert: Web GUI, VS Code Extension, Terminal UI, Desktop App.
Entscheidung: **Web GUI (lokal)** — bleibt Python, schönste UI, schnellster Weg.

### Schritt 52: FastAPI Backend
`src/omads/gui/server.py`:
- FastAPI App mit REST + WebSocket
- REST: `/api/settings`, `/api/usage`, `/api/status`, `/api/ledger`
- WebSocket `/ws`: Bidirektionale Kommunikation für Chat + Live-Events
- Task-Ausführung in Background-Thread mit Event-Streaming
- Globaler State: Settings, Usage-Tracking, aktive Connections

### Schritt 53: Frontend
`src/omads/gui/frontend.html` — Single-Page-App (HTML/CSS/JS, kein Framework):
- **Chat-Panel** (links): Nachrichten mit Agent-Labels, farbcodiert
- **Agent-Aktivität** (rechts): Live-Log aller Tool-Aufrufe und Entscheidungen
- **Header**: Usage-Anzeige (Claude Tasks, OpenAI Calls, Kosten), Verbindungsstatus
- **Settings-Modal**: Projekt-Pfad, Permissions, Claude-Modell, OpenAI-Modell, Breaker an/aus
- Dark Theme, monospace Font, responsive

### Schritt 54: CLI-Command `omads gui`
- `omads gui` startet FastAPI-Server auf `localhost:8080`
- Browser öffnet sich automatisch
- Dependencies: `fastapi`, `uvicorn`, `websockets` zu `pyproject.toml` hinzugefügt

### Schritt 55: Selbstmodifikationsschutz
- OMADS hat sich beim Testen selbst modifiziert (Ziel-Repo = OMADS-Ordner)
- Fix: Prüfung ob `target_repo == get_project_root()` → Blockiert mit Fehlermeldung
- Betrifft: `server.py` WebSocket-Handler

### Schritt 56: Ordner-Picker im GUI
- Grafischer Verzeichnis-Browser statt manueller Pfad-Eingabe
- REST-Endpoint `/api/browse?path=...` listet Unterverzeichnisse
- Frontend: klickbare Ordner-Navigation mit "Auswählen"-Button
- Betrifft: `server.py`, `frontend.html`

### Schritt 57: Live-Streaming Builder-Output
- Claude CLI mit `--output-format stream-json --verbose` für Echtzeit-Events
- Tool-Calls werden als klappbare Blöcke angezeigt (Read, Edit, Write, Bash)
- Text-Output wird live im Chat angezeigt
- Stop-Button zum Abbrechen laufender Tasks
- Betrifft: `server.py` (`_run_builder_with_events`), `frontend.html`

### Schritt 58: Persistente Einstellungen
- Config-Datei: `~/.config/omads/gui_settings.json`
- Laden/Speichern mit `_load_config()` / `_save_config()`
- Einstellungen überleben GUI-Neustarts
- Betrifft: `server.py`

### Schritt 59: Erweiterte Settings mit Tabs
- Settings-Modal mit 4 Tabs: Projekt, Claude Code, OpenAI, Kosten
- **Claude Code Tab:** Modell-Auswahl (Sonnet/Opus/Haiku), Permission-Modus, Max Turns
- **OpenAI Tab:** Breaker-Modell, Chat-Modell (beide dynamisch von API geladen), Temperature, Max Tokens, Timeout
- **Kosten Tab:** Exaktes Usage-Tracking (nur echte API-Werte, keine Schätzungen)
  - Claude: Task-Count + Hinweis "claude.ai/account"
  - OpenAI: Calls, Input/Output Tokens, Kosten, Pro-Modell-Aufschlüsselung
- Offizielle OpenAI-Preistabelle implementiert (gpt-4o, gpt-4o-mini, o3, o4-mini, etc.)
- `/api/openai-models` Endpoint für dynamisches Laden verfügbarer Modelle
- `switchTab()`, erweiterte `loadSettings()`/`saveSettings()`, `updateUsageDisplay()`
- Betrifft: `server.py`, `frontend.html`

### Schritt 60: Selbstmodifikationssperre entfernt + Backup
- Sperre entfernt: OMADS darf jetzt auf Wunsch auch sein eigenes Projekt bearbeiten
- Backup-Archiv angelegt: `/home/dani/Schreibtisch/AI-Commerce/backups/omads-backup-20260315-131051.tar.gz`
- Zum Wiederherstellen: `tar xzf .../omads-backup-*.tar.gz -C /home/dani/Schreibtisch/AI-Commerce/`
- Betrifft: `server.py`

### Schritt 61: OpenAI Modell-Filter erweitert
- Filter um `gpt-5`, `codex` Patterns erweitert (vorher nur gpt-4/o3/o4)
- Zusätzliche Skip-Patterns: `embedding`, `tts`, `whisper`, `dall-e`, `transcribe`
- Preistabelle um gpt-5, gpt-5.4, codex-mini ergänzt
- Jetzt sichtbar: gpt-5.4, gpt-5.3-codex, gpt-5.2-codex, alle o-Modelle
- Betrifft: `server.py` (`get_openai_models`, `_OPENAI_PRICES`)

### Schritt 62: Claude Modell-Auswahl Fix
- Vollständige Modell-IDs (`claude-opus-4-6`) verursachten `afk-mode-2026-01-31` Beta-Header-Fehler
- Claude CLI akzeptiert nur Kurzformen: `sonnet`, `opus`, `haiku`
- Dropdown auf 3 Kurzform-Optionen reduziert (Sonnet 4.6, Opus 4.6, Haiku 4.5)
- Betrifft: `frontend.html` (Claude-Modell-Select)

### Schritt 63: Claude CLI Update (2.1.74 → 2.1.76)
- Fehler `afk-mode-2026-01-31` Beta-Header war ein Bug in Claude CLI 2.1.74
- Update via `npm install -g @anthropic-ai/claude-code@latest` auf 2.1.76
- Test bestätigt: kein Header-Fehler mehr

### Schritt 64: GUI Tool-Block Rendering Fix
- Tool-Blöcke wurden als dünne, unlesbare Linien dargestellt (Screenshot)
- Ursache: kein min-height, leere Events wurden gerendert
- Fix: min-height 32px, leere Text/Tool-Events gefiltert, text-overflow: ellipsis
- Chevron (▶) nur bei vorhandenem Detail, mehr Tool-Icons (Agent, Skill, etc.)
- Betrifft: `frontend.html` (CSS + JavaScript)

### Schritt 65: Agent-Mode-Toggle im Header
- Drei-Wege-Schalter im Header: **Nur Claude** | **Auto** | **Hybrid**
- `Nur Claude` → Breaker wird nie gestartet (spart OpenAI-Kosten)
- `Auto` → Director entscheidet basierend auf Risiko-Klassifikation (Standard)
- `Hybrid` → Breaker läuft immer mit (maximale Code-Qualität)
- Mode wird persistent in `gui_settings.json` gespeichert (`agent_mode` Feld)
- Mode beim Laden der Settings wiederhergestellt
- Backend-Logik: `agent_mode` überschreibt Director-Entscheidung
- Betrifft: `frontend.html` (CSS Toggle-Buttons + JS), `server.py` (Settings + Task-Thread)

### Schritt 66: Browser öffnet erst wenn Server bereit ist
- Problem: `webbrowser.open()` wurde vor `uvicorn.run()` aufgerufen → 404 im Browser
- Fix: Daemon-Thread pollt den Server (max 15s, alle 0.5s) und öffnet Browser erst bei Antwort
- Dritte Person sieht jetzt sofort die GUI, kein manuelles Neuladen nötig
- Betrifft: `server.py` (`start_gui`)

### Schritt 67: OpenAI-Modell-Einstellungen bleiben persistent
- Problem: Gespeicherte OpenAI-Modelle (z.B. gpt-5.4) gingen bei GUI-Neustart verloren
- Ursache: Dropdowns hatten beim Laden nur statische Optionen (o3, gpt-4o-mini), gespeicherter Wert existierte nicht → Fallback auf erstes Element
- Fix: `loadSettings()` ruft zuerst `loadOpenAIModels()` auf, dann werden gespeicherte Werte gesetzt
- Falls gespeichertes Modell nicht in API-Liste, wird es als "(gespeichert)" Option eingefügt
- Betrifft: `frontend.html` (`loadSettings`, `loadOpenAIModels`)

### Schritt 68: Drei kritische GUI-Fixes (Stop/Mode/Modell)
- **Fix 1 — Stop stoppt alles:** Abbruch-Checks nach Builder, vor Breaker, vor Conformance/Judge. Stop beendet jetzt den gesamten Task, nicht nur den Builder.
- **Fix 2 — Mode-Anzeige:** Director zeigt jetzt den effektiven GUI-Mode ("Nur Claude", "Hybrid", "Auto → builder_only") statt den internen Director-Modus.
- **Fix 3 — Breaker-Modell aus GUI:** `model="o3"` war in `breaker/agent.py` hardcoded. Jetzt wird das in den GUI-Settings gewählte Modell (`openai_model`) und Timeout an den Breaker übergeben. Agent-Label zeigt dynamisch "OpenAI gpt-5.4" statt immer "OpenAI o3".
- `run_breaker()` akzeptiert jetzt `model` und `timeout` Parameter
- `_run_breaker_attempt()` nutzt diese Parameter statt Hardcoded-Werte
- Import-Konflikt behoben: `run_breaker` Variable vs `run_breaker` Funktion → umbenannt
- Betrifft: `server.py` (Task-Thread komplett überarbeitet), `breaker/agent.py` (model/timeout parametrisiert)

### Schritt 69: Session-Wiederverwendung (--resume)
- Problem: Jeder Task startete eine neue Claude Code Session → Projekt wurde jedes Mal komplett neu gelesen
- Fix: Session-ID wird aus dem stream-json Output extrahiert und pro Repo gespeichert (`_repo_sessions` dict)
- Bei Folge-Tasks wird `--resume <session_id>` an Claude CLI übergeben → vorheriger Kontext bleibt erhalten
- GUI zeigt "Setzt vorherige Session fort..." statt "Baut..." wenn eine Session existiert
- Nach erfolgreichem Task: "Session gespeichert — Folge-Tasks nutzen den vorhandenen Kontext."
- Funktioniert für alle Projekte, keine CLAUDE.md nötig
- Betrifft: `server.py` (`_repo_sessions`, `_run_builder_with_events` mit --resume)

### Schritt 70: Projekt-Management + Historie
- **Projekt-Registry:** Persistente Liste aller Projekte in `~/.config/omads/projects.json` (id, name, path, created, last_used)
- **Projekt-Historie:** Jeder Task und Chat wird pro Projekt in `~/.config/omads/history/<id>.jsonl` geloggt mit Zeitstempel
  - `user_input` → was der Nutzer eingegeben hat
  - `task_result` → Ergebnis mit decision, score, files_changed, breaker_findings
  - `chat` → Frage + Antwort + Modell
- **Sidebar:** Links im GUI, zeigt alle Projekte (sortiert nach last_used), klickbar zum Wechseln
- **Neues Projekt anlegen:** Modal mit Namensfeld + Ordner-Picker, Auto-Name aus Ordner
- **Historie-Panel:** Unten in der Sidebar, zeigt Verlauf des aktiven Projekts mit Zeitstempel
  - Farbige Badges: accept (grün), reject (rot), escalate (gelb), chat (blau)
- **REST-Endpoints:** `/api/projects` (GET/POST), `/api/projects/switch` (POST), `/api/projects/{id}` (DELETE), `/api/projects/{id}/history` (GET)
- Chat wird beim Projektwechsel geleert
- Betrifft: `server.py` (Projekt-Registry + Historie + 5 neue Endpoints), `frontend.html` (Sidebar + Historie + Neues-Projekt-Modal)

### Schritt 71: Live-Log Panel (Debug-Ansicht)
- **Ein-/ausblendares Panel** rechts neben dem Chat, über "Live-Log" Button im Header
- Zeigt 1:1 was Claude Code und OpenAI tun — wie im Terminal, mit Zeitstempel (Datum + Uhrzeit)
- **3 Tabs:** Alle | Claude Code | OpenAI — zum Filtern nach Agent
- **Geloggte Events:** task_start, stream_text, stream_tool (mit Details), agent_status, agent_activity, stream_cost, task_complete, task_stopped, task_error, chat_response
- **Farbcodierung:** Tools gelb, Errors rot, Text normal, Agents farbig (Claude blau, OpenAI magenta, System gelb)
- **Tool-Results:** Builder streamt jetzt auch Tool-Ergebnisse (z.B. Dateiinhalt nach Read) als [Result] Vorschau
- Panel teilt sich 50/50 mit dem Chat wenn geöffnet
- Schließbar über × Button oder erneuten Klick auf "Live-Log"
- Betrifft: `frontend.html` (CSS + HTML + JS), `server.py` (Tool-Result Events)

### Schritt 72: Builder ↔ Breaker Retry-Loop (3 Ebenen)
- **Architektur-Entscheidung:** Spec sagt "kein autonomes Refixing" und linearer Flow. Lösung: `--resume` auf dieselbe Claude Code Session mit Breaker-Findings als Fix-Prompt → neuer Task im Ledger, aber effizient (cached Tokens, kein Neulesen)
- **Ebene 1 — Prävention:** `_get_historical_findings()` lädt die letzten 10 Breaker-Findings aus `data/breaker/*.json` und fügt sie dem Builder-Prompt hinzu. Builder vermeidet bekannte Fehler bevor er sie macht.
- **Ebene 2 — Reaktion:** Wenn Breaker medium/high Findings meldet → `_build_fix_prompt()` erzeugt Fix-Prompt mit allen Findings + Severity-Icons. Builder wird via `--resume <session_id>` fortgesetzt (kennt bereits alle Dateien, ~80% günstiger).
- **Ebene 3 — Escalation:** Nach max. N Runden (einstellbar via GUI) → weiter zum Judge. User bekommt Meldung "Max. Korrekturrunden erreicht".
- **Retry-Logik:** Nur medium/high Findings lösen Retry aus. Low Findings → direkt zum Judge. Keine Findings → sofort raus aus dem Loop.
- **GUI-Einstellung:** Neues Dropdown "Max Korrekturrunden" im Claude Code Tab (0-5, Standard: 2)
- **Stop-Button:** `_task_cancelled` wird in jeder Loop-Iteration geprüft → vollständiger Abbruch von Builder UND Breaker
- Betrifft: `server.py` (Retry-Loop, `_get_historical_findings`, `_build_fix_prompt`, `override_prompt` Parameter), `frontend.html` (Max-Retries Setting)

### Schritt 73: Session-Persistenz (--resume über GUI-Neustarts hinweg)
- **Problem:** `_repo_sessions` war nur im RAM → bei GUI-Neustart gingen alle Session-IDs verloren → Claude Code las bei jedem Neustart alles nochmal
- **Lösung:** Sessions werden persistent in `~/.config/omads/sessions.json` gespeichert (`_load_sessions`, `_save_sessions`)
- **Verifiziert:** `--resume` funktioniert korrekt — Claude Code erinnert sich an vorherige Konversation (getestet mit "merke dir 42" → "welche Zahl?" → "42")
- **Effekt:** Folge-Tasks im selben Repo brauchen kein erneutes Datei-Lesen, ~80% weniger Tokens
- Betrifft: `server.py` (`_SESSIONS_PATH`, `_load_sessions`, `_save_sessions`)

### Schritt 74: Historie im Chat-Stream statt Sidebar
- **Vorher:** Separater "Verlauf"-Bereich unten in der Sidebar — schlecht sichtbar, nicht scrollbar
- **Nachher:** Historie wird direkt im Chat-Fenster gerendert — wie bei Codex/ChatGPT
- Chronologisch (älteste oben, neueste unten), frei scrollbar
- **Datums-Trenner** bei neuem Tag (z.B. "2026-03-15")
- **Zeitstempel** auf jeder Nachricht (grau, klein)
- User-Nachrichten als Chat-Bubbles, Task-Ergebnisse als Result-Banner, Chat-Antworten mit Agent-Label
- "Verlauf (N Einträge)" Trennlinie oben, "Jetzt" Trennlinie unten
- Sidebar zeigt nur noch Projekte (+ Neu), keine Historie mehr
- Beim Projekt-Wechsel: Chat wird geleert und Historie des neuen Projekts geladen
- Betrifft: `frontend.html` (CSS: `msg-timestamp`, `msg-history-divider`; JS: `loadHistoryIntoChat` ersetzt `renderHistory`)

### Schritt 75: Kosten-Tracking persistent + pro Projekt
- **Problem 1:** Usage-Daten nur im RAM → bei GUI-Neustart auf Null
- **Problem 2:** Inkonsistente Dezimalstellen (Header 3, Settings 4)
- **Problem 3:** Keine Projekt-spezifischen Kosten
- **Lösung:**
  - Usage persistent in `~/.config/omads/usage.json` (`_load_usage`, `_save_usage`)
  - Neues Feld `by_project` — jedes Projekt hat eigene Kosten-Daten (claude_tasks, openai_calls, tokens, cost, by_model)
  - `_track_project_usage()` wird bei jedem Tracking-Call aufgerufen
  - `_get_usage_with_project()` liefert Usage + aktive Projekt-Daten für WebSocket-Events
  - Kosten-Tab zeigt jetzt "Aktives Projekt" (oben) + "Gesamt über alle Projekte" (unten)
  - Header zeigt Projekt-Kosten statt Gesamtkosten
  - Dezimalstellen überall auf 4 vereinheitlicht ($0.0000)
  - "Kosten zurücksetzen" Button im Kosten-Tab
  - `/api/usage/reset` Endpoint
- **Fix:** `max_tokens` → `max_completion_tokens` für neuere OpenAI-Modelle (gpt-5.4)
- **Verifiziert:** Chat mit gpt-5.4 → Kosten korrekt getrackt: $0.000196, 96 In / 19 Out Tokens, pro Projekt zugeordnet
- Betrifft: `server.py` (Usage-Persistenz, Projekt-Tracking, max_completion_tokens), `frontend.html` (Kosten-Tab, Dezimalstellen)

### Schritt 76: Markdown-Formatierung im Chat
- **Problem:** Formatierter Text (Markdown, Code-Blöcke, Listen, Fett/Kursiv) wird im Chat komplett unformatiert dargestellt — alles als flacher Text ohne Struktur
- **Ursache:** `textContent` und `esc()` rendern alles als reinen Text, Zeilenumbrüche gehen verloren
- **Lösung:** Neue `formatMsg()`-Funktion im Frontend, die einfaches Markdown rendert:
  - Code-Blöcke (` ``` `) → `<pre><code>` mit Styling
  - Inline-Code (`` ` ``) → `<code>` mit Background
  - Fett (`**text**`) → `<strong>`
  - Kursiv (`*text*`) → `<em>`
  - Überschriften (`#`, `##`, `###`) → fettgedruckt mit Größen
  - Listen (`-`, `*`) → Bullet Points (`•`)
  - Nummerierte Listen → beibehalten
  - Zeilenumbrüche → `<br>`
- Alle Render-Stellen verwenden jetzt `formatMsg()` statt `esc()`:
  - User-Nachrichten (live + History)
  - Agent-Antworten (`addAgent`)
  - Chat-History (Fragen + Antworten)
- Sicherheit: Text wird zuerst via `esc()` HTML-escaped, dann werden nur Markdown-Muster in HTML umgewandelt (kein XSS-Risiko)
- Betrifft: `frontend.html` (CSS + JavaScript)

### Schritt 77: OpenAI Reasoning-Model-Kompatibilität
- **Problem:** Reasoning-Modelle (o1, o3, o4) unterstützen weder `max_tokens`/`max_completion_tokens` noch `temperature`. Wenn ein User in den GUI-Einstellungen ein Reasoning-Modell als Chat-Modell wählt (z.B. o3-mini, o4-mini), crasht der Chat-Handler.
- **Lösung:**
  - Erkennung via `model.startswith(("o1", "o3", "o4"))` → `is_reasoning` Flag
  - Reasoning-Modelle: kein `max_completion_tokens`, kein `temperature`, `developer`-Rolle statt `system`
  - Normale Modelle: `max_completion_tokens` + `temperature` wie bisher
  - API-Timeout für Reasoning-Modelle auf 60s erhöht (statt 15s), da sie länger denken
  - Sync API-Call in `asyncio.run_in_executor()` ausgelagert, damit der WebSocket-Event-Loop nicht blockiert wird
  - Traceback-Logging im Exception-Handler hinzugefügt
- **Getestet:** Alle 5 Modelltypen funktionieren fehlerfrei durch die GUI:
  - ✓ gpt-4o-mini (Standard-Chat)
  - ✓ gpt-5.4 (neuestes)
  - ✓ o3-mini (Reasoning)
  - ✓ o4-mini (Reasoning)
  - ✓ gpt-4.1-nano (Budget)
- Betrifft: `server.py` (Chat-Handler `_handle_chat_ws`)

### Schritt 78: Chat von OpenAI API auf Claude CLI umgestellt
- **Vorher:** Chat-Fragen gingen an OpenAI API (gpt-5.4 etc.) → kostete Geld pro Nachricht, brauchte API-Key, hatte kein Projekt-Kontextwissen
- **Nachher:** Chat läuft über Claude CLI (`claude -p`) → kostenlos (Claude-Abo), kennt das Projekt, Session bleibt persistent
- **Architektur jetzt:**
  - Chat → Claude CLI (kostenlos, Session-persistent via `--resume`)
  - Builder → Claude CLI (kostenlos, separater Prozess pro Task)
  - Breaker → OpenAI API (bewusst anderer Anbieter für unabhängige Prüfung)
- **Technische Details:**
  - `_handle_chat_ws()` komplett neugeschrieben: ruft `_run_claude_chat()` auf
  - `_run_claude_chat()`: startet `claude -p <text> --output-format json --model <model>`
  - Session-Persistenz: `--resume <session_id>` über `~/.config/omads/chat_sessions.json`
  - Automatischer Fallback: wenn Session ungültig, Neustart ohne `--resume`
  - Subprocess in `run_in_executor()` für non-blocking async
  - Agent-Label zeigt jetzt "Claude sonnet/opus" statt "gpt-5.4"
  - Neuer Usage-Typ `claude_chat` für Projekt-Tracking (ohne Kosten)
- **Auch behoben:** `_is_question_or_chat()` Heuristik verbessert — kurze Sätze mit Aktionsverben aber ohne Tech-Begriffe werden als Chat erkannt ("schreib HI" → Chat, "schreib einen Login-Endpunkt" → Task)
- **Getestet:**
  - ✓ "Was ist Python?" → Claude opus antwortet korrekt (~2s)
  - ✓ "Worüber haben wir gesprochen?" → erinnert sich (Session-Persistenz)
  - ✓ "sag HI" → Chat (kein Builder-Start)
  - ✓ "schreib noch mal HI" → Chat (kein Builder-Start)
  - ✓ "HI" → Chat
- Betrifft: `server.py` (Chat-Handler, Session-Persistenz), `cli/main.py` (Heuristik)

### Schritt 79: GUI-Bereinigung — OpenAI Chat-Dropdown entfernt
- **Problem:** In den GUI-Einstellungen konnte man noch ein OpenAI Chat-Modell (gpt-5.4 etc.) auswählen, obwohl Chat jetzt über Claude CLI läuft
- **Lösung:**
  - Chat-Modell Dropdown (`sChat`) komplett entfernt
  - Chat-Temperature und Chat-Max-Tokens Eingabefelder entfernt
  - `loadOpenAIModels()` vereinfacht — befüllt nur noch Breaker-Dropdown
  - `saveSettings()` sendet keine Chat-Modell-Settings mehr
  - Hinweistext ergänzt: "Chat läuft über Claude CLI (kostenlos). OpenAI wird nur für den Breaker genutzt."
- **Zusätzlich behoben:**
  - Kosten-Anzeige im Header auf 0.0000 beim Start → `refreshUsage()` im Init hinzugefügt
  - `usage_update` Event nach jedem Chat gesendet → Header bleibt aktuell
  - 529 Overloaded Error → Retry mit Backoff (3 Versuche, 2-4s Pause)
- Betrifft: `frontend.html` (Settings-UI, Init), `server.py` (Retry, Usage-Events)

### Schritt 80: Result-Cards — Verständliche Ergebnisanzeige für Nicht-ITler
- **Vorher:** Einfache farbige Banner mit "Fertig! (Score: 85%)" / "Fehlgeschlagen" / "Bitte prüfen"
- **Nachher:** Interaktive Result-Cards mit verständlichen Texten, Details und Aktions-Buttons
- **3 Karten-Typen:**
  1. **Accept (grün)** — "Erfolgreich abgeschlossen" + "3 Dateien geändert · Alle Prüfungen bestanden"
     - Aufklappbare Details (Findings, geänderte Dateien)
     - Button: [Neuer Auftrag]
  2. **Reject (rot)** — "Nicht abgeschlossen" + "2 Probleme gefunden"
     - Aufklappbare Gründe (z.B. "Validierung fehlt", "Test schlägt fehl")
     - Button: [Anpassen & Neu starten] — lädt Task-Text zurück ins Eingabefeld
  3. **Escalate (gelb)** — "Deine Entscheidung nötig" + kontextabhängiger Untertitel
     - Aufklappbare Gründe (z.B. "Permission-Check entfernt", "Scope-Verletzung")
     - Buttons: [Freigeben] [Ablehnen] [Nachbessern lassen]
- **Progressive Disclosure:** Summary sofort sichtbar, Details aufklappbar via "▶ Gründe anzeigen"
- **Dynamische Texte:** Untertitel werden aus den tatsächlichen Findings generiert (nicht statisch)
- **Escalation-Workflow:** Freigeben/Ablehnen schreibt Human-Approval ins Ledger; Nachbessern lädt Task-Text ins Eingabefeld
- **Backend:** `task_complete`-Event erweitert um `summary`, `files_changed`, `findings`, `conformance_issues`
- **Ledger:** Neue Funktion `append_human_approval()` für GUI-Entscheidungen bei Escalations
- **Test-Endpoint:** `/api/test-result-cards` zum visuellen Testen aller drei Karten-Typen
- Betrifft: `frontend.html` (CSS + JS), `server.py` (Event-Daten, Escalation-Handler, Test-Endpoint), `ledger/ledger.py` (Human-Approval)

### Schritt 81: Multi-Provider Breaker (OpenAI + Anthropic)
- **Vorher:** Breaker konnte nur OpenAI-Modelle nutzen, API-Key nur über Umgebungsvariable
- **Nachher:** Breaker unterstützt OpenAI UND Anthropic-Modelle, Keys direkt in der GUI eingebbar
- **Breaker-Agent (`breaker/agent.py`):**
  - Provider-Erkennung: `_detect_provider()` erkennt automatisch ob OpenAI oder Anthropic
  - Zwei Adapter: `_run_breaker_openai()` und `_run_breaker_anthropic()`
  - Shared Utilities: `_read_patch()`, `_parse_findings()`, `_make_error_result()`
  - Anthropic-Modell-Mapping: sonnet→claude-sonnet-4-6, opus→claude-opus-4-6, haiku→claude-haiku-4-5-20251001
- **GUI Settings — API-Key-Verwaltung:**
  - Zwei Eingabefelder im Breaker-Tab: OpenAI Key + Anthropic Key
  - Keys werden maskiert angezeigt (••••••••xxxx), niemals im Klartext zurückgegeben
  - "Zeigen/Verbergen"-Toggle pro Key-Feld
  - Keys werden persistent in `~/.config/omads/gui_settings.json` gespeichert
  - Beim Start automatisch als Umgebungsvariablen gesetzt
  - ENV-Keys haben Vorrang, Hinweis "Aus Umgebungsvariable geladen" wird angezeigt
  - Maskierte Keys werden beim Speichern nicht überschrieben (Schutz vor Datenverlust)
- **Breaker-Modell-Dropdown:**
  - Kombinierte Liste mit Optgroups (Anthropic / OpenAI)
  - Anthropic-Modelle nur sichtbar wenn Key vorhanden
  - Provider-Badge zeigt aktuellen Anbieter (grün=OpenAI, braun=Anthropic)
  - Nach Key-Änderung wird Modell-Liste automatisch neu geladen
- **Server (`server.py`):**
  - `/api/breaker-models` gibt kombinierte Modell-Liste zurück
  - `/api/settings` GET: Keys maskiert, Source-Info (env/settings)
  - `/api/settings` POST: Maskierte Keys werden ignoriert, neue Keys sofort in ENV gesetzt
  - Alte Chat-Model-Defaults entfernt (openai_chat_model/temperature/max_tokens)
- Betrifft: `breaker/agent.py`, `gui/server.py`, `gui/frontend.html`

### Schritt 82: API-Key-Felder in GUI Settings
- API-Keys für OpenAI und Anthropic direkt im Breaker-Tab eingebbar
- Sicherheit: Keys werden serverseitig maskiert, nie an WebSocket-Broadcasts gesendet
- Persistenz: Keys in `~/.config/omads/gui_settings.json` gespeichert
- Legacy-Cleanup: Alte `openai_chat_*` Felder aus Settings entfernt

### Schritt 83: Breaker von OpenAI/Anthropic API auf Codex CLI umgestellt
- **Vorher:** Breaker nutzte OpenAI API (o3) oder Anthropic API (Sonnet/Opus/Haiku) — API-Kosten pro Review
- **Nachher:** Breaker nutzt Codex CLI als Subprocess mit read-only Sandbox — kostenlos via OpenAI-Abo
- **Breaker-Agent (`breaker/agent.py`):**
  - Komplett neu: `_run_breaker_codex()` mit `subprocess.run(["codex", "exec", "-s", "read-only", ...])`
  - Entfernt: `_run_breaker_openai()`, `_run_breaker_anthropic()`, `_detect_provider()`
  - Prompt via stdin (BREAKER_SYSTEM_PROMPT + User-Prompt kombiniert)
  - Robustes Parsing: JSON-Array aus Freitext extrahieren (find `[` ... `]`)
  - Fehlerbehandlung: FileNotFoundError (Codex nicht installiert), TimeoutExpired, returncode != 0
  - Modell optional (`-m` Flag nur wenn gesetzt, sonst Codex-Default gpt-5.4)
- **Prompt überarbeitet:**
  - "Du bist ein Code-Reviewer mit ausschließlich Leserechten"
  - Severity-Klassifikation hinzugefügt (critical/high/medium/low)
  - Keine Lösungsvorschläge, keine Refactoring-Vorschläge
- **GUI Backend (`server.py`):**
  - Settings bereinigt: `openai_model/temperature/max_tokens/api_key` → `codex_model` + `breaker_timeout`
  - `_apply_api_keys()` entfernt — kein API-Key nötig
  - `/api/breaker-models` + `/api/openai-models` Endpoints entfernt
  - OpenAI-Preistabelle + Kosten-Tracking entfernt → einfacher `codex_reviews` Zähler
  - `breaker_label = "Codex CLI"` (statt `f"OpenAI {model}"`)
- **GUI Frontend (`frontend.html`):**
  - Breaker-Tab: API-Key-Felder + Modell-Dropdown + Provider-Badge entfernt
  - Neu: statisches "Codex CLI" Badge + Modell-Textfeld (optional, Placeholder "Standard gpt-5.4")
  - Header: `OpenAI: X | $Y` → `Codex: X | via Abo`
  - Usage-Tab: Token/Kosten-Zeilen entfernt → nur noch "Claude Tasks" + "Codex Reviews" (beide "via Abo")
  - Log-Tabs: "OpenAI" → "Codex"
  - `loadOpenAIModels()`, `_updateProviderBadge()`, `toggleKeyVis()` entfernt
- **CLI (`main.py`):** "Breaker: OpenAI o3" → "Breaker: Codex CLI (read-only)"
- **Ergebnis:** Builder + Breaker laufen jetzt beide kostenlos über Abos — keine API-Kosten mehr

### Schritt 84: Architektur-Vereinfachung — Nur Modus 1 (Claude CLI + Codex Auto-Review)
**Grundsatzentscheidung:** Die bisherige Zwei-Pfad-Architektur (Chat vs. Task mit Heuristik-basiertem Routing) wird durch einen einzigen Modus ersetzt.

- **Vorher:** `_is_question_or_chat()` Heuristik entschied ob Eingabe Chat (→ Claude CLI einfach) oder Task (→ Director → Builder → Breaker → Judge Pipeline). Fehleranfällig: Chat-Nachricht löste versehentlich Task auf dem eigenen Projekt aus.
- **Nachher:** Alles geht direkt an Claude CLI. Kein Routing, kein Raten. Claude CLI entscheidet selbst ob es chattet oder coden soll.

- **Neue Architektur:**
  - **Claude Code CLI** = einziger Gesprächspartner (Chat + Code, alles via `--output-format stream-json`)
  - **Codex Auto-Review** = reviewt automatisch im Hintergrund nach jeder Code-Änderung
  - **Kein Director, kein Judge, kein Ledger** im GUI-Flow mehr (Pipeline-Code bleibt im Repo, wird nur nicht aufgerufen)
  - **Keine Chat-Erkennung** = `_is_question_or_chat()` komplett entfernt

- **server.py:**
  - WebSocket-Handler: `_is_question_or_chat` Import + Routing entfernt
  - `_run_task_thread()` + `_handle_chat_ws()` + `_run_builder_with_events()` → ersetzt durch `_run_claude_session_thread()`
  - Neue `_run_codex_auto_review()` — Codex CLI prüft automatisch nach Code-Änderungen
  - `_get_historical_findings()`, `_build_fix_prompt()` — entfernt (Pipeline-spezifisch)
  - Settings: `agent_mode`, `max_retries`, `breaker_enabled` → `auto_review` (bool)

- **frontend.html:**
  - Mode-Toggle (Nur Claude / Auto / Hybrid) → ersetzt durch statisches "Auto-Review" Badge
  - Retry-Loop-Einstellungen entfernt
  - Breaker-Tab → "Auto-Review" Tab
  - `breaker_enabled` Dropdown → `auto_review` Toggle
  - `setMode()` Funktion + `agentMode` Variable entfernt
  - CSS: `.mode-toggle`, `.mode-btn` Styles entfernt

- **cli/main.py:**
  - `_is_question_or_chat()` — 90 Zeilen Heuristik komplett gelöscht
  - `_handle_chat()` — OpenAI API (gpt-4o-mini) Funktion komplett gelöscht
  - Interaktive Session: alles geht an `_run_interactive_task()` (kein Routing)

- **Settings bereinigt:**
  - `gui_settings.json`: `agent_mode`, `max_retries`, `breaker_enabled` entfernt
  - Neu: `auto_review: true` (Codex reviewt automatisch)

- **Ergebnis:** Radikal vereinfachter Flow — User redet mit Claude CLI, Codex passt im Hintergrund auf. ~300 Zeilen Code entfernt.

### Schritt 85: Bugfixes — Claude CLI Integration + Session-Persistenz + Live-Log
Mehrere Bugs aus dem Live-Test behoben:

1. **API-Error 400 (`afk-mode-2026-01-31`):** `--permission-mode auto` sendet einen veralteten Beta-Header in Claude CLI v2.1.74. Fix: Default auf `bypassPermissions` geändert.

2. **`/api/status` Server-Crash:** Endpoint referenzierte `_settings["breaker_enabled"]`, das seit Schritt 84 nicht mehr existiert. Fix: `_settings.get("auto_review", True)`.

3. **Doppelte Antworten:** Claude-Antwort erschien als `stream_text` (live) UND als `chat_response` (final). Fix: `chat_response` nur wenn nichts live gestreamt wurde.

4. **"Abgebrochen" am Ende:** `task_stopped` mit leerem Text wurde als Unlock missbraucht, Frontend zeigte "Abgebrochen". Fix: neues `unlock` Event ohne Text.

5. **Codex-Findings nicht an Claude zurückgegeben:** Auto-Review zeigte Findings an, aber Claude bekam sie nie. Fix: `_run_codex_auto_review()` gibt Findings als String zurück, Caller sendet Fix-Prompt via `--resume`.

6. **Session-Fehler nach Neustart:** Abgelaufene Session-IDs in `chat_sessions.json` verursachten 400-Errors. Fix: Bei Session-Fehler wird Session automatisch zurückgesetzt.

7. **Historie verschwindet nach Server-Neustart:** `loadProjects()` wurde nur beim Page-Load aufgerufen, nicht beim WebSocket-Reconnect. Fix: `loadProjects()` auch in `ws.onopen`.

8. **Live-Log zu wenig Detail:** Tool-Details auf 120 Zeichen, Thinking auf 2000, Results auf 1000 gekürzt. Fix: Alle Truncation-Limits entfernt. CSS auf `white-space: pre-wrap` für mehrzeilige Darstellung.

9. **Stille Fehler:** `catch(e){}` in Frontend verschluckte Fehler. Fix: `console.error()` hinzugefügt.

10. **OMADS-Kontext für Claude:** Claude wusste nicht, dass es in OMADS läuft. Fix: `--append-system-prompt` mit OMADS-Kontext bei jedem CLI-Aufruf.

**Getestet:** Automatisierter WebSocket-Test mit Datei-Erstellung → Write-Tool (voller Inhalt) → Tool-Result → Codex Auto-Review (mit Heartbeat) → Claude Auto-Fix. Historie überlebt Server-Neustart (33 Einträge persistent).

---

## Aktuelle Dateistruktur

```
two agents/
├── CLAUDE.md                         ← Projektkontext für Claude
├── PROJEKTPROTOKOLL.md               ← Diese Datei
├── OMADS_Specification.md            ← Vollständige Architektur-Spezifikation
├── start prompt project.md           ← Initialer Projekt-Prompt
├── pyproject.toml                    ← Python-Projekt-Konfiguration
├── src/omads/
│   ├── cli/main.py                   ← CLI + Interaktive Session
│   ├── cli/task_input.py             ← Task Input Validation
│   ├── contracts/models.py           ← 9+ Pydantic-Datenverträge
│   ├── director/director.py          ← Task-Routing + Risikobewertung
│   ├── director/decomposition.py     ← Task-Zerlegung
│   ├── director/learning.py          ← Adaptives Routing
│   ├── builder/agent.py              ← Claude CLI Integration
│   ├── breaker/agent.py              ← Codex CLI Integration (read-only)
│   ├── conformance/checker.py        ← Regelprüfung (8 Checks)
│   ├── judge/judge.py                ← Entscheidung (accept/reject/escalate)
│   ├── ledger/ledger.py              ← Append-only Protokoll
│   ├── monitoring/                   ← Post-Merge + Risk Monitor
│   ├── validation/                   ← Self-Validation
│   ├── context/bundle.py             ← Kontext-Pakete für Agenten
│   ├── governance/                   ← Temporal, Scorecard, Confidence, Version-Breaks
│   ├── observability/event_bus.py    ← Event-Streaming
│   ├── isolation/container.py        ← Container-Sicherheit
│   ├── policies/                     ← Diff-Signature + AST-Semantic
│   ├── gui/server.py                 ← FastAPI Web-Backend
│   └── gui/frontend.html             ← Web-Frontend (SPA)
├── tests/                            ← 92 Tests (alle bestanden)
├── dna/                              ← DNA Store (Policies, Scorecards, etc.)
└── data/                             ← Laufzeitdaten (Ledger, Events, Patches)
```

---

## Kennzahlen

| Metrik | Wert |
|--------|------|
| Python-Dateien | ~30 |
| Codezeilen (geschätzt) | ~5.000+ |
| Pydantic-Modelle | 15+ |
| CLI-Commands | 9 (run, do, gui, status, ledger, validate, version-breaks, confirm-finding, confirm-version-break) |
| Unit-Tests | 92 (alle bestanden) |
| Architektur-Komponenten | 14 (CLI, Director, Builder, Breaker, Conformance, Judge, Ledger, Monitoring, Validation, Context, Governance, Observability, Isolation, Policies) |
| Echte E2E-Runs | 2 (Taschenrechner-Aufgaben) |
| Breaker-Kosten pro Run | kostenlos (Codex CLI via Abo) |
| Chat-Kosten pro Antwort | kostenlos (Claude CLI via Abo) |

---

## Wichtige Entscheidungen

1. **Claude CLI statt Claude API als Builder** — Claude Code CLI hat Dateisystem-Zugriff, kann selbst editieren, testen, committen. Die API kann das nicht.
2. **Codex CLI als Breaker** — Codex CLI mit read-only Sandbox prüft den Builder-Output. Kostenlos via OpenAI-Abo, kein API-Key nötig. Ersetzt OpenAI/Anthropic API seit Schritt 83.
3. **Claude CLI für alles** — Einziger Gesprächspartner für Chat + Code. Kostenlos über Claude-Abo, session-persistent via `--resume`. Kein Chat/Task-Routing mehr seit Schritt 84.
4. **Pydantic für Datenverträge** — Erzwingt Schema-Validierung, JSON-Serialisierung out of the box.
5. **Append-only Ledger** — Entscheidungen können nie rückwirkend geändert werden. Transparenz und Nachvollziehbarkeit.
6. **Web GUI statt VS Code Extension** — Bleibt in Python, kein TypeScript nötig, schneller umsetzbar, plattformunabhängig.
7. **learning_enabled: false** — Director-Learning braucht historische Daten. Wird erst aktiviert wenn genug Tasks gelaufen sind.

---

### GUI: AI-Modell-Konfiguration (2026-03-20)
In der Web-GUI können jetzt die Reasoning-Einstellungen beider Agents konfiguriert werden:
- **Claude Code:** `--effort` Flag (low/medium/high/max) — steuert die Denktiefe
- **Codex:** `model_reasoning_effort` (low/medium/high/xhigh) + `service_tier` (fast on/off) via `-c` Config-Overrides
- Einstellungen werden persistent gespeichert und an beide CLI-Aufrufe (Haupt-Session, Fix-Session, Auto-Review, Vergleichs-Review) weitergegeben

### GUI: Echtzeit-Token-Tracking + Rate-Limit-Anzeige (2026-03-20)
Kontingentverbrauch wird jetzt in Echtzeit angezeigt — keine Schätzungen, nur echte API-Werte:
- **Claude Code:** Token-Daten aus `stream-json` Events (`assistant.message.usage`, `result.total_cost_usd`, `rate_limit_event`)
- **Codex:** Token-Daten aus `--json` JSONL-Output (`turn.completed.usage`)
- **Header:** Kompakte Anzeige (Input/Output + Kosten + Rate-Limit-Reset-Countdown) für beide Agents
- **Settings-Tab:** Detaillierte Aufschlüsselung (Input, Output, Cache, Kosten, Rate-Limit-Status + Reset)
- **Rate-Limit-Integration:** `rate_limit_event` wird gespeichert und angezeigt — Status (OK/LIMIT), Reset-Countdown, 5h-Fenster-Info
- **Hinweis:** Exakte Prozente (wie "Session 45%") sind nicht über die CLI verfügbar — diese werden nur in der Claude Code App intern berechnet. Stattdessen zeigen wir echte Token-Zähler, Kosten und Rate-Limit-Status/Reset.
- In-Memory Session-Akkumulation über alle Aufrufe, Reset-Button, 30s Auto-Refresh
- API-Endpunkte: `GET /api/tokens` (inkl. Rate-Limit-Felder), `POST /api/tokens/reset`

### Projekt-Aufräumung: Legacy archiviert, GUI als Source of Truth (2026-03-20)
Das Projekt wurde radikal verschlankt. Die GUI (server.py + frontend.html) ist jetzt die einzige aktive Architektur:
- **Archiviert nach `_legacy/`:** 14 Pipeline-Module (builder, breaker, director, judge, conformance, ledger, etc.), 91 Tests, alte Spezifikation, DNA Store Dateien, Pipeline-Artefakte
- **Entkoppelt:** `cold_start.py` braucht kein `contracts.models` mehr (eigene OperationalPhase Enum)
- **Verschlankt:** `cli/main.py` nur noch GUI-Startbefehl + `_format_tool_use` Helper
- **Dependencies:** `openai` und `pydantic` entfernt (GUI braucht beides nicht)
- **Version:** 0.1.0 → 0.2.0
- **Aktive Dateien:** 7 Python-Dateien + 1 HTML-Frontend (statt ~45 Python-Dateien)

### Onboarding-System für neue Nutzer (2026-03-21)
OMADS soll auf GitHub veröffentlicht werden — dafür braucht es ein vollständiges Onboarding:
- **Health-Check Endpoint** (`GET /api/health`): Prüft ob `claude` und `codex` im PATH sind, liest Versionen aus, prüft Claude-Authentifizierung (`~/.claude/.credentials.json`). Gibt strukturiertes JSON zurück.
- **Onboarding-Banner** im Frontend: Beim Start wird `/api/health` abgefragt. Bei fehlenden CLIs zeigt ein gelb umrandetes Banner mit CLI-Cards (grün=OK, rot=fehlt) genau, was zu installieren ist — inkl. npm-Befehle und Links. Bei vollständiger Installation: kurze Bestätigung die nach 5s verschwindet.
- **README.md** erstellt: Vollständige Setup-Anleitung von A bis Z, plattformunabhängig (Windows, macOS, Linux). Enthält: Voraussetzungen-Tabelle, Schritt-für-Schritt Installation mit OS-spezifischen Details (collapsible), CLI-Einrichtung mit Abo-Hinweisen, OMADS-Einrichtung, Feature-Übersicht, Konfigurationsreferenz, Fehlerbehebung.

### Security-Review + Fixes (2026-03-21)
Umfassendes Security-Review durch das OMADS 3-Schritt-Verfahren (Claude Code → Codex → Synthese):

**Runde 1 — 6 Security-Fixes:**
- **Path Traversal**: `_validate_project_id()` mit Regex `^[a-zA-Z0-9_-]+$` in allen Projekt-Routen
- **DOM-XSS**: Inline `onclick` durch `data-*` Attribute + `addEventListener` ersetzt (Projekt-Delete-Button)
- **WebSocket Origin-Check**: Dynamisch aus Server-Port abgeleitet statt hardcoded
- **CORS Middleware**: `CORSMiddleware` mit localhost-only Regex
- **CSP Header**: `default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'` + `X-Content-Type-Options: nosniff`
- **Thread-Safety**: `_process_lock` konsistent auf alle `_active_process`-Zuweisungen angewendet

**Runde 2 — Concurrency-Fixes + Timeout-Entfernung:**
- **Codex-Timeout entfernt**: `breaker_timeout` (120s deadline) komplett entfernt — Codex wird nicht mehr abgewürgt, sondern arbeitet bis EOF oder manueller Abbruch. Setting aus Backend, Frontend und Config entfernt.
- **`_active_process` Reads gelockt**: Alle 5 ungelockten Reads (`refresh_claude_runtime_status`, `refresh_codex_runtime_status`, WebSocket-Handler für chat/review/apply_fixes`) mit `_process_lock` geschützt gegen TOCTOU-Race.
- **`_task_cancelled` Race behoben**: Reset (`= False`) in Worker-Threads und Set (`= True`) im Stop-Handler jetzt unter `_process_lock` — kein gegenseitiges Überschreiben mehr möglich.
- **`_connections` Thread-sicher**: Eigener `_connections_lock` eingeführt. `broadcast()` und `broadcast_sync()` arbeiten auf Snapshot-Kopien. `append()`/`remove()` unter Lock.

**Runde 3 — Performance-Fixes (aus OMADS Performance-Review):**
Diesmal hat Codex komplett fertig gearbeitet (kein Timeout mehr!) — die Timeout-Entfernung aus Runde 2 hat sich direkt bewährt. Codex hat das wichtigste Finding (stderr-Pipe-Deadlock) geliefert, das Claude übersehen hatte.
- **stderr-Pipe-Deadlock behoben** (Codex-Finding): Alle 6 `Popen`-Aufrufe von `stderr=subprocess.PIPE` auf `stderr=subprocess.DEVNULL` umgestellt. Vorher konnte ein voller stderr-Puffer (64KB auf Linux) den Child-Prozess blockieren und den Worker zum Hängen bringen.
- **Inactivity-Timeout für Codex** (Codex-Finding): 15 Minuten ohne Output → Prozess wird gekillt und `_active_process` freigegeben. Kein starrer Timer, sondern Safety-Net falls Codex wirklich hängt. In beiden Codex-Schleifen (3-Schritt-Review + Auto-Review) implementiert.
- **DOM-Limit Live-Log**: Max 1000 Einträge im Live-Log, älteste werden automatisch entfernt.
- **DOM-Limit Chat-Stream**: Max 500 Elemente im Chat-Bereich via `scrollDown()`. Verhindert unbegrenztes DOM-Wachstum bei langen Sessions.
- **scrollDown() Reflow-Throttling** (Codex-Finding): `requestAnimationFrame`-basiert statt bei jedem Token einen forced Layout-Reflow. Verhindert Rendering-Stau bei schnellem Streaming.
- **Timeout-Setting aus Frontend entfernt**: Das Eingabefeld "Timeout (Sekunden)" in den Codex-Einstellungen wurde entfernt, da es keine Funktion mehr hat.

### Runde 4: Full-Review Fixes (2026-03-21, Nachmittag)

Vierter OMADS-Review-Zyklus — vollständiger 3-Schritt-Review (Claude Code → Codex → Synthese). Codex hat erneut wichtige Findings geliefert, die Claude übersehen hatte.

**HOCH-Fixes:**
- **F1: Codex-Popen ohne `env=_build_cli_env()`** (Codex-Finding): Beide Codex-Subprozesse (3-Schritt-Review + Auto-Review) erbten die komplette Server-Umgebung statt der Env-Allowlist. Fix: `env=_build_cli_env()` hinzugefügt.
- **F2: `_pending_review_fixes` global statt pro Projekt** (Codex-Finding): Nach Projektwechsel konnten Review-Fixes aus Projekt A auf Projekt B angewendet werden. Fix: Dict `{repo_path: fixes_text}` statt einfacher String.
- **F3: `_active_process = None` vor `process.wait()`** (Beide): Race Window erlaubte neuen Task während alter noch terminiert. Fix: Erst `wait(timeout=30)`, bei Timeout `kill()` + `wait()`, dann erst `_active_process = None`. An 4 Stellen korrigiert.
- **F4: `_get_memory_path` nur `.name`** (Beide): Zwei Repos mit gleichem Ordnernamen überschrieben sich gegenseitig. Fix: SHA-256-Hash des vollständigen Pfads als Suffix (`api_a0ace028.md` statt `api.md`).

**MITTEL-Fixes:**
- **F5: `set_repo` ohne Validierung**: Kein `is_dir()`-Check, kein Home-Dir-Check. Fix: Konsistent mit `/api/browse` — nur Verzeichnisse innerhalb `$HOME` erlaubt. Auch in `create_project` nachgezogen.
- **F6: `_read_log` lädt komplette Datei**: Fix: `collections.deque(f, maxlen=500)` — liest zeilenweise, behält nur die letzten 500.
- **F7: `resultAction` XSS via onclick**: String-Interpolation in `onclick`-Attribute war anfällig für Intent-Strings mit Quotes. Fix: `data-*` Attribute + `addEventListener` (wie bei Delete-Button bereits gemacht).
- **F8: `loadProjects()` doppelt**: Beim Init + ws.onopen = Race Condition. Fix: Init-Aufruf entfernt, ws.onopen reicht.
- **F9: WebSocket-Reconnect ohne Backoff**: Fester 2s-Intervall bei Server-Down = hunderte Verbindungsversuche. Fix: Exponential Backoff (2s → 4s → 8s → ... → max 30s), Reset bei erfolgreicher Verbindung.

### Runde 5: Vollständiger Review-Zyklus (2026-03-21, Nachmittag)

Fünfter OMADS-Review-Zyklus. Codex hat erneut kritische Findings geliefert — insbesondere den `startswith`-Bug in der Pfad-Validierung, die erst in Runde 4 eingebaut wurde und von Anfang an fehlerhaft war.

**HOCH-Fixes:**
- **F1: `startswith`-Check Bug** (Codex-Finding): `/home/dani_backup` passierte den Home-Check, weil `startswith("/home/dani")` auch Geschwisterpfade matcht. Fix: `startswith(home + "/")` oder Gleichheit. An 3 Stellen korrigiert.
- **F2: `update_settings` validiert `target_repo` nicht**: Über den REST-Endpoint konnte `target_repo` auf beliebige Pfade gesetzt werden. Fix: Extra-Validierung mit `is_dir()` + korrektem Home-Check.
- **F3: `switch_project` ohne Pfad-Validierung**: Ein gespeichertes Projekt mit gelöschtem Verzeichnis wurde ungeprüft als `cwd` verwendet. Fix: `is_dir()`-Check vor dem Wechsel.
- **F4: Codex-Fehlermeldungen als Code-Findings**: Auth-/CLI-Fehler wurden an Claude zum "Fixen" weitergeleitet. Fix: Nur bei `returncode == 0` als Finding behandeln.
- **F5: Auto-Review-Prozess nicht in `_active_process`** (Codex-Finding): `stop` konnte den Codex-Auto-Review nicht abbrechen. Fix: Prozess nach Popen registrieren, in finally-Block freigeben.

**MITTEL-Fixes:**
- **F6: `send()` ignoriert `busy`-Flag**: User konnte während laufendem Task erneut senden → UI-Lock-Zustand. Fix: `if (busy) return;` + `case 'error'` in WebSocket-Handler.
- **F7: `outerHTML` durch CSS-Toggle**: DOM-Node-Replacement bei `lock()`/`unlock()` konnte UI brechen. Fix: Beide Buttons permanent im DOM, Ein-/Ausblenden per `display`.
- **F8: Logs im falschen Projekt bei Projektwechsel** (Codex-Finding): `broadcast_sync` las die Projekt-ID global statt task-gebunden. Fix: Projekt-ID beim Task-Start einfrieren, als `proj_id_override` durchreichen.
- **F9: `_read_history` und `get_ledger` tail-read**: Komplettes Einlesen wie bei `_read_log` vor Runde 4. Fix: `deque(f, maxlen=N)`.
- **F10: Onboard-Banner ohne `esc()`**: Version-/Pfad-Strings roh in `innerHTML`. Fix: Konsequent `esc()` anwenden.

---

### Quick-Fixes nach OMADS Self-Review (2026-03-21, Abend)

OMADS hat sich selbst analysiert und Verbesserungsvorschläge gemacht. Drei schnelle Punkte sofort umgesetzt:

- **LICENSE-Datei (MIT)**: Fehlte komplett — ohne Lizenz kann niemand den Code legal nutzen. `LICENSE` + `pyproject.toml` `license`-Feld ergänzt.
- **Keyboard-Shortcuts**: `Escape`-Taste stoppt laufenden Task (in `onKey()` ergänzt). `Ctrl+Enter` war bereits vorhanden (`Enter` ohne Shift).
- **Token-Verschwendung gefixt**: `_load_project_memory()` wurde bei JEDEM Prompt aufgerufen und per `--append-system-prompt` mitgesendet — auch wenn die Session via `--resume` fortgesetzt wurde. Claude CLI hat bei `--resume` bereits den vollen Kontext. Fix: Memory nur bei neuer Session laden (kein `session_id` vorhanden).

---

## Aktive Aufgabenliste ausgelagert

Die aktive Warteliste wurde nach `BACKLOG.md` verschoben, damit offene Arbeit auf GitHub und fuer alle Agenten sofort sichtbar ist.

`PROJEKTPROTOKOLL.md` dient ab hier nur noch als Historie und Projekttagebuch.

---

### Backlog-Repriorisierung + erste Testbasis (2026-03-22)

Die von Claude vorgeschlagene Warteliste wurde technisch eingeordnet und neu priorisiert. Fuer das Multi-Agent-Setup sind derzeit fehlende Tests und ungesicherter Shared State wichtiger als Komfort-Features wie Dark Mode oder Swagger.

- **BACKLOG neu priorisiert**: Fokus jetzt auf Shared State, Error Recovery, Pydantic-Models und erst danach dem Split von `server.py`.
- **Smoke-Tests eingefuehrt**: Erste Testbasis fuer Server-Start, Security-Header, Pfad-Validierung und Chat-Session-Persistenz angelegt.
- **README um Test-Workflow erweitert**: Kurzer Einstiegspunkt fuer Agenten und Entwickler, wie die neue Testbasis lokal ausgefuehrt wird.
- **`pyproject.toml` um Dev-Dependencies erweitert**: `pytest` und `httpx` als optionale Dev-Abhaengigkeiten dokumentiert.

Ziel dieser Runde: Nicht nur Features sammeln, sondern die naechsten Arbeiten absichern und fuer weitere Agenten nachvollziehbar machen.

---

### Shared-State-Hardening (2026-03-22)

Der naechste Kernpunkt fuer das Multi-Agent-Setup wurde umgesetzt: gemeinsam genutzter Server-State ist jetzt deutlich robuster gegen parallele Zugriffe aus Threads und Agenten.

- **Settings unter Lock + Snapshot-Helfer**: `_settings` wird nicht mehr ungeschuetzt direkt verteilt, sondern ueber konsistente Snapshots gelesen und atomar aktualisiert.
- **Chat-Sessions unter Lock**: `_chat_sessions` hat jetzt eigene Getter/Setter mit persistenter Speicherung unter Lock, damit Session-IDs sich nicht gegenseitig ueberschreiben.
- **Datei-Schreibpfade abgesichert**: Config, Projekt-Registry, GUI-Status, Chat-Sessions, Projekt-Memory sowie JSONL-History/Logs laufen jetzt ueber per-Datei-Locks und atomare Writes.
- **Smoke-Tests erweitert**: Testbasis prueft jetzt zusaetzlich persistierte Settings/Projekte, Log-Filterung und Session-Roundtrips.

Verifikation:
- `pytest` erfolgreich: 5 Tests gruen
- Syntax-Check erfolgreich: `python -m py_compile` ueber das Projekt-Python

---

### Graceful Error Recovery vervollstaendigt (2026-03-22)

Der naechste Backlog-Punkt wurde abgeschlossen: die wichtigsten Fehlerpfade fuehren jetzt nicht mehr stillschweigend zu einem scheinbar erfolgreichen Abschluss.

- **Claude-Task-Fehler sichtbar gemacht**: Ein nicht erfolgreich beendeter Claude-Prozess liefert jetzt einen klaren `task_error` mit Exit-Code statt nur einem irrefuehrenden "Fertig".
- **Review-Schritt-Fehler abgefangen**: Wenn Claude im Review-Schritt 1 oder in der Synthese scheitert, wird der Review sauber abgebrochen und sichtbar als Fehler gemeldet.
- **Fix-Lauf abgesichert**: Ein fehlgeschlagener automatischer Claude-Fix-Lauf meldet jetzt einen echten Fehler statt faelschlich "Fixes angewendet".
- **UI entsperrt bei Verbindungsabbruch**: Wenn die WebSocket-Verbindung waehrend eines laufenden Tasks wegbricht, entsperrt sich die UI und zeigt eine klare Reconnect-Meldung.
- **Tests erweitert**: Fehlerpfade fuer Claude-Task und Review-Schritt 1 sind jetzt in der Smoke-Testbasis enthalten.

Verifikation:
- `pytest` erfolgreich: 7 Tests gruen
- Syntax-Check erfolgreich: `python -m py_compile` ueber das Projekt-Python

---

### Pydantic-Request-Modelle (2026-03-22)

Die REST-Endpunkte arbeiten jetzt nicht mehr mit rohen Request-Dicts, sondern mit klaren Pydantic-Request-Modellen. Die Umstellung wurde bewusst kompatibel gehalten, damit die bestehende GUI nicht bricht.

- **Pydantic fuer Settings/Projekte**: `update_settings`, `create_project` und `switch_project` nutzen jetzt Request-Modelle statt `dict`.
- **Extra-Felder werden ignoriert**: Unerwartete Keys werden weiter weich behandelt, statt die GUI hart scheitern zu lassen.
- **Bestehendes API-Verhalten bleibt stabil**: Fehlende Projektdaten liefern weiterhin die bisherige fachliche Fehlermeldung statt einer unerwarteten Umstellung aller Flows.
- **Tests erweitert**: Smoke-Tests decken jetzt auch das Ignorieren unbekannter Request-Felder und den weiterhin stabilen Projekt-Create-Fehlerpfad ab.

Verifikation:
- `pytest` erfolgreich: 7 Tests gruen
- Syntax-Check erfolgreich: `python -m py_compile` ueber das Projekt-Python
