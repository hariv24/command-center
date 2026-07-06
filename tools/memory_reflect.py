"""
Nightly memory reflection — closes the self-improvement loop that was supposed
to make knowledge_base.md a living memory but sat at ~80 words because it only
grew from board sessions, and only from a fire-and-forget task that used to get
cancelled before it finished (see board.py's run_board_async fix).

This pulls the day's FULL delta (daily log, vitals, decisions, time/expenses —
not just board Q&A) and asks the model to propose changes to the knowledge base
as explicit operations, mem0-style: ADD new bullets, REMOVE (by substring match)
entries that are now stale/superseded. Applied deterministically in code, not
left to the model to rewrite the whole file (that's what the monthly
consolidate_knowledge_base in board.py is for).

Run nightly via cron (22:30 IST, after the evening brief):
    python3 tools/memory_reflect.py

Also triggers an incremental personal-RAG rebuild so vector search stays
current without waiting for the next board session.
"""

import asyncio
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from llm import call_llm

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
KB_PATH = DATA_DIR / "knowledge_base.md"
SESSIONS_DIR = ROOT / "sessions"


def _load(path):
    return json.loads(path.read_text()) if path.exists() else []


def _today_delta(today_iso):
    """Everything that happened today, across every module — the input a human
    would actually reflect on before writing a journal entry."""
    parts = []

    logs = _load(DATA_DIR / "daily_log.json")
    today_log = next((l for l in logs if l.get("date") == today_iso), None)
    if today_log:
        parts.append(
            f"DAILY LOG: Did: {today_log.get('did','')} | Blocked: {today_log.get('didnt','')} | "
            f"Energy: {today_log.get('energy','')} | Tomorrow: {today_log.get('tomorrow','')}"
        )

    vitals = [v for v in _load(DATA_DIR / "vitals.json") if v.get("date") == today_iso]
    if vitals:
        parts.append("WELLNESS CHECK-INS:\n" + "\n".join(f"- {v.get('user','')[:200]}" for v in vitals[:5]))

    decisions = [d for d in _load(DATA_DIR / "decisions.json") if d.get("date") == today_iso]
    if decisions:
        parts.append("DECISIONS LOGGED:\n" + "\n".join(
            f"- \"{d.get('decision','')[:150]}\" (confidence {d.get('confidence','?')}/10)" for d in decisions
        ))
    resolved = [d for d in _load(DATA_DIR / "decisions.json") if d.get("outcome_date") == today_iso]
    if resolved:
        parts.append("DECISIONS RESOLVED TODAY:\n" + "\n".join(
            f"- \"{d.get('decision','')[:120]}\" -> {d.get('outcome','')}: {d.get('lesson','')[:150]}" for d in resolved
        ))

    sessions_today = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            s = json.loads(f.read_text())
            if s.get("timestamp", "")[:10] == today_iso:
                sessions_today.append(f"- Asked board: \"{s['question'][:120]}\"")
        except Exception:
            continue
    if sessions_today:
        parts.append("BOARD SESSIONS TODAY:\n" + "\n".join(sessions_today[:5]))

    time_logs = [t for t in _load(DATA_DIR / "time_logs.json") if t.get("date") == today_iso]
    if time_logs:
        total = sum(float(t.get("hours", 0)) for t in time_logs)
        parts.append(f"TIME LOGGED TODAY: {total:.1f}h across {len(time_logs)} entries")

    return "\n\n".join(parts)


