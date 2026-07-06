"""
Anticipation engine — the system reaches out before being asked, instead of only
answering when Hariv opens the dashboard or messages the bot. Every signal below
is computed deterministically (gap_engine style — no LLM, no hallucination risk);
one fast-tier call turns whichever signals actually fired into 2-3 sharp lines.
Zero signals fires nothing — silence is a feature, not a bug (a daily message with
nothing to say trains you to ignore it).

Run via cron (08:45 IST, before the 09:00 recommendation push):
    python3 tools/anticipate.py
"""

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from llm import call_llm
import gap_engine

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"


def _load(path):
    return json.loads(path.read_text()) if path.exists() else []


def _blocker_streak_signal(logs, today):
    """Same blocker text repeated 2+ days running."""
    recent = sorted([l for l in logs if l.get("date", "") >= (today - timedelta(days=10)).isoformat()],
                     key=lambda x: x["date"], reverse=True)
    if not recent:
        return None
    last_blocker = recent[0].get("didnt", "")
    if not last_blocker:
        return None
    streak = 0
    for l in recent[1:]:
        if last_blocker[:25].lower() in l.get("didnt", "").lower():
            streak += 1
        else:
            break
    if streak >= 1:
        return f"Same blocker {streak + 1} days running: \"{last_blocker[:80]}\""
    return None


def _energy_decline_signal(logs, today):
    """Energy dropped every day for the last 3 logged days."""
    recent = sorted([l for l in logs if l.get("energy") not in (None, "")], key=lambda x: x["date"])[-3:]
    if len(recent) < 3:
        return None
    vals = [float(l["energy"]) for l in recent]
    if vals[0] > vals[1] > vals[2]:
        return f"Energy declining 3 days straight: {vals[0]:.0f} -> {vals[1]:.0f} -> {vals[2]:.0f}"
    return None


def _momentum_drop_signal(time_logs, today):
    """This week's revenue-work momentum is less than half of last week's."""
    week_start = today - timedelta(days=today.weekday())
    prev_week_start = week_start - timedelta(days=7)

    def momentum(entries):
        total = sum(float(e.get("hours", 0)) for e in entries)
        rev = sum(float(e.get("hours", 0)) for e in entries if e.get("category") in ("Agency Work", "TENANTZA SaaS"))
        return (rev / total * 100) if total > 0 else None

    this_week = [t for t in time_logs if t.get("date", "") >= week_start.isoformat()]
    last_week = [t for t in time_logs if prev_week_start.isoformat() <= t.get("date", "") < week_start.isoformat()]
    m_this, m_last = momentum(this_week), momentum(last_week)
    if m_this is not None and m_last is not None and m_last > 0 and m_this < m_last / 2:
        return f"Momentum dropped: {m_this:.0f}% revenue work this week vs {m_last:.0f}% last week"
    return None


def _silent_pipeline_signal(pipeline):
    """Open deals with no future next_action_date — sitting untouched."""
    today_iso = date.today().isoformat()
    open_stages = ("contacted", "demo", "proposal")
    silent = [
        p for p in pipeline
        if p.get("stage") in open_stages and not (p.get("next_action_date") or "") > today_iso
    ]
    if silent:
        names = ", ".join(p.get("name", "?") for p in silent[:3])
        return f"{len(silent)} open pipeline deal(s) with no next action scheduled: {names}"
    return None


def _overdue_crm_signal(crm):
    today_iso = date.today().isoformat()
    overdue = [c for c in crm if c.get("next_action") and (c.get("next_action_date") or "9999") < today_iso]
    if overdue:
        names = ", ".join(c.get("name", "?") for c in overdue[:3])
        return f"{len(overdue)} overdue follow-up(s): {names}"
    return None


def _streak_broke_signal(logs, today):
    """Was logging consistently, then stopped in the last 1-2 days."""
    has_log = {l.get("date") for l in logs}
    today_iso = today.isoformat()
    yesterday_iso = (today - timedelta(days=1)).isoformat()
    day_before_iso = (today - timedelta(days=2)).isoformat()
    three_days_ago_iso = (today - timedelta(days=3)).isoformat()
    if today_iso not in has_log and yesterday_iso not in has_log and (
        day_before_iso in has_log or three_days_ago_iso in has_log
    ):
        return "Log streak just broke — no entry in the last 2 days after logging consistently"
    return None


def _new_broken_commitment_signal(recs):
    """A commitment crossed its deadline in the last day (not already stale/known)."""
    scoreboard = gap_engine.compute_commitment_scoreboard(recs)
    today_iso = date.today().isoformat()
    yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
    fresh = [
        r for r in scoreboard["broken_items"]
        if (r.get("deadline") or r.get("date") or "") in (today_iso, yesterday_iso)
    ]
    if fresh:
        return f"Commitment just crossed its deadline: \"{fresh[0].get('recommendation','')[:100]}\""
    return None


def collect_signals():
    today = date.today()
    logs = _load(DATA_DIR / "daily_log.json")
    time_logs = _load(DATA_DIR / "time_logs.json")
    pipeline = _load(DATA_DIR / "pipeline.json")
    crm = _load(DATA_DIR / "crm.json")
    recs = _load(DATA_DIR / "recommendations.json")

    checks = [
        _blocker_streak_signal(logs, today),
        _energy_decline_signal(logs, today),
        _momentum_drop_signal(time_logs, today),
        _silent_pipeline_signal(pipeline),
        _overdue_crm_signal(crm),
        _streak_broke_signal(logs, today),
        _new_broken_commitment_signal(recs),
    ]
    return [c for c in checks if c]


async def run_anticipation():
    signals = collect_signals()
    if not signals:
        print("[anticipate] no signals fired — staying silent")
        return

    prompt = f"""These deterministic signals fired for a founder's morning check today, from his own logged
data (not guesses):

{chr(10).join(f'- {s}' for s in signals)}

Write 2-3 sharp lines for a Telegram message calling these out directly — the way a blunt co-founder would,
not a corporate dashboard. No greeting, no sign-off, no "Hey!" — get straight to it. Reference the specifics
given, don't generalize."""

    try:
        message = await call_llm([{"role": "user", "content": prompt}], tier="fast", max_tokens=250, temperature=0.6, timeout=60)
        message = message.strip()
    except Exception as e:
        print(f"[anticipate] LLM formatting failed, sending raw signals: {e}")
        message = "\n".join(f"- {s}" for s in signals)

    try:
        from tools.telegram_bot import send
        send(f"*Before you start today:*\n{message}")
        print(f"[anticipate] sent {len(signals)} signal(s)")
    except Exception as e:
        print(f"[anticipate] telegram push failed: {e}")


if __name__ == "__main__":
    asyncio.run(run_anticipation())
