"""
Daily Intelligence Brief — 6-7 stories across different angles.
Auto-runs at 5am IST via cron. Covers: breaking, hottest, viral, controversial, startup, AI, wildcard.
"""

import asyncio
import os
import re
import sys
import json
import html
import urllib.request
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from llm import call_llm

load_dotenv(Path(__file__).parent.parent / ".env")


def _fix_markdown_headers(text):
    """
    Deterministic cleanup, not left to LLM compliance: CommonMark requires a
    space after '#'/'##' for it to render as a header at all — without it,
    browsers show the literal '##text' instead of an <h2>. The model
    occasionally drops the space (especially right after inserting a bracketed
    tag like '##[HOTTEST]'), so we enforce it here rather than hoping every
    generation gets the template exactly right.
    """
    # Capture the hash run together with the first following char, requiring
    # that char to be neither '#' nor whitespace — this is what prevents the
    # regex from backtracking into an already-correctly-spaced header (e.g.
    # matching just one '#' of "## text" and inserting a bogus extra space).
    return re.sub(r"^(#+)([^#\s])", r"\1 \2", text, flags=re.MULTILINE)

PROFILE_PATH = Path(__file__).parent.parent / "profile.md"

# Topic filters for relevance scoring
AI_TECH_TOPICS = [
    "AI agents", "LLM", "GPT", "Claude", "Gemini", "OpenAI", "Anthropic",
    "MCP", "Claude Code", "reasoning model", "o3", "Llama", "open source AI",
    "AI coding", "Codex", "Cursor", "vibe coding", "agentic", "context engineering",
    "agent framework", "software 3.0", "AI productivity", "AI replacing",
]
STARTUP_BIZ_TOPICS = [
    "startup", "founder", "SaaS", "vertical SaaS", "automation", "n8n", "no-code",
    "ERP", "manufacturing AI", "build vs buy", "workflow automation",
    "funding", "VC", "venture", "bootstrapped", "revenue", "MRR", "ARR",
    "India tech", "SaaS killer", "product market fit", "B2B",
]
ALL_TOPICS = AI_TECH_TOPICS + STARTUP_BIZ_TOPICS

REDDIT_SUBS = [
    "MachineLearning", "LocalLLaMA", "artificial",
    "SaaS", "startups", "ChatGPT", "ClaudeAI", "singularity",
    "programming", "technology", "Entrepreneur",
]

CATEGORIES = {
    "HOTTEST": "Most upvoted story of the day — the one everyone is talking about",
    "VIRAL": "Most commented — generating the most debate and discussion",
    "MOST CONTROVERSIAL": "Highest comment-to-upvote ratio — people strongly disagree on this",
    "BREAKING": "Newest story — just dropped, not yet widely discussed",
    "AI & TECH": "The most impactful AI or technology development right now",
    "STARTUP & BUSINESS": "Most relevant to founders, SaaS, and building businesses",
    "WILDCARD": "Something unexpected — outside AI/startup, but worth knowing",
}


def fetch_url(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 DailyBrief/1.0"
    })
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode())


