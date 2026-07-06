# Command Center

A personal AI command center built on the **WAT framework** (Workflows, Agents, Tools). A board of 7 world-class advisors — grounded in their own writing via RAG, not just persona prompts — daily intelligence briefs, morning/evening/weekly briefings, a nightly memory engine that keeps a living knowledge base current, a proactive anticipation layer that reaches out before you ask, and full life-ops tracking: goals, decisions, convictions, vitals, money, CRM, and quarterly bets. A Telegram bot mirrors most of it so the loop doesn't require opening a laptop.

![Dashboard](https://img.shields.io/badge/Flask-4000-black?style=flat-square&logo=flask) ![LLM](https://img.shields.io/badge/OpenRouter%20%E2%86%92%20Groq-primary%2Ffallback-orange?style=flat-square) ![RAG](https://img.shields.io/badge/RAG-Chroma%20%2B%20fastembed-4B8BBE?style=flat-square) ![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

---

## What it does

| Module | Description |
|---|---|
| **Board** | Ask 7 AI advisors (Musk, Bezos, Buffett, Jobs, Munger, Thiel, Dalio) anything. Smart routing picks 1-4 relevant advisors per question. Each response is grounded in the advisor's own writing/interviews via RAG (4,800+ chunks across all 7), plus your own past sessions via a second, personal RAG index. |
| **Debate Mode** | Two advisors argue opposing sides across two rounds, a third judges. Structured disagreement instead of parallel monologues — surfaces the real tension in a decision. |
| **Intel** | Daily intelligence brief — 7 stories across HN + Reddit, one per angle (hottest, viral, controversial, breaking, AI/tech, startup/biz, wildcard), boosted toward stories that match your active goals or pipeline deals. Ends with a "Board's Verdict." Auto-generated at 5am. |
| **Morning / Evening Briefs** | Morning brief opens with accountability flags (missed logs, stuck blockers, low momentum) and the day's one thing. Evening brief closes the loop — did today earn its place, what's the pattern this week. Both persist and are browsable in an archive. |
| **Weekly Synthesis** | Sunday cross-module review, opening with a deterministic "Week in Numbers" block (energy, momentum, MRR, commitments, calibration) computed in code — not left to the model to get right. |
| **Quarterly Bets** | One committed bet per quarter. The board challenges it on commit, every morning brief and the Sunday auto-board reference it, and it's judged honestly at quarter-end. |
| **Memory Reflection** | Nightly job reads the day's full delta (logs, vitals, decisions, board sessions) and proposes ADD/REMOVE operations against `knowledge_base.md` — the mem0 pattern, applied deterministically. A Sunday-only job proposes `profile.md` updates, approved or rejected via Telegram. |
| **Anticipation Engine** | Morning deterministic scan — blocker streaks, energy decline, momentum drops, silent pipeline, overdue CRM follow-ups, newly-broken commitments — pushed to Telegram. No LLM hallucination risk; silent when nothing fired. |
| **Goals** | Milestone tracker with time logging, weekly hours, and goal velocity (moving/slow/stalled) inferred from logs + decisions + hours. |
| **Decisions** | Decision journal with outcome tracking, review-date reminders, and a full calibration panel (stated confidence vs. actual success rate by bucket). |
| **Convictions** | Contrarian thesis tracker, extractable from a dedicated training chat. Validating/invalidating a conviction writes it into the knowledge base. |
| **Vitals** | Wellness coach — "you at 50, talking to you at 23." Reads everything else in the system as context so it doesn't ask what it should already know. |
| **Money** | Expense tracker with bank statement import (CSV → AI auto-categorizes), plus pipeline/MRR tracking with a deterministic gap-to-target engine (`gap_engine.py`). |
| **History** | Every board/debate session stored and searchable, plus the full knowledge base, viewable and editable. |
| **Telegram Bot** | `/log`, `/evening`, `/board`, `/debate`, `/ask` (follow-up), `/brief`, `/recs`, `/scan`, `/retro`, voice notes (transcribed via Groq Whisper). Push notifications for morning brief, evening brief, unlogged nag, aging recommendations, decision reviews, weekly synthesis, and anticipation signals. |

---

## Architecture — WAT Framework

```
Workflows (workflows/*.md)   →   Plain-language SOPs
        ↓
Agent (Claude / app.py)      →   Reads workflows, orchestrates tools, handles failures
        ↓
Tools (tools/*.py)           →   Deterministic Python: API calls, scraping, RAG, file ops
```

AI handles reasoning and orchestration. Deterministic code handles execution and math (`gap_engine.py` computes MRR gap, runway, commitment scoreboard, and decision calibration — the LLM only narrates numbers it's given, never invents them).

---

## The self-improving loop

1. **You ask the board or log your day** → session/log saved.
2. **Nightly (10:30pm)**, `tools/memory_reflect.py` reads the full day's delta across every module and proposes specific ADD/REMOVE operations against `knowledge_base.md` — not a blind append, an actual editorial pass.
3. **Sundays**, the same job proposes a `profile.md` rewrite reflecting what actually changed that week (stale claims like "3 weeks into gym" don't linger) — you approve or reject it over Telegram.
4. **Every board/debate session** re-indexes your personal RAG (`personal_rag_db/`) incrementally, so retrieval stays current without a manual rebuild.
5. **Monthly**, `consolidate_knowledge_base()` rewrites the KB to merge duplicates and drop stale entries so it never grows unbounded.
6. **Every morning**, the anticipation engine and the brief itself both read the current state of everything above — goals, KB, wellness, quarterly bet, commitment scoreboard — before a word is generated.

The KB and the two RAG indexes are the memory. They compound instead of resetting every session.

---

## The board

Seven advisors, each with a distinct voice and mental model — and each grounded in their own actual writing, not just a persona prompt:

| Advisor | Edge | RAG corpus |
|---|---|---|
| **Elon Musk** | First principles, physics thinking, 10x not 10% | Lex Fridman interview, Tesla earnings calls |
| **Jeff Bezos** | Day 1 mentality, customer obsession, long-term compounding | All 24 Amazon shareholder letters, 1997-2021 |
| **Warren Buffett** | Circle of competence, moats, patience | All Berkshire Hathaway letters, 1977-2024 |
| **Steve Jobs** | Simplicity, taste, saying no to 1000 things | D5/D8 interviews, 1995 Smithsonian oral history, Stanford commencement |
| **Charlie Munger** | Inversion, mental models, avoiding stupidity | Poor Charlie's Almanack, "Elementary Worldly Wisdom," "Psychology of Human Misjudgment" |
| **Peter Thiel** | Contrarian secrets, 0→1, escaping competition | Zero to One, full CS183 Stanford course |
| **Ray Dalio** | Radical transparency, principles, macro patterns | Principles: Life & Work, "How the Economic Machine Works" |

Smart routing picks 1-4 relevant advisors per question (fast-tier call, not all 7 every time). Each response pulls the most relevant passages from that advisor's own words (`chroma_db/`, ~4,800 chunks total) plus your own history (`personal_rag_db/`) via fastembed + Chroma — local, free, no API cost. **Debate mode** picks 2 advisors likely to disagree plus a judge, runs two argument rounds, and renders a verdict instead of averaging opinions away.

After every session, 1-3 specific recommendations are extracted and tracked against deadlines (the commitment scoreboard — kept vs. broken — is surfaced to the board itself in future sessions, so unactioned advice gets confronted rather than forgotten).

---

## RAG pipeline

Two independent Chroma vector stores, both embedded with `fastembed` (ONNX runtime — no PyTorch, ~100MB instead of ~1.5GB, chosen to run on a 1GB VM):

- **`chroma_db/`** — the advisors' own words. Built once on a dev machine (`tools/collect_advisor_sources.py` auto-fetches public letters/speeches, `tools/build_advisor_rag.py` chunks + embeds), then rsynced to the server. Query-time retrieval via `tools/query_advisor_rag.py`.
- **`personal_rag_db/`** — your own history (board sessions, daily logs, decisions, wellness/conviction chats). Built incrementally after every session (`tools/build_personal_rag.py`) — runs server-side, never overwritten by a deploy (excluded from `deploy.sh`'s tarball on purpose).

Both fail soft to empty results if the index is missing — a broken or absent RAG index degrades quality, it never breaks a session.

---

## Onboarding — Profile Interview

The first thing to do is create your `profile.md`. This file is what makes every output personal and specific — the board reads it, the morning brief reads it, the intel analysis reads it. Answer honestly; specificity is what makes the system work.

### Part 1 — Who you are
1. What do you do for work right now, and how do you feel about it?
2. What are you building on the side, or what do you want to build?
3. How old are you, and what stage of life does that feel like to you?
4. What's your financial situation honestly — stable, stretched, surviving?
5. What does your typical week look like? How is your time actually split?

### Part 2 — Where you're going
6. What's your north star — the thing you're pointing everything toward?
7. What's your first major milestone and when do you need to hit it?
8. What would you regret most in 5 years if you didn't do it?
9. What does success look like to you — be specific, not aspirational?

### Part 3 — What's in the way
10. What's the #1 thing blocking you right now?
11. What do you keep avoiding that you know you should do?
12. What's a belief about yourself that might be limiting you?
13. What have you tried that didn't work, and why do you think it failed?

### Part 4 — Who you are beyond work
14. What relationships matter most to you right now, and how are they actually going?
15. What are you anxious about that you don't usually talk about?
16. What do you do to recover when things feel hard?
17. What kind of person do you want to be, not just what do you want to achieve?

### Part 5 — How the board should think about you
18. What kind of advice do you tend to ignore even when it's right?
19. When are you most likely to make a bad decision?
20. What's a contrarian view you hold that most people in your circle disagree with?
21. What do you want the advisors to push back on hardest?

Write the answers into `profile.md` as honest prose — no schema required.

---

## Stack

- **Backend**: Python + Flask (port 4000), gunicorn in production (2 workers × 4 threads, SSE-capable)
- **LLM provider chain**: [OpenRouter](https://openrouter.ai) (primary — free models, per-request billing, 1000 req/day at $10 one-time credit) → [Groq](https://groq.com) (fallback, also the fast-tier default for routing/parsing) → [Together.ai](https://together.ai) (last resort)
- **RAG**: ChromaDB + fastembed (`BAAI/bge-base-en-v1.5`, ONNX) — two independent stores, advisor voice + personal history
- **Intel sources**: Hacker News Firebase API + 11 subreddits' JSON API (no auth needed)
- **Telegram**: long-polling bot (`tools/telegram_bot.py`), no webhook/TLS setup required; voice transcription via Groq Whisper
- **Frontend**: Vanilla HTML/CSS/JS — no framework, no build step, no chart library (inline SVG sparklines)
- **Auth**: Flask session-based login on every route except `/healthz`; cron endpoints are restricted to localhost instead of session auth (they're hit by crontab on the same box)
- **Storage**: JSON files in `data/` + Markdown in `sessions/` and `briefs/` — no database
- **Deploy**: systemd (`Restart=always`, memory-capped) on a GCP VM, Caddy for automatic TLS, idempotent bootstrap script
- **Cron** (all IST): intel 5am, morning brief 5:30am (+6am Telegram push), anticipation 8:45am, aging recs + decision reviews 9am, unlogged nag 9:30pm, evening brief push 9:45pm, memory reflection 10:30pm, weekly synthesis Sunday 8am (+8:05am push), auto-board Sunday 8pm, KB consolidation monthly, quarterly bet retro on the 1st of Jan/Apr/Jul/Oct

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/hariv24/command-center
cd command-center
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements-build.txt` is Mac/dev-only (just `pypdf`, for extracting RAG source PDFs) — not needed on the server.

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=...        # openrouter.ai/keys — add $10 credit for 1000 free req/day (50/day without)
GROQ_API_KEY=...              # console.groq.com — fallback + fast-tier routing
TOGETHER_API_KEY=...          # optional third fallback

CC_SECRET_KEY=any-random-string
CC_USERNAME=your_username
CC_PASSWORD=your_password

TELEGRAM_BOT_TOKEN=...        # optional — message @BotFather to create one
TELEGRAM_CHAT_ID=...          # captured automatically on first message to the bot
```

### 3. Create your profile

Go through the [Onboarding Interview](#onboarding--profile-interview) above and write your answers into `profile.md`.

### 4. Set your north stars

In `app.py`, find `NORTH_STAR` and set your own milestones (shown as countdown strips on Home):

```python
NORTH_STAR = {
    "milestone_1": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this means."},
    "milestone_2": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this means."},
    "milestone_3": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this means."},
}
```

### 5. Build the advisor RAG corpus (optional but recommended)

```bash
pip install -r requirements-build.txt
python3 tools/collect_advisor_sources.py    # auto-fetches public letters/speeches
python3 tools/build_advisor_rag.py          # embeds into chroma_db/
```

Board responses work without this (fails soft to persona-only), but are noticeably sharper with it.

### 6. Download advisor photos (optional)

```bash
python3 tools/download_advisor_images.py
```

### 7. Run

```bash
python3 app.py
```

Open `http://localhost:4000`. Log in with your `.env` credentials.

### 8. Run the Telegram bot (optional)

```bash
python3 tools/telegram_bot.py
```

Message it anything — it captures your chat ID and tells you to add it to `.env` as `TELEGRAM_CHAT_ID`, then restart.

---

## Deploy to a server

Built for a small GCP/AWS/Oracle VM (e2-medium or equivalent, ~1-2GB RAM works — RAG runs on fastembed specifically to fit this).

```bash
cp .deploy.env.example .deploy.env   # fill in DEPLOY_HOST, DEPLOY_KEY, DEPLOY_USER, CC_DOMAIN
```

**First-time setup** — ssh in and run the idempotent bootstrap (swapfile, venv, Caddy, systemd services, cron):

```bash
ssh -i key/<your-key> ubuntu@<host>
~/agent/deploy/setup_server.sh
```

**Every subsequent deploy**:

```bash
./deploy.sh
```

Tars the repo (excluding secrets, data, and the RAG stores — those are synced separately or built server-side), scp's it over, reinstalls dependencies, restarts the `commandcenter` service.

**Sync the advisor RAG index** (built on your dev machine, not shipped by `deploy.sh`):

```bash
rsync -avz -e "ssh -i key/<your-key>" chroma_db/ ubuntu@<host>:~/agent/chroma_db/
```

Full runbook: `workflows/deploy_gcp.md`.

---

## File structure

```
app.py                          # All Flask routes, live-context builder, brief/scan/retro generators
board.py                        # Board engine — personas, routing, debate mode, RAG, KB, session storage
llm.py                          # Provider chain (OpenRouter → Groq → Together), streaming, reasoning-exclusion
gap_engine.py                   # Deterministic trajectory math — MRR gap, runway, commitment scoreboard, calibration
tools/
  daily_brief.py                 # HN + Reddit scraper, goal-aware story selection, parallel LLM analysis
  memory_reflect.py              # Nightly KB reflection + Sunday profile refresh proposal
  anticipate.py                  # Morning deterministic pattern-deviation scan
  telegram_bot.py                 # Telegram companion — commands + scheduled pushes
  collect_advisor_sources.py      # Auto-fetches free advisor source material
  build_advisor_rag.py            # Chunks + embeds advisor sources into chroma_db/
  query_advisor_rag.py            # Query-time retrieval for board sessions
  build_personal_rag.py           # Incremental personal-history RAG index
  ingest_content_folder.py        # Manual PDF/txt ingestion for advisor corpus
  board_session.py                # CLI wrapper for a one-off board session
  download_advisor_images.py      # Fetches advisor headshots for the UI
workflows/
  board_session.md                # SOP for board sessions
  daily_brief.md                  # SOP for the intel pipeline
  deploy_gcp.md                   # Full server deploy runbook
deploy/
  setup_server.sh                 # Idempotent server bootstrap (swap, venv, Caddy, systemd, cron)
templates/
  index.html                      # Single-page dashboard
  login.html                      # Login page
static/advisors/                 # Advisor photos
rag_sources/                     # Raw text for the advisor RAG corpus (gitignored, large)
chroma_db/                       # Advisor-voice vector store (gitignored, rsynced to server)
personal_rag_db/                 # Personal-history vector store (gitignored, built server-side)
data/                            # All JSON storage — goals, decisions, pipeline, recs, etc. (gitignored)
sessions/                        # Board/debate session history (gitignored)
briefs/                          # Intel/morning/evening/weekly/retro briefs (gitignored)
profile.md                       # Your personal profile (gitignored)
.env / .deploy.env                # API keys, credentials, deploy target (gitignored)
```

---

## Security notes

Every `/api/*` route except `/healthz` requires an authenticated session. Cron endpoints (`/api/cron/*`) skip session auth but reject any request not originating from `127.0.0.1`/`::1`, since they're only ever called by crontab on the same box. If you expose this beyond localhost, put a real reverse-proxy TLS layer in front (the deploy scripts set up Caddy for exactly this) and never commit `.env`, `.deploy.env`, or the `key/` directory — all three are gitignored by default.

---

## Why this works

Most AI productivity tools fail because they're generic and forget everything between sessions. This one is built to compound:

- **Profile** makes every output specific to you, kept current by a weekly proposed rewrite instead of drifting silently
- **Two RAG indexes** ground the board in real primary sources and in your own actual history, not just persona prompts
- **Knowledge base** grows through an actual nightly editorial pass (add/remove), not a blind append
- **Deterministic math** (`gap_engine.py`) means the numbers in every brief are always right, even if the model narrating them isn't
- **Anticipation** means the system sometimes reaches you before you open it
- **Debate mode** means the board sometimes disagrees with itself instead of always converging to comfortable consensus

It's built to get harder to replace over time, not easier.

---

## License

MIT — build on it, fork it, make it yours.
