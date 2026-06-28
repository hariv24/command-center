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
3. **You log your day** → last 7 days feed tomorrow's morning brief
4. **Tomorrow morning** → brief is already generated, waiting, built from everything above

The KB is the brain. It compounds.

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
- **Cron**: Two entries — 5:00am intel brief, 5:30am morning brief (both idempotent)

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

Create `profile.md` — this is what makes everything personal. The board reads it, the morning brief reads it, the intel brief reads it.

```markdown
# My Profile

I'm a [your background]. Currently building [your projects].
Goal: [your north star]. Timeline: [when].

## What I'm working on
- [Project 1]
- [Project 2]

## My constraints
- [Time / money / team constraints]

## What I care about
- [Values, priorities]
```

The more honest and specific this is, the better every output gets.

### 4. Set your north stars

In `app.py`, find `NORTH_STAR` and update it with your own 3 milestone targets — anything you want to count down to:

```python
NORTH_STAR = {
    "milestone_1": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this milestone means."},
    "milestone_2": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this milestone means."},
    "milestone_3": {"label": "Your Label", "target": "YYYY-MM-DD", "age": 0, "what": "What this milestone means."},
}
```

These show as countdown strips on your home dashboard. Set them to whatever matters to you.

### 5. Download advisor photos

```bash
python3 tools/download_advisor_images.py
```

### 6. Run

```bash
python3 app.py
```

Open `http://localhost:4000`. Log in with the credentials from your `.env`.

---

## Deploy to a server (optional)

The system is designed to run 24/7 on a cheap Linux VM (Oracle Free Tier works great).

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

Add (adjust timezone offset to match your local 5am):

```
0 5 * * * curl -s -X POST http://localhost:4000/api/cron/intel >> ~/intel_cron.log 2>&1
30 5 * * * curl -s -X POST http://localhost:4000/api/cron/morning >> ~/morning_cron.log 2>&1
0 6 * * * curl -s -X POST http://localhost:4000/api/cron/intel >> ~/intel_cron.log 2>&1
0 6 * * * curl -s -X POST http://localhost:4000/api/cron/morning >> ~/morning_cron.log 2>&1
```

The 6am entries are retry fallbacks — both endpoints are idempotent and skip if already generated.

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
4. Runs all 7 through the LLM in parallel — each gets a long-form analysis with community sentiment, bull/bear cases, and direct implications for you
5. Generates a "Board's Verdict" — the macro pattern across all 7 stories + one specific bet + one warning
6. Saves to `briefs/YYYY-MM-DD.md`

The brief is waiting in the Intel panel when you open the dashboard. Every past brief is accessible via date picker.

---

## File structure

```
app.py                    # All Flask routes
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

Most AI productivity tools fail because they're generic. This system is designed to compound:

- **Profile** makes every output specific to you, not generic advice
- **Knowledge base** grows with every board session — the system literally learns your situation
- **Daily logs** feed the morning brief — it knows what blocked you yesterday
- **Decision journal** creates accountability and pattern recognition over time
- **Convictions tracker** forces you to articulate your edge and stress-test it

Six months in, the morning brief knows your blockers, your board sessions reference past decisions, and the KB has hundreds of accumulated insights. It gets harder to replace, not easier.

---

## License

MIT — build on it, fork it, make it yours.
