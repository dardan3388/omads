# PROJECT_RULES.md

Ergaenzende Projektregeln fuer dieses Repository.

## Repo-Kontext

- Projektname: `omads`
- Hauptbranch: `main`
- Es kann jederzeit eine bereits veraenderte Working Tree geben. Vor jedem Edit muss der aktuelle Diff gelesen werden.
- `BACKLOG.md` ist die zentrale, sichtbare Aufgabenliste fuer offene Arbeit.
- `PROJEKTPROTOKOLL.md` ist das Projekttagebuch fuer bereits erledigte oder dokumentierte Aenderungen.

## Zusammenarbeit

- Produktive Aenderungen in bereits modifizierten Dateien nur nach Diff-Pruefung erweitern, niemals blind ersetzen.
- Metadateien wie `AGENTS.md`, `PROJECT_RULES.md` und `.gitignore` duerfen gepflegt werden, ohne laufende Feature-Arbeit in `src/` zu stoeren.
- Bei Zwischenstaenden mit sinnvoller Historie den Nutzer aktiv fragen: `Soll ich den aktuellen Stand synchronisieren?`

## Git und Sicherheit

- Remote soll mit einem privaten GitHub-Repository verbunden sein.
- Secrets, lokale Umgebungen, Logdateien und generierte Artefakte bleiben ausserhalb von Commits.