def fetch_hn_story_with_comments(story_id, max_comments=8):
    try:
        item = fetch_url(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        if not item or item.get("type") != "story" or not item.get("title"):
            return None
        story = {
            "id": story_id,
            "title": item.get("title", ""),
            "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
            "score": item.get("score", 0),
            "comment_count": item.get("descendants", 0),
            "time": item.get("time", 0),
            "source": "Hacker News",
            "selftext": "",
            "comments": []
        }
        for kid_id in item.get("kids", [])[:max_comments]:
            try:
                comment = fetch_url(f"https://hacker-news.firebaseio.com/v0/item/{kid_id}.json")
                if comment and comment.get("text") and not comment.get("deleted") and not comment.get("dead"):
                    text = html.unescape(comment["text"])
                    text = text.replace("<p>", "\n").replace("</p>", "")
                    for tag in ["<i>", "</i>", "<b>", "</b>", "<code>", "</code>", "<pre>", "</pre>", "<a href=\"", "\">", "</a>"]:
                        text = text.replace(tag, "")
                    story["comments"].append(text[:500].strip())
            except Exception:
                continue
        return story
    except Exception:
        return None


def fetch_hn_stories(limit=80):
    try:
        top_ids = fetch_url("https://hacker-news.firebaseio.com/v0/topstories.json")[:limit]
        new_ids = fetch_url("https://hacker-news.firebaseio.com/v0/newstories.json")[:20]
        all_ids = list(dict.fromkeys(top_ids + new_ids))
        stories = []
        for story_id in all_ids:
            story = fetch_hn_story_with_comments(story_id, max_comments=6)
            if story:
                stories.append(story)
        return stories
    except Exception as e:
        print(f"  HN fetch failed: {e}")
        return []


def fetch_reddit_posts_with_comments(subreddit, limit=10):
    try:
        data = fetch_url(
            f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}",
            headers={"User-Agent": "DailyBrief:1.0 (personal research tool)"}
        )
        posts = []
        for post in data.get("data", {}).get("children", []):
            p = post.get("data", {})
            if p.get("score", 0) < 30:
                continue
            post_entry = {
                "title": p.get("title", ""),
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "score": p.get("score", 0),
                "comment_count": p.get("num_comments", 0),
                "time": p.get("created_utc", 0),
                "source": f"r/{subreddit}",
                "selftext": p.get("selftext", "")[:600],
                "comments": []
            }
            try:
                permalink = p.get("permalink", "")
                comments_data = fetch_url(
                    f"https://www.reddit.com{permalink}.json?limit=5&sort=top",
                    headers={"User-Agent": "DailyBrief:1.0 (personal research tool)"}
                )
                if len(comments_data) > 1:
                    comment_listing = comments_data[1].get("data", {}).get("children", [])
                    for c in comment_listing[:5]:
                        body = c.get("data", {}).get("body", "")
                        if body and len(body) > 30:
                            post_entry["comments"].append(body[:500].strip())
            except Exception:
                pass
            posts.append(post_entry)
        return posts
    except Exception as e:
        print(f"  r/{subreddit} failed: {e}")
        return []


def is_ai_tech(title, text=""):
    combined = (title + " " + text).lower()
    return any(t.lower() in combined for t in AI_TECH_TOPICS)


def is_startup_biz(title, text=""):
    combined = (title + " " + text).lower()
    return any(t.lower() in combined for t in STARTUP_BIZ_TOPICS)


def is_relevant(title, text=""):
    return is_ai_tech(title, text) or is_startup_biz(title, text)


def build_goal_keywords(goals, pipeline=None):
    """
    Words worth boosting in story selection because they're what Hariv is actually
    working toward right now — not just generic AI/startup topic matches.
    """
    words = set()
    for g in goals or []:
        if g.get("status") != "active":
            continue
        for field in (g.get("title", ""), g.get("why", ""), g.get("category", "")):
            for w in re.split(r"\W+", field.lower()):
                if len(w) > 3:
                    words.add(w)
    for p in pipeline or []:
        for field in (p.get("name", ""), p.get("source", "")):
            for w in re.split(r"\W+", field.lower()):
                if len(w) > 3:
                    words.add(w)
    # Generic filler words that end up long enough to pass the len>3 filter but
    # carry no real signal — drop them so they don't falsely "match" every story.
    words -= {"active", "target", "before", "every", "which", "there", "month", "year", "with"}
    return words


def is_goal_match(title, text, goal_keywords):
    if not goal_keywords:
        return False
    combined = (title + " " + text).lower()
    return any(w in combined for w in goal_keywords)


