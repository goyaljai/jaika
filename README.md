# Gemini UI

A self-hosted web interface for Google's Gemini CLI. Chat with Gemini in your browser with session management, file uploads, PDF generation, skills system, and a full REST API.

**Free** — runs on Google's free Gemini tier (1,000 requests/day).

**Local** — everything runs on your machine, no data leaves your laptop.

---

## Quick Start

### macOS

```bash
# 1. Install prerequisites
brew install node python3 pandoc
brew install --cask basictex
eval "$(/usr/libexec/path_helper)"

# 2. Install Gemini CLI
npm install -g @google/gemini-cli

# 3. Run Gemini UI
curl -fsSL https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui.sh | bash
```

### Ubuntu / Debian

```bash
# 1. Install prerequisites
sudo apt update
sudo apt install -y nodejs npm python3 python3-venv python3-pip pandoc \
  texlive-latex-base texlive-fonts-recommended texlive-latex-extra

# 2. Install Gemini CLI
npm install -g @google/gemini-cli

# 3. Run Gemini UI
curl -fsSL https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui.sh | bash
```

### Fedora / RHEL

```bash
# 1. Install prerequisites
sudo dnf install -y nodejs npm python3 python3-pip pandoc texlive-scheme-basic

# 2. Install Gemini CLI
npm install -g @google/gemini-cli

# 3. Run Gemini UI
curl -fsSL https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui.sh | bash
```

### Automatic Install (any platform)

The shell script auto-detects your OS and installs everything for you:

```bash
curl -fsSL https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui.sh | bash
```

On first run, a browser window opens for Google sign-in. After that, go to `http://localhost:5001`.

---

## Features

| Feature | Description |
|---------|-------------|
| **Chat UI** | Dark-themed chat interface with Markdown rendering and syntax-highlighted code blocks |
| **Sessions** | Create, switch, delete independent chat sessions. Auto-named from first message. Persisted on disk |
| **File Upload** | Attach any file (images, code, PDFs) for Gemini to analyze |
| **PDF Generation** | File creation prompts auto-generate PDFs with LaTeX math support via pandoc |
| **Skills (.md)** | Upload SKILL.md files to give Gemini specialized knowledge |
| **Model Fallback** | Auto-switches between models when one hits capacity (429) |
| **REST API** | 15 endpoints for programmatic access |
| **Smart Detection** | Automatically detects "create a file" vs normal questions |

---

## API Reference

Base URL: `http://localhost:5001`

### Prompts

**Send a text prompt:**
```bash
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "what is 2+2"}'
```
```json
{"type": "text", "text": "2 + 2 is 4."}
```

**Send a file creation prompt:**
```bash
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "create a python script that prints fibonacci numbers"}'
```
```json
{"type": "files", "files": [{"serverName": "abc123_fibonacci.py", "originalName": "fibonacci.py"}]}
```

**Send a prompt with file attachment:**
```bash
# Upload first
curl -s -F "file=@screenshot.png" http://localhost:5001/api/upload
# Then reference the path
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "what is in this image?", "files": ["/path/from/upload/response"]}'
```

### File Operations

| Endpoint | Method | curl |
|----------|--------|------|
| Upload a file | POST | `curl -s -F "file=@myfile.py" http://localhost:5001/api/upload` |
| Download a generated file | GET | `curl -O http://localhost:5001/api/download/<serverName>` |

### Sessions

| Endpoint | Method | curl |
|----------|--------|------|
| List all sessions | GET | `curl -s http://localhost:5001/api/sessions` |
| Create new session | POST | `curl -s -X POST http://localhost:5001/api/sessions -H "Content-Type: application/json" -d '{"name": "My Chat"}'` |
| Get session with messages | GET | `curl -s http://localhost:5001/api/sessions/<id>` |
| Rename session | PUT | `curl -s -X PUT http://localhost:5001/api/sessions/<id> -H "Content-Type: application/json" -d '{"name": "New Name"}'` |
| Delete session | DELETE | `curl -s -X DELETE http://localhost:5001/api/sessions/<id>` |
| Add message | POST | `curl -s -X POST http://localhost:5001/api/sessions/<id>/messages -H "Content-Type: application/json" -d '{"role": "user", "text": "hello"}'` |
| Clear messages | DELETE | `curl -s -X DELETE http://localhost:5001/api/sessions/<id>/messages` |

### Skills

| Endpoint | Method | curl |
|----------|--------|------|
| List skills | GET | `curl -s http://localhost:5001/api/skills` |
| Upload skill | POST | `curl -s -F "file=@my-skill.md" http://localhost:5001/api/skills/upload` |
| Delete skill | DELETE | `curl -s -X DELETE http://localhost:5001/api/skills/my-skill` |

---

## How It Works

```
[Browser UI]  ←→  [Flask Server]  ←→  [Gemini CLI]  ←→  [Google Gemini API]
   HTML/JS          Python             Node.js             Cloud
```

1. Browser sends prompt to Flask via REST API
2. Flask classifies intent (text vs file creation) using local keyword matching
3. Flask spawns `gemini --prompt "..."` as a subprocess
4. For file requests, runs with `--yolo` (auto-approve tools) and detects new files
5. Markdown files are converted to PDF via `pandoc + pdflatex`
6. Response (text or file download link) sent back as JSON

---

## Project Structure

```
~/.gemini-ui/
├── app.py                  # Flask server
├── templates/
│   └── index.html          # Full UI (single file)
├── data/
│   ├── sessions/           # Chat history (JSON per session)
│   ├── uploads/            # User uploaded files
│   └── outputs/            # Generated PDFs and files
└── venv/                   # Python virtual environment
```

---

## Requirements

| Dependency | Version | Purpose |
|------------|---------|---------|
| Node.js | 18+ | Runs Gemini CLI |
| Python | 3.8+ | Runs Flask server |
| Pandoc | any | Markdown → PDF conversion |
| LaTeX | basictex / texlive | Renders LaTeX math in PDFs |
| Gemini CLI | latest | AI engine |

All installed automatically by the shell script.

---

## Re-running

After first install, just run the same command again:

```bash
curl -fsSL https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui.sh | bash
```

It skips already-installed dependencies and starts the server.

---

## License

MIT
