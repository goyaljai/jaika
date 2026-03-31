# Jaika — Free Gemini AI in Your Browser

One command. No API keys. No billing. Just sign in with Gmail and go.

A self-hosted browser interface for Google's Gemini CLI — turning a terminal-only AI agent into a full-featured web app with chat, file uploads, PDF generation, skills, and a REST API. Completely free (1,000 requests/day).

## What is this?

Google released [Gemini CLI](https://github.com/google-gemini/gemini-cli) — a free, powerful AI agent that runs in your terminal. It gives you 60 requests/minute and 1,000 requests/day on a free Google account. But it's terminal-only.

**Gemini UI** wraps the CLI in a Flask web server and gives it a browser-based chat interface. You get all the power of Gemini CLI with a proper UI — sessions, file uploads, downloadable PDFs, custom skills, and a REST API you can hit from scripts, bots, or other tools.

### Why?

- **Non-technical users** can't use a CLI. This gives them a ChatGPT-like interface.
- **Developers** get a REST API with 15 endpoints for automation, scripting, and integration.
- **Students/Researchers** get auto-generated PDFs with properly rendered LaTeX math formulas.
- **Teams** can share skills (`.md` files) that give Gemini domain expertise on demand.
- **It's free.** No API keys to manage, no billing — just sign in with Google.

### Key Principles

- **Zero sudo** — everything installs to your home directory. No admin password needed.
- **Zero cloud** — runs entirely on your machine. No data leaves your laptop.
- **Zero cost** — uses Google's free Gemini tier.
- **One command** — single shell script sets up everything from scratch.

---

## Quick Start

```bash
curl -fsSL https://github.com/goyaljai/jaika/raw/refs/heads/main/gemini-ui.sh | bash
```

That's it. Works on macOS and Linux. The script:

1. Installs Node.js (via Homebrew on mac, nvm on Linux)
2. Installs Python 3 (via Homebrew on mac, checks existing on Linux)
3. Installs Gemini CLI (`@google/gemini-cli`)
4. Installs Pandoc (via Homebrew on mac, static binary on Linux)
5. Installs TinyTeX for LaTeX math rendering (userspace, no sudo)
6. Downloads the app from GitHub into `~/.gemini-ui/`
7. Creates a Python venv and installs Flask
8. Opens browser for Google OAuth (first run only)
9. Starts the server and opens `http://localhost:5001`

On subsequent runs, it skips already-installed dependencies and goes straight to launching.

---

## Features

### Chat Interface
Dark-themed, responsive chat UI with full Markdown rendering — headings, bold, italic, tables, blockquotes, and syntax-highlighted code blocks for all major languages. Powered by [marked.js](https://github.com/markedjs/marked) and [highlight.js](https://highlightjs.org/).

### Session Management
Create, switch, and delete independent chat sessions from the sidebar. Each session has its own memory. Sessions auto-name themselves from your first message. All history is stored as JSON files on disk — survives browser reload, works across different browsers, works in incognito.

### Smart Intent Detection
Every prompt is classified locally (zero API cost, zero latency) using keyword matching:
- **"What is gravity?"** → text mode — Gemini responds in chat
- **"Create a sorting algorithms cheatsheet"** → file mode — Gemini creates a file, you get a PDF download

No extra API call for classification. The detection looks for action words (`create`, `generate`, `write`, `make`) combined with object words (`file`, `document`, `script`, `cheatsheet`, `report`).

### File Upload & Analysis
Attach any file — screenshots, source code, PDFs, images — via the paperclip button. Files are uploaded to the server, and their paths are passed to Gemini for analysis. Gemini can read and understand the content of uploaded files.

### PDF Generation with LaTeX
When Gemini creates a document (e.g., a cheatsheet with math formulas), the backend:
1. Detects the new file by diffing `~/` before and after the Gemini run
2. Converts `.md` → `.pdf` using `pandoc + pdflatex` (TinyTeX)
3. LaTeX math renders properly: `f(x) = 1/σ√(2π) · e^(-(x-μ)²/2σ²)`
4. Serves the PDF for one-click download in the browser

### Skills System (SKILL.md)
Upload `.md` files as skills via the UI panel. Skills are installed into `~/.gemini/skills/` in the proper folder structure that Gemini CLI auto-discovers. When a prompt matches a skill's description, Gemini activates it automatically. Toggle skills on/off or delete them from the UI.

### Conversation Memory
Each session is linked to a Gemini CLI session via `--resume`. This means Gemini remembers everything said within a session — names, context, files created, preferences. Start a new session for a fresh conversation with no prior memory.

### Model Fallback
When the default Gemini model hits capacity (HTTP 429), the backend automatically tries the next model in the chain:
```
default → gemini-2.5-pro → gemini-2.5-flash → gemini-2.0-flash
```
The user never sees a capacity error — they just get a response.

### Noise Filtering
Gemini CLI outputs a lot of noise — MCP errors, auth logs, stack traces, retry messages. The backend filters all of it so users only see clean, relevant output.

### REST API
15 endpoints for full programmatic access. Use it from scripts, bots, CI/CD, or any HTTP client.

---

## Architecture

```
┌─────────────┐     HTTP/JSON     ┌──────────────┐    subprocess    ┌─────────────┐    OAuth+API    ┌─────────────┐
│  Browser UI │ ◄──────────────► │ Flask Server │ ◄──────────────► │ Gemini CLI  │ ◄────────────► │ Google API  │
│  HTML/JS    │                  │  Python      │                  │  Node.js    │                │  Cloud      │
└─────────────┘                  └──────────────┘                  └─────────────┘                └─────────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │  pandoc+pdflatex  │
                              │  (PDF generation) │
                              └──────────────────┘
```

1. Browser sends prompt to Flask via REST API
2. Flask classifies intent (text vs file) using local keyword matching
3. Flask spawns `gemini --prompt "..."` as a subprocess
4. For file requests, runs with `--yolo` and detects newly created files
5. `.md` files are converted to PDF via `pandoc + pdflatex`
6. Response (JSON with text or file download link) sent back to browser

---

## API Reference

Base URL: `http://localhost:5001`

### Prompts

**Text prompt:**
```bash
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "what is 2+2"}'
```
```json
{"type": "text", "text": "2 + 2 is 4.", "session_id": "abc123"}
```
Pass the returned `session_id` in subsequent calls to maintain conversation memory:

**Follow-up with memory:**
```bash
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "what did I just ask?", "session_id": "abc123"}'
```

**File creation prompt:**
```bash
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "create a python script that prints fibonacci numbers"}'
```
```json
{"type": "files", "files": [{"serverName": "abc123_fibonacci.py", "originalName": "fibonacci.py"}]}
```

**Prompt with file attachment:**
```bash
# Upload first
curl -s -F "file=@screenshot.png" http://localhost:5001/api/upload
# Then reference the path in your prompt
curl -s http://localhost:5001/api/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "what is in this image?", "files": ["/path/from/upload/response"]}'
```

### Files

| Action | Method | curl |
|--------|--------|------|
| Upload | POST | `curl -s -F "file=@myfile.py" http://localhost:5001/api/upload` |
| Download | GET | `curl -O http://localhost:5001/api/download/<serverName>` |

### Sessions

| Action | Method | curl |
|--------|--------|------|
| List all | GET | `curl -s http://localhost:5001/api/sessions` |
| Create | POST | `curl -s -X POST http://localhost:5001/api/sessions -H "Content-Type: application/json" -d '{"name": "My Chat"}'` |
| Get with messages | GET | `curl -s http://localhost:5001/api/sessions/<id>` |
| Rename | PUT | `curl -s -X PUT http://localhost:5001/api/sessions/<id> -H "Content-Type: application/json" -d '{"name": "New Name"}'` |
| Delete | DELETE | `curl -s -X DELETE http://localhost:5001/api/sessions/<id>` |
| Add message | POST | `curl -s -X POST http://localhost:5001/api/sessions/<id>/messages -H "Content-Type: application/json" -d '{"role":"user","text":"hello"}'` |
| Clear messages | DELETE | `curl -s -X DELETE http://localhost:5001/api/sessions/<id>/messages` |

### Skills

| Action | Method | curl |
|--------|--------|------|
| List | GET | `curl -s http://localhost:5001/api/skills` |
| Upload | POST | `curl -s -F "file=@my-skill.md" http://localhost:5001/api/skills/upload` |
| Delete | DELETE | `curl -s -X DELETE http://localhost:5001/api/skills/my-skill` |

---

## Project Structure

```
~/.gemini-ui/                    # Hidden install directory
├── app.py                       # Flask server (~450 lines)
├── templates/
│   └── index.html               # Full UI (single file, ~1100 lines)
├── data/
│   ├── sessions/                # Chat history (one JSON file per session)
│   ├── uploads/                 # User uploaded files
│   └── outputs/                 # Generated PDFs and files
├── venv/                        # Python virtual environment
└── server.log                   # Server logs
```

---

## Dependencies

| Tool | Install Location | Purpose |
|------|-----------------|---------|
| Node.js | Homebrew / nvm (`~/.nvm/`) | Runs Gemini CLI |
| Python 3 | Homebrew / system | Runs Flask server |
| Gemini CLI | npm global | AI engine |
| Pandoc | Homebrew / `~/.local/bin/` | Markdown to PDF |
| TinyTeX | `~/Library/TinyTeX/` or `~/.TinyTeX/` | LaTeX math in PDFs |
| Flask | `~/.gemini-ui/venv/` | Web framework |

All installed automatically by the shell script. Zero sudo on macOS. Zero sudo on Linux (except Python if not pre-installed — the script will tell you).

---

## Tech Stack

- **Frontend**: Vanilla HTML/CSS/JS, marked.js, highlight.js, html2pdf.js
- **Backend**: Python 3, Flask
- **AI**: Google Gemini CLI (@google/gemini-cli)
- **PDF**: Pandoc + TinyTeX (pdflatex)
- **Auth**: Google OAuth (handled by Gemini CLI)
- **Storage**: JSON files on disk

~1,550 total lines of code.

---

## License

MIT
