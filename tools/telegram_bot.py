"""
Telegram bot — the push half of the accountability loop. Long-polling (no
webhook/TLS needed), single-user (hard-rejects any other chat ID once
TELEGRAM_CHAT_ID is set). Imports board.py functions directly — no HTTP
round-trip to the Flask app for the board/quick-answer paths.

Run as its own process (systemd service `ccbot`, see deploy/setup_server.sh):
    python3 tools/telegram_bot.py

First run: message the bot anything, it captures your chat ID and tells you
to save it to .env as TELEGRAM_CHAT_ID, then restart.

Commands:
  /log      — guided daily log (did -> blocked -> tomorrow -> energy)
  /board Q  — full board session, one message per advisor as they finish
  /brief    — today's morning brief (from cache, or triggers generation)
  /recs     — pending recommendations with a done-marking reply
  (voice)   — transcribed via Groq Whisper, then routed like text
  (text)    — fast-tier classified as a question (-> quick board) or a log entry
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import requests
from llm import call_llm
import board as board_mod
import gap_engine

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # set after first message capture
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

DATA_DIR = Path(__file__).parent.parent / "data"
DAILY_LOG_FILE = DATA_DIR / "daily_log.json"
TIME_FILE = DATA_DIR / "time_logs.json"
EXPENSES_FILE = DATA_DIR / "expenses.json"
PIPELINE_FILE = DATA_DIR / "pipeline.json"
GOALS_FILE = DATA_DIR / "goals.json"
DECISIONS_FILE = DATA_DIR / "decisions.json"
RECS_FILE = DATA_DIR / "recommendations.json"
BRIEFS_DIR = Path(__file__).parent.parent / "briefs"
MORNING_CACHE_FILE = DATA_DIR / "morning_cache.json"
EVENING_CACHE_FILE = DATA_DIR / "evening_cache.json"

# In-memory /log flow state (single user, so a module-level dict is fine)
_log_flow = {}


def _load(path):
    return json.loads(path.read_text()) if path.exists() else []


def _save(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def send(text, chat_id=None, reply_markup=None):
    chat_id = chat_id or CHAT_ID
    if not chat_id:
        print(f"[telegram_bot] no CHAT_ID set yet, would have sent: {text[:80]}")
        return
    payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        print(f"[telegram_bot] send failed: {e}")


def _is_authorized(chat_id):
    if not CHAT_ID:
        return True  # not yet configured — first message captures it
    return str(chat_id) == str(CHAT_ID)


async def _classify_text(text):
    """Fast-tier: is this a question for the board, or a log-style statement?"""
    prompt = f"""Classify this message from a founder using his personal command center bot:
"{text}"