async def run_nightly_reflection():
    today_iso = date.today().isoformat()
    delta = _today_delta(today_iso)
    if not delta.strip():
        print("[memory_reflect] nothing logged today — skipping")
        return

    existing_kb = KB_PATH.read_text() if KB_PATH.exists() else "# Knowledge Base\n"

    prompt = f"""You maintain a founder's personal knowledge base — a living memory of patterns, lessons,
people, and business facts learned about him over time. It's read by an AI board of advisors before every
session, so it needs to stay current and free of stale/contradicted entries.

CURRENT KNOWLEDGE BASE:
{existing_kb[-6000:]}

TODAY'S DELTA ({today_iso}) — everything that happened today across his logs, wellness check-ins, decisions,
and board sessions:
{delta}

Decide what to ADD and what to REMOVE from the knowledge base based on today.

Return ONLY a JSON object:
{{
  "add": ["- [{today_iso}] Category: specific, reusable insight", ...],
  "remove_contains": ["a short exact substring from an EXISTING bullet above that is now stale, superseded, or contradicted by today"]
}}

Rules:
- "add": 0-3 bullets max. Only add something a person would actually want to remember weeks from now — a
  concrete pattern, a fact about a person/business, a lesson from a decision, a recurring blocker. Categories:
  Pattern, Lesson, Person, Business Fact, Conviction, Decision. Skip if today was unremarkable — empty array is fine.
- "remove_contains": only for bullets from the CURRENT KB above that today's delta makes obsolete (e.g. "waiting
  on Shakti" if Shakti just responded, a stale "3 weeks into gym" once months have passed, a resolved blocker).
  Use a short exact substring so it can be matched — don't paraphrase. Empty array if nothing is stale.
Return ONLY the JSON object, no other text."""

    try:
        raw = await call_llm([{"role": "user", "content": prompt}], tier="heavy", max_tokens=1500, temperature=0.3, timeout=120)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            print("[memory_reflect] no JSON in response, skipping")
            return
        ops = json.loads(match.group())
    except Exception as e:
        print(f"[memory_reflect] LLM call failed: {e}")
        return

    to_add = [b for b in ops.get("add", []) if isinstance(b, str) and b.strip()]
    to_remove = [s for s in ops.get("remove_contains", []) if isinstance(s, str) and s.strip()]

    if not to_add and not to_remove:
        print("[memory_reflect] no changes proposed")
        return

    lines = existing_kb.splitlines()
    if to_remove:
        kept = []
        removed_count = 0
        for line in lines:
            if any(sub in line for sub in to_remove):
                removed_count += 1
                continue
            kept.append(line)
        lines = kept
        print(f"[memory_reflect] removed {removed_count} stale line(s)")
    existing_kb = "\n".join(lines)

    if to_add:
        insert = "\n".join(to_add) + "\n"
        if "## Daily Reflections" in existing_kb:
            existing_kb = existing_kb.replace("## Daily Reflections\n", f"## Daily Reflections\n{insert}")
        else:
            existing_kb = existing_kb.rstrip() + f"\n\n## Daily Reflections\n{insert}"
        print(f"[memory_reflect] added {len(to_add)} bullet(s)")

    KB_PATH.parent.mkdir(exist_ok=True)
    KB_PATH.write_text(existing_kb.strip() + "\n")

    # Keep personal RAG current without waiting for the next board session.
    try:
        from tools.build_personal_rag import build_incremental
        build_incremental()
        print("[memory_reflect] personal RAG updated")
    except Exception as e:
        print(f"[memory_reflect] personal RAG update skipped: {e}")


PROFILE_PATH = ROOT / "profile.md"
PROFILE_PROPOSAL_PATH = DATA_DIR / "profile_proposal.json"


