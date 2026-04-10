# Jaika v2 — API Reference & High Level Design

---

## High Level Design (HLD)

> For engineers understanding the system architecture.

### Overview

Jaika is a multi-tenant AI SaaS platform. Users authenticate via Google OAuth, and their requests are proxied to Google's Gemini AI backend (`cloudcode-pa.googleapis.com`). The server owns all API keys and access tokens — users never see a Gemini API key.

### Architecture Diagram

```
Client (browser / curl / SDK)
       │
       │  X-User-Id: <uid>
       ▼
┌─────────────────────────────────────────────────────────┐
│                    Flask App (app.py)                    │
│                                                         │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  auth.py │  │  gemini.py   │  │  api_compat.py   │  │
│  │  OAuth   │  │  Direct API  │  │  OpenAI/Anthropic│  │
│  │  tokens  │  │  calls       │  │  /Gemini routers │  │
│  └──────────┘  └──────────────┘  └──────────────────┘  │
│                       │                                  │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Prompt Engine (prompt_engine.py)    │   │
│  │  Input guardrails → Brand subs → Output filter   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │sessions.py│ │  files.py │ │ skills.py │              │
│  │per-user  │  │upload/    │ │sys-prompt │              │
│  │history   │  │convert    │ │modules    │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
       │
       ▼
cloudcode-pa.googleapis.com  (Google Gemini backend)
  /v1internal:generateContent
  /v1internal:streamGenerateContent
  /v1internal:loadCodeAssist
```

### Data Flow — Chat Request

```
1. Client  →  POST /api/prompt  {prompt, session_id, stream}
2. app.py  →  auth.login_required checks X-User-Id header
3. app.py  →  load conversation history from sessions.py
4. app.py  →  load memory facts from data/users/{uid}/memory.json
5. app.py  →  prompt_engine.build_prompt() — injects system prompt + hints + memory
6. app.py  →  prompt_engine.check_input_guardrails() — blocks injection attempts
7. gemini.py → get_access_token(uid) — refreshes OAuth token if <5min to expiry
8. gemini.py → POST cloudcode-pa.googleapis.com/v1internal:generateContent
               headers: Authorization: Bearer <google_oauth_token>
               body: {model, project, request: {contents, systemInstruction, generationConfig}}
9. gemini.py → model fallback: gemini-3-flash-preview → gemini-3.1-flash-lite-preview → gemini-2.5-flash → gemini-2.5-flash-lite on 404/429/503
10. gemini.py → check_output_guardrails() — strips credentials, replaces brand names
11. app.py  →  save assistant reply to session history
12. app.py  →  return {type, text, session_id} to client
```

### Key Files

| File | Responsibility |
|---|---|
| `app.py` | Flask routes, tier enforcement, business logic |
| `auth.py` | Google OAuth, token storage/refresh, admin/pro checks |
| `gemini.py` | Direct Gemini API calls, model fallback, streaming |
| `prompt_engine.py` | System prompt, input guardrails, output sanitization, intent hints |
| `sessions.py` | Per-user conversation history (JSON files) |
| `files.py` | File upload, format conversion (DOCX→text, XLSX→CSV, etc.) |
| `skills.py` | Named system prompt modules |
| `api_compat.py` | OpenAI/Anthropic/Gemini-native proxy routers |
| `pdf.py` | Markdown → PDF conversion (Pro feature) |
| `templates/index.html` | Single-page app: admin chat UI + user docs portal |

### Data Storage

All data is on the local filesystem. No external database.

```
data/
├── admins.json           # list of admin emails
├── pro_users.json        # list of pro emails
├── models.json           # model config: fallback chain, thinking model, TTS model (admin-managed)
├── contacts.json         # master user registry (uid → email, refresh_token)
└── users/
    └── {user_id}/
        ├── user.json     # email, name, picture
        ├── token.json    # OAuth access + refresh token
        ├── memory.json   # persistent facts injected into every chat
        ├── sessions/     # one JSON file per session (conversation history)
        ├── uploads/      # user-uploaded files (1hr TTL)
        └── outputs/      # AI-generated files (30min TTL)
```

