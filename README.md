# Command Center

A personal AI command center built on the **WAT framework** (Workflows, Agents, Tools). Board of 7 world-class advisors, daily intelligence briefs from Hacker News + Reddit, morning briefings, and full life-ops tracking — goals, decisions, convictions, vitals, and finances.

![Dashboard](https://img.shields.io/badge/Flask-4000-black?style=flat-square&logo=flask) ![LLM](https://img.shields.io/badge/Groq-llama--3.3--70b-orange?style=flat-square) ![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

---

## What it does

| Module | Description |
|---|---|
| **Board** | Ask questions to 7 AI advisors (Musk, Bezos, Buffett, Jobs, Munger, Thiel, Dalio). Smart routing picks the 2-3 most relevant for your question. |
| **Intel** | Daily intelligence brief — 7 stories across HN + Reddit, each a different angle: hottest, viral, controversial, breaking, AI/tech, startup/biz, wildcard. Auto-generated at 5am daily. |
| **Morning Brief** | Personalized daily brief built from your logs, board sessions, and knowledge base. Waits for you when you wake up. |
| **Goals** | Milestone tracker with time logging and weekly hour tracking. |
| **Decisions** | Decision journal with outcome tracking and automatic KB injection. |
| **Convictions** | Contrarian thesis tracker. Log your edge, stress-test it through the board. |
| **Vitals** | Wellness coach that reads your daily logs and builds a running picture of your health. |
| **Money** | Expense tracker with bank statement import (paste → AI parses). |
| **History** | Every board session stored and searchable. Living knowledge base that grows with every session. |

---

## Architecture — WAT Framework

```
Workflows (workflows/*.md)   →   You write plain-language SOPs
        ↓
Agent (Claude / app.py)      →   Reads workflows, orchestrates tools, handles failures
        ↓
Tools (tools/*.py)           →   Deterministic Python: API calls, scraping, file ops
```

The key insight: AI handles reasoning and orchestration. Deterministic scripts handle execution. When each step is reliable, multi-step pipelines actually work.

---

## Self-improving loop

Every time you use the system, it gets smarter:

1. **You ask the board** → board session saved → 2-3 insights extracted → appended to `knowledge_base.md`
2. **You log a decision** → appended to KB with date and confidence
3. **You validate a conviction** → written to KB as a confirmed mental model
4. **You log your day** → last 7 days feed tomorrow's morning brief
5. **Tomorrow morning** → brief is already generated, waiting, built from everything above

The KB is the brain. It compounds.

---

## Onboarding — Profile Interview

The first thing to do is create your `profile.md`. This file is what makes every output personal and specific. The board reads it, the morning brief reads it, the intel analysis reads it.

Answer these questions honestly. The more specific and vulnerable you are, the better the system works. Don't write what sounds good — write what's actually true.

---

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

---

Once you've answered these, write the answers into `profile.md` in any format that feels natural. The system reads it as context — no schema required, just honest prose.

**Starter template:**

```markdown
# My Profile

## Who I am
[Your background, current work, age, how you feel about where you are]

## What I'm building
[Current projects, what you're working toward]

## My north star
[The big goal. When. What it means.]

## What's in the way
[Honest blockers — internal and external]

## Beyond work
[Relationships, anxieties, what you care about outside of building]

## How to advise me
[What to push back on. When I'm likely to be wrong. What I avoid.]
```

---

## What's New — v2.0 (Full Interconnection Update)

This update wires all 9 modules together into a single living system. Previously each module was an isolated silo. Now they share context continuously.

### 6 interconnections added

**1. Universal context builder**
A single `_build_live_context()` function in `app.py` assembles a real-time snapshot from all your data — active goals with days remaining, open decisions, unactioned board recommendations, and last 3 days of daily logs. Every LLM call can pull from this. Only injects sections that have actual data — no empty noise injected into prompts.

**2. Board gets live context**
The board of advisors now receives your active goals, open decisions, recent log energy/output, and unactioned previous recommendations as part of every session. Advisors no longer give generic founder advice — they give advice relative to your actual situation. KB excerpt also increased from 1,200 to 2,500 characters so less historical context gets lost.

**3. Morning brief leads with Intel's Board's Verdict**
The morning brief now opens with the macro pattern extracted from that morning's intelligence brief. The intel pipeline runs at 5am, morning brief at 5:30am — so the macro signal is always ready. Approaching goal deadlines and unactioned recommendations also surface automatically.

**4. Evening brief has continuity**
The evening reflection now loads the morning brief, any decisions logged during the day, and any board sessions run that day. Morning sets the intention. Evening evaluates it. No longer disconnected.

**5. Intel brief knows your goals**
The "Direct implications for you" section of each story analysis now references your actual active goals. Instead of generic "founders should watch this," it says what this story means for your specific targets and timeline.

**6. Conviction → KB feedback loop**
When you mark a conviction as validated or invalidated, it's automatically written to the knowledge base. The board sees it in all future sessions — your confirmed mental models become part of the permanent context.

### Other fixes in this release

- **7-story intel guaranteed** — Added fallback logic so all 7 category slots always fill, even when the relevant story pool is thin. Previously could return 3 stories if only 3 relevant stories were found.
- **Intel date pills cleaned up** — The date selector in the Intel panel now only shows actual intel brief files (`YYYY-MM-DD`), not morning/retro/weekly files that were polluting the list.
- **Wellness coach rewritten** — The coach persona is now "you at 50, looking back at you at 23." Direct, personal, no clinical distance. Covers the things you won't bring to the board: relationships, family, financial anxiety, important personal decisions.
- **Weekly synthesis engine** — New Sunday 8pm cron that synthesizes the week across all modules: what happened, what patterns emerged, what the board said, what you decided, what's next.
- **Background threading for intel** — Long LLM operations (3-5 min) now run in a background thread. The UI polls every 10 seconds instead of blocking and timing out.

---

## Stack

- **Backend**: Python + Flask (port 4000)
- **LLM**: [Groq](https://groq.com) — `llama-3.3-70b-versatile` (reasoning), `llama-3.1-8b-instant` (routing/parsing)
- **Intel sources**: Hacker News Firebase API + Reddit JSON API (no auth needed)
- **Frontend**: Vanilla HTML/CSS/JS — no framework, no build step
- **Design**: BMW M aesthetic — near-black canvas, 0px border-radius, spotlight border cards, Outfit + JetBrains Mono
- **Auth**: Flask session-based login
- **Storage**: JSON files in `data/` + Markdown in `sessions/` and `briefs/`
- **Deploy**: Systemd service on any Linux VM (`Restart=always`)
- **Cron**: Intel at 5am, retry at 5:30am. Morning brief at 5:30am, retry at 6am. Weekly synthesis Sunday 8pm.

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/hariv24/command-center
cd command-center
python3 -m venv venv
source venv/bin/activate
pip install flask groq python-dotenv requests
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
GROQ_API_KEY=your_groq_api_key       # free at console.groq.com
CC_SECRET_KEY=any-random-string
CC_USERNAME=your_username
CC_PASSWORD=your_password
```

### 3. Create your profile

Go through the [Onboarding Interview](#onboarding--profile-interview) above and write your answers into `profile.md`. The more honest and specific, the better every output becomes.

### 4. Set your north stars

In `app.py`, find `NORTH_STAR` and update it with your own milestones:

```python
NORTH_STAR = {
    "milestone_1": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this means."},
    "milestone_2": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this means."},
    "milestone_3": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this means."},
}
```

These show as countdown strips on the home dashboard.

### 5. Download advisor photos

```bash
python3 tools/download_advisor_images.py
```

### 6. Run

```bash
python3 app.py
```

Open `http://localhost:4000`. Log in with your `.env` credentials.

---

## Deploy to a server (optional)

Designed to run 24/7 on a cheap Linux VM (Oracle Free Tier works great).

**Systemd service** (`/etc/systemd/system/commandcenter.service`):

```ini
[Unit]
Description=Command Center
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/agent
ExecStart=/home/ubuntu/agent/venv/bin/python3 app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable commandcenter
sudo systemctl start commandcenter
```

**Cron** (auto-generates briefs daily):

```bash
crontab -e
```

Add (adjust for your timezone — these are UTC+5:30 IST):

```
0 5 * * * curl -s -X POST http://localhost:4000/api/cron/intel >> ~/intel_cron.log 2>&1
30 5 * * * curl -s -X POST http://localhost:4000/api/cron/morning >> ~/morning_cron.log 2>&1
30 5 * * * curl -s -X POST http://localhost:4000/api/cron/intel >> ~/intel_cron.log 2>&1
0 6 * * * curl -s -X POST http://localhost:4000/api/cron/morning >> ~/morning_cron.log 2>&1
0 20 * * 0 curl -s -X POST http://localhost:4000/api/cron/weekly >> ~/weekly_cron.log 2>&1
```

The 5:30am and 6am entries are retry fallbacks — all cron endpoints are idempotent and skip if already generated that day.

---

## The board

Seven advisors, each with a distinct voice and mental model:

| Advisor | Edge |
|---|---|
| **Elon Musk** | First principles, physics thinking, 10x not 10% |
| **Jeff Bezos** | Day 1 mentality, customer obsession, long-term compounding |
| **Warren Buffett** | Circle of competence, moats, patience |
| **Steve Jobs** | Simplicity, taste, saying no to 1000 things |
| **Charlie Munger** | Inversion, mental models, avoiding stupidity |
| **Peter Thiel** | Contrarian secrets, 0→1, escaping competition |
| **Ray Dalio** | Radical transparency, principles, macro patterns |

Smart routing picks the 2-3 most relevant for each question — you don't talk to all 7 every time.

After every session, 2-3 key insights are automatically extracted and added to your knowledge base. Future sessions read this KB, so the board gets more calibrated to your situation over time.

---

## Intelligence brief

Every morning at 5am, the system:

1. Fetches top 80 stories from Hacker News (with top comments)
2. Fetches hot posts from 11 subreddits (with top comments)
3. Selects 7 stories, one per category: **Hottest · Viral · Controversial · Breaking · AI & Tech · Startup & Business · Wildcard**
4. Runs all 7 through the LLM in parallel — each gets a long-form analysis with community sentiment, bull/bear cases, and direct implications for your specific goals
5. Generates a "Board's Verdict" — the macro pattern across all 7 stories + one specific bet + one warning
6. Saves to `briefs/YYYY-MM-DD.md`

The brief is waiting in the Intel panel when you open the dashboard. Every past brief is accessible via date picker.

---

## File structure

```
app.py                    # All Flask routes + _build_live_context()
board.py                  # Board engine — personas, routing, KB, session storage
tools/
  daily_brief.py          # HN + Reddit scraper + parallel LLM analysis
  board_session.py        # CLI wrapper for board sessions
  download_advisor_images.py
workflows/
  board_session.md        # SOP for running board sessions
  daily_brief.md          # SOP for the intel pipeline
templates/
  index.html              # Single-page dashboard
  login.html              # Login page
static/advisors/          # Advisor photos
data/                     # All JSON storage (gitignored — personal data)
sessions/                 # Board session history (gitignored)
briefs/                   # Daily intel + morning briefs (gitignored)
profile.md                # Your personal profile (gitignored)
.env                      # API keys and credentials (gitignored)
.env.example              # Template
```

---

## Why this works

Most AI productivity tools fail because they're generic. This system compounds:

- **Profile** makes every output specific to you, not generic advice
- **Knowledge base** grows with every board session — the system literally learns your situation
- **Daily logs** feed the morning brief — it knows what blocked you yesterday
- **Decision journal** creates accountability and pattern recognition over time
- **Convictions tracker** forces you to articulate your edge and stress-test it
- **All modules share context** — board knows your goals, morning brief knows the news, evening knows the morning

Six months in, the morning brief knows your blockers, your board sessions reference past decisions, the KB has hundreds of accumulated insights, and your validated convictions are built into every advisor's mental model of you. It gets harder to replace, not easier.

---

## License

MIT — build on it, fork it, make it yours.