Is this (a) a QUESTION seeking advice/analysis, or (b) a LOG statement about what he did/felt/spent?
Reply with exactly one word: QUESTION or LOG"""
    try:
        result = await call_llm([{"role": "user", "content": prompt}], tier="fast", max_tokens=10, temperature=0)
        return "question" if "QUESTION" in result.upper() else "log"
    except Exception:
        return "question"


def _transcribe_voice(file_id):
    """Download a Telegram voice note and transcribe it via Groq Whisper."""
    r = requests.get(f"{API}/getFile", params={"file_id": file_id}, timeout=15).json()
    file_path = r["result"]["file_path"]
    audio = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30).content

    groq_key = os.getenv("GROQ_API_KEY")
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {groq_key}"},
        files={"file": ("voice.ogg", audio, "audio/ogg")},
        data={"model": "whisper-large-v3"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


async def _handle_log_flow(chat_id, text, then_evening=False):
    state = _log_flow.get(chat_id)
    if text in ("/log", "/evening") or not state:
        _log_flow[chat_id] = {"step": "did", "did": "", "didnt": "", "tomorrow": "", "energy": None,
                               "then_evening": then_evening}
        send("What did you get done today?", chat_id)
        return True
    step = state["step"]
    if step == "did":
        state["did"] = text
        state["step"] = "didnt"
        send("What blocked you or didn't get done?", chat_id)
    elif step == "didnt":
        state["didnt"] = text
        state["step"] = "tomorrow"
        send("What's the focus for tomorrow?", chat_id)
    elif step == "tomorrow":
        state["tomorrow"] = text
        state["step"] = "energy"
        send("Energy today, 1-10?", chat_id)
    elif step == "energy":
        try:
            state["energy"] = int(re.search(r"\d+", text).group())
        except Exception:
            state["energy"] = 7
        logs = _load(DAILY_LOG_FILE)
        today = date.today().isoformat()
        logs = [l for l in logs if l.get("date") != today]
        logs.append({
            "date": today, "did": state["did"], "didnt": state["didnt"],
            "tomorrow": state["tomorrow"], "energy": state["energy"],
            "timestamp": datetime.now().isoformat(),
        })
        _save(DAILY_LOG_FILE, logs)
        # Match the app's own save_daily_log behavior — a new log invalidates
        # both cached briefs so they regenerate against today's real entry.
        _save(MORNING_CACHE_FILE, {"date": "none", "content": ""})
        _save(EVENING_CACHE_FILE, {"date": "none", "content": ""})
        want_evening = state.get("then_evening")
        del _log_flow[chat_id]
        if want_evening:
            send("Logged. Pulling tonight's brief...", chat_id)
            _send_evening_brief(chat_id)
        else:
            send("Logged. See you tomorrow.", chat_id)
    return True


def _send_evening_brief(chat_id):
    """Generates (or serves cached) evening brief via the same helper the dashboard
    route uses — one code path, so the Telegram and web briefs never drift apart."""
    try:
        import app as app_mod
        result = app_mod._generate_evening_brief()
        send(result["reflection"], chat_id)
    except Exception as e:
        send(f"Couldn't generate tonight's brief: {e}", chat_id)


async def _handle_evening(chat_id):
    logs = _load(DAILY_LOG_FILE)
    today = date.today().isoformat()
    if any(l.get("date") == today for l in logs):
        send("Pulling tonight's brief...", chat_id)
        _send_evening_brief(chat_id)
    else:
        send("Let's log today first.", chat_id)
        await _handle_log_flow(chat_id, "/evening", then_evening=True)


# Chat_id -> last session_id, so /ask knows which session a follow-up belongs to
# without the user having to repeat context. Single-user bot, module dict is fine.
_last_session = {}


async def _handle_board_question(chat_id, question):
    send("Convening the board...", chat_id)
    try:
        result = await board_mod.run_board_async(question)
        _last_session[chat_id] = result["session_id"]
        for r in result["responses"]:
            send(f"*{r['name']}* ({r['role']}):\n{r['response']}", chat_id)
        if result.get("synthesis"):
            send(f"*Synthesis:*\n{result['synthesis']}", chat_id)
    except Exception as e:
        send(f"Board session failed: {e}", chat_id)


async def _handle_debate_question(chat_id, question):
    send("Two advisors are arguing this out (takes a bit longer)...", chat_id)
    try:
        result = await board_mod.run_debate_async(question)
        _last_session[chat_id] = result["session_id"]
        dbt = result["debate"]
        for d in (dbt["debater_a"], dbt["debater_b"]):
            send(f"*{d['name']}* ({d['role']}):\n{d['opening']}\n\n_Rebuttal:_ {d['rebuttal']}", chat_id)
        send(f"*Judge — {dbt['judge']['name']}:*\n{dbt['judge']['verdict']}", chat_id)
    except Exception as e:
        send(f"Debate failed: {e}", chat_id)


async def _handle_quick_question(chat_id, question):
    try:
        result = await board_mod.get_quick_response(question)
        send(f"*{result['advisor']}*: {result['response']}", chat_id)
    except Exception as e:
        send(f"Couldn't get an answer: {e}", chat_id)


async def _handle_ask_followup(chat_id, question):
    session_id = _last_session.get(chat_id)
    if not session_id:
        send("No active board session to follow up on — start one with /board or /debate first.", chat_id)
        return
    try:
        result = await board_mod.run_followup_async(session_id, question)
        for r in result.get("responses", []):
            send(f"*{r['name']}*: {r['response']}", chat_id)
    except Exception as e:
        send(f"Follow-up failed: {e}", chat_id)


def _handle_scan(chat_id):
    async def _run():
        try:
            import app as app_mod
            result = await app_mod._generate_opportunity_scan()
            send(result, chat_id)
        except Exception as e:
            send(f"Scan failed: {e}", chat_id)
    return _run()


def _handle_retro(chat_id):
    async def _run():
        try:
            import app as app_mod
            result = await app_mod._generate_retrospective()
            send(result, chat_id)
        except Exception as e:
            send(f"Retrospective failed: {e}", chat_id)
    return _run()


def _handle_recs(chat_id):
    recs = _load(RECS_FILE)
    pending = [r for r in recs if r.get("status") == "pending"]
    if not pending:
        send("No pending recommendations.", chat_id)
        return
    for r in pending[:10]:
        markup = {"inline_keyboard": [[{"text": "Mark done", "callback_data": f"done:{r['id']}"}]]}
        send(f"[{r.get('date','')}] {r.get('advisor','Board')}: {r.get('recommendation','')} ({r.get('timeframe','')})",
             chat_id, reply_markup=markup)


def _handle_brief(chat_id):
    alert = _broken_commitments_alert()
    if alert:
        send(alert, chat_id)
    cache = json.loads(MORNING_CACHE_FILE.read_text()) if MORNING_CACHE_FILE.exists() else {}
    today = date.today().isoformat()
    if cache.get("date") == today and cache.get("content"):
        send(cache["content"], chat_id)
    else:
        send("No brief cached yet for today — open the dashboard to generate one, or wait for the 6am push.", chat_id)


def _mark_rec_done(rec_id, chat_id):
    recs = _load(RECS_FILE)
    for r in recs:
        if r["id"] == rec_id:
            r["status"] = "actioned"
            r["actioned_date"] = date.today().isoformat()
    _save(RECS_FILE, recs)
    send("Marked done.", chat_id)


async def _route_text(chat_id, text):
    if chat_id in _log_flow:
        await _handle_log_flow(chat_id, text)
        return
    kind = await _classify_text(text)
    if kind == "log":
        logs = _load(DAILY_LOG_FILE)
        today = date.today().isoformat()
        today_entry = next((l for l in logs if l.get("date") == today), None)
        if today_entry:
            today_entry["did"] = (today_entry.get("did", "") + " " + text).strip()
            today_entry["timestamp"] = datetime.now().isoformat()
        else:
            logs.append({"date": today, "did": text, "didnt": "", "tomorrow": "", "energy": None,
                         "timestamp": datetime.now().isoformat()})
        _save(DAILY_LOG_FILE, logs)
        send("Logged.", chat_id)
    else:
        await _handle_quick_question(chat_id, text)


async def _handle_update(update):
    global CHAT_ID
    if "callback_query" in update:
        cb = update["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        if not _is_authorized(chat_id):
            return
        data = cb.get("data", "")
        if data.startswith("done:"):
            _mark_rec_done(data.split(":", 1)[1], chat_id)
        requests.post(f"{API}/answerCallbackQuery", json={"callback_query_id": cb["id"]}, timeout=10)
        return

    msg = update.get("message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]

    if not CHAT_ID:
        CHAT_ID = str(chat_id)
        send(f"Chat ID captured: {chat_id}\nAdd this to .env as TELEGRAM_CHAT_ID and restart the bot to lock it in.", chat_id)
        return

    if not _is_authorized(chat_id):
        print(f"[telegram_bot] rejected message from unauthorized chat {chat_id}")
        return

    if "voice" in msg:
        try:
            text = _transcribe_voice(msg["voice"]["file_id"])
            send(f"Heard: \"{text}\"", chat_id)
        except Exception as e:
            send(f"Couldn't transcribe that: {e}", chat_id)
            return
    else:
        text = msg.get("text", "").strip()
    if not text:
        return

    if text.startswith("/board "):
        await _handle_board_question(chat_id, text[len("/board "):].strip())
    elif text.startswith("/debate "):
        await _handle_debate_question(chat_id, text[len("/debate "):].strip())
    elif text.startswith("/ask "):
        await _handle_ask_followup(chat_id, text[len("/ask "):].strip())
    elif text == "/log":
        await _handle_log_flow(chat_id, text)
    elif text == "/evening":
        await _handle_evening(chat_id)
    elif text == "/brief":
        _handle_brief(chat_id)
    elif text == "/recs":
        _handle_recs(chat_id)
    elif text == "/scan":
        await _handle_scan(chat_id)
    elif text == "/retro":
        await _handle_retro(chat_id)
    elif text == "/approveprofile":
        _handle_profile_decision(chat_id, approve=True)
    elif text == "/rejectprofile":
        _handle_profile_decision(chat_id, approve=False)
    elif text == "/start":
        send("Command center bot online. /log to log your day, /evening for tonight's brief, /board <question> for the full board, /debate <question> for two advisors to argue it out, /ask <followup> to continue the last session, /brief for this morning's brief, /recs for pending recommendations, /scan for opportunities, /retro for a weekly retrospective. Or just talk to me.", chat_id)
    else:
        await _route_text(chat_id, text)


def _handle_profile_decision(chat_id, approve):
    proposal_path = DATA_DIR / "profile_proposal.json"
    if not proposal_path.exists():
        send("No pending profile update.", chat_id)
        return
    proposal = json.loads(proposal_path.read_text())
    if approve:
        profile_path = Path(__file__).parent.parent / "profile.md"
        profile_path.write_text(proposal["updated_profile"])
        send(f"Profile updated: {proposal.get('diff_summary','')}", chat_id)
    else:
        send("Profile update dismissed. Kept as-is.", chat_id)
    proposal_path.unlink()


async def poll_loop():
    if not BOT_TOKEN:
        print("[telegram_bot] TELEGRAM_BOT_TOKEN not set, exiting")
        return
    offset = 0
    print(f"[telegram_bot] polling started (chat_id={'set' if CHAT_ID else 'not set — send it a message'})")
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 25}, timeout=30).json()
            for update in r.get("result", []):
                offset = update["update_id"] + 1
                await _handle_update(update)
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            print(f"[telegram_bot] poll error: {e}")
            time.sleep(5)


# ── Push notifications (called by cron via `python3 tools/telegram_bot.py push_X`) ──

def _broken_commitments_alert():
    """Deterministic (no LLM) — surfaced ahead of the brief text so a broken
    commitment can't get buried inside a long generated paragraph."""
    recs = _load(RECS_FILE)
    scoreboard = gap_engine.compute_commitment_scoreboard(recs)
    if scoreboard["broken"] == 0:
        return None
    lines = [f"⚠️ {scoreboard['broken']} broken commitment(s) to the board (kept {scoreboard['kept']}, ratio {scoreboard['kept_ratio_pct']}%):"]
    for item in scoreboard.get("broken_items", [])[:3]:
        lines.append(f"- \"{item.get('recommendation','')[:100]}\" (due {item.get('deadline') or item.get('date','')})")
    return "\n".join(lines)


