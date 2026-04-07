# Jaika v2 — High-Level Design

## Architecture Diagram

```
                                    JAIKA v2 ARCHITECTURE
 ========================================================================================

  CLIENTS                        JAIKA SERVER (Flask :5244)                    EXTERNAL
 --------                       ---------------------------                   --------

 +-------------+                 +------------------------------------------+
 | Web Browser |---[HTML/JS]---->|  Templates (Jinja2)                      |
 | (UI)        |<---[SSE]-------|  / login, /chat, /admin                   |
 +-------------+                 +------------------------------------------+
                                          |
                                          v
 +-------------+    +-----------+  +------------------------------------------+
 | Any HTTP    |--->| auth.py   |->| app.py  (Main Router)                   |
 | Client      |    |           |  |                                         |
 | (curl, SDK) |    | Google    |  |  /api/prompt ----+                      |
 +-------------+    | OAuth 2.0 |  |  /api/memory     |                      |
                    | Token     |  |  /api/sessions   |                      |
 +-------------+    | Refresh   |  |  /api/files      |                      |
 | OpenAI SDK  |--->| X-User-Id |  |  /api/upload     |                      |
 | (compat)    |    | or Cookie |  |  /api/fetch      |                      |
 +-------------+    +-----------+  |  /api/stt        |                      |
                                   |  /api/tts        |                      |
 +-------------+                   |  /api/generate/* |                      |
 | Anthropic   |--->               |  /api/skills     |                      |
 | SDK (compat)|   api_compat.py   |  /api/admin/*    |                      |
 +-------------+   +----------+    +--------+---------+                      |
                   |          |             |                                 |
 +-------------+   | OpenAI   |             v                                |
 | Gemini SDK  |-->| /v1/chat |    +------------------+    +---------------+ |
 | (compat)    |   |          |--->| prompt_engine.py  |    | sessions.py   | |
 +-------------+   | Anthropic|    |                   |    |               | |
                   | /v1/msg  |    | - System Prompt   |    | - Create/List | |
                   |          |    | - Input Guardrails|    | - Messages    | |
                   | Gemini   |    | - Output Guards   |    | - History     | |
                   | /v1beta  |    | - Intent Detect   |    | - JSON files  | |
                   +----+-----+    | - File Meta-Prompt|    +-------+-------+ |
                        |          +--------+----------+            |         |
                        |                   |                       v         |
                        |                   v              +---------------+  |
                        |          +------------------+    | data/         |  |
                        +--------->| gemini.py        |    |  users/       |  |
                                   |                  |    |   <uid>/      |  |
                                   | - generate()     |    |    sessions/  |  |
                                   | - stream_gen()   |    |    files/     |  |
                                   | - generate_img() |    |    memory.json|  |
                                   | - transcribe()   |    |    token.json |  |
                                   | - Retry+Backoff  |    +---------------+  |
                                   +--------+---------+                       |
                                            |                                 |
                                            v                                 |
                                   +-------------------+                      |
                                   | cloudcode-pa      |    +--------------+  |
                                   | .googleapis.com   |    | skills.py    |  |
                                   |                   |    | - Load .md   |  |
                                   | /v1internal:      |    | - Build sys  |  |
                                   |  generateContent  |    |   instruction|  |
                                   |  streamGenerate   |    +--------------+  |
                                   |  loadCodeAssist   |                      |
                                   |  onboardUser      |    +--------------+  |
                                   +-------------------+    | files.py     |  |
                                                            | - Upload     |  |
                                                            | - Convert    |  |
                                                            | - PDF parse  |  |
                                                            +--------------+  |
```

## Request Flow (Single Chat Message)