### Authentication Architecture

- **Login flow**: User runs `curl https://server/login | bash` → browser opens Google OAuth → script catches the callback code → sends to `/auth/exchange` → server exchanges for tokens → saves to `data/users/{uid}/token.json`
- **Request auth**: Every request includes `X-User-Id: <uid>` header. Server loads the token for that uid.
- **Token refresh (on-demand)**: `auth.get_access_token(uid)` automatically calls Google's token refresh endpoint if the token expires within 5 minutes (`expires_in - 300`).
- **Token refresh (background)**: A background daemon thread (`token-refresh`) runs every 30 minutes and proactively refreshes tokens for all known users. This prevents the first request after a long idle period from hitting a stale token.
- **Compat routers**: OpenAI uses `Authorization: Bearer <uid>`, Anthropic uses `x-api-key: <uid>`, Gemini native uses `?key=<uid>`. All are normalized to uid → token lookup.

### OAuth Token Lifecycle

| Field | Description |
|---|---|
| `access_token` | Short-lived API credential. Valid for `expires_in` seconds (typically 3599 = ~1 hour). |
| `expires_in` | Seconds until the access_token expires. e.g. `3599` = ~1 hour. After this, the token cannot be used. |
| `refresh_token` | Long-lived credential used to get a new access_token without user re-login. Stays valid indefinitely unless revoked. |
| `saved_at` | Unix timestamp when the token was saved. Used to compute `expires_at = saved_at + expires_in`. |

**Token expiry flow:**
```
1. User makes a request
2. get_access_token(uid) checks: time.time() > saved_at + expires_in - 300
3. If expired (within 5min buffer): call Google /token with refresh_token
4. Google returns new access_token (+ new expires_in)
5. Save updated token.json
6. Proceed with the new access_token
```

**When refresh_token gets revoked:**
- User explicitly revokes access in Google Account settings
- Token unused for 6+ months (Google inactivity policy)
- App re-authenticates with `prompt=consent` (issues a new refresh_token, invalidating old one)
- In these cases, user must re-login: `curl -sL https://server/login | bash`

### Multi-tenant Isolation

Each user's data lives entirely under `data/users/{user_id}/`. Two concurrent users share:
- The Flask process (thread-safe; each request reads its own data)
- The model fallback list (read-only config)

Nothing else is shared. User A cannot access User B's sessions, files, memory, or token.

### Tier Enforcement

Enforced server-side in `app.py` before any API call:

| Check | Location |
|---|---|
| STT/TTS/voice-prompt requires Pro | Start of each handler |
| Thinking mode requires Pro | `/api/prompt` before generate call |
| Grounding requires Pro | `/api/prompt` before generate call |
| Web fetch with AI requires Pro | `/api/fetch` after raw fetch |
| Skills write (create/delete) requires Pro | `/api/skills/upload`, `/api/skills/<name> DELETE` |
| PDF requires Pro | `/api/pdf` |
| File gen: 5/day for free | In-memory counter `_file_gen_counts` (resets daily) |
| Storage cap: 50MB free / 500MB pro | Checked before every upload |
| Session limit: 10 free / 25 pro | `/api/sessions POST` |
| Admin endpoints | `@admin_required` decorator |

### Gemini API Details