def push_morning_brief():
    """Called by cron at 6:00am, 30 min after /api/cron/morning kicks off generation
    at 5:30am. Retries because generation sometimes runs long — without this, a slow
    LLM call meant Hariv got the "isn't ready" fallback with no second chance."""
    today = date.today().isoformat()
    alert = _broken_commitments_alert()
    if alert:
        send(alert)
    for attempt in range(3):
        cache = json.loads(MORNING_CACHE_FILE.read_text()) if MORNING_CACHE_FILE.exists() else {}
        if cache.get("date") == today and cache.get("content"):
            send(cache["content"])
            return
        if attempt < 2:
            time.sleep(300)
    send("Morning brief isn't ready yet — check the dashboard shortly.")


def push_unlogged_nag():
    logs = _load(DAILY_LOG_FILE)
    today = date.today().isoformat()
    if not any(l.get("date") == today for l in logs):
        send("You haven't logged today yet. Send /evening to log it now and get tonight's brief.")


def push_evening_brief():
    """21:45 cron — if today's already logged, push the evening brief straight to
    Telegram instead of waiting for /evening to be sent manually. If not logged yet,
    the 21:30 unlogged-nag already covers prompting for it."""
    logs = _load(DAILY_LOG_FILE)
    today = date.today().isoformat()
    if not any(l.get("date") == today for l in logs):
        return
    try:
        _send_evening_brief(None)
    except Exception as e:
        print(f"[telegram_bot] push_evening_brief failed: {e}")