```
  User sends "Hello"
       |
       v
  1. app.py: POST /api/prompt
       |
       v
  2. auth.py: login_required() --- checks X-User-Id / session cookie
       |                            validates token exists & not expired
       v
  3. prompt_engine.py: check_input_guardrails(prompt)
       |                 - blocks injection attempts
       |                 - blocks jailbreak patterns
       v
  4. skills.py: build_system_instruction()
       |          - loads SYSTEM_PROMPT from prompt_engine
       |          - appends all .md skill files
       v
  5. app.py: loads user memory facts from data/<uid>/memory.json
       |      appends to system_instruction
       v
  6. sessions.py: get_conversation_history(uid, session_id)
       |           - loads all past messages from JSON
       |           - formats as Gemini contents array
       v
  7. gemini.py: generate() or stream_generate()
       |
       |   +--- Try gemini-2.5-flash --------+
       |   |                                  |
       |   |  POST cloudcode-pa/v1internal    |
       |   |       :generateContent           |
       |   |                                  |
       |   |  200? ---> parse response        |
       |   |  429? ---> classify error        |
       |   |    retryable? sleep(server_delay |
       |   |               + 1-3s buffer)     |
       |   |               retry (max 3x)     |
       |   |    terminal? ---> try next model |
       |   |  404? ---> try gemini-2.0-flash  |
       |   +----------------------------------+
       |
       v
  8. prompt_engine.py: check_output_guardrails(text)
       |                 - strips "Google" branding
       |                 - strips system prompt leaks
       v
  9. sessions.py: add_message(uid, sid, "assistant", text)
       |           - persists to JSON file
       v
  10. Return JSON: { "text": "Hi! How can I help?", "session_id": "abc123" }
```

## Data Model

```
  data/
  +-- users/
      +-- <google_user_id>/
          +-- token.json          # OAuth refresh + access tokens
          +-- memory.json         # ["fact1", "fact2", ...]
          +-- contacts.json       # user profile (name, email, picture)
          +-- sessions/
          |   +-- <session_id>.json
          |       {
          |         "id": "abc123",
          |         "title": "Chat about Python",
          |         "created": "2026-04-07T...",
          |         "messages": [
          |           {"role": "user", "text": "Hello", "ts": ...},
          |           {"role": "model", "text": "Hi!", "ts": ...}
          |         ]
          |       }
          +-- files/
          |   +-- <file_id>.json  # metadata (name, mime, size)
          |   +-- <file_id>.bin   # raw file content
          +-- skills/
              +-- <skill_name>.md # custom system prompt additions
```

## Retry Strategy (Ported from gemini-cli)

```
  Request
    |
    v
  [Attempt 1] --200--> Success
    |
   429/503
    |
    v
  Classify Error
    |
    +-- QUOTA_EXHAUSTED (daily) --> Terminal --> Try next model
    +-- PerDay violation ---------> Terminal --> Try next model
    +-- RATE_LIMIT_EXCEEDED ------> Retryable
    +-- "reset after Xs" --------->  |
                                     v
                              Sleep(server_delay + 1-3s buffer)
                                     |
                                     v
                              [Attempt 2] --200--> Success
                                     |
                                    429
                                     |
                                     v
                              Sleep(server_delay + 1-3s buffer)
                                     |
                                     v
                              [Attempt 3] --200--> Success
                                     |
                                    429
                                     |
                                     v
                              Give up --> Try next model in chain
                                          [gemini-2.5-flash] --> [gemini-2.0-flash]
```

## API Surface

```
  NATIVE API                  COMPAT LAYERS                ADMIN
  ----------                  -------------                -----
  POST /api/prompt            GET  /v1/models              GET  /api/admin/users
  POST /api/upload            POST /v1/chat/completions    POST /api/admin/users/<id>/promote
  GET  /api/files             POST /v1/messages            POST /api/admin/users/<id>/demote
  GET  /api/files/<id>        POST /v1beta/models/         DEL  /api/admin/users/<id>
  DEL  /api/files/<id>             :generateContent        DEL  /api/admin/users/<id>/sessions
  GET  /api/sessions                                       GET  /api/admin/vitals
  POST /api/sessions                                       GET  /api/admin/contacts
  PUT  /api/sessions/<id>     AUTH
  DEL  /api/sessions/<id>     ----
  DEL  /api/sessions/<id>/msg GET  /auth/status
  GET  /api/memory            GET  /auth/start
  POST /api/memory            POST /auth/exchange
  DEL  /api/memory            GET  /auth/poll
  DEL  /api/memory/<idx>      GET  /auth/lookup
  POST /api/fetch             GET  /api/me
  POST /api/stt
  POST /api/tts               DOCS
  POST /api/generate/file     ----
  POST /api/generate/image    GET  /api/docs
  GET  /api/skills
  POST /api/skills/upload
  GET  /api/skills/<name>
  DEL  /api/skills/<name>
```
