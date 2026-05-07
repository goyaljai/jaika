# Master Prompt — Personal Website + AI Chatbot (Raunak Jain)

Copy everything from the horizontal rule below and paste it into your vibe-coding agent (Claude, Cursor, ChatGPT, etc.).
Fill in the `[TODO]` placeholders with your real details first.

---

## TASK

Build me a complete, single-file `index.html` personal website with an embedded AI chatbot in the bottom-right corner.

---

## ABOUT ME (from LinkedIn)

```
Name: Raunak Jain
Headline: Product Leader — 14+ years building and scaling digital products
Location: Greater Bengaluru Area, India
LinkedIn: https://www.linkedin.com/in/raunakjain
Email: [TODO: your email]

About:
Product Leader with 14+ years of experience building and scaling digital products.
Currently at InMobi, Bengaluru. IIT Bombay alumnus (JEE AIR 1522).
[TODO: paste your full LinkedIn About section here]

Experience:
- InMobi — [TODO: role title], Bengaluru, [TODO: year] – Present
  [TODO: what you do]
- [TODO: paste remaining experience from LinkedIn]

Education:
- IIT Bombay, 2007–2011 (JEE AIR 1522). Cultural Councillor 2009, Social Secretary 2008.
- [TODO: any other education]

Skills:
Product Strategy, Roadmap, 0→1 Building, Growth, A/B Testing, OKRs, Stakeholder Management,
[TODO: add your full skills list]

Publications:
- "Impact of Indoor Vs Outdoor air pollution on the health of Infants below the age of 3 yrs." — IIM Bangalore, Dec 2012
```

---

## CHATBOT CONTEXT

The chatbot must answer as Raunak Jain — in first person, using this knowledge base:

```
[TODO: paste the full contents of skills.md here]
```

---

## JAIKA API INTEGRATION — EXACT IMPLEMENTATION

> This is critical. Follow this exactly — do not use any other AI API.

### Server details
```
Base URL:  http://35-207-202-131.sslip.io:5244
User ID:   112750385266622618824
Auth:      X-User-Id header (not Bearer token)
```

### How the API works
- **Endpoint:** `POST /api/prompt`
- **Headers:** `Content-Type: application/json` + `X-User-Id: 112750385266622618824`
- **Body:** `{ "prompt": "...", "session_id": "...", "stream": false }`
- **Response:** `{ "text": "...", "session_id": "abc123" }`
- Pass the `session_id` back on every subsequent message to maintain conversation history
- If `session_id` is `null` or omitted on the first call, the server creates a new session and returns its ID

### How the persona works

The chatbot uses the `_persona` skill — a one-time upload that tells the server to answer as you instead of as Jaika. Raunak uploads it once; all visitors automatically get the persona.

**One-time setup (run this once after logging in):**
```bash
# Upload your persona — replace [TODO] with your skills.md content
curl -X POST http://35-207-202-131.sslip.io:5244/api/skills/upload \
  -H "X-User-Id: 112750385266622618824" \
  -H "Content-Type: application/json" \
  -d '{"name": "_persona", "content": "[TODO: paste full skills.md content here]"}'
```

### JavaScript implementation (use this exactly)

```javascript
const JAIKA_SERVER = "http://35-207-202-131.sslip.io:5244";
const JAIKA_USER_ID = "112750385266622618824";  // Raunak's UID — visitors use his quota, no accounts needed

let chatSessionId = null;

// Send a message — persona is set server-side via _persona skill, no need to pass it per-request
async function sendMessage(userText) {
  const resp = await fetch(`${JAIKA_SERVER}/api/prompt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": JAIKA_USER_ID          // Raunak's UID — visitors don't need accounts
    },
    body: JSON.stringify({
      prompt: userText,
      session_id: chatSessionId,           // null on first call, reuse after
      stream: false
    })
  });

  if (!resp.ok) throw new Error("API error");
  const data = await resp.json();
  if (data.session_id) chatSessionId = data.session_id;  // save and reuse
  return data.text;
}
```

**How it works:**
- `_persona` skill is stored server-side for UID `112750385266622618824`
- Every request from any visitor with that UID gets Raunak's persona as the system instruction
- Off-topic questions (maths, coding, world events) are automatically refused
- Career/fit questions ("Would you be a good fit at Meta?") are answered using Raunak's background
- Update persona anytime: re-run the upload command above with new content
- Revert to Jaika: `curl -X DELETE .../api/skills/_persona -H "X-User-Id: ..."`

### Error handling
- If the API is unreachable, show in the chat bubble:
  > "Raunak is currently unavailable here. Reach out directly on [LinkedIn](https://www.linkedin.com/in/raunakjain)."
- Never show raw error messages to the user

---

## WEBSITE REQUIREMENTS

### Design
- Single self-contained `index.html` (all CSS + JS inline, zero external frameworks except Google Fonts CDN)
- Dark theme: background `#0d1117`, surface `#161b22`, border `#30363d`, text `#e6edf3`, accent `#58a6ff`
- Clean, minimal, modern — inspired by Linear / Vercel / GitHub aesthetics
- Fully responsive (mobile + desktop)
- Smooth scroll, subtle fade-in on scroll animations

### Sections (in order)
1. **Hero** — Name, headline, short punchy bio, two CTA buttons: `View Work` (scrolls to Projects) + `Chat with Me` (opens chatbot)
2. **About** — 2–3 paragraphs, round avatar with initials `RJ` as fallback
3. **Experience** — Vertical timeline, each item: company name, role, dates, 2-line impact statement
4. **Projects / Work** — 2–3 card grid. Each card: title, description, tags
5. **Skills** — Grouped pill tags: Product, Technical, Domain
6. **Contact** — LinkedIn icon link, email, any other links

### Chatbot widget (bottom-right)
- Floating button: 52px dark circle, chat bubble icon, subtle pulse animation
- Click opens a panel: 320px wide × 460px tall, bottom-right, 20px margin
- Panel header: avatar with initials `RJ` + "Chat with Raunak" + close `×` button
- Welcome message shown immediately (before user types):
  > "Hey! I'm Raunak. Ask me about my work, background, or anything on your mind."
- Message bubbles: user = right-aligned accent color, bot = left-aligned dark surface
- Typing indicator: three animated dots while waiting for API response
- Input field + Send button at bottom
- Small footer inside panel: `Powered by Jaika` (plain text, subtle)
- On mobile: panel takes full width, anchored to bottom

---

## OUTPUT REQUIREMENTS

- One single `index.html` file, completely self-contained
- No build step, no npm, no React — pure HTML + CSS + JS
- Works when opened as `file://` AND when served from any web server
- The `skills.md` content should be inlined as a JS const at the top of the script tag:
  ```javascript
  const SKILLS_MD_CONTENT = `...paste skills.md here...`;
  ```
- Comment each major section in the HTML and JS

---

## CHECKLIST FOR THE AGENT

Before finishing, verify:
- [ ] `JAIKA_SERVER` points to `http://35-207-202-131.sslip.io:5244`
- [ ] `JAIKA_USER_ID` is `112750385266622618824`
- [ ] Auth header is `X-User-Id`, not `Authorization: Bearer`
- [ ] `_persona` skill has been uploaded server-side (one-time setup via curl above)
- [ ] No `persona` field in API requests — persona is handled server-side
- [ ] `session_id` is saved from first response and reused in all subsequent calls
- [ ] Error fallback shows LinkedIn link, not raw error
- [ ] All `[TODO]` placeholders in the HTML are filled with real content
- [ ] Single file, no external JS frameworks

---