def select_diverse_stories(all_stories, goal_keywords=None):
    """
    Pick 7 stories across different angles: hottest, viral, controversial,
    breaking, AI/tech, startup/biz, wildcard.
    Each slot gets a distinct story — no duplicates.
    Falls back to any unseen story if the preferred pool is exhausted.
    AI & TECH and STARTUP & BUSINESS are additionally boosted toward stories
    that match Hariv's active goals/pipeline, not just generic topic keywords —
    so intel actually tracks what he's working on, not just what's trending.
    """
    relevant = [s for s in all_stories if is_relevant(s["title"], s.get("selftext", ""))]
    irrelevant = [s for s in all_stories if not is_relevant(s["title"], s.get("selftext", ""))]
    all_by_score = sorted(all_stories, key=lambda x: x["score"], reverse=True)

    seen_ids = set()
    selected = {}
    goal_matched_keys = set()

    def pick(pool, key, fallback=None):
        """Pick first unseen story from pool, falling back to fallback pool, then any story."""
        for p in [pool, fallback or [], all_by_score]:
            for s in p:
                sid = s.get("id") or s["title"][:50]
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    selected[key] = s
                    return s
        return None

    # HOTTEST — highest score (relevant preferred, any as fallback)
    by_score = sorted(relevant, key=lambda x: x["score"], reverse=True)
    pick(by_score, "HOTTEST", fallback=all_by_score)

    # VIRAL — highest comment count
    by_comments = sorted(relevant, key=lambda x: x["comment_count"], reverse=True)
    pick(by_comments, "VIRAL", fallback=sorted(all_stories, key=lambda x: x["comment_count"], reverse=True))

    # MOST CONTROVERSIAL — high comments relative to score
    controversial = sorted(relevant, key=lambda x: x["comment_count"] / (x["score"] + 1), reverse=True)
    pick(controversial, "MOST CONTROVERSIAL", fallback=sorted(all_stories, key=lambda x: x["comment_count"] / (x["score"] + 1), reverse=True))

    # BREAKING — newest timestamp
    by_time = sorted(all_stories, key=lambda x: x.get("time", 0), reverse=True)
    breaking_pool = [s for s in by_time if is_relevant(s["title"], s.get("selftext", ""))]
    pick(breaking_pool or by_time, "BREAKING", fallback=by_time)

    # AI & TECH — top scored AI/tech story, goal-matching stories boosted to the front
    ai_pool = sorted(
        [s for s in all_stories if is_ai_tech(s["title"], s.get("selftext", ""))],
        key=lambda x: (is_goal_match(x["title"], x.get("selftext", ""), goal_keywords), x["score"]),
        reverse=True,
    )
    picked = pick(ai_pool, "AI & TECH", fallback=all_by_score)
    if picked and is_goal_match(picked["title"], picked.get("selftext", ""), goal_keywords):
        goal_matched_keys.add("AI & TECH")

    # STARTUP & BUSINESS — top scored startup/biz story, same goal boost
    biz_pool = sorted(
        [s for s in all_stories if is_startup_biz(s["title"], s.get("selftext", ""))],
        key=lambda x: (is_goal_match(x["title"], x.get("selftext", ""), goal_keywords), x["score"]),
        reverse=True,
    )
    picked = pick(biz_pool, "STARTUP & BUSINESS", fallback=all_by_score)
    if picked and is_goal_match(picked["title"], picked.get("selftext", ""), goal_keywords):
        goal_matched_keys.add("STARTUP & BUSINESS")

    # WILDCARD — outside AI/startup world
    wild_pool = sorted(irrelevant, key=lambda x: x["score"], reverse=True)
    pick(wild_pool, "WILDCARD", fallback=all_by_score)

    return selected, goal_matched_keys  # dict: category -> story, set of categories picked for goal relevance


def format_story_for_prompt(story, category, desc):
    lines = [
        f"### [{category}] — {desc}",
        f"HEADLINE: {story['title']}",
        f"Source: {story['source']} | Upvotes: {story['score']} | Comments: {story['comment_count']}",
        f"URL: {story['url']}"
    ]
    if story.get("selftext"):
        lines.append(f"Post body: {story['selftext'][:400]}")
    if story.get("comments"):
        lines.append(f"\nTop community comments ({len(story['comments'])} shown):")
        for i, c in enumerate(story["comments"][:6], 1):
            lines.append(f"  [{i}] {c[:350]}")
    return "\n".join(lines)


