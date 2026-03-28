# Getting Started

This guide is the detailed setup reference for OMADS.

Use the main [README](../README.md) for the product overview and the quick public-facing entry point. Use this page when you want the full local setup, alternative start modes, Docker instructions, or troubleshooting help.

## Requirements

OMADS needs:

- Python 3.11+
- Node.js
- at least one authenticated CLI: `claude` or `codex`

Best experience: install both CLIs so OMADS can use builder switching and cross-agent review without feature gaps.

## 1. Install Python

### Windows

Download Python 3.11+ from [python.org](https://www.python.org/downloads/).

Make sure to enable **Add Python to PATH** during installation.

```powershell
python --version
```

### macOS

```bash
brew install python@3.12
```

### Linux

```bash
# Ubuntu / Debian / Linux Mint
sudo apt update && sudo apt install python3.12 python3.12-venv python3-pip

# Fedora
sudo dnf install python3.12

# Arch
sudo pacman -S python
```

## 2. Install Node.js

### Windows

Download Node.js 22+ (LTS) from [nodejs.org](https://nodejs.org/).

```powershell
node --version
npm --version
```

### macOS

```bash
brew install node@22
```

### Linux

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

## 3. Install Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

For first-time login, run:

```bash
claude
```

This opens a browser window for authentication.

## 4. Install Codex CLI

```bash
npm install -g @openai/codex
```

For first-time login, run:

```bash
codex
```

If Codex is not installed, OMADS still works for Claude-only usage, but any flow that depends on Codex as builder or reviewer will be unavailable.

## 5. Clone And Install OMADS

```bash
git clone https://github.com/dardan3388/omads.git
cd omads
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows PowerShell:

```powershell
git clone https://github.com/dardan3388/omads.git
cd omads
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## 6. Start OMADS

### Easiest local start on Linux or macOS

```bash
./start-omads.sh
```

This script creates `.venv` if needed, installs OMADS into it if needed, and starts the GUI.

### Windows PowerShell

```powershell
.\start-omads.ps1
```

If your execution policy blocks local scripts:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-omads.ps1
```

### Standard CLI start

```bash
source .venv/bin/activate
omads gui
```

Without shell activation:

```bash
.venv/bin/omads gui
```

Module form:

```bash
.venv/bin/python -m omads gui
```

Headless or remote start:

```bash
omads gui --host 0.0.0.0 --no-browser
```

Custom port:

```bash
omads gui --port 9090
```

The default URL is `http://localhost:8080`.

## Docker

OMADS includes a Docker image with Python, Node.js, Git, Claude Code CLI, and Codex CLI preinstalled:

```bash
docker build -t omads .
docker run --rm -p 8080:8080 omads
```

Then open `http://localhost:8080`.

Important notes:

- The Docker image starts OMADS with `--no-browser`.
- The default in-container project root is `/workspace`.
- The image includes `git`, `claude`, and `codex`.

For a reusable local container workflow with mounted auth directories and persistent OMADS state:

```bash
cp .env.docker.example .env
docker compose up --build
```

The bundled `compose.yaml` mounts:

- your selected workspace to `/workspace`
- `~/.claude` into the container for Claude Code authentication
- `~/.codex` into the container for Codex authentication
- a named Docker volume for persistent OMADS GUI state

## First Launch

When the GUI opens for the first time, OMADS checks whether Claude Code CLI and Codex CLI are available:

- **Everything green**: start working immediately.
- **CLI missing**: the onboarding banner explains what to install.
- **Not authenticated**: open the CLI once in a terminal and complete login.

To register a project:

1. Click **+ New** in the sidebar.
2. Choose your project directory.
3. Start chatting with your selected builder.

## Troubleshooting

### `omads: command not found`

You are either outside the repository, the local `.venv` is not activated, or OMADS was not installed into that environment yet.

Use:

```bash
./start-omads.sh
```

or:

```bash
.venv/bin/omads gui
```

### `python: command not found`

On many Linux systems the command is `python3`, not `python`.

### `>>>`

That means you are inside the Python interpreter. Exit it with `exit()` or `Ctrl+D`, then run the OMADS command in the normal shell.

### `Claude CLI not found`

```bash
which claude        # Linux/macOS
where claude        # Windows

npm install -g @anthropic-ai/claude-code
```

### `Codex CLI not installed`

OMADS still works without Codex, but any flow that needs Codex as builder or reviewer is unavailable.

```bash
npm install -g @openai/codex
```

### Port 8080 already in use

```bash
omads gui --port 9090
```

## Local API Docs

While OMADS is running, the FastAPI docs are available at:

- `http://localhost:8080/docs`
- `http://localhost:8080/redoc`
- `http://localhost:8080/openapi.json`