- **Endpoint**: `https://cloudcode-pa.googleapis.com/v1internal:generateContent`
- **Auth**: User's Google OAuth token (same credentials as Gemini Code Assist)
- **Project discovery**: `loadCodeAssist` call → returns `cloudaicompanionProject` ID (cached 1hr per user)
- **Onboarding**: If user has no `currentTier`, server calls `onboardUser` automatically
- **Streaming**: `/v1internal:streamGenerateContent?alt=sse` — server-sent events, proxied to client
- **Model names sent**: Exact Gemini model IDs, e.g. `gemini-3-flash-preview`, `gemini-2.5-flash`
- **Model fallback**: On 404/429/503, immediately skip to next model (no waiting). Only the last model in the chain retries up to 3× with exponential backoff. This prevents long timeouts when preview models are rate-limited.
- **Fallback chain**: `gemini-3-flash-preview → gemini-3.1-flash-lite-preview → gemini-2.5-flash → gemini-2.5-flash-lite`
- **TTS model**: Uses `gemini-2.5-flash` first (supports audio modalities), then falls back through the chain if not available.

### Output Sanitization Pipeline

Every model response passes through `check_output_guardrails()`:
1. Redact API keys, secrets, tokens (regex patterns)
2. Replace identity claims ("large language model, trained by Google" → "Jaika, an AI assistant")
3. Replace brand names ("Gemini Code Assist" → "Jaika", "Google" → "Open Source")

### File Download Architecture

Generated files (HTML, SVG, PDF, images) are served at:
```
GET /api/download/{uid}/{filename}
```
No auth header needed — the `uid` in the URL path is the authorization token. Files have a random 8-hex-char component in their name (~4 billion combinations) and expire after 30 minutes, making them effectively single-use links.

---

Jaika is a SaaS AI platform backed by Google's Gemini models via direct `cloudcode-pa.googleapis.com` API calls. No Gemini CLI is required.

---

## Authentication

Every request must include the user's Google ID as a header:

```
X-User-Id: <your_user_id>
```

The user ID is obtained after the one-time Google OAuth login (`curl -sL https://your-server/login | bash`).

**Compat routers** accept alternative auth formats:
- OpenAI router: `Authorization: Bearer <user_id>`
- Anthropic router: `x-api-key: <user_id>`
- Gemini native: `?key=<user_id>` query param

---

## Access Tiers

| Feature | Free | Pro | Admin |
|---|---|---|---|
| Chat & prompt | ✅ Unlimited | ✅ Unlimited | ✅ Unlimited |
| File upload | ✅ 50MB cap | ✅ 500MB cap | ✅ Unlimited |
| Sessions | ✅ 10 max | ✅ 25 max | ✅ Unlimited |
| File generation | ✅ 5/day | ✅ Unlimited | ✅ Unlimited |
| Memory (facts) | ✅ | ✅ | ✅ |
| Web fetch | ✅ | ✅ | ✅ |
| Skills (read) | ✅ | ✅ | ✅ |
| **Web Search & Grounding** | ❌ | ✅ | ✅ |
| PDF generation | ❌ | ✅ | ✅ |
| STT (speech-to-text) | ❌ | ✅ | ✅ |
| TTS (text-to-speech) | ❌ | ✅ | ✅ |
| Voice prompt | ❌ | ✅ | ✅ |
| Admin panel & user mgmt | ❌ | ❌ | ✅ |
| Compat routers (OpenAI/Anthropic/Gemini) | ✅ | ✅ | ✅ |

---

## Base URL

```
https://your-server.com
```

All examples below use `$SERVER` for the base URL and `$UID` for your user ID.

```bash
SERVER="https://your-server.com"
UID="your_user_id"
H='-H "X-User-Id: '$UID'"'
C='-H "Content-Type: application/json"'
```

---

## Endpoints

### Chat & Prompt

#### `POST /api/prompt`
Send a message to Gemini. Supports text, file attachments, thinking mode, grounding, and streaming.

**Body:**
```json
{
  "prompt": "string",
  "session_id": "string (optional — resumes conversation)",
  "stream": false,
  "file_ids": ["file_id_1"],
  "thinking": false,
  "thinking_budget": 8192,
  "grounding": false,
  "response_format": "json"
}
```

**Response (non-stream):**
```json
{
  "type": "text",
  "text": "...",
  "session_id": "abc123",
  "grounding": { ... }
}
```