def _week_summary():
    """Same-week facts a person would use to update their own bio — logs, decisions,
    resolved outcomes, time allocation. Deliberately not the full wrapped-stats block
    (that's numbers for a brief); this is narrative material for rewriting prose."""
    today = date.today()
    week_start = (today - timedelta(days=7)).isoformat()
    parts = []

    logs = [l for l in _load(DATA_DIR / "daily_log.json") if l.get("date", "") >= week_start]
    if logs:
        parts.append("THIS WEEK'S LOGS:\n" + "\n".join(
            f"- [{l['date']}] Did: {l.get('did','')[:150]} | Blocked: {l.get('didnt','')[:100]}" for l in logs
        ))

    decisions = [d for d in _load(DATA_DIR / "decisions.json") if d.get("date", "") >= week_start]
    if decisions:
        parts.append("DECISIONS THIS WEEK:\n" + "\n".join(f"- {d.get('decision','')[:150]}" for d in decisions))
    resolved = [d for d in _load(DATA_DIR / "decisions.json") if (d.get("outcome_date") or "") >= week_start]
    if resolved:
        parts.append("RESOLVED THIS WEEK:\n" + "\n".join(
            f"- \"{d.get('decision','')[:120]}\" -> {d.get('outcome','')}" for d in resolved
        ))

    pipeline = _load(DATA_DIR / "pipeline.json")
    won = [p for p in pipeline if (p.get("won_date") or "") >= week_start]
    if won:
        parts.append("DEALS WON THIS WEEK:\n" + "\n".join(f"- {p.get('name','')} (₹{p.get('mrr_value',0):,.0f}/mo)" for p in won))

    return "\n\n".join(parts)


async def weekly_profile_refresh():
    """
    Sunday-only: propose a profile.md rewrite reflecting the week's reality (profile.md
    is static prose with no update path otherwise — it drifts silently: "3 weeks into
    gym" doesn't update itself). Never auto-applies — writes a proposal Telegram surfaces,
    approved/rejected via /approveprofile /rejectprofile.
    """
    if date.today().weekday() != 6:
        print("[memory_reflect] not Sunday, skipping profile refresh")
        return

    week_summary = _week_summary()
    if not week_summary.strip():
        print("[memory_reflect] no data this week, skipping profile refresh")
        return

    current_profile = PROFILE_PATH.read_text() if PROFILE_PATH.exists() else ""

    prompt = f"""This is a founder's personal profile file, read by an AI board of advisors before every session
so they know who they're talking to. It's static prose that drifts out of date (e.g. "3 weeks into gym" doesn't
update itself as months pass).

CURRENT PROFILE.MD:
{current_profile}

THIS WEEK'S ACTUAL DATA:
{week_summary}

Propose an updated version of profile.md that reflects reality — update stale specifics (time-based claims,
client counts, milestone status), but do NOT invent facts not supported by the data above, and do NOT change
the overall structure/sections or his stated goals/identity unless the data directly contradicts them.

Return ONLY a JSON object:
{{"updated_profile": "the full new profile.md content", "diff_summary": "1-2 sentences: what changed and why"}}
If nothing meaningfully changed this week, set "diff_summary" to "" and repeat the current profile unchanged."""

    try:
        raw = await call_llm([{"role": "user", "content": prompt}], tier="heavy", max_tokens=2000, temperature=0.3, timeout=120)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            print("[memory_reflect] profile refresh: no JSON in response")
            return
        result = json.loads(match.group())
    except Exception as e:
        print(f"[memory_reflect] profile refresh failed: {e}")
        return

    diff_summary = (result.get("diff_summary") or "").strip()
    updated = (result.get("updated_profile") or "").strip()
    if not diff_summary or not updated:
        print("[memory_reflect] profile refresh: no meaningful change this week")
        return

    DATA_DIR.mkdir(exist_ok=True)
    PROFILE_PROPOSAL_PATH.write_text(json.dumps({
        "date": date.today().isoformat(),
        "updated_profile": updated,
        "diff_summary": diff_summary,
    }, indent=2))
    print(f"[memory_reflect] profile proposal written: {diff_summary}")

    try:
        from tools.telegram_bot import send
        send(f"*Profile update proposed:*\n{diff_summary}\n\nReply /approveprofile to apply, /rejectprofile to dismiss.")
    except Exception as e:
        print(f"[memory_reflect] telegram push skipped: {e}")


if __name__ == "__main__":
    asyncio.run(run_nightly_reflection())
    asyncio.run(weekly_profile_refresh())
