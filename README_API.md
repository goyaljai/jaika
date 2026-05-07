# Jaika — API Internals & Rate Limit Strategy

## Architecture Overview

Jaika is a Flask backend that wraps the Google Gemini API (via `cloudcode-pa.googleapis.com/v1internal`) and exposes multiple interfaces:

- **Native Jaika API** — `/api/prompt`, `/api/upload`, `/api/memory`, etc.
- **OpenAI-compatible** — `/v1/chat/completions`, `/v1/models`
- **Anthropic-compatible** — `/v1/messages`
- **Gemini-native** — `/v1beta/models/:generateContent`

All routes ultimately call the same `generate()` / `stream_generate()` functions in `gemini.py`.

---

## Endpoint: cloudcode-pa vs generativelanguage

| Auth Method | Endpoint | Used By |
|-------------|----------|---------|
| OAuth (Login with Google) | `cloudcode-pa.googleapis.com/v1internal` | Jaika, gemini-cli |
| API Key | `generativelanguage.googleapis.com/v1beta` | Google AI Studio |
| Vertex AI | Regional endpoints | Enterprise |

Jaika uses the **same endpoint and auth as gemini-cli** — OAuth bearer tokens with `v1internal` APIs. This means:

- Same rate limits as gemini-cli
- Same tier system (free tier, paid tiers)
- Same project discovery via `loadCodeAssist`

---

## Rate Limits (Free Tier)

| Metric | Limit |
|--------|-------|
| Requests per minute (RPM) | ~2–10 (varies by model and load) |
| Requests per day (RPD) | 1,000 |
| Input tokens per day | ~6M |
| Concurrent requests | Low (appears to be 1–2) |

**Important nuances:**
- The per-minute limit on `v1internal` is much lower than the public API's 60 RPM
- Rate limits are **per-user** (tied to Google account), not per-app
- The server returns `429` with `"reset after Xs"` in the error message
- Daily quota exhaustion returns `QUOTA_EXHAUSTED` (terminal — no retry helps)

---

## Retry Strategy (Ported from gemini-cli)

### How gemini-cli Does It

gemini-cli implements a sophisticated retry system:

1. **Error Classification:**
   - `RATE_LIMIT_EXCEEDED` → Retryable (wait and retry same model)
   - `QUOTA_EXHAUSTED` → Terminal (fall back to next model)
   - `PerDay` quota violations → Terminal
   - `PerMinute` violations → Retryable (60s suggested wait)
   - Parses `RetryInfo` from response details for server-suggested delay

2. **Exponential Backoff:**
   - Max 10 attempts (1 initial + 9 retries)
   - Initial delay: 5s, max delay: 30s
   - Exponential: delay doubles each attempt
   - Jitter: +0–20% for quota errors, +/–30% for others
   - Streaming uses fewer retries (4 max) with shorter initial delay (1s)

3. **Model Fallback:**
   - Default chain: `gemini-2.5-pro` → `gemini-2.5-flash`
   - Only falls back on **terminal** errors (daily quota, model not found)
   - On retryable errors, retries **same model** with backoff
   - Retry counter resets when falling back to a new model

4. **Max Retryable Delay**: If server says wait > 300s, treat as terminal

### How Jaika Implements It

Ported to Python in `gemini.py`:

```python
def _classify_error(resp):
    # Returns ("retryable", delay_seconds) or ("terminal", reason)

def _retry_delay(attempt, base_delay):
    delay = min(base_delay * (2 ** attempt), RETRY_MAX_DELAY)
    jitter = delay * random.uniform(0, 0.2)
    return delay + jitter
```

**Key config:**
```python
RETRY_MAX_ATTEMPTS = 10
RETRY_INITIAL_DELAY = 5.0    # seconds
RETRY_MAX_DELAY = 30.0       # seconds
MAX_RETRYABLE_DELAY = 300    # terminal if server says wait > 5min
```

---

