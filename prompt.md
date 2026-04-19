# Codex Meta Prompt

You are building a full working portfolio website with a text-to-text chatbot powered by Jaika.

This product must feel like a polished personal website first, with an embedded chatbot that can answer as the person using the `_persona` flow.

## Non-Negotiable First Step

Do not start coding immediately.

Your first response must be clarifying questions only.

Before writing, editing, scaffolding, or running implementation commands, ask concise questions about:

- who the portfolio is for
- what source materials are available
- what public contact details should be shown
- what Jaika base URL should be used
- whether the `_persona` skill upload endpoint should be wired and used up front
- whether Jaika authentication already works in the browser
- whether CORS is enabled for the Jaika API
- whether placeholders are acceptable
- whether there are any GitHub Pages repo or subpath constraints

Wait for answers before implementation.

If important information is missing, do not guess. Ask first.
If the user explicitly says to proceed with placeholders, continue with reasonable defaults and clearly label placeholders.

## Hosting Constraint

This must be built to work as a static website deployable on GitHub Pages.

That means:

- prefer plain `HTML`, `CSS`, and `JavaScript`
- do not assume Node, Vite, React, Next.js, or any server runtime
- do not require a backend unless the user explicitly approves a separate backend
- do not rely on server-side rendering
- do not rely on build-only environment variables
- use relative asset paths that work on GitHub Pages
- avoid client-side routing that breaks on static hosting
- use browser storage where appropriate for persistence

## Personal Project Credential Rule

Treat this as a personal project prototype. If code is already written check and continue from where it is left and complete.

If needed to make the static GitHub Pages version work, it is acceptable to hardcode Jaika values directly in the client code, including:

- API keys
- bearer tokens
- base URLs
- other auth values required by the user's own account

Do not stop implementation just because credentials would be visible in the browser.

Prefer placing these values in one clearly labeled config section so they can be changed later.

You may still mention that this is not production-safe, but do not block on it.

## Goal

Build an end-to-end portfolio site that:

- looks like a premium personal brand site, not a generic dashboard
- has sections for hero, about, selected projects, writing or experience, and contact
- includes a chat interface that answers questions about the person in first person
- creates and uploads `_persona` content from source documents such as LinkedIn PDF, resume, bio notes, and project writeups
- extracts durable facts into memory
- stores and resumes conversations with Jaika sessions
- uses Jaika prompt enhancement, guardrails, and evals as first-class product features

## Critical Implementation Decisions

These decisions override any generic portfolio-bot pattern:

- `_persona.md` is the source of truth for persona behavior.
- Generate the `_persona.md` content directly from the same user-uploaded source material that is used to create the website copy.
- The final persona artifact should exist as markdown and be publishable directly.
- When publishing the persona, upload the contents of `_persona.md` to Jaika using the `_persona` skill name.
- After `_persona` is published, use `POST /api/prompt` in the normal Jaika chat flow for conversations.
- Store and reuse one persistent Jaika session instead of creating many independent sessions for the same portfolio chatbot.
- Local UI can still show memory facts, source previews, or operator notes, but `_persona` should be treated as the main persona layer.
- Do not expose debug-oriented retrieval panels, developer status boards, prompt internals, or raw eval plumbing in the main user-facing interface unless the user explicitly asks for an admin/debug surface.

## Input Material

The user may provide any mix of:

- LinkedIn PDF export
- resume PDF or DOCX
- website copy
- project notes
- case studies
- writing samples
- testimonials
- plain text bio

Treat this material as the source of truth for:

- persona voice
- factual memory
- project summaries
- contact details
- writing style
- scope boundaries

If multiple files are available, ingest all of them.
If only one document is available, still build a working flow from that single source.

## Required Product Behavior

### 1. Portfolio UI

Build a responsive portfolio website with:

- a visually strong landing section
- an about section
- a selected projects section
- a writing, experience, or highlights section
- a contact section
- a persistent chatbot panel, dock, or bottom-right popup

The chatbot UI should feel integrated into the brand, not bolted on.

### 2. Persona Setup Flow

Create an onboarding or admin flow where the user can:

- upload LinkedIn PDF, resume, or other source files
- paste additional freeform text
- review the generated `_persona.md` content
- review or edit extracted memory facts
- publish the persona to Jaika

The first version should work even if `_persona.md` creation is simple and deterministic.
Do not block the experience on perfect extraction.

### 3. Chat Flow

The chat flow must:

- create a session if one does not exist
- continue the same session for follow-up questions
- stream or progressively render responses when available
- reload prior session history when reopening the app
- stay scoped to the person and their work when `_persona` is active

### 4. Make persona.md from the data user gave. Jaika will already take care of it once you upload it.

Prefer `_persona.md` as the actual file name if the user is using Jaika's `_persona` skill directly.

