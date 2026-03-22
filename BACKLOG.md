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

- [ ] Graceful Error Recovery vervollstaendigen
  Die Basis ist vorhanden, aber restliche Fehlerpfade sollen konsistent sichtbare Fehlermeldungen und saubere UI-Entsperrung liefern.

- [ ] Pydantic-Models einfuehren
  Request-Validation fuer REST-Endpoints von rohen Dicts auf klare Modelle umstellen.

- [ ] `server.py` in Module splitten
  Monolith nach gesicherter Testbasis in Bereiche wie config, projects, agents, websocket und rest aufteilen.

## Danach

- [ ] Testabdeckung ausbauen
  Die ersten Smoke-Tests stehen. Als Naechstes koennen WebSocket-, Auto-Review-, Shared-State-Fehler- und UI-Fehlerpfade abgesichert werden.

- [ ] Setup-Script / Docker
  Onboarding vereinfachen ueber `install.sh`, `Dockerfile` oder beides.

- [ ] Diff-Viewer
  Bessere Code-Anzeige direkt in der GUI.

- [ ] Dark/Light-Mode Toggle
  Umschaltbares Theme fuer die Oberflaeche.

- [ ] OpenAPI/Swagger-Docs
  REST-Endpoints dokumentieren und sichtbarer machen.

- [ ] GitHub Issue-Templates und `CONTRIBUTING.md`
  Zusammenarbeit auf GitHub strukturieren.

## Technische Schulden

- [ ] Stream-Parsing in Helper-Funktion extrahieren
  Duplizierte Parsing-Logik zwischen Claude und Codex abbauen.