async def analyze_story(story, category, category_desc, story_idx, profile="", goal_match=False):
    story_text = format_story_for_prompt(story, category, category_desc)
    # Heavy tier, 1M context window, per-request billing — no reason to truncate
    # the profile to a sliver when the whole thing costs nothing extra to include.
    profile_ctx = profile if profile else "An ambitious founder building toward a big goal."
    goal_note = (
        "\nThis story was surfaced specifically because it matches one of the founder's active goals "
        "or open pipeline deals — make the 'Direct implications' section concrete about which goal/deal "
        "it touches and what to do about it, not generic.\n" if goal_match else ""
    )

    prompt = f"""You are briefing a founder with this profile:
{profile_ctx}
{goal_note}
TODAY'S STORY — CATEGORY: {category} ({category_desc})

{story_text}

Write a long-form intelligence brief section. Structure:

## [{category}] {story['title']}

**What's happening and why it matters:**
[2-3 paragraphs. Explain the story fully. Who's involved. What changed. What led to this. Why now. Give context — don't assume the reader knows anything.]

**What the community is actually saying:**
[Summarize the real debate in the comments. Quote or paraphrase 2-3 specific viewpoints. What are people arguing about? What's the majority view? What's the contrarian take? What are people afraid of or excited about?]

**Arguments FOR this development:**
[3-4 genuine arguments from believers]

**Arguments AGAINST / Skeptics say:**
[3-4 genuine counterarguments and concerns]

**What happens next — realistic futures:**
[2 paragraphs: likely 6-12 month progression. Bull case. Bear case. Signals to watch.]

**Direct implications for you:**
[Be personal and specific based on the founder profile above. Opportunity or threat? One specific action to consider.]

Make this comprehensive. A reader should fully understand this story and its implications from this section alone. Do not summarize. Explain."""

    # This runs in a background thread nobody's watching live — favor letting a
    # slow-but-successful large generation finish over failing fast (unlike the
    # interactive board, which needs the tighter default timeout).
    return await call_llm([{"role": "user", "content": prompt}], tier="heavy", max_tokens=4000, temperature=0.7, timeout=300)


async def generate_board_verdict(story_map, profile):
    stories_summary = "\n".join(
        f"- [{cat}] {s['title']} ({s['source']}, {s['score']} pts)"
        for cat, s in story_map.items()
    )
    prompt = f"""Founder profile: {profile}

Today's 7 intelligence stories they just read:
{stories_summary}

Write a "Board's Verdict" — as if Elon Musk, Jeff Bezos, Warren Buffett, Steve Jobs, Charlie Munger, Peter Thiel, and Ray Dalio just read all of this together and are giving the founder their collective read:

**The Pattern:** [2 paragraphs — what's the macro pattern across these 7 stories? What does it mean for the next 12 months? What is the world moving toward?]

**The Bet:** [1 paragraph — given the founder's current context, what is the ONE strategic shift or move these 7 would unanimously recommend right now? Be specific. No filler.]

**The Warning:** [1 paragraph — what is the one thing the founder is probably not paying attention to that could derail their path? What should they be afraid of that they're probably not?]

Be direct. Be specific. Write as if their future depends on this."""

    return await call_llm([{"role": "user", "content": prompt}], tier="heavy", max_tokens=2400, temperature=0.7, timeout=300)


