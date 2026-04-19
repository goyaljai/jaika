# Jaika v2

An open-source AI assistant with real-time voice chat, powered by Google Gemini — self-hosted on Android phones as VPS.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## What is Jaika?

Jaika is a full-featured AI chatbot that runs on rooted Android phones via a Linux chroot. No cloud servers, no monthly bills — just phones plugged into chargers.

**Live demo:** [ai-vps-goyaljai.tail98a210.ts.net](https://ai-vps-goyaljai.tail98a210.ts.net)

### Features

- **Real-time voice chat** — VAD auto-stop, sentence-split TTS, filler audio for zero perceived latency
- **ElevenLabs TTS** — cloned voice support with automatic key rotation and Gemini TTS fallback
- **Multi-model fallback** — Gemini 3 Flash → 3.1 Flash Lite → 2.5 Flash → 2.5 Flash Lite
- **Auto-provisioning** — new users get a GCP project automatically on first login (mirrors gemini-cli flow)
- **Per-user personas** — `_persona` skill replaces system prompt entirely per user
- **File upload** — images, PDFs, audio transcription, all inline in chat
- **Image generation** — Gemini Imagen via chat
- **Video generation** — Veo 2 text-to-video and image-to-video
- **Streaming responses** — SSE streaming with markdown rendering
- **Google OAuth** — secure login, token auto-refresh
- **Admin panel** — user management, model config, contacts
- **Bot pages** — public-facing chatbot widgets (no login required)
- **PDF export** — conversations to PDF via LaTeX
- **Web grounding** — SerpAPI integration for real-time web search
- **Mobile-first UI** — responsive, dark theme, split-layout pages

### Voice Pipeline

```
User speaks → VAD detects silence → STT (Gemini Flash)
  → Filler audio plays instantly ("Yeah, so umm...")
  → LLM generates response → Filler stops
  → ElevenLabs TTS streams audio → User hears response
  → 4s silence timeout → Call ends
```

- **Filler audio:** 7 pre-generated clips in the cloned voice, chained A→B→C pattern
- **Bye detection:** regex on user input ("bye", "goodbye", "see you") → pre-generated farewell audio
- **Error handling:** pre-generated error audio on any failure, no silent crashes

## Quick Start

### Prerequisites

- Python 3.10+
- Google OAuth credentials (client ID + secret)
- Gemini API access (via Google Cloud or gemini-cli OAuth)

### Install

```bash
git clone https://github.com/goyaljai/jaika-v2.git
cd jaika-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env with your credentials:
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SECRET_KEY
#   ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (optional — falls back to Gemini TTS)
```

### Run

```bash
# Development
python3 app.py

# Production
gunicorn --bind 0.0.0.0:5244 --workers 4 --threads 4 --timeout 120 app:app
```

### Deploy to Android VPS

See [medium.md](medium.md) for the full guide on running Jaika on rooted Android phones.

```bash
# One-command deploy to both devices
bash push_devices.sh
```

## Architecture

```
Browser ←→ Tailscale VPN ←→ Android Phone ←→ Linux Chroot ←→ Gunicorn ←→ Flask
                                                                |
                                          +---------+-----------+-----------+
                                          |         |           |           |
                                       Gemini   ElevenLabs   SerpAPI    Files
                                       (Chat,    (TTS)       (Search)   (Upload,
                                        STT,                             PDF)
                                        Image,
                                        Video)
```

### Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Flask, Gunicorn, Python 3.10 |
| **LLM** | Google Gemini (cloudcode-pa API, multi-model fallback) |
| **TTS** | ElevenLabs (streaming, cloned voices) + Gemini TTS fallback |
| **STT** | Gemini Flash (audio transcription) |
| **Auth** | Google OAuth 2.0 (PKCE, auto token refresh) |
| **Hosting** | Rooted Android phones, Ubuntu chroot, Tailscale |
| **Process mgmt** | Supervisord (auto-restart on crash) |
| **Frontend** | Vanilla JS, marked.js, highlight.js |

### API Endpoints

54 endpoints across chat, sessions, voice, files, generation, skills, memory, and admin. See [README_API.md](README_API.md) for full details.

Key endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/prompt` | POST | Chat with Gemini (streaming or non-streaming) |
| `/api/stt` | POST | Speech-to-text (audio upload) |
| `/api/tts` | POST | Text-to-speech (ElevenLabs → Gemini fallback) |
| `/api/sessions` | GET/POST | Session management |
| `/api/skills/upload` | POST | Upload persona/skill |
| `/api/generate/image` | POST | Image generation (Imagen) |
| `/api/generate/video` | POST | Video generation (Veo 2) |
| `/goyaljai` | GET | Public bot page (no login required) |

### External APIs

| Service | Used for |
|---|---|
| Google Cloudcode (`cloudcode-pa.googleapis.com`) | Chat, streaming, user onboarding |
| Google GenAI (`generativelanguage.googleapis.com`) | TTS (Gemini), video generation (Veo) |
| ElevenLabs (`api.elevenlabs.io`) | Primary TTS with cloned voices |
| SerpAPI (`serpapi.com`) | Web search grounding |
| Google OAuth (`oauth2.googleapis.com`) | Authentication, token refresh |

## Project Structure

```
jaika-v2/
├── app.py              # Main Flask app, routes, bot auth
├── auth.py             # OAuth, login_required, token management
├── gemini.py           # Gemini API (chat, stream, image, video, TTS, STT)
├── prompt_engine.py    # System prompt builder
├── skills.py           # Per-user skills/persona system
├── sessions.py         # Session & message storage
├── files.py            # File upload & management
├── pdf.py              # PDF export
├── templates/
│   ├── index.html      # Main chat UI
│   └── bot_goyaljai.html  # Public bot page with voice
├── static/
│   └── filler_*.mp3    # Pre-generated voice fillers
├── data/               # User data, sessions, skills, uploads
├── deploy.sh           # Server deployment script
├── prompts.md          # 5 HLD flow diagram prompts
├── medium.md           # Article: Android phone as VPS
└── requirements.txt
```

## Voice Filler System

Pre-generated audio clips fill the silence during LLM processing:

| Order | Clip | Text |
|---|---|---|
| 1st (A) | filler_1.mp3 | "Yeah, hey, so umm..." |
| 2nd (A) | filler_2.mp3 | "Oh, right, so..." |
| 3rd (A) | filler_3.mp3 | "Okay, yeah, so basically..." |
| 4th (A) | filler_4.mp3 | "So, umm, yeah, actually..." |
| Chain (B) | filler_5.mp3 | "...yeah, let me think about that for a sec." |
| Chain (B) | filler_6.mp3 | "...right, okay, give me a moment." |
| Long wait (C) | filler_7.mp3 | "Just give me a moment. Let me collect my thoughts." |
| Bye | filler_bye.mp3 | "It was great talking to you!" |
| Error | filler_error.mp3 | "Something went wrong on my end..." |

Pattern: A→B→(1.5s timeout)→C, all stop when real TTS arrives.

## Contributing

1. Fork the repo
2. Create a feature branch
3. Make changes
4. Deploy to test device: `bash push_devices.sh`
5. Submit a PR

## License

MIT

## Author

**Jai Goyal** — Android Lead at InMobi/Glance

- [LinkedIn](https://linkedin.com/in/goyaljai)
- [Medium](https://goyaljai.medium.com)
- [GitHub](https://github.com/goyaljai)