**Response (stream=true):** Server-Sent Events (SSE)
```
data: {"model": "gemini-3-flash-preview", "type": "start"}
data: {"text": "Hello"}
data: {"text": " world"}
data: {"type": "done"}
```

**Notes:**
- If no `session_id` is given, a new session is created and its ID is returned.
- `grounding: true` enables real-time web search via SerpAPI — response includes `grounding.sources`. **Pro/Admin only.**
- `thinking: true` uses the configured thinking model (default: `gemini-3-flash-preview`) with extended reasoning.
- `response_format: "json"` tells Gemini to output valid JSON.
- Per-user memory facts are automatically injected into the system prompt.
- Model fallback: `gemini-3-flash-preview → gemini-3.1-flash-lite-preview → gemini-2.5-flash → gemini-2.5-flash-lite` on 404/429/503.

**Examples:**
```bash
# Basic
curl -X POST $SERVER/api/prompt \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "Hello!", "stream": false}'

# Streaming
curl -N -X POST $SERVER/api/prompt \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "Explain black holes", "stream": true}'

# With thinking + grounding
curl -X POST $SERVER/api/prompt \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "What won the F1 2026 season?", "thinking": true, "grounding": true}'

# Session continuation
curl -X POST $SERVER/api/prompt \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "What did I just ask?", "session_id": "SESSION_ID"}'
```

---

### Voice & Audio  _(Pro/Admin only)_

#### `POST /api/stt`
Transcribe audio to text using Gemini.

**Body:** Multipart form with `file` field.
**Supported formats:** mp3, wav, webm, ogg, m4a, flac, aac, aiff.
**Response:** `{"text": "transcribed text"}`

```bash
curl -X POST $SERVER/api/stt \
  -H "X-User-Id: $UID" \
  -F "file=@recording.wav"
```

#### `POST /api/voice-prompt`  _(Pro/Admin only)_
Audio → STT transcript → Gemini → text response. One-shot voice interaction.

**Body:** Multipart form with `file` field. Optional form fields: `session_id`, `stream`.
**Response:** `{"transcript": "...", "text": "...", "session_id": "..."}`
**Streaming response:** SSE — first event `{type: "transcript", text: "..."}`, then text chunks.

```bash
curl -X POST $SERVER/api/voice-prompt \
  -H "X-User-Id: $UID" \
  -F "file=@question.mp3"
```

#### `POST /api/tts`  _(Pro/Admin only)_
Text-to-speech via Gemini `responseModalities: AUDIO`.

**Body:** `{"text": "string", "voice": "Aoede"}` — Voices: Aoede, Charon, Fenrir, Kore, Puck.
**Response:** `audio/wav` binary on success, or `502 {"error": "TTS not available. Audio output is not allowlisted on this backend."}` if the backend doesn't support audio output for this account.

> **Backend limitation**: Audio output (`responseModalities: AUDIO`) requires the user's Google account to be allowlisted by the `cloudcode-pa.googleapis.com` backend. This is not enabled for all accounts. If your account returns "not allowlisted", TTS will not work regardless of model choice. All 4 models (gemini-3-flash-preview, gemini-3.1-flash-lite-preview, gemini-2.5-flash, gemini-2.5-flash-lite) are attempted before returning an error.

```bash
curl -X POST $SERVER/api/tts \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"text": "Hello world!", "voice": "Aoede"}' \
  -o speech.wav
```

---

### Web Fetch

#### `POST /api/fetch`
Fetch a URL and optionally analyse it with Gemini.

**Body:**
```json
{
  "url": "https://example.com",
  "prompt": "Summarise this page (optional)",
  "session_id": "optional"
}
```

**Response (no prompt):** `{"text": "<raw HTML/text>", "url": "..."}`
**Response (with prompt):** `{"text": "<AI analysis>", "url": "...", "session_id": "..."}`