async def run_daily_brief_async():
    if not any(os.getenv(v) for v in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY")):
        raise ValueError("No LLM API key set (OPENROUTER_API_KEY / GROQ_API_KEY) in .env")

    profile = PROFILE_PATH.read_text() if PROFILE_PATH.exists() else ""

    # Load active goals + open pipeline to make intel analysis goal-aware
    goals_file = Path(__file__).parent.parent / "data" / "goals.json"
    pipeline_file = Path(__file__).parent.parent / "data" / "pipeline.json"
    goals, pipeline = [], []
    goals_ctx = ""
    if goals_file.exists():
        try:
            goals = json.loads(goals_file.read_text())
            active = [g for g in goals if g.get("status") == "active"]
            if active:
                goals_ctx = "Active goals:\n" + "\n".join(
                    f"- {g['title']}: target {g.get('target','')} by {g.get('deadline','')}"
                    for g in active[:6]
                )
        except Exception:
            pass
    if pipeline_file.exists():
        try:
            pipeline = json.loads(pipeline_file.read_text())
        except Exception:
            pass
    goal_keywords = build_goal_keywords(goals, pipeline)

    print("  Fetching HN stories + comments...")
    hn_stories = fetch_hn_stories(80)

    print("  Fetching Reddit posts + comments...")
    reddit_stories = []
    for sub in REDDIT_SUBS:
        reddit_stories.extend(fetch_reddit_posts_with_comments(sub, 8))

    all_stories = hn_stories + reddit_stories

    # Deduplicate by title
    seen_titles = set()
    deduped = []
    for s in all_stories:
        t = s["title"].lower()[:60]
        if t not in seen_titles:
            seen_titles.add(t)
            deduped.append(s)

    print(f"  Selecting 7 stories across categories from {len(deduped)} total...")
    story_map, goal_matched_keys = select_diverse_stories(deduped, goal_keywords=goal_keywords)

    if not story_map:
        return "No stories found today. Sources may be down. Try again in a few hours."

    if goal_matched_keys:
        print(f"  Goal-matched categories: {', '.join(goal_matched_keys)}")

    print(f"  Analyzing {len(story_map)} stories one at a time...")

    full_profile = (profile + ("\n\n" + goals_ctx if goals_ctx else "")).strip()

    # Sequential, not asyncio.gather(*analysis_tasks) — 7 concurrent heavy-tier
    # calls (plus each one's own fallback-model retries) could burst well past
    # OpenRouter's 20-requests/minute cap, and gather() without
    # return_exceptions=True meant a single failed story took down the ENTIRE
    # brief (no fallback text, no partial output — the whole cron run just
    # failed). Now a failed story is skipped with a visible placeholder
    # instead of silently killing every other story's analysis too.
    analyses = []
    for idx, (cat, story) in enumerate(story_map.items()):
        try:
            analysis = await analyze_story(story, cat, CATEGORIES[cat], idx + 1, full_profile, goal_match=cat in goal_matched_keys)
        except Exception as e:
            print(f"  [{cat}] analysis failed, skipping: {e}")
            analysis = f"## [{cat}] {story['title']}\n\n*Analysis failed to generate for this story — source data is still linked above.*\n\nURL: {story['url']}"
        analyses.append(analysis)

    try:
        board_verdict = await generate_board_verdict(story_map, full_profile)
    except Exception as e:
        print(f"  Board's Verdict failed, skipping: {e}")
        board_verdict = "*Board's Verdict failed to generate today.*"

    date_str = datetime.now().strftime("%A, %B %d %Y")
    sections = [
        f"# Daily Intelligence Brief — {date_str}",
        f"*{len(story_map)} stories across {', '.join(story_map.keys())}*",
        "",
        "---",
        "",
    ]

    for (cat, story), analysis in zip(story_map.items(), analyses):
        sections.append(analysis)
        sections.append("\n---\n")

    sections.extend([
        "## Board's Verdict",
        board_verdict
    ])

    full_brief = _fix_markdown_headers("\n\n".join(sections))

    briefs_dir = Path(__file__).parent.parent / "briefs"
    briefs_dir.mkdir(exist_ok=True)
    date_str_file = datetime.now().strftime('%Y-%m-%d')
    brief_file = briefs_dir / f"{date_str_file}.md"
    brief_file.write_text(full_brief)
    print(f"  Saved to {brief_file}")

    return full_brief


async def _run_cli():
    brief = await run_daily_brief_async()
    print(f"\n{'='*60}")
    print(f"  DAILY BRIEF — {datetime.now().strftime('%A, %B %d %Y')}")
    print(f"{'='*60}\n")
    print(brief)
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(_run_cli())
