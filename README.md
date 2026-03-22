# OMADS — Orchestrated Multi-Agent Development System

OMADS ist eine Web-GUI, die zwei KI-Agenten orchestriert:

- **Claude Code CLI** — Builder-Agent (Chat, Code-Generierung, Debugging)
- **Codex CLI** — Auto-Reviewer (prüft automatisch nach jeder Code-Änderung)

Keine API-Keys nötig — beide CLIs laufen über bestehende Abos (Claude Pro/Max/Team + ChatGPT Plus/Pro).

![OMADS GUI](https://img.shields.io/badge/Port-8080-blue) ![Python](https://img.shields.io/badge/Python-3.11+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Projekt-Navigation

Wenn mehrere Agenten oder Entwickler am selben Stand arbeiten, sind diese Dateien die Einstiegspunkte:

- `BACKLOG.md` - zentrale Quelle fuer offene Aufgaben und Prioritaeten
- `PROJEKTPROTOKOLL.md` - historische Dokumentation bereits umgesetzter Arbeit
- `AGENTS.md` - verbindliche Arbeitsregeln fuer alle Coding-Agenten
- `PROJECT_RULES.md` - repo-spezifische Zusatzregeln

---

## Voraussetzungen

| Was | Mindestversion | Wozu |
|-----|---------------|------|
| **Python** | 3.11+ | OMADS Backend |
| **Node.js** | 18+ (Claude Code), 22+ (Codex) | Für die CLI-Tools |
| **npm** | (kommt mit Node.js) | Installation der CLIs |
| **Claude Code CLI** | aktuell | Builder-Agent |
| **Codex CLI** | aktuell | Auto-Reviewer (optional) |

### Abo-Voraussetzungen

OMADS nutzt **keine API-Keys**. Stattdessen authentifizieren sich beide CLIs über dein bestehendes Abo:

- **Claude Code CLI** → [Claude Pro, Max oder Team](https://claude.ai) Abo
- **Codex CLI** → [ChatGPT Plus oder Pro](https://chatgpt.com) Abo

---

## Installation

### 1. Python installieren

<details>
<summary><strong>Windows</strong></summary>

Lade Python 3.11+ von [python.org](https://www.python.org/downloads/) herunter.

**Wichtig:** Beim Installer "Add Python to PATH" anhaken.

```powershell
python --version   # Sollte 3.11+ zeigen
```
</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
# Mit Homebrew (empfohlen)
brew install python@3.12

# Oder von python.org herunterladen
```
</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
# Ubuntu / Debian / Linux Mint
sudo apt update && sudo apt install python3.12 python3.12-venv python3-pip

# Fedora
sudo dnf install python3.12

# Arch
sudo pacman -S python
```
</details>

### 2. Node.js installieren

<details>
<summary><strong>Windows</strong></summary>

Lade Node.js 22+ (LTS) von [nodejs.org](https://nodejs.org/) herunter und installiere es.

```powershell
node --version   # Sollte v22+ zeigen
npm --version
```
</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
brew install node@22
# oder von nodejs.org herunterladen
```
</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
# Ubuntu / Debian / Linux Mint (via NodeSource)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Oder via nvm (empfohlen für alle Distros)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install 22
nvm use 22
```
</details>

### 3. Claude Code CLI installieren

```bash
npm install -g @anthropic-ai/claude-code
```

**Erstmalige Anmeldung** — einmal im Terminal starten:

```bash
claude
```

Es öffnet sich ein Browser-Fenster zur Anmeldung mit deinem Claude-Abo. Nach erfolgreicher Anmeldung kannst du das Terminal wieder schließen.

### 4. Codex CLI installieren (optional)

```bash
npm install -g @openai/codex
```

**Erstmalige Anmeldung:**

```bash
codex
```

Melde dich mit deinem ChatGPT-Abo an. Codex wird als Auto-Reviewer verwendet — wenn es nicht installiert ist, funktioniert OMADS trotzdem, nur ohne automatisches Code-Review.

### 5. OMADS klonen und einrichten

```bash
# Repository klonen
git clone https://github.com/DEIN-USERNAME/omads.git
cd omads

# Virtual Environment erstellen
python3 -m venv .venv

# Virtual Environment aktivieren
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat

# OMADS installieren
pip install -e .
```

### 6. OMADS starten

```bash
omads gui
```

Die GUI öffnet sich automatisch im Browser unter **http://localhost:8080**.

Alternativ mit benutzerdefiniertem Port:

```bash
omads gui --port 9090
```

---

## Erster Start — Was passiert

Beim ersten Öffnen der GUI prüft OMADS automatisch, ob Claude Code CLI und Codex CLI verfügbar sind:

- **Alles grün** → Du kannst direkt loslegen
- **CLI fehlt** → Das Onboarding-Banner zeigt dir genau, was zu tun ist
- **Nicht authentifiziert** → Starte die CLI einmal manuell im Terminal zur Anmeldung

### Projekt registrieren

1. Klicke auf **"+ Neu"** in der Sidebar
2. Wähle das Verzeichnis deines Projekts
3. Fertig — du kannst jetzt mit Claude Code chatten

---

## So funktioniert OMADS

```
Browser (localhost:8080)
    ↕ WebSocket + REST
FastAPI Backend
    ├── Claude Code CLI (Builder — schreibt Code)
    └── Codex CLI (Reviewer — prüft Code, read-only)
```

1. Du schreibst eine Aufgabe im Chat
2. **Claude Code** arbeitet die Aufgabe ab (live gestreamt)
3. Nach Code-Änderungen startet **Codex** automatisch ein Review
4. Bei Problemen fixt Claude Code die Findings automatisch
5. Du siehst alles in Echtzeit — Token-Verbrauch, Tool-Aufrufe, Reviews

### Features

- Chat mit Claude Code (Live-Streaming)
- Automatisches Code-Review durch Codex nach jeder Änderung
- 3-Schritte Code-Review (Claude → Codex → Synthese)
- Echtzeit Token-Tracking (Input/Output/Cache/Kosten)
- Rate-Limit-Status mit Reset-Countdown
- Projekt-Verwaltung (mehrere Repos gleichzeitig)
- Einstellungen (Modell, Effort, Permissions, Codex-Config)
- Session-Memory (Claude erinnert sich an vorherige Gespräche)
- Live-Log (alle CLI-Events in Echtzeit)

---

## Konfiguration

Alle Einstellungen sind über die GUI steuerbar (Zahnrad-Icon). Persistent gespeichert in `~/.config/omads/`.

| Einstellung | Standard | Beschreibung |
|------------|----------|-------------|
| Claude Modell | sonnet | Claude-Modell (sonnet, opus, haiku) |
| Effort | high | Denktiefe (low, medium, high, max) |
| Max Turns | 25 | Arbeitschritte pro Aufgabe |
| Permission Mode | default | Berechtigungsmodus für Claude CLI |
| Codex Modell | (Standard) | gpt-5.4, o4-mini, gpt-4.1, etc. |
| Codex Reasoning | high | Reasoning-Level für Reviews |
| Auto-Review | aktiviert | Codex prüft nach jeder Änderung |
| Timeout | 120s | Max. Wartezeit für Codex-Reviews |

---

## Fehlerbehebung

### "Claude CLI nicht gefunden"

```bash
# Prüfe ob claude im PATH ist
which claude        # Linux/macOS
where claude        # Windows

# Falls nicht: neu installieren
npm install -g @anthropic-ai/claude-code
```

### "Codex CLI nicht installiert"

OMADS funktioniert auch ohne Codex — Auto-Review wird dann übersprungen. Zum Installieren:

```bash
npm install -g @openai/codex
```

### Port 8080 belegt

```bash
omads gui --port 9090
```

### Virtual Environment vergessen

Wenn `omads` nicht gefunden wird:

```bash
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\Activate.ps1   # Windows PowerShell
```

---

## Tech Stack

- **Python 3.11+** mit FastAPI + Uvicorn + WebSockets
- **Claude Code CLI** (`claude -p`, stream-json)
- **Codex CLI** (`codex exec`, --json, read-only)
- **Frontend:** Vanilla HTML/CSS/JS (kein Framework, keine Build-Tools)

---

## Lizenz

MIT