```bash
# Raw fetch
curl -X POST $SERVER/api/fetch \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"url": "https://httpbin.org/json"}'

# With AI analysis
curl -X POST $SERVER/api/fetch \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "prompt": "What is this page about?"}'
```

---

### Persistent Memory

Per-user key facts injected into every chat prompt as system context.

#### `GET /api/memory`
List all memory facts.

#### `POST /api/memory`
Add a fact. Body: `{"fact": "string"}`
Returns: `{"facts": [...all facts...]}`

#### `DELETE /api/memory/<index>`
Delete fact at 0-based index.

#### `DELETE /api/memory`
Clear all facts.

```bash
curl $SERVER/api/memory -H "X-User-Id: $UID"
curl -X POST $SERVER/api/memory -H "X-User-Id: $UID" \
  -H "Content-Type: application/json" -d '{"fact": "I prefer Python 3.12"}'
curl -X DELETE $SERVER/api/memory/0 -H "X-User-Id: $UID"
curl -X DELETE $SERVER/api/memory -H "X-User-Id: $UID"
```

---

### File Upload

#### `POST /api/upload`
Upload a file to use in prompts. Auto-deleted after 1 hour.

**Body:** Multipart form with `file` field.
**Supported:** images, PDF, DOCX, XLSX, PPTX, audio, video, code, txt, md, ipynb.
**Storage caps:** Free = 50MB, Pro = 500MB, Admin = unlimited.

```bash
FILE_ID=$(curl -sX POST $SERVER/api/upload \
  -H "X-User-Id: $UID" \
  -F "file=@report.pdf" | python3 -c "import sys,json; print(json.load(sys.stdin)['file_id'])")
```

#### `GET /api/files`
List uploaded files.

#### `GET /api/files/<file_id>`
Get metadata for a file.

#### `GET /api/files/<file_id>/download`
Download the raw file.

#### `DELETE /api/files/<file_id>`
Delete a file.

---

### File Generation

All generation counts toward a **5/day** limit for free users. Pro/Admin = unlimited.

#### `POST /api/generate/file`
Generate a file from a prompt.

**Body:** `{"prompt": "string", "type": "html|svg|csv|json|py|image|video"}`
**Response:** `{"file_url": "/api/download/...", "filename": "...", "mime_type": "...", "size": N, "remaining": "4"}`

```bash
curl -X POST $SERVER/api/generate/file \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "landing page for a coffee shop", "type": "html"}'
```

#### `POST /api/generate/image`
Generate an image. Uses Gemini native image output; falls back to SVG if unavailable.

**Body:** `{"prompt": "string", "fallback_svg": true}`

#### `POST /api/generate/video`
Generate an animated HTML5 file (CSS/JS animation).

#### `POST /api/pdf`  _(Pro/Admin only)_
Convert Markdown to PDF.

**Body:** `{"markdown": "# Title\n\nContent..."}`
**Response:** `{"path": "/api/download/...", "filename": "..."}`

#### `GET /api/download/<filename>`
Download a generated output file.

---

### Sessions

Sessions store conversation history. Limit: Free = 10, Pro = 25 (FIFO), Admin = unlimited.

#### `GET /api/sessions`
List all sessions.

#### `POST /api/sessions`
Create a session. Body: `{"title": "optional title"}`
Returns `201` with session object.

#### `GET /api/sessions/<session_id>`
Get session + full message history.

#### `PUT /api/sessions/<session_id>`
Rename session. Body: `{"title": "New Name"}`

#### `DELETE /api/sessions/<session_id>`
Delete a session and all its messages.

#### `DELETE /api/sessions/<session_id>/messages`
Clear messages but keep the session.

---

### Skills

Skills are **per-user** named `.md` files stored at `data/users/{uid}/skills/`. They extend the system prompt with domain expertise for that user only.

#### `GET /api/skills`
List all skills for the authenticated user.

