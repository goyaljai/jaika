# Skills.md Generator — Structured Data Architect Prompt

Use this prompt with any AI coding tool (Claude, ChatGPT, Cursor, etc.) to convert your raw info into an optimized `skills.md` file that works well with the Jaika persona chatbot.

---

## THE PROMPT (paste this into your AI, fill in the [PASTE] section)

```
Task: Act as a Structured Data Architect. Your goal is to convert my info into a highly optimized skills.md file designed for an AI Knowledge Agent.

Formatting Rules:
- Markdown Only: Use clear H1, H2, and H3 headers.
- Explicit Attributes: Use bullet points for facts. Avoid long, flowery paragraphs.
- Data Categorization: Group information into logical blocks.
- No Ambiguity: Use precise values, dates, and metrics where available.
- Keyword Density: Ensure key industry terms are present so the agent can "map" them to user queries later.

Structure Template to Follow:

# [Subject Name] — Overview
A high-level summary (2-3 sentences max).

# Capabilities / Skills
Hard data points, tools, and areas of expertise — listed precisely.

# Context & History
Past roles, companies, dates, and measurable impact. Be specific.

# Logic & Style
How this person operates — their decision-making style, values, working principles.

# Contact & Meta
Links, contact info, or references.

Input Data:
[PASTE YOUR RAW INFO, LINKEDIN TEXT, OR PROFILE NOTES HERE]
```

---

## WHY THIS FORMAT WORKS

The Jaika persona system treats your `skills.md` as the only source of truth (Closed-World Assumption). A well-structured file means:

| Bad format | Good format |
|---|---|
| "I have extensive experience in product management across multiple organizations" | "14 years product leadership · InMobi · IIT Bombay (JEE AIR 1522)" |
| Wall of text bio | Bullet-pointed facts with dates and metrics |
| Vague sections | Named H2 sections (`## Capabilities`, `## History`) the AI can locate |
| Missing dates | Explicit date ranges: "2007–2011", "2019–Present" |

---

## HOW TO USE

1. Copy the prompt block above
2. Paste it into Claude / ChatGPT / Cursor
3. Replace `[PASTE YOUR RAW INFO...]` with your LinkedIn text, CV, or notes
4. Let the AI generate the structured `skills.md`
5. Upload to Jaika as your `_persona`:

```bash
curl -X POST http://SERVER/api/skills/upload \
  -H "X-User-Id: YOUR_UID" \
  -H "Content-Type: application/json" \
  -d '{"name": "_persona", "content": "...paste generated skills.md content here..."}'
```

---

## EXAMPLE OUTPUT STRUCTURE

```markdown
# Raunak Jain — Overview
Product Leader with 14+ years building digital products. Currently at InMobi, Bengaluru.
IIT Bombay alumnus (JEE AIR 1522).

# Capabilities / Skills
## Product
- Product Strategy · Product Roadmap · 0→1 Product Building
- User Research · A/B Testing · Growth
- OKRs · Stakeholder Management

## Technical
- Data analysis · SQL · Analytics tools

## Domain
- AdTech / Mobile Advertising (InMobi)
- Air quality / health research (IIM Bangalore)

# Context & History
- **InMobi** — Product Leader · Bengaluru · [Year]–Present
  - [2-3 lines on ownership and impact]
- **IIT Bombay** — 2007–2011 · JEE AIR 1522
  - Cultural Councillor (2009), Social Secretary (2008)

# Logic & Style
- 14+ years of building — values outcomes over processes
- Direct communicator, bias for action
- Strong on data-driven decisions

# Contact & Meta
- LinkedIn: https://www.linkedin.com/in/raunakjain
- Email: [your email]
- Publications: "Impact of Indoor Vs Outdoor air pollution on the health of Infants below the age of 3 yrs." — IIM Bangalore, December 2012
```
