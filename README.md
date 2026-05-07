<div align="center">

# 🤖 Jaika

### The Only Native AI Product You Want

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/version-2.0.1-green.svg)](https://github.com/goyaljai/jaika/releases)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/goyaljai/jaika/pulls)
[![Powered by Gemini](https://img.shields.io/badge/Powered%20by-Gemini-orange.svg)](https://deepmind.google/technologies/gemini/)
[![LangChain](https://img.shields.io/badge/LangChain-enabled-blueviolet.svg)](https://www.langchain.com/)

**[Live Demo](https://ai-vps-goyaljai.tail98a210.ts.net)** · **[API Docs](README_API.md)** · **[Medium Article](medium.md)** · **[Report Bug](https://github.com/goyaljai/jaika/issues)**

</div>

---

## Why Jaika?

Most self-hosted AI assistants still need a cloud server. Jaika runs entirely on **rooted Android phones** via a Linux chroot — no AWS, no VPS bill, just phones plugged into chargers. Built on **LangChain** for composable AI pipelines and **Gemini** for multimodal intelligence.

| | Jaika | Cloud-hosted AI |
|---|---|---|
| **Monthly cost** | ~$0 | $20–$100+ |
| **Voice latency** | Near-zero (filler audio) | 1–3s silence |
| **Multi-model fallback** | ✅ Automatic | ❌ Manual |
| **Per-user personas** | ✅ Built-in | ❌ Custom build |
| **LangChain pipelines** | ✅ Native | ❌ Extra setup |
| **Self-hosted** | ✅ Your hardware | ❌ Vendor lock-in |

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Voice Pipeline](#voice-pipeline)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Deploy to Android VPS](#deploy-to-android-vps)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Real-time voice chat** — VAD auto-stop, sentence-split TTS, filler audio for zero perceived latency
- **LangChain pipelines** — composable chains for prompt routing, memory, tool use, and RAG
- **Multi-model fallback** — Gemini 2.5 Flash → 2.5 Flash Lite → 3 Flash → 3.1 Flash Lite, automatic
- **ElevenLabs TTS** — cloned voice support with automatic key rotation and Gemini TTS fallback
- **Per-user personas** — `_persona` skill replaces the system prompt entirely, per user
- **File upload** — images, PDFs, audio transcription, all inline in chat
- **Image generation** — Gemini Imagen via chat
- **Video generation** — Veo 2 text-to-video and image-to-video
- **Streaming responses** — SSE streaming with markdown rendering
- **Google OAuth** — secure login, automatic token refresh
- **Admin panel** — user management, model config, contacts
- **Bot pages** — public-facing chatbot widgets (no login required)
- **PDF export** — conversations to PDF via LaTeX
- **Web grounding** — SerpAPI integration for real-time web search
- **OpenAI / Anthropic / Gemini SDK compatible** — drop-in via `api_compat.py`
- **Mobile-first UI** — responsive, dark theme, split-layout

---

## Quick Start

### Prerequisites

- Python 3.10+
- Google OAuth credentials (client ID + secret) — [create here](https://console.cloud.google.com/apis/credentials)
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

Open `http://localhost:5244` — sign in with Google and start chatting.

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
    ├── Memory retrieval
    ├── Skill / persona routing
    ├── Tool use (search, files, image gen)
    └── Prompt assembly
    │
    ▼
LLM generates response (Gemini, multi-model fallback)
    │
    ▼
Filler stops ──► ElevenLabs TTS streams audio
    │
    ▼
User hears response
    │
    ▼
4s silence timeout ──► Call ends
```

### Filler Clips

| Order | Clip | Text |
|---|---|---|
| 1st | filler_1.mp3 | "Yeah, hey, so umm..." |
| 2nd | filler_2.mp3 | "Oh, right, so..." |
| 3rd | filler_3.mp3 | "Okay, yeah, so basically..." |
| 4th | filler_4.mp3 | "So, umm, yeah, actually..." |
| Chain | filler_5.mp3 | "...yeah, let me think about that for a sec." |
| Chain | filler_6.mp3 | "...right, okay, give me a moment." |
| Long wait | filler_7.mp3 | "Just give me a moment. Let me collect my thoughts." |
| Bye | filler_bye.mp3 | "It was great talking to you!" |
| Error | filler_error.mp3 | "Something went wrong on my end..." |

Pattern: `A → B → (1.5s timeout) → C`, all stop the moment real TTS arrives.

---

## Architecture

```
Browser ←──────────────────────────────── Tailscale VPN ───────────────────────────────►
                                                │
                                    Android Phone (Linux Chroot)
                                                │
                                        Gunicorn :5244
                                                │
                                           Flask app
                                    ┌───────────┴───────────┐
                                 auth.py            LangChain Pipeline
                              Google OAuth        ┌────────────────────┐
                              Token refresh       │  prompt_engine.py  │
                                    │             │  Memory / RAG      │
                                    │             │  Skill routing     │
                                    │             │  Tool use          │
                                    └──────────┬──┴────────────────────┘
                                               │
                                          gemini.py
                               ┌───────────────┼───────────────┐
                           Chat/Stream      Image/Video       STT/TTS
                           (cloudcode-pa)   (Veo, Imagen)  (ElevenLabs)
```

### Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Flask, Gunicorn, Python 3.10 |
| **AI Orchestration** | LangChain (chains, memory, tool use, RAG) |
| **LLM** | Google Gemini (multi-model fallback) |
| **TTS** | ElevenLabs (streaming, cloned voices) + Gemini TTS fallback |
| **STT** | Gemini Flash (audio transcription) |
| **Auth** | Google OAuth 2.0 (PKCE, auto token refresh) |
| **Hosting** | Rooted Android phones, Ubuntu chroot, Tailscale |
| **Process mgmt** | Supervisord (auto-restart on crash) |
| **Frontend** | Vanilla JS, marked.js, highlight.js |
| **SDK compat** | OpenAI, Anthropic, Gemini (via `api_compat.py`) |

---

## Project Structure

```
jaika/
├── app.py              # Main Flask app, all routes
├── auth.py             # Google OAuth, login_required, token management
├── gemini.py           # Gemini API — chat, stream, image, video, TTS, STT
├── prompt_engine.py    # LangChain pipeline — prompt builder, memory, guardrails
├── skills.py           # Per-user skills and persona system
├── sessions.py         # Session and message storage
├── files.py            # File upload and management
├── pdf.py              # PDF export via LaTeX
├── api_compat.py       # OpenAI / Anthropic / Gemini SDK compatibility layer
├── grpc_server.py      # gRPC server
├── templates/
│   ├── index.html      # Main chat UI
│   ├── bot.html        # Public bot widget
│   └── slides.html     # Presentation mode
├── static/
│   └── filler_*.mp3    # Pre-generated voice filler clips
├── deploy.sh           # One-command deploy to Android devices
├── master_prompt.md    # Default system prompt
├── skills.md           # Built-in skills reference
├── medium.md           # Android-as-VPS setup guide
└── requirements.txt
```

---

## API Reference

54 endpoints across chat, sessions, voice, files, generation, skills, memory, and admin.

See **[README_API.md](README_API.md)** for the full reference.

Key endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/prompt` | POST | Chat with Gemini via LangChain (streaming or non-streaming) |
| `/api/stt` | POST | Speech-to-text (audio upload) |
| `/api/tts` | POST | Text-to-speech (ElevenLabs → Gemini fallback) |
| `/api/sessions` | GET/POST | Session management |
| `/api/generate/image` | POST | Image generation (Imagen) |
| `/api/generate/video` | POST | Video generation (Veo 2) |
| `/api/skills/upload` | POST | Upload persona or skill |
| `/goyaljai` | GET | Public bot page (no login) |

### SDK Compatibility

Jaika exposes OpenAI, Anthropic, and Gemini-compatible endpoints via `api_compat.py`:

```python
# Drop-in OpenAI client
client = OpenAI(base_url="http://localhost:5244/v1", api_key="your_user_id")

# Drop-in Anthropic client
client = Anthropic(base_url="http://localhost:5244/v1", api_key="your_user_id")
```

---

## Deploy to Android VPS

Full guide: [medium.md](medium.md)

```bash
# Prerequisites: rooted Android, Termux, Linux chroot (Ubuntu)

# Install dependencies in chroot
apt install python3 python3-pip supervisor

# Clone and configure
git clone https://github.com/goyaljai/jaika.git
cd jaika && cp .env.example .env
# Edit .env

# Start with supervisord (auto-restarts on crash)
supervisord -c supervisord.conf

# Deploy to both devices in one command
bash deploy.sh
```

**Why Android?**
- Always-on, fanless, ~5W idle
- 8–12GB RAM on flagship phones
- No datacenter costs
- Tailscale handles VPN + DNS automatically

---

## Roadmap

- [ ] Web UI installer (no CLI setup)
- [ ] Multi-device load balancing
- [ ] WhatsApp / Telegram bot integration
- [ ] Local STT fallback (Whisper on-device)
- [ ] Expanded LangChain tool integrations
- [ ] Plugin marketplace for skills
- [ ] Docker image for non-Android deployments

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make changes and test: `python3 test_suite.py`
4. Deploy to a test device: `bash deploy.sh`
5. Open a PR

Please keep PRs focused — one feature or fix per PR.

---

## License

MIT — see [LICENSE](LICENSE)

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