#### `GET /api/skills/<name>`
Get skill content.

#### `POST /api/skills/upload`
Upload or update a skill via JSON or file.

```bash
# JSON
curl -X POST $SERVER/api/skills/upload \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"name": "coding", "content": "You are an expert programmer."}'

# File
curl -X POST $SERVER/api/skills/upload \
  -H "X-User-Id: $UID" \
  -F "file=@coding.md"
```

#### `DELETE /api/skills/<name>`
Delete a skill.

```bash
curl -X DELETE $SERVER/api/skills/coding -H "X-User-Id: $UID"
```

---

### `_persona` — Persona Chatbot

The special skill name `_persona` **replaces** the default "You are Jaika" system instruction entirely for that user. Use it to create a custom persona chatbot — e.g., a portfolio site chatbot that answers as you.

When `_persona` is active:
- The bot answers **only** questions about the person described (career, background, projects, contact).
- **Off-topic questions** (maths, coding help, world events, trivia) are refused with: `"That's outside what I share here — feel free to reach out to me directly!"`
- Without `_persona`, the bot behaves as normal Jaika.

```bash
# Upload persona (creates or replaces)
curl -X POST $SERVER/api/skills/upload \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"name": "_persona", "content": "You are Raunak Jain, Product Leader at InMobi..."}'

# Or upload from a skills.md file
curl -X POST $SERVER/api/skills/upload \
  -H "X-User-Id: $UID" \
  -F "file=@skills.md" -F "name=_persona"

# Test — should answer as the person
curl -X POST $SERVER/api/prompt \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me about your career", "stream": false}'

# Test — off-topic, should refuse
curl -X POST $SERVER/api/prompt \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"prompt": "What is mean and variance?", "stream": false}'

# Delete persona (reverts to Jaika)
curl -X DELETE $SERVER/api/skills/_persona -H "X-User-Id: $UID"
```

**Scope gate (automatic when `_persona` is active):**
| Question type | Behavior |
|---|---|
| About the person (career, projects, background) | Answers using skills.md as sole source of truth |
| Career fit ("Would you suit a GPM role at Meta?") | Synthesizes skills.md evidence + external role context |
| Info not in document (placeholders, missing fields) | "Based on what I've shared, that info isn't available" |
| Off-topic (maths, world events, trivia, coding help) | "That's outside what I share here..." |

**Use case — portfolio website chatbot:**
Visitors use the site owner's `X-User-Id` in every API call. The owner uploads `_persona` once with their bio. Visitors can then ask about the owner and the bot answers in first person. No per-visitor accounts needed.

**Tip:** Generate an optimized `skills.md` using the Structured Data Architect prompt in `demo/skills_template.md`. Well-structured bullet-point facts → precise answers. Vague paragraph bios → vague answers.

---

### User & Auth

#### `GET /api/me`
Current user info including tier, storage, limits.

**Response:**
```json
{
  "user_id": "...",
  "email": "...",
  "name": "...",
  "is_admin": false,
  "is_pro": false,
  "tier_id": "...",
  "tier_name": "Jaika (Powered by Gemini)",
  "storage_used_bytes": 1234,
  "storage_cap_bytes": 52428800,
  "session_limit": 10,
  "file_gen_limit": 5
}
```

#### `GET /auth/status`
Check if the user's token is valid.

#### `GET /auth/logout`
Revoke token and log out.

---

## Compat Routers

Jaika proxies three standard AI APIs, mapping model names to Gemini internally.

### OpenAI-Compatible (`/v1/...`)

Auth: `Authorization: Bearer <user_id>`

| OpenAI model | Maps to |
|---|---|
| `gpt-4o`, `gpt-4`, `gpt-4-turbo` | `gemini-3-flash-preview` |
| `gpt-4o-mini`, `gpt-3.5-turbo` | `gemini-3.1-flash-lite-preview` |
| `gemini-*` | used as-is |

