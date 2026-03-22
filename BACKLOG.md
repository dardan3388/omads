# BACKLOG.md

Zentrale, sichtbare Aufgabenliste fuer dieses Repository.

Diese Datei ist die einzige Quelle der Wahrheit fuer offene Arbeit, Prioritaeten und naechste Schritte.
`PROJEKTPROTOKOLL.md` bleibt Projekt-Historie und beschreibt umgesetzte Arbeit, aber keine aktive Warteliste mehr.

## Nutzung

- Neue Agenten lesen zuerst `AGENTS.md`, `PROJECT_RULES.md` und danach diese Datei.
- Offene Arbeit wird hier gepflegt, nicht versteckt in Verlaufstexten.
- Hoechste Prioritaet zuerst bearbeiten, sofern der Nutzer nichts anderes vorgibt.
- Erledigte Punkte werden hier entfernt oder als abgeschlossen dokumentiert und anschliessend in `PROJEKTPROTOKOLL.md` historisiert.

## Jetzt

- [ ] Graceful Error Recovery
  CLI-Crashs sollen die UI sauber entsperren und eine klare Fehlermeldung anzeigen.

- [ ] `server.py` in Module splitten
  Monolith in Bereiche wie config, projects, agents, websocket und rest aufteilen.

- [ ] Tests schreiben
  Mindestens Smoke-Tests fuer Server-Start, Pfad-Validierung und Session-Management.

## Danach

- [ ] Setup-Script / Docker
  Onboarding vereinfachen ueber `install.sh`, `Dockerfile` oder beides.

- [ ] Diff-Viewer
  Bessere Code-Anzeige direkt in der GUI.

- [ ] Dark/Light-Mode Toggle
  Umschaltbares Theme fuer die Oberflaeche.

- [ ] OpenAPI/Swagger-Docs
  REST-Endpoints dokumentieren und sichtbarer machen.

- [ ] Pydantic-Models
  Request-Validation von rohen Dicts auf klare Modelle umstellen.

- [ ] GitHub Issue-Templates und `CONTRIBUTING.md`
  Zusammenarbeit auf GitHub strukturieren.

## Technische Schulden

- [ ] `_chat_sessions` unter Lock
  Thread-Safety fuer Session-Dict absichern.

- [ ] Stream-Parsing in Helper-Funktion extrahieren
  Duplizierte Parsing-Logik zwischen Claude und Codex abbauen.

- [ ] `_settings` Thread-Lock
  Concurrent Reads/Writes absichern.

- [ ] `_append_log` File-Locking
  Parallel-Writes auf JSONL absichern.
