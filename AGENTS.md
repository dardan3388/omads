# AGENTS.md

Zentrale Regeln fuer alle Coding-Agenten in diesem Repository. Diese Datei ist verbindlich fuer Claude Code, Codex und weitere Agenten.

## Grundprinzip

- Git ist die einzige Quelle der Wahrheit fuer Aenderungen.
- Alle Agenten arbeiten auf demselben Projektstand.
- Bestehende oder parallele Aenderungen duerfen nicht unbewusst ueberschrieben werden.
- Vor jeder Arbeit muss der aktuelle Projektstand geprueft werden.

## Pflicht bei jedem Start

Vor jeder inhaltlichen Arbeit immer in dieser Reihenfolge:

1. `AGENTS.md` lesen.
2. `PROJECT_RULES.md` lesen, falls vorhanden.
3. `git status` pruefen.
4. `git log --oneline --decorate -5` pruefen, um die letzten Aenderungen zu verstehen.
5. Vor Dateiaenderungen zusaetzlich `git diff` pruefen.

## Git-Setup

- Repository muss initialisiert sein.
- Standard-Branch ist `main`.
- Wenn noch kein Remote verbunden ist, soll ein privates GitHub-Repository erstellt und als Remote verbunden werden.
- Niemals sensible Daten committen.

## Pflicht fuer parallele Agentenarbeit

- Gehe immer davon aus, dass andere Agenten gleichzeitig im selben Repo arbeiten.
- Revertiere oder ueberschreibe keine fremden Aenderungen ohne ausdrueckliche Freigabe.
- Vor Aenderungen aktiv Git verwenden:
- `git status`
- `git diff`
- `git log`

## Sync-Regel

- Bei sinnvollen Zwischenstaenden aktiv fragen: `Soll ich den aktuellen Stand synchronisieren?`
- Immer auf die Antwort des Nutzers warten.
- Wenn der Nutzer `sync jetzt` sagt, exakt diesen Ablauf ausfuehren:
- `git add .`
- `git commit -m "<klare, sinnvolle Beschreibung>"`
- `git push`

## .gitignore / Sicherheit

Folgende Muster muessen vorhanden bleiben oder ergaenzt werden:

- Python: `__pycache__/`, `*.pyc`, `venv/`, `.env`
- Node: `node_modules/`
- OS: `.DS_Store`, `Thumbs.db`
- Logs: `*.log`
- Secrets: `.env*`, `secrets.*`

Niemals committen:

- Passwoerter
- API Keys
- sonstige sensible Daten

## Arbeitsstil

- Kleine, gezielte Aenderungen statt grossflaechiger Ueberschreibungen.
- Vor dem Editieren den aktuellen Diff-Kontext verstehen.
- Nur Dateien anfassen, die fuer den aktuellen Auftrag noetig sind.
- Unklare Konflikte zuerst sichtbar machen statt raten.

## Ziel

- Minimaler Aufwand fuer den Nutzer.
- Einheitlicher Stand fuer alle Agenten.
- Nachvollziehbare, sichere und saubere Git-Historie.