```bash
# List models
curl $SERVER/v1/models -H "Authorization: Bearer $UID"

# Chat completion
curl -X POST $SERVER/v1/chat/completions \
  -H "Authorization: Bearer $UID" -H "Content-Type: application/json" \
  -d '{"model": "gemini-3-flash-preview", "messages": [{"role": "user", "content": "Hello"}]}'

# Streaming
curl -N -X POST $SERVER/v1/chat/completions \
  -H "Authorization: Bearer $UID" -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "stream": true}'
```

**Python:**
```python
import openai
client = openai.OpenAI(base_url="https://your-server.com/v1", api_key="YOUR_UID")
resp = client.chat.completions.create(
    model="gemini-3-flash-preview",
    messages=[{"role": "user", "content": "Hello"}]
)
print(resp.choices[0].message.content)
```

### Anthropic-Compatible (`/v1/messages`)

Auth: `x-api-key: <user_id>`

| Claude model | Maps to |
|---|---|
| `claude-opus-4`, `claude-3-opus`, `claude-3-5-sonnet` | `gemini-3-flash-preview` |
| `claude-sonnet-4`, `claude-3-sonnet`, `claude-haiku-*` | `gemini-3.1-flash-lite-preview` |

```bash
curl -X POST $SERVER/v1/messages \
  -H "x-api-key: $UID" -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-4", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 1024}'
```

**Python:**
```python
import anthropic
client = anthropic.Anthropic(base_url="https://your-server.com", api_key="YOUR_UID")
msg = client.messages.create(
    model="claude-opus-4", max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}]
)
print(msg.content[0].text)
```

### Gemini-Native (`/v1beta/...`)

Auth: `?key=<user_id>` query param

```bash
# List models
curl "$SERVER/v1beta/models?key=$UID"

# Generate content
curl -X POST "$SERVER/v1beta/models/gemini-3-flash-preview:generateContent?key=$UID" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'

# Streaming
curl -N -X POST "$SERVER/v1beta/models/gemini-3-flash-preview:streamGenerateContent?key=$UID" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Stream this"}]}]}'
```

---

## Admin Endpoints

All require `is_admin = true`.

#### `GET /api/admin/vitals`
Server disk, memory, uptime, and per-user stats.

#### `GET /api/admin/users`
List all users with email, session count, disk usage.

#### `POST /api/admin/users/<user_id>/promote`
Promote user to `pro` or `admin`. Body: `{"role": "pro"}`

#### `POST /api/admin/users/<user_id>/demote`
Demote from `pro` or `admin`. Body: `{"role": "pro"}`

#### `DELETE /api/admin/users/<user_id>`
Delete user and all their data.

#### `DELETE /api/admin/users/<user_id>/sessions`
Clear all sessions for a user.

#### `GET /api/admin/emails` / `POST` / `DELETE`
Manage the admin emails list.

#### `GET /api/admin/pro` / `POST` / `DELETE`
Manage the pro users list.

#### `GET /api/admin/contacts`
Download master contact list (JSON).

#### `GET /api/eval/guardrails`
Run input guardrail test suite (instant, no API calls).

#### `GET /api/admin/models`
Get the current model configuration (fallback chain, thinking model, TTS model).

**Response:**
```json
{
  "fallback": ["gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
  "thinking": "gemini-3-flash-preview",
  "tts": "gemini-3-flash-preview"
}
```

```bash
curl $SERVER/api/admin/models -H "X-User-Id: $UID"
```

#### `POST /api/admin/models`
Partially update the model configuration. All fields are optional — only provided fields are updated.

**Body:**
```json
{
  "fallback": ["gemini-3-flash-preview", "gemini-2.5-flash"],
  "thinking": "gemini-3-flash-preview",
  "tts": "gemini-3-flash-preview"
}
```

