# Daily Brief Workflow

## Objective
Every morning, pull the top relevant stories from Hacker News and Reddit on Hariv's tracked topics. Summarize them with context, arguments, and what they mean specifically for his businesses and goals.

## Topics Tracked
- AI agents, LLMs, new model releases (Claude, GPT, Gemini, etc.)
- LLM replacing developers / SaaS disruption
- Vertical SaaS news
- Automation tools (n8n, no-code, agentic)
- Startup/founder stories
- India tech scene

## Sources
- Hacker News (top 40 stories)
- r/MachineLearning, r/LocalLLaMA, r/artificial, r/SaaS, r/startups, r/ChatGPT, r/ClaudeAI

## How to Run
```bash
cd /Users/harivkannan/Desktop/DOLLA/agent
python tools/daily_brief.py
```

## Output Structure
For the top 5 relevant stories:
- What happened
- The argument (what people are debating)
- What this means for Hariv specifically
- Signal strength: 🔥 Must know / ⚡ Worth knowing / 📌 Keep on radar

Then: Board's Take — what the 7 advisors would tell Hariv based on today's news.

Brief is also saved to `.tmp/brief_YYYY-MM-DD.md`

## When to Run
Every morning before starting work. Takes ~60 seconds.

## Setup (First Time)
```bash
pip install anthropic python-dotenv
# Add your ANTHROPIC_API_KEY to agent/.env
```

## Adding More Topics
Edit the `TOPICS` list in `tools/daily_brief.py`.
Edit `REDDIT_SUBS` to add more subreddits.

## Automating (Optional)
To run automatically every morning, add to crontab:
```
0 7 * * * cd /Users/harivkannan/Desktop/DOLLA/agent && python tools/daily_brief.py
```