def push_decision_reviews():
    """Daily: surface decisions whose review_date has arrived and are still unresolved —
    closes the calibration loop instead of letting review_date sit there unused."""
    decisions = _load(DECISIONS_FILE)
    today = date.today().isoformat()
    due = [d for d in decisions if not d.get("outcome") and d.get("review_date") and d["review_date"] <= today]
    if due:
        lines = "\n".join(f"- \"{d.get('decision','')[:100]}\" (due {d.get('review_date')})" for d in due[:5])
        send(f"*Decisions due for review:*\n{lines}\n\nWhat happened? Update them on the dashboard.")


def push_weekly_synthesis():
    weekly_file = Path(__file__).parent.parent / "briefs"
    files = sorted(weekly_file.glob("weekly_*.md"), reverse=True)
    if files:
        content = files[0].read_text()
        if content:
            send(content)


def push_aging_recommendations():
    recs = _load(RECS_FILE)
    today = date.today()
    old = []
    for r in recs:
        if r.get("status") != "pending":
            continue
        try:
            d = date.fromisoformat(r.get("date", ""))
        except Exception:
            continue
        if (today - d).days >= 7:
            old.append(r)
    if old:
        lines = "\n".join(f"- [{r.get('date')}] {r.get('advisor','Board')}: {r.get('recommendation','')}" for r in old[:5])
        send(f"*Unacted advice (7+ days old):*\n{lines}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("push_"):
        globals()[sys.argv[1]]()
    else:
        asyncio.run(poll_loop())