```bash
# Set thinking model
curl -X POST $SERVER/api/admin/models \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"thinking": "gemini-3-flash-preview"}'

# Replace entire fallback chain
curl -X POST $SERVER/api/admin/models \
  -H "X-User-Id: $UID" -H "Content-Type: application/json" \
  -d '{"fallback": ["gemini-3-flash-preview", "gemini-2.5-flash"]}'
```

#### `DELETE /api/admin/models/fallback/<model>`
Remove a single model from the fallback chain.

```bash
curl -X DELETE $SERVER/api/admin/models/fallback/gemini-3.1-flash-lite-preview \
  -H "X-User-Id: $UID"
```

**Notes:**
- Changes take effect within 60 seconds (cache TTL).
- Fallback chain must have at least one model; the delete is silently ignored if the model is not in the list.
- Config is persisted to `data/models.json` and survives server restarts.

---

## Error Responses

| Code | Meaning |
|---|---|
| `400` | Bad request (missing field, invalid input) |
| `401` | Not authenticated or token expired |
| `403` | Feature requires Pro or Admin |
| `404` | Resource not found |
| `429` | Limit reached (sessions, storage, file gen) |
| `502` | Upstream Gemini API error or feature unsupported |
| `500` | Internal server error |

---

## Architecture Notes

- **No CLI subprocess.** All Gemini calls go directly to `cloudcode-pa.googleapis.com/v1internal`.
- **Per-user isolation.** All data lives under `data/users/{user_id}/` — sessions, uploads, outputs, memory, token, skills. Two users sharing a server have zero overlap.
- **Token refresh.** OAuth tokens are refreshed automatically 5 minutes before expiry.
- **Model fallback.** On 404/429/503 admin-configurable fallback chain (default: `gemini-3-flash-preview → gemini-3.1-flash-lite-preview → gemini-2.5-flash → gemini-2.5-flash-lite`). Managed via `GET/POST /api/admin/models`, persisted to `data/models.json`, cached for 60 seconds.
- **Brand guardrails.** Output is filtered to replace "Gemini Code Assist" → "Jaika" etc.
- **Input guardrails.** Prompt injection patterns are blocked before hitting the model.
- **File TTL.** Uploaded files auto-delete after 1 hour. Generated outputs after 30 minutes.
- **Skills are per-user.** Each user has `data/users/{uid}/skills/`. No user's skills affect another user's chat.
- **`_persona` skill.** Special skill name that replaces the default system prompt for that user. Enables persona chatbots (e.g., portfolio sites). Off-topic questions are refused automatically via scope gate prepended before the persona content.

---

## Deployment — Device Restart Commands

Files live inside a chroot at `/data/local/linux/rootfs/opt/jaika-v2/` on each Android device.

```bash
# Push updated files into chroot (from Mac/dev machine)
adb -s <SERIAL> push app.py /storage/emulated/0/jaika-v2/app.py
adb -s <SERIAL> shell "su 0 sh -c 'cp /storage/emulated/0/jaika-v2/app.py /data/local/linux/rootfs/opt/jaika-v2/app.py'"

# Restart jaika via supervisorctl inside chroot
adb -s <SERIAL> shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart jaika'"

# Check status
adb -s <SERIAL> shell "su 0 sh -c 'chroot /data/local/linux/rootfs /usr/bin/supervisorctl status'"
```

**Device serials:**
| Device | Serial |
|---|---|
| Device 1 (primary) | `N1VT460414` |
| Device 2 (secondary) | `NB9AA90129` |

**Two-device deploy shortcut:**
```bash
for SERIAL in N1VT460414 NB9AA90129; do
  adb -s $SERIAL push skills.py /storage/emulated/0/jaika-v2/skills.py
  adb -s $SERIAL shell "su 0 sh -c 'cp /storage/emulated/0/jaika-v2/skills.py /data/local/linux/rootfs/opt/jaika-v2/skills.py && chroot /data/local/linux/rootfs /usr/bin/supervisorctl restart jaika'"
done
```