## Model Selection

### Fallback Chain
```python
MODEL_FALLBACK = ["gemini-2.5-flash", "gemini-2.0-flash"]
```
- Flash first (highest RPM on free tier)
- No pro model in fallback (saves quota)
- Only falls back on 404 (model not found) or terminal quota errors

### Thinking Mode
```python
MODEL_THINKING = "gemini-2.5-flash"
```

---

## API Call Inventory

| Endpoint | Gemini Calls | Notes |
|----------|-------------|-------|
| `POST /api/prompt` | 1 | Single generate or stream_generate |
| `POST /api/voice-prompt` | 2 | 1 transcribe + 1 generate |
| `POST /api/stt` | 1 | Transcription via generate |
| `POST /api/tts` | 1 | Direct generateContent with audio |
| `POST /api/fetch` (with prompt) | 1 | URL content + LLM analysis |
| `POST /api/fetch` (no prompt) | 0 | Raw fetch only, no LLM |
| `POST /api/generate/file` | 1 | File generation via generate |
| `POST /api/generate/image` | 1–2 | Native image; SVG fallback if failed |
| `POST /v1/chat/completions` | 1–2 | OpenAI compat → generate/stream |
| `POST /v1/messages` | 1–2 | Anthropic compat → generate |
| `POST /v1beta/.../generateContent` | 1–2 | Gemini native compat → generate |

**Overhead calls (not per-request):**
- `loadCodeAssist` — 1 call per user per hour (project discovery, cached)
- `onboardUser` — 1–7 calls total for new users only (one-time)

---

## Common Issues & Fixes

### 1. "Service temporarily busy" on every request
**Cause:** Rate limited (429). The retry logic handles this automatically.

**If persistent:** Daily quota (1000 RPD) may be exhausted:
```bash
curl -s http://localhost:5244/api/me -H "X-User-Id: <uid>" | python3 -m json.tool
```

### 2. Quota burns too fast
- Voice prompts use 2 API calls (transcribe + respond)
- Image generation with SVG fallback uses up to 2 calls
- Multiple browser tabs hitting the API simultaneously

### 3. Context window bloat
- Conversation history is unbounded — all messages sent to API every turn
- Memory facts injected on every request
- System instruction rebuilt from disk on every request

**Recommended:** Sliding window on conversation history (keep last N messages) + cache `build_system_instruction()` output.

### 4. Server logs for debugging
```
[GEMINI] model=gemini-2.5-flash attempt=1 status=200     # Success
[GEMINI] model=gemini-2.5-flash attempt=1 status=429     # Rate limited
Model gemini-2.5-flash: retryable, waiting 33.6s          # Waiting for reset
Model gemini-2.5-flash: terminal quota error: Daily...    # Quota exhausted
Model gemini-2.5-flash not found, falling back             # 404, trying next model
```

---

## Authentication Flow

1. User logs in via Google OAuth (`auth.py`)
2. Access token stored per user, auto-refreshed if expires within 300s
3. Uses the same client credentials as gemini-cli
4. Token passed as `Authorization: Bearer {token}` to cloudcode-pa

---

## Test Suite

```bash
python3 test_suite.py
```

Covers: Auth, Chat (stream/non-stream), Memory, Web Fetch, STT/TTS, File upload/download, Sessions, Skills, File/Image generation, OpenAI/Anthropic/Gemini compat routes, Admin APIs, Security (path traversal, injection, auth enforcement, output guardrails).

Tests use a 1s delay between LLM calls. Rate limits are handled transparently by retry logic.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | Required |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | Required |
| `SECRET_KEY` | Flask session secret | Random |
| `DATA_DIR` | Storage directory | `./data` |
| `PORT` | Server port | `5244` |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS key | Optional |
| `ELEVENLABS_VOICE_ID` | Cloned voice ID | Optional |
| `SERP_API_KEY` | SerpAPI key for web grounding (Pro) | Optional |