### 5. Guardrails and Evals

Expose guardrails and eval thinking in the product:

- keep the persona chatbot on-topic
- refuse irrelevant questions when needed
- make the prompt layer visible in the code structure
- add a way to run or display guardrail eval status if the backend exposes it

## Jaika API Requirements

Use these Jaika endpoints where relevant:

- `GET /api/me`
- `POST /api/prompt`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/<id>`
- `DELETE /api/sessions/<id>/messages`
- `GET /api/memory`
- `POST /api/memory`
- `DELETE /api/memory`
- `GET /api/skills`
- `POST /api/skills/upload`
- `DELETE /api/skills/<name>`
- `POST /api/upload`
- `GET /api/files`
- `GET /api/eval/guardrails` if available to this user

Important persona behavior:

- upload `_persona` via `POST /api/skills/upload`
- `_persona` replaces the default Jaika system prompt for this user account until deleted
- `_persona` should make the bot answer as the person
- `_persona` can also be used as the main guard / behavior layer for the chatbot, including whether it should stay narrowly portfolio-scoped or answer broader world questions in a controlled way
- memory facts should be added via `POST /api/memory`
- sessions should be resumed with `session_id`
- after `_persona` is added, continue using `POST /api/prompt` as the normal chat endpoint

## Suggested End-to-End Flow

1. Load `GET /api/me` to confirm the signed-in user.
2. Upload LinkedIn PDF, resume, and supporting files.
3. Send files to `POST /api/upload`.
4. Generate structured persona content from uploaded files and pasted text.
5. Upload the final persona using:

```json
{
  "name": "_persona",
  "content": "..."
}
```

6. Extract durable facts and store them with `POST /api/memory`.
7. Create or load a chat session.
8. Send chat messages through `POST /api/prompt` using `session_id`.
9. Render responses in the portfolio chatbot UI.
10. Restore session history on reload.

## Stack Guidance

Because this must deploy on GitHub Pages, prefer:

- `index.html`
- `styles.css`
- modular `script.js` files
- browser-native `fetch`
- browser storage such as `localStorage` for client persistence
- progressive enhancement and a simple static file structure

Do not introduce Node-based tooling unless the user explicitly asks for it.

If document parsing in-browser is too heavy or unreliable, implement a graceful fallback:

- allow pasted text extraction
- allow plain-text notes
- clearly explain what file types work in the static version
- ask before adding any non-static dependency

## Persona Generation Guidance

Create `_persona.md` content as structured markdown, not a vague paragraph.

Aim for sections like:

- Overview
- Capabilities
- Projects
- Career / History
- Style / Working Principles
- Contact / Meta

Extract memory facts as short durable lines, for example:

- "Built Jaika and AI product prototypes"
- "Prefers concise technical communication"
- "Portfolio emphasizes product building, engineering, and shipping"

## UX Bar

The site should feel intentional and premium:

- avoid bland dashboard styling
- avoid generic AI demo styling
- make typography, spacing, and layout feel authored
- make the chat panel feel like part of the portfolio narrative
- ensure the site works on desktop and mobile
- prefer a premium dark theme unless the user explicitly asks for a different visual direction
- prefer a bottom-right floating chat popup over a permanent wide sidebar unless the user explicitly wants a sidebar
- keep the visible UI clean; avoid shipping debug blocks, retrieval diagnostics, raw status boards, or implementation-explainer panels in the default experience

If the repo already has a design system or brand direction, follow it.
If not, choose a bold but professional direction.

## Implementation Standards

- keep the data flow explicit
- separate UI, API calls, and persona ingestion logic
- make error states usable
- do not fake success states
- handle missing or partial source documents gracefully
- keep the code organized enough for iteration
- explain any GitHub Pages limitations clearly before implementing a workaround
- avoid unnecessary local duplication of persona instructions once Jaika `_persona` is active
- use the uploaded source material as the basis for both portfolio copy and `_persona.md`
- keep admin or setup controls available without letting them dominate the landing experience

## Acceptance Criteria

The feature is complete only if all of the following are true:

- the portfolio site renders and looks deliberate
- the chatbot can send and receive real messages
- `_persona` upload is wired
- memory creation is wired
- session creation and session continuation are wired
- source documents can be uploaded and used in the setup flow
- the app handles missing or partial source documents gracefully
- the code is organized enough for iteration
- the site can be deployed as a static GitHub Pages website

## What To Deliver

Deliver:

- the working static website UI
- the API client wiring
- the onboarding flow for persona setup
- the chat flow
- helpers for extracting persona and memory from uploaded material within static-hosting constraints
- concise setup and GitHub Pages deployment instructions

When making implementation choices, bias toward the fastest path to a real, usable static website.
