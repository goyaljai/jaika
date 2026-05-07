<div align="center">

# 🤖 Jaika

### The AI Platform Builders Love

**Build with AI for free. Chat, files, memory, generation —**
**50+ REST endpoints. Drop-in OpenAI and Anthropic SDK support.**

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/version-2.0.1-green.svg)](https://github.com/goyaljai/jaika/releases)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/goyaljai/jaika/pulls)
[![50+ Endpoints](https://img.shields.io/badge/endpoints-50+-green.svg)](#api-reference)
[![3 SDK Routers](https://img.shields.io/badge/SDK_routers-3-orange.svg)](#sdk-compatibility)

**[Live Demo](https://35-207-202-131.sslip.io/)** · **[API Docs](README_API.md)** · **[Medium Article](medium.md)** · **[Report Bug](https://github.com/goyaljai/jaika/issues)**

</div>

---

## Sign in with Google — start building in seconds

```bash
curl -sL https://35-207-202-131.sslip.io/auth/script | bash
```

---

## Quick API

```bash
# Chat with AI
curl -X POST https://35-207-202-131.sslip.io/api/prompt \
  -H "X-User-Id: YOUR_USER_ID" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello!", "stream": false}'
```

```python
# Drop-in OpenAI replacement
import openai
client = openai.OpenAI(
    base_url="https://35-207-202-131.sslip.io/v1",
    api_key="YOUR_USER_ID"
)
resp = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

```bash
# AI file generation
curl -X POST https://35-207-202-131.sslip.io/api/generate/file \
  -H "X-User-Id: YOUR_USER_ID" \
  -d '{"prompt": "coffee shop landing page", "type": "html"}'
```

---

## Why Jaika?

| | Jaika | Hosted AI APIs |
|---|---|---|
| **Cost** | ~$0/month | $20–$100+ |
| **OpenAI SDK** | ✅ Drop-in | ✅ Native |
| **Anthropic SDK** | ✅ Drop-in | ✅ Native |
| **Voice** | ✅ Built-in | ❌ Separate service |
| **LangChain + RAG** | ✅ Native pipelines | ❌ Extra setup |
| **Files + Memory** | ✅ Per-user | ❌ Custom build |

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [SDK Compatibility](#sdk-compatibility)
- [Voice Pipeline](#voice-pipeline)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Features

### 💬 Chat & AI
- **50+ REST endpoints** — chat, memory, sessions, files, voice, generation, admin
- **LangChain pipelines with RAG** — composable chains for prompt routing, memory, tool use, and retrieval
- **Multi-model fallback** — Gemini 2.5 Flash → 2.5 Flash Lite → 3 Flash → 3.1 Flash Lite, automatic
- **Per-user personas** — `_persona` skill replaces the system prompt entirely, per user
- **Web grounding** — SerpAPI integration for real-time search
- **Streaming responses** — SSE streaming with markdown rendering

### 🗣 Voice & Audio
- **Real-time voice chat** — VAD auto-stop, sentence-split TTS, filler audio for zero perceived latency
- **ElevenLabs TTS** — cloned voice support with automatic key rotation and Gemini TTS fallback
- **STT** — Gemini Flash audio transcription

### 📁 Files & Generation
- **File upload** — images, PDFs, audio transcription, all inline in chat
- **AI file generation** — generate HTML, code, docs from a prompt
- **Image generation** — Gemini Imagen via chat
- **Video generation** — Veo 2 text-to-video and image-to-video
- **PDF export** — conversations to PDF via LaTeX

### 🔌 SDK & Integrations
- **3 SDK routers** — OpenAI, Anthropic, and Gemini-native drop-in compatible
- **Google OAuth** — secure login, automatic token refresh
- **Admin panel** — user management, model config, contacts
- **Bot pages** — public-facing chatbot widgets (no login required)
- **Mobile-first UI** — responsive, dark theme, split-layout

---

## Quick Start

### Prerequisites

- Python 3.10+
- Google OAuth credentials — [create here](https://console.cloud.google.com/apis/credentials)
- Gemini API access

### Install

```bash
git clone https://github.com/goyaljai/jaika.git
cd jaika
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
SECRET_KEY=your_random_secret_key

# Optional — falls back to Gemini TTS if not set
ELEVENLABS_API_KEY=your_key
ELEVENLABS_VOICE_ID=your_voice_id

# Optional — enables web search grounding
SERPAPI_KEY=your_key
```

### Run

```bash
# Development
python3 app.py

# Production
gunicorn --bind 0.0.0.0:5244 --workers 4 --threads 4 --timeout 120 app:app
```

Open `https://35-207-202-131.sslip.io/` — sign in with Google and start building.

---

## API Reference

50+ endpoints across chat, sessions, voice, files, generation, skills, memory, and admin.

See **[README_API.md](README_API.md)** for the full reference.

### Core Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/prompt` | Chat with AI (streaming or non-streaming) |
| `POST` | `/api/stt` | Speech-to-text |
| `POST` | `/api/tts` | Text-to-speech (ElevenLabs → Gemini fallback) |
| `GET/POST` | `/api/sessions` | Session management |
| `POST` | `/api/memory` | Store and retrieve user memory |
| `POST` | `/api/upload` | Upload files (images, PDFs, audio) |
| `POST` | `/api/generate/image` | Image generation (Imagen) |
| `POST` | `/api/generate/video` | Video generation (Veo 2) |
| `POST` | `/api/generate/file` | AI file generation (HTML, code, docs) |
| `POST` | `/api/fetch` | Fetch URL + optional AI analysis |
| `POST` | `/api/skills/upload` | Upload persona or skill |
| `GET` | `/goyaljai` | Public bot page (no login) |

---

## SDK Compatibility

Jaika exposes 3 SDK-compatible routers via `api_compat.py`. Change `base_url` — everything else stays the same.

**OpenAI SDK**
```python
from openai import OpenAI
client = OpenAI(
    base_url="https://35-207-202-131.sslip.io/v1",
    api_key="YOUR_USER_ID"
)
resp = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

**Anthropic SDK**
```python
from anthropic import Anthropic
client = Anthropic(
    base_url="https://35-207-202-131.sslip.io/v1",
    api_key="YOUR_USER_ID"
)
msg = client.messages.create(
    model="gemini-2.5-flash",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
```

**Gemini-native**
```bash
curl -X POST https://35-207-202-131.sslip.io/v1beta/models/gemini-2.5-flash:generateContent \
  -H "X-User-Id: YOUR_USER_ID" \
  -d '{"contents": [{"parts": [{"text": "Hello!"}]}]}'
```

---

## Voice Pipeline

```
User speaks
    │
    ▼
VAD detects silence
    │
    ▼
STT (Gemini Flash) ──► Filler audio plays instantly
    │                    ("Yeah, so umm..." in cloned voice)
    ▼
LangChain Pipeline
    ├── Memory retrieval (RAG)
    ├── Skill / persona routing
    ├── Tool use (search, files, image gen)
    └── Prompt assembly
    │
    ▼
LLM (Gemini, multi-model fallback)
    │
    ▼
Filler stops ──► ElevenLabs TTS streams audio
    │
    ▼
4s silence timeout ──► Call ends
```

---

## Architecture

```
Browser / curl / SDK
         │
         │  X-User-Id: <uid>
         ▼
┌──────────────────────────────────────────────┐
│              Flask App (app.py)              │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │ auth.py  │ │gemini.py │ │api_compat.py│  │
│  │ OAuth    │ │ Direct   │ │ OpenAI /    │  │
│  │ tokens   │ │ API      │ │ Anthropic / │  │
│  └──────────┘ └──────────┘ │ Gemini      │  │
│                             └─────────────┘  │
│  ┌──────────────────────────────────────┐    │
│  │   LangChain — prompt_engine.py       │    │
│  │   RAG · Memory · Guardrails · Skills │    │
│  └──────────────────────────────────────┘    │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│  │sessions  │ │ files.py │ │skills.py │     │
│  │per-user  │ │upload /  │ │personas  │     │
│  │history   │ │convert   │ │skills    │     │
│  └──────────┘ └──────────┘ └──────────┘     │
└──────────────────────────────────────────────┘
         │
         ▼
cloudcode-pa.googleapis.com  (Google Gemini)
```

### Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Flask, Gunicorn, Python 3.10 |
| **AI Orchestration** | LangChain (chains, memory, RAG, tool use) |
| **LLM** | Google Gemini (multi-model fallback) |
| **TTS** | ElevenLabs + Gemini TTS fallback |
| **STT** | Gemini Flash |
| **Auth** | Google OAuth 2.0 (PKCE, auto token refresh) |
| **Hosting** | GCP |
| **SDK compat** | OpenAI, Anthropic, Gemini |

---

## Project Structure

```
jaika/
├── app.py              # Main Flask app, all routes
├── auth.py             # Google OAuth, token management
├── gemini.py           # Gemini API — chat, stream, image, video, TTS, STT
├── prompt_engine.py    # LangChain — prompt builder, RAG, memory, guardrails
├── skills.py           # Per-user skills and persona system
├── sessions.py         # Session and message storage
├── files.py            # File upload and management
├── pdf.py              # PDF export via LaTeX
├── api_compat.py       # OpenAI / Anthropic / Gemini SDK routers
├── grpc_server.py      # gRPC server
├── templates/
│   ├── index.html      # Main chat UI
│   ├── bot.html        # Public bot widget
│   └── slides.html     # Presentation mode
├── static/
│   └── filler_*.mp3    # Pre-generated voice filler clips
└── requirements.txt
```

---

## Roadmap

- [ ] Web UI installer (no CLI setup)
- [ ] WhatsApp / Telegram bot integration
- [ ] Local STT fallback (Whisper on-device)
- [ ] Expanded LangChain tool integrations
- [ ] Plugin marketplace for skills
- [ ] Docker image

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Test: `python3 test_suite.py`
4. Open a PR — one feature or fix per PR

---

## License

MIT

---

## Author

**Jai Goyal** — Android / AI Lead at Glance (InMobi Group)

[![LinkedIn](https://img.shields.io/badge/LinkedIn-goyaljai-blue?logo=linkedin)](https://linkedin.com/in/goyaljai)
[![Medium](https://img.shields.io/badge/Medium-goyaljai-black?logo=medium)](https://goyaljai.medium.com)
[![GitHub](https://img.shields.io/badge/GitHub-goyaljai-grey?logo=github)](https://github.com/goyaljai)

---

<div align="center">
  <sub>If Jaika saved you a server bill, consider leaving a ⭐</sub>
</div>
