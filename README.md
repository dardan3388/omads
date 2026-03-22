# OMADS — Orchestrated Multi-Agent Development System

OMADS is a web GUI that orchestrates two AI agents:

- **Claude Code CLI** — builder agent for chat, coding, and debugging
- **Codex CLI** — auto-reviewer that checks code after changes

No API keys are required. Both CLIs use existing subscriptions such as Claude Pro/Max/Team and ChatGPT Plus/Pro.

![OMADS GUI](https://img.shields.io/badge/Port-8080-blue) ![Python](https://img.shields.io/badge/Python-3.11+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Project Navigation

If multiple agents or developers work on the same repository state, these are the main entry points:

- `README.md` — onboarding, installation, and product overview
- `BACKLOG.md` — active priorities and next tasks
- `CHANGELOG.md` — notable shipped changes
- `docs/architecture.md` — current architecture and module boundaries
- `AGENTS.md` — binding workflow rules for coding agents
- `PROJECT_RULES.md` — repository-specific collaboration rules

---

## Backend Structure

The old GUI backend monolith has been split into focused modules:

- `src/omads/gui/server.py` — stable compatibility facade
- `src/omads/gui/app.py` — FastAPI app, middleware, and router wiring
- `src/omads/gui/routes.py` — REST endpoints
- `src/omads/gui/websocket.py` — WebSocket endpoint and GUI command handling
- `src/omads/gui/state.py` — persistent settings, project registry, GUI status, logs, chat sessions, project memory
- `src/omads/gui/runtime.py` — runtime state, broadcasts, and Claude/Codex task runners
- `src/omads/gui/launcher.py` — local startup via Uvicorn and browser opening

Functional changes should usually happen in the appropriate module instead of the facade in `server.py`.

---

## Tests

Run the smoke-test suite with `pytest`:

```bash
pip install -e ".[dev]"
pytest
```

The current tests cover server startup, security headers, settings and project validation, runtime status refresh, health/status/ledger endpoints, WebSocket guardrails, log filtering, chat-session persistence, mocked Codex auto-review outcomes, and mocked Claude/Codex review-fix handoff paths without requiring live CLI quota.

---

## Requirements

| Tool | Minimum version | Purpose |
|------|-----------------|---------|
| **Python** | 3.11+ | OMADS backend |
| **Node.js** | 18+ for Claude Code, 22+ for Codex | CLI tools |
| **npm** | bundled with Node.js | CLI installation |
| **Claude Code CLI** | current | Builder agent |
| **Codex CLI** | current | Auto-reviewer (optional) |

### Subscription Requirements

OMADS does **not** rely on API keys. Both CLIs authenticate through your existing subscriptions:

- **Claude Code CLI** → [Claude Pro, Max, or Team](https://claude.ai)
- **Codex CLI** → [ChatGPT Plus or Pro](https://chatgpt.com)

---

## Installation

### 1. Install Python

<details>
<summary><strong>Windows</strong></summary>

Download Python 3.11+ from [python.org](https://www.python.org/downloads/).

Make sure to enable **Add Python to PATH** during installation.

```powershell
python --version   # Should show 3.11+
```
</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
brew install python@3.12
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

### 2. Install Node.js

<details>
<summary><strong>Windows</strong></summary>

Download Node.js 22+ (LTS) from [nodejs.org](https://nodejs.org/).

```powershell
node --version   # Should show v22+
npm --version
```
</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
brew install node@22
```
</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
# Ubuntu / Debian / Linux Mint (via NodeSource)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Or via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install 22
nvm use 22
```
</details>

### 3. Install Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

For first-time login, run:

```bash
claude
```

This opens a browser window for authentication.

### 4. Install Codex CLI (optional)

```bash
npm install -g @openai/codex
```

For first-time login, run:

```bash
codex
```

If Codex is not installed, OMADS still works, but automatic review is skipped.

### 5. Clone and set up OMADS

```bash
git clone https://github.com/<your-username>/omads.git
cd omads

python3 -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Windows CMD
.venv\Scripts\activate.bat

pip install -e .
```

### 6. Start OMADS

The easiest local start on Linux or macOS is:

```bash
./start-omads.sh
```

This helper script creates `.venv` if needed, installs OMADS into it, and launches the GUI.

On Windows PowerShell, use:

```powershell
.\start-omads.ps1
```

If your execution policy blocks local scripts, use:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-omads.ps1
```

Standard Python developer start:

```bash
source .venv/bin/activate
omads gui
```

No shell activation:

```bash
.venv/bin/omads gui
```

Module form:

```bash
.venv/bin/python -m omads gui
```

Headless / remote start:

```bash
omads gui --host 0.0.0.0 --no-browser
```

The GUI opens automatically at **http://localhost:8080**.

You can also start it on a custom port:

```bash
./start-omads.sh --port 9090
```

Or with the installed CLI:

```bash
omads gui --port 9090
```

### Docker

OMADS now includes a basic Docker image for headless startup:

```bash
docker build -t omads .
docker run --rm -p 8080:8080 omads
```

Then open `http://localhost:8080`.

Important:

- The Docker image starts OMADS with `--no-browser`.
- This basic image is useful for trying the GUI or running the backend in a container.
- A fully polished Docker workflow for authenticated Claude Code / Codex usage and mounted project workspaces is still an open follow-up item.

### Common Start Mistakes

- `omads: command not found`
  You are either outside the repository, the local `.venv` is not activated, or OMADS was not installed into that environment yet. Use `./start-omads.sh` or `.venv/bin/omads gui`.

- `python: command not found`
  On many Linux systems the command is `python3`, not `python`.

- `>>>`
  That means you are inside the Python interpreter. Exit it with `exit()` or `Ctrl+D`, then run the OMADS command in the normal shell.

---

## First Launch

When the GUI opens for the first time, OMADS checks whether Claude Code CLI and Codex CLI are available:

- **Everything green** → start working immediately
- **CLI missing** → the onboarding banner explains what to install
- **Not authenticated** → open the CLI once in a terminal and complete login

### Register a Project

1. Click **+ New** in the sidebar
2. Choose your project directory
3. Start chatting with Claude Code

---

## How OMADS Works

```text
Browser (localhost:8080)
    ↕ WebSocket + REST
FastAPI Backend
    ├── Claude Code CLI (builder, writes code)
    └── Codex CLI (reviewer, read-only)
```

1. You send a task in the chat
2. **Claude Code** works on it with live streaming
3. After code changes, **Codex** starts an automatic review
4. If Codex finds issues, Claude can fix them
5. You see the full process in real time

### Features

- Chat with Claude Code
- Automatic Codex review after code changes
- Three-step review flow (Claude → Codex → synthesis)
- Real-time token and activity tracking
- Claude rate-limit status with reset countdown
- Multi-project management
- Settings for models, effort, permissions, and Codex config
- Session memory for continuing work
- Live logs of CLI events

---

## Configuration

All settings are controlled through the GUI and stored in `~/.config/omads/`.

| Setting | Default | Description |
|---------|---------|-------------|
| Claude model | `sonnet` | Claude model such as `sonnet`, `opus`, or `haiku` |
| Effort | `high` | Claude reasoning depth |
| Max turns | `25` | Working steps per task |
| Permission mode | `default` | Permission mode for Claude CLI |
| Codex model | default | Model override for Codex |
| Codex reasoning | `high` | Review reasoning level |
| Auto review | enabled | Run Codex after code changes |

---

## Troubleshooting

### "Claude CLI not found"

```bash
which claude        # Linux/macOS
where claude        # Windows

npm install -g @anthropic-ai/claude-code
```

### "Codex CLI not installed"

OMADS still works without Codex, but auto-review is skipped.

```bash
npm install -g @openai/codex
```

### Port 8080 already in use

```bash
omads gui --port 9090
```

### Virtual environment not activated

```bash
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\Activate.ps1   # Windows PowerShell
```

---

## Tech Stack

- **Python 3.11+** with FastAPI, Uvicorn, and WebSockets
- **Claude Code CLI** (`claude -p`, `stream-json`)
- **Codex CLI** (`codex exec`, `--json`, read-only)
- **Frontend:** vanilla HTML/CSS/JS without a build step

---

## License

MIT
