# Daily Brief Workflow

## Objective
Every morning, pull 7 stories across different angles from Hacker News and Reddit, analyze each in depth, and close with a "Board's Verdict" — the 7 advisors' collective read on what the day's news means for Hariv's goals. Selection is goal-aware: the AI & TECH and STARTUP & BUSINESS slots are boosted toward stories that match his active goals or open pipeline deals, not just generic topic keywords.

## Categories (one story each, 7 total)
HOTTEST, VIRAL, MOST CONTROVERSIAL, BREAKING, AI & TECH, STARTUP & BUSINESS, WILDCARD — see `CATEGORIES` in `tools/daily_brief.py`.

## Sources
- Hacker News (top 80 + newest 20 stories, up to 6 comments each)
- Reddit (11 subreddits — see `REDDIT_SUBS` in `tools/daily_brief.py`): MachineLearning, LocalLLaMA, artificial, SaaS, startups, ChatGPT, ClaudeAI, singularity, programming, technology, Entrepreneur

## How to Run
```bash
cd /Users/harivkannan/Desktop/DOLLA/agent
python tools/daily_brief.py
```
In production this runs automatically at 5:00am IST via `POST /api/cron/intel` (see `deploy/setup_server.sh`), then gets pushed to Telegram at 6:00am.

## Output Structure
For each of the 7 stories:
- What's happening and why it matters
- What the community is actually saying
- Arguments for / against
- What happens next — bull case, bear case, signals to watch
- Direct implications for Hariv (explicitly tied to the matched goal/deal when the story was goal-boosted)

Then: **Board's Verdict** — the Pattern, the Bet, the Warning, as if all 7 advisors read the day's stories together.

Brief is saved to `briefs/YYYY-MM-DD.md`.

## When to Run
Automatic at 5am IST. Manual runs are idempotent-safe but will regenerate and overwrite today's file.

## Setup (First Time)
Uses the same provider chain as the rest of the app — no separate setup needed. Requires `OPENROUTER_API_KEY` (and/or `GROQ_API_KEY`, `TOGETHER_API_KEY`) in `agent/.env`. See `llm.py`.

## Adding More Topics
Edit `AI_TECH_TOPICS` / `STARTUP_BIZ_TOPICS` in `tools/daily_brief.py` for the keyword-based relevance filter. Goal-driven boosting (`build_goal_keywords`) pulls automatically from `data/goals.json` and `data/pipeline.json` — no manual edit needed as goals/pipeline change.
Edit `REDDIT_SUBS` to add more subreddits.

## Automating
Already wired via `deploy/setup_server.sh`'s cron block (`0 5 * * * curl -s -X POST http://localhost:4000/api/cron/intel`). The endpoint is idempotent — skips if today's brief already exists.
