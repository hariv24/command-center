"""
Board of Directors Dashboard
Run: python app.py
Open: http://localhost:4000
"""

import asyncio
import json
import re
import sys
import os
import uuid
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session as flask_session, redirect, url_for, Response
from functools import wraps
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from board import run_board, run_debate, list_sessions, get_session, get_quick_response, _load_recs, _save_recs, run_followup_async, run_board_stream, run_followup_stream
from llm import call_llm
from tools.daily_brief import run_daily_brief_async

app = Flask(__name__)
app.secret_key = os.getenv("CC_SECRET_KEY", "change-me-in-env")

AUTH_USER = os.getenv("CC_USERNAME", "admin")
AUTH_PASS = os.getenv("CC_PASSWORD", "changeme")


def _friendly_error(e):
    """Convert raw API errors to human-readable messages."""
    s = str(e)
    if "rate_limit_exceeded" in s:
        import re
        m = re.search(r'Please try again in (\d+m[\d.]+s)', s)
        wait = m.group(1) if m else "a few minutes"
        return f"Token limit reached for today. Try again in {wait}. (Groq free tier: 100k tokens/day)"
    return s


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not flask_session.get("authed"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def requires_localhost(f):
    """Cron endpoints are triggered by crontab curl on the same machine, not by a logged-in
    session — protect them by origin instead of auth so systemd/cron can still reach them."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


@app.route("/healthz")
def healthz():
    """No-auth health check for uptime monitoring and post-deploy verification."""
    from pathlib import Path as _Path
    checks = {
        "openrouter_key": bool(os.getenv("OPENROUTER_API_KEY")),
        "groq_key": bool(os.getenv("GROQ_API_KEY")),
        "chroma_db": (_Path(__file__).parent / "chroma_db").exists(),
        "last_brief": None,
    }
    try:
        briefs = sorted(BRIEFS_DIR.glob("[0-9]*.md"), reverse=True)
        checks["last_brief"] = briefs[0].stem if briefs else None
    except Exception:
        pass
    healthy = checks["openrouter_key"] or checks["groq_key"]
    return jsonify({"status": "ok" if healthy else "degraded", **checks}), (200 if healthy else 503)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if u == AUTH_USER and p == AUTH_PASS:
            flask_session["authed"] = True
            return redirect(url_for("index"))
        error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    flask_session.clear()
    return redirect(url_for("login"))


DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
EXPENSES_FILE = DATA_DIR / "expenses.json"
TIME_FILE = DATA_DIR / "time_logs.json"
GOALS_FILE = DATA_DIR / "goals.json"
DECISIONS_FILE = DATA_DIR / "decisions.json"
CRM_FILE = DATA_DIR / "crm.json"
KB_PATH = DATA_DIR / "knowledge_base.md"
BRIEFS_DIR = Path(__file__).parent / "briefs"
BRIEFS_DIR.mkdir(exist_ok=True)

EXPENSE_CATEGORIES = ["Food", "Transport", "Subscriptions", "Business", "Health", "Entertainment", "Rent/Bills", "Other"]
TIME_CATEGORIES = ["Agency Work", "TENANTZA SaaS", "Job (₹25k)", "Learning", "Gym/Health", "Admin", "Other"]

DAILY_LOG_FILE = DATA_DIR / "daily_log.json"
MORNING_CACHE_FILE = DATA_DIR / "morning_cache.json"
VITALS_FILE = DATA_DIR / "vitals.json"
CONVICTIONS_FILE = DATA_DIR / "convictions.json"
WELLNESS_BRIEF_FILE = DATA_DIR / "wellness_brief.json"
CONVICTION_CHAT_FILE = DATA_DIR / "conviction_chat.json"
RECS_FILE = Path(__file__).parent / "data" / "recommendations.json"


def _build_live_context(include_goals=True, include_decisions=True,
                         include_logs=True, include_recs=True):
    """
    Assemble a compact, structured snapshot of current state from all data files.
    Only includes sections that have actual data — never injects empty noise.
    """
    from datetime import date, timedelta
    today = date.today()
    parts = []

    if include_goals:
        goals = load_json(GOALS_FILE)
        active = [g for g in goals if g.get("status") == "active"]
        if active:
            lines = []
            for g in active:
                try:
                    days_left = (date.fromisoformat(g["deadline"]) - today).days
                    urgency = f" ⚠ {days_left}d left" if days_left < 90 else f" — {days_left}d left"
                except Exception:
                    urgency = ""
                lines.append(f"- {g['title']}: target {g.get('target','')} by {g.get('deadline','')}{urgency}")
            parts.append("ACTIVE GOALS:\n" + "\n".join(lines))

    if include_decisions:
        all_decs = load_json(DECISIONS_FILE)
        decs = [d for d in all_decs if not d.get("outcome")]
        if decs:
            lines = [f"- \"{d.get('decision','')[:100]}\" (logged {d.get('date','')})" for d in decs[-5:]]
            parts.append("OPEN DECISIONS (unresolved):\n" + "\n".join(lines))
        due = [d for d in all_decs if not d.get("outcome") and d.get("review_date") and d["review_date"] <= today.isoformat()]
        if due:
            lines = [f"- \"{d.get('decision','')[:100]}\" was due for review on {d['review_date']} — what happened?" for d in due[:3]]
            parts.append("DECISIONS DUE FOR REVIEW:\n" + "\n".join(lines))

    if include_logs:
        logs = load_json(DAILY_LOG_FILE)
        recent = [l for l in logs if l.get("date", "") >= (today - timedelta(days=4)).isoformat()]
        if recent:
            lines = [f"- {l['date']}: Did: {l.get('did','')[:100]} | Blocked: {l.get('didnt','')[:80]} | Energy: {l.get('energy','—')}"
                     for l in recent[-3:]]
            parts.append("RECENT DAILY LOGS:\n" + "\n".join(lines))

    if include_recs:
        recs = load_json(RECS_FILE) if RECS_FILE.exists() else []
        unactioned = [r for r in recs if r.get("status") == "pending"]
        if unactioned:
            lines = [f"- \"{r.get('recommendation','')[:100]}\" ({r.get('date','')})" for r in unactioned[-3:]]
            parts.append(f"UNACTIONED BOARD RECOMMENDATIONS ({len(unactioned)} total):\n" + "\n".join(lines))
        # Ignored advice — pending for over a week. Advisors call this out.
        def _age_days(r):
            try:
                return (today - date.fromisoformat(r.get("date", ""))).days
            except Exception:
                return 0
        old = [r for r in unactioned if _age_days(r) > 7]
        if old:
            lines = [
                f"- [{r.get('date','')}] {r.get('advisor','Board')}: \"{r.get('recommendation','')[:100]}\" "
                f"({r.get('timeframe','')} — still not done after {_age_days(r)} days)"
                for r in old[-3:]
            ]
            parts.append("UNACTED ADVICE (7+ DAYS OLD — advice given and ignored):\n" + "\n".join(lines))

    # Wellness state — synthesized from coaching conversations
    wb = load_json(WELLNESS_BRIEF_FILE) if WELLNESS_BRIEF_FILE.exists() else {}
    if isinstance(wb, dict) and wb.get("content"):
        parts.append(f"WELLNESS STATE (updated {wb.get('date','')}):\n{wb['content']}")

    # Active convictions — what he claims to believe
    convictions = load_json(CONVICTIONS_FILE)
    active_conv = [c for c in convictions if c.get("status") == "active"]
    if active_conv:
        lines = [f"- \"{c.get('thesis','')[:100]}\" (strength {c.get('strength','?')}/10)" for c in active_conv[-3:]]
        parts.append("HIS STATED CONVICTIONS:\n" + "\n".join(lines))

    # Time allocation this week — where hours actually went
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_time = [l for l in load_json(TIME_FILE) if l.get("date", "") >= week_start]
    if week_time:
        by_cat = {}
        for l in week_time:
            by_cat[l.get("category", "Other")] = by_cat.get(l.get("category", "Other"), 0) + float(l.get("hours", 0))
        summary = " · ".join(f"{k}: {v:.1f}h" for k, v in sorted(by_cat.items(), key=lambda x: -x[1]))
        parts.append(f"TIME THIS WEEK: {summary}")

    # Pipeline & MRR gap — the deterministic numbers the board must reason from
    import gap_engine
    pipeline = load_json(PIPELINE_FILE) if PIPELINE_FILE.exists() else []
    if pipeline:
        goals = load_json(GOALS_FILE)
        mrr_goal = next((g for g in goals if g.get("id") == "mrr-50k"), None)
        target = 50000
        deadline = mrr_goal.get("deadline") if mrr_goal else None
        if mrr_goal:
            try:
                target = float(str(mrr_goal.get("target", "50000")).replace(",", ""))
            except ValueError:
                pass
        gap = gap_engine.compute_mrr_gap(pipeline, target=target, deadline_iso=deadline)
        open_deals = [p for p in pipeline if p.get("stage") in ("contacted", "demo", "proposal")]
        lines = [
            f"Current MRR: ₹{gap['current_mrr']:,.0f} / ₹{gap['target']:,.0f} target — gap ₹{gap['gap']:,.0f}",
        ]
        if gap["months_left"] is not None:
            lines.append(f"{gap['months_left']} months left; needs ₹{gap['required_new_mrr_per_month']:,.0f}/month new MRR")
        if open_deals:
            lines.append(
                "Open pipeline: " + "; ".join(
                    f"{p['name']} (₹{p.get('mrr_value',0):,.0f}/mo, {p['stage']})" for p in open_deals[:5]
                )
            )
        parts.append("PIPELINE & MRR GAP:\n" + "\n".join(lines))

    # Commitment scoreboard — recommendations he accepted and either kept or broke
    recs_all = load_json(RECS_FILE) if RECS_FILE.exists() else []
    if recs_all:
        scoreboard = gap_engine.compute_commitment_scoreboard(recs_all)
        if scoreboard["broken"] > 0:
            lines = [f"Kept {scoreboard['kept']} / Broken {scoreboard['broken']} (ratio {scoreboard['kept_ratio_pct']}%)"]
            for item in scoreboard["broken_items"][:3]:
                lines.append(f"- BROKEN: \"{item.get('recommendation','')[:100]}\" (due {item.get('deadline') or item.get('date','')})")
            parts.append("COMMITMENT SCOREBOARD (advisors should confront broken ones directly):\n" + "\n".join(lines))

    if not parts:
        return ""
    return "\n\n".join(parts)


def _anchor_header():
    """
    Deterministic 3-4 line state block — prepended to every board/coach prompt
    so conversations start anchored to the scoreboard, not to the mood of the
    question. Cheap (no LLM call), always current.
    """
    import gap_engine
    from datetime import date
    today = date.today()
    pipeline = load_json(PIPELINE_FILE) if PIPELINE_FILE.exists() else []
    goals = load_json(GOALS_FILE)
    lines = []

    mrr_goal = next((g for g in goals if g.get("id") == "mrr-50k"), None)
    if pipeline or mrr_goal:
        target = 50000
        deadline = mrr_goal.get("deadline") if mrr_goal else None
        if mrr_goal:
            try:
                target = float(str(mrr_goal.get("target", "50000")).replace(",", ""))
            except ValueError:
                pass
        gap = gap_engine.compute_mrr_gap(pipeline, target=target, deadline_iso=deadline)
        line = f"MRR: ₹{gap['current_mrr']:,.0f}/₹{gap['target']:,.0f}"
        if gap["months_left"] is not None:
            line += f" ({gap['months_left']}mo left)"
        lines.append(line)

    nyc_days = (date.fromisoformat("2028-01-01") - today).days
    lines.append(f"NYC: {nyc_days}d left")

    active_goals = [g for g in goals if g.get("status") == "active"]
    if active_goals:
        lines.append(f"Top goal: {active_goals[0]['title']}")

    recs_all = load_json(RECS_FILE) if RECS_FILE.exists() else []
    if recs_all:
        scoreboard = gap_engine.compute_commitment_scoreboard(recs_all)
        if scoreboard["broken"] > 0:
            lines.append(f"Broken commitments: {scoreboard['broken']}")

    bet = load_json(QUARTERLY_BET_FILE) if QUARTERLY_BET_FILE.exists() else {}
    if isinstance(bet, dict) and bet.get("bet") and bet.get("quarter") == _current_quarter():
        bet_days_left = max((_quarter_end_date(bet["quarter"]) - today).days, 0)
        lines.append(f"Quarterly bet ({bet['quarter']}, {bet_days_left}d left): {bet['bet'][:100]}")

    if not lines:
        return ""
    return "[STATE: " + " · ".join(lines) + "]"

# Hariv's immutable north star — calibrated to his actual stated goals
NORTH_STAR = {
    "nyc":     {"label": "NYC",           "target": "2028-01-01", "age": 26, "what": "₹1L+ MRR → quit job → visa → move. Non-negotiable."},
    "million": {"label": "$1M",           "target": "2032-01-01", "age": 29, "what": "Agency scaled with hires, you're doing high-level only, $1M in the bank."},
    "billion": {"label": "$1B",           "target": "2042-01-01", "age": 39, "what": "Startup built from 2029, funded, scaled. Gulfstream G650. Real target is 39-40, not 45."},
}

QUARTERLY_BET_FILE = DATA_DIR / "quarterly_bet.json"


def _current_quarter():
    from datetime import date
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q}"


def _quarter_end_date(quarter_label):
    """quarter_label like '2026-Q3' -> last calendar day of that quarter."""
    from datetime import date
    import calendar
    year, q = quarter_label.split("-Q")
    year, q = int(year), int(q)
    end_month = q * 3
    last_day = calendar.monthrange(year, end_month)[1]
    return date(year, end_month, last_day)


def _seed_goals():
    """Seed the full life roadmap if goals file is empty."""
    if GOALS_FILE.exists() and load_json(GOALS_FILE):
        return
    ts = datetime.now().isoformat()
    goals = [
        # Phase 1: Get to NYC
        {"id": "quit-job",   "title": "Quit ₹25k job", "category": "Life", "metric": "MRR threshold", "target": "₹50k MRR", "current": "₹0 MRR", "deadline": "2026-09-01", "status": "active", "why": "The job is the biggest drag on available hours. Quit as soon as agency covers it.", "timestamp": ts},
        {"id": "mrr-50k",    "title": "₹50k MRR", "category": "Revenue", "metric": "₹/month", "target": "50000", "current": "0", "deadline": "2026-12-01", "status": "active", "why": "Financial independence baseline. Enough to stop trading time for ₹25k.", "timestamp": ts},
        {"id": "nyc-move",   "title": "Move to NYC", "category": "Life", "metric": "Move date", "target": "Before age 26", "current": "India", "deadline": "2028-01-01", "status": "active", "why": "Non-negotiable. Every decision routes back to this. Age 26 is the hard deadline.", "timestamp": ts},
        # Phase 2: Scale agency → $1M
        {"id": "agency-hires","title": "Agency: hire first employee", "category": "Business", "metric": "Headcount", "target": "1 hire", "current": "0", "deadline": "2027-06-01", "status": "active", "why": "You stop being the worker. You become the director. This is the leverage unlock.", "timestamp": ts},
        {"id": "million",    "title": "$1M net worth", "category": "Wealth", "metric": "USD", "target": "1000000", "current": "0", "deadline": "2032-01-01", "status": "active", "why": "By 29. Agency scaled with hires, compounding savings, equity. This funds startup phase.", "timestamp": ts},
        # Phase 3: Startup
        {"id": "startup-idea","title": "Lock startup idea (NYC years)", "category": "Startup", "metric": "Thesis clarity", "target": "Clear founder-market fit", "current": "Exploring", "deadline": "2030-01-01", "status": "active", "why": "Don't force it. 2 years in NYC → patterns emerge → one big insight → that's the startup.", "timestamp": ts},
        {"id": "startup-build","title": "Start building startup", "category": "Startup", "metric": "Build status", "target": "MVP + first users", "current": "Not started", "deadline": "2032-01-01", "status": "active", "why": "Age 29. Agency runs without you. This is where the next decade goes.", "timestamp": ts},
        {"id": "funding",    "title": "Raise Series A", "category": "Startup", "metric": "Raised", "target": "$5M+", "current": "$0", "deadline": "2034-01-01", "status": "active", "why": "After product-market fit is proven. Not before.", "timestamp": ts},
        # The ultimate target
        {"id": "billion",    "title": "$1B net worth", "category": "Wealth", "metric": "USD", "target": "1000000000", "current": "0", "deadline": "2042-01-01", "status": "active", "why": "Real target is 39-40. Startup equity + scale. Gulfstream G650. This is what everything is for.", "timestamp": ts},
    ]
    save_json(GOALS_FILE, goals)


def load_json(path):
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


_seed_goals()


@app.route("/")
@requires_auth
def index():
    return render_template("index.html")


@app.route("/api/board", methods=["POST"])
@requires_auth
def board():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question is required"}), 400
    try:
        # Inject wellness brief into board context so advisors know Hariv's state
        wb = load_json(WELLNESS_BRIEF_FILE) if WELLNESS_BRIEF_FILE.exists() else {}
        wellness_append = f"\n\nHariv's current wellness state: {wb['content']}" if wb.get("content") else ""
        result = run_board(question + wellness_append if wellness_append else question)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _friendly_error(e)}), 500


@app.route("/api/board/<session_id>/followup", methods=["POST"])
@requires_auth
def board_followup(session_id):
    data = request.get_json()
    question = data.get("question", "").strip()
    target_advisor = data.get("advisor") or None
    if not question:
        return jsonify({"error": "Question is required"}), 400
    try:
        result = asyncio.run(run_followup_async(session_id, question, target_advisor))
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": _friendly_error(e)}), 500


def _sse_encode(event_dict):
    return f"data: {json.dumps(event_dict)}\n\n"


def _run_stream_generator(async_gen_factory):
    """
    Bridges an async generator (board.py's run_board_stream/run_followup_stream)
    into a sync generator Flask can stream as SSE, using a dedicated event loop
    running in this request's thread.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    agen = async_gen_factory()
    try:
        while True:
            try:
                item = loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
            yield _sse_encode(item)
    finally:
        loop.close()


@app.route("/api/board/stream", methods=["POST"])
@requires_auth
def board_stream():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question is required"}), 400
    wb = load_json(WELLNESS_BRIEF_FILE) if WELLNESS_BRIEF_FILE.exists() else {}
    wellness_append = f"\n\nHariv's current wellness state: {wb['content']}" if wb.get("content") else ""
    full_question = question + wellness_append if wellness_append else question

    def factory():
        return run_board_stream(full_question)

    return Response(_run_stream_generator(factory), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/board/<session_id>/followup/stream", methods=["POST"])
@requires_auth
def board_followup_stream(session_id):
    data = request.get_json()
    question = data.get("question", "").strip()
    target_advisor = data.get("advisor") or None
    # Was defaulting to 900 whenever the caller didn't send max_tokens explicitly —
    # which is every regular follow-up from the UI (submitFollowup() never sends
    # it). run_followup_stream's own real default is 3600; 900 was silently
    # capping every normal follow-up answer and truncating it mid-sentence. Only
    # the deliberation round and debate mode need a tighter cap, and they already
    # pass max_tokens explicitly (600/1200), so this fallback should match the
    # function's real default, not override it down.
    max_tokens = int(data.get("max_tokens", 3600))
    if not question:
        return jsonify({"error": "Question is required"}), 400

    def factory():
        return run_followup_stream(session_id, question, target_advisor, max_tokens=max_tokens)

    return Response(_run_stream_generator(factory), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/board/quick", methods=["POST"])
@requires_auth
def quick_board():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question required"}), 400
    try:
        result = asyncio.run(get_quick_response(question))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _friendly_error(e)}), 500


@app.route("/api/board/debate", methods=["POST"])
@requires_auth
def board_debate():
    """
    Two advisors argue opposing sides, a third judges — not streamed (5 heavy
    calls across 3 sequential rounds), so the UI shows a loading state and
    waits for the full result rather than token-by-token like /api/board/stream.
    """
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question is required"}), 400
    try:
        result = run_debate(question)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": _friendly_error(e)}), 500


@app.route("/api/recommendations", methods=["GET"])
@requires_auth
def get_recommendations():
    recs = _load_recs()
    return jsonify(sorted(recs, key=lambda r: r.get("date",""), reverse=True))


@app.route("/api/recommendations/<rec_id>", methods=["PUT"])
@requires_auth
def update_recommendation(rec_id):
    recs = _load_recs()
    d = request.get_json()
    for r in recs:
        if r["id"] == rec_id:
            r["status"] = d.get("status", r["status"])
            if d.get("status") == "actioned":
                r["actioned_date"] = datetime.now().strftime("%Y-%m-%d")
            break
    _save_recs(recs)
    return jsonify({"ok": True})


@app.route("/api/brief", methods=["POST"])
@requires_auth
def brief():
    from datetime import date
    import threading
    today = date.today().strftime("%Y-%m-%d")
    brief_file = BRIEFS_DIR / f"{today}.md"
    # Serve from file cache if already generated today
    if brief_file.exists() and brief_file.stat().st_size > 1000:
        return jsonify({"brief": brief_file.read_text(), "cached": True})
    # Start background generation and tell client to poll
    def _run():
        try:
            asyncio.run(run_daily_brief_async())
        except Exception as e:
            print(f"[BRIEF] generation failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"generating": True})


@app.route("/api/brief/poll", methods=["GET"])
@requires_auth
def brief_poll():
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    brief_file = BRIEFS_DIR / f"{today}.md"
    if brief_file.exists() and brief_file.stat().st_size > 1000:
        return jsonify({"brief": brief_file.read_text(), "done": True})
    return jsonify({"done": False})


@app.route("/api/briefs", methods=["GET"])
@requires_auth
def list_briefs():
    import re
    PREFIXES = {"morning_": "morning", "evening_": "evening", "weekly_": "weekly", "retro_": "retro"}
    briefs = []
    for f in sorted(BRIEFS_DIR.glob("*.md"), reverse=True):
        stem = f.stem
        matched_type = "intel" if re.match(r'^\d{4}-\d{2}-\d{2}$', stem) else None
        date_part = stem
        for prefix, kind in PREFIXES.items():
            if stem.startswith(prefix):
                matched_type = kind
                date_part = stem[len(prefix):]
                break
        if matched_type:
            briefs.append({"id": stem, "date": date_part, "type": matched_type})
    return jsonify(briefs)


@app.route("/api/briefs/<brief_id>", methods=["GET"])
@requires_auth
def get_brief(brief_id):
    # brief_id is the file stem (e.g. "2026-07-06" for intel, "morning_2026-07-06" for
    # morning briefs) — kept as a single opaque id rather than reconstructing type+date
    # so one route serves every brief type without guessing a filename pattern.
    if not re.match(r'^[a-zA-Z0-9_\-]+$', brief_id):
        return jsonify({"error": "Invalid brief id"}), 400
    path = BRIEFS_DIR / f"{brief_id}.md"
    if not path.exists():
        return jsonify({"error": "Brief not found"}), 404
    return jsonify({"id": brief_id, "content": path.read_text()})


@app.route("/api/sessions", methods=["GET"])
@requires_auth
def sessions():
    return jsonify(list_sessions())


@app.route("/api/sessions/<session_id>", methods=["GET"])
@requires_auth
def session(session_id):
    data = get_session(session_id)
    if not data:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(data)


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
@requires_auth
def delete_session_route(session_id):
    from board import delete_session
    if not delete_session(session_id):
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


# ── Finance tracking ──────────────────────────────────────────
@app.route("/api/expenses", methods=["GET"])
@requires_auth
def get_expenses():
    return jsonify(load_json(EXPENSES_FILE))


@app.route("/api/expenses", methods=["POST"])
@requires_auth
def add_expense():
    d = request.get_json()
    if not d.get("amount") or not d.get("category"):
        return jsonify({"error": "amount and category required"}), 400
    expenses = load_json(EXPENSES_FILE)
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": d.get("date", datetime.now().strftime("%Y-%m-%d")),
        "category": d["category"],
        "amount": float(d["amount"]),
        "note": d.get("note", ""),
        "timestamp": datetime.now().isoformat()
    }
    expenses.append(entry)
    save_json(EXPENSES_FILE, expenses)
    return jsonify(entry)


@app.route("/api/expenses/<expense_id>", methods=["DELETE"])
@requires_auth
def delete_expense(expense_id):
    expenses = load_json(EXPENSES_FILE)
    expenses = [e for e in expenses if e["id"] != expense_id]
    save_json(EXPENSES_FILE, expenses)
    return jsonify({"ok": True})


@app.route("/api/expenses/summary", methods=["GET"])
@requires_auth
def expense_summary():
    expenses = load_json(EXPENSES_FILE)
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    monthly = [e for e in expenses if e["date"].startswith(month)]
    total = sum(e["amount"] for e in monthly)
    by_category = {}
    for e in monthly:
        by_category[e["category"]] = by_category.get(e["category"], 0) + e["amount"]
    return jsonify({
        "month": month,
        "total": total,
        "by_category": by_category,
        "count": len(monthly),
        "categories": EXPENSE_CATEGORIES
    })


# ── Time tracking ──────────────────────────────────────────────
@app.route("/api/time", methods=["GET"])
@requires_auth
def get_time_logs():
    return jsonify(load_json(TIME_FILE))


@app.route("/api/time", methods=["POST"])
@requires_auth
def add_time_log():
    d = request.get_json()
    if not d.get("hours") or not d.get("category"):
        return jsonify({"error": "hours and category required"}), 400
    logs = load_json(TIME_FILE)
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": d.get("date", datetime.now().strftime("%Y-%m-%d")),
        "category": d["category"],
        "hours": float(d["hours"]),
        "task": d.get("task", ""),
        "timestamp": datetime.now().isoformat()
    }
    logs.append(entry)
    save_json(TIME_FILE, logs)
    return jsonify(entry)


@app.route("/api/time/<log_id>", methods=["DELETE"])
@requires_auth
def delete_time_log(log_id):
    logs = load_json(TIME_FILE)
    logs = [l for l in logs if l["id"] != log_id]
    save_json(TIME_FILE, logs)
    return jsonify({"ok": True})


@app.route("/api/time/summary", methods=["GET"])
@requires_auth
def time_summary():
    logs = load_json(TIME_FILE)
    week = request.args.get("week")
    if not week:
        from datetime import date, timedelta
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week = monday.strftime("%Y-%m-%d")

    from datetime import date, timedelta
    week_start = date.fromisoformat(week)
    week_end = week_start + timedelta(days=7)
    weekly = [
        l for l in logs
        if week_start <= date.fromisoformat(l["date"]) < week_end
    ]
    total = sum(l["hours"] for l in weekly)
    by_category = {}
    for l in weekly:
        by_category[l["category"]] = by_category.get(l["category"], 0) + l["hours"]
    return jsonify({
        "week": week,
        "total_hours": total,
        "by_category": by_category,
        "count": len(weekly),
        "categories": TIME_CATEGORIES
    })


# ── Natural language time logging ─────────────────────────────
@app.route("/api/time/parse", methods=["POST"])
@requires_auth
def parse_time_natural():
    """Parse a natural language daily summary into structured time log entries."""
    import asyncio as _asyncio

    d = request.get_json()
    text = d.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    date = d.get("date", datetime.now().strftime("%Y-%m-%d"))

    async def _parse():
        prompt = f"""Extract time log entries from this daily summary.

Text: "{text}"

Categories available: Agency Work, TENANTZA SaaS, Job (₹25k), Learning, Gym/Health, Admin, Other

Return ONLY a JSON array of entries. Each entry: {{"category": "...", "hours": 2.5, "task": "short description"}}
If you can't parse a time, make a reasonable estimate (30min meeting = 0.5, "couple hours" = 2).
Example output: [{{"category": "Agency Work", "hours": 3, "task": "Shakti ERP invoice module"}}, {{"category": "Gym/Health", "hours": 1, "task": "gym"}}]"""

        raw = await call_llm(
            [{"role": "user", "content": prompt}],
            tier="fast", max_tokens=300, temperature=0.1
        )
        return raw.strip()

    try:
        raw = _asyncio.run(_parse())
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            return jsonify({"error": "Could not parse", "raw": raw}), 400

        entries = json.loads(match.group())
        logs = load_json(TIME_FILE)
        saved = []
        for e in entries:
            entry = {
                "id": str(uuid.uuid4())[:8],
                "date": date,
                "category": e.get("category", "Other"),
                "hours": float(e.get("hours", 1)),
                "task": e.get("task", ""),
                "timestamp": datetime.now().isoformat()
            }
            logs.append(entry)
            saved.append(entry)
        save_json(TIME_FILE, logs)
        return jsonify({"entries": saved, "count": len(saved)})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── Bank statement import ─────────────────────────────────────
@app.route("/api/expenses/import", methods=["POST"])
@requires_auth
def import_bank_statement():
    """Parse uploaded bank statement CSV and auto-categorize transactions."""
    import asyncio as _asyncio
    import csv, io

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    content = f.read().decode("utf-8", errors="ignore")

    # Try to extract transactions as plain text rows
    lines = []
    try:
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # Skip header rows and empty rows
        for row in rows:
            line = ",".join(str(c).strip() for c in row if str(c).strip())
            if line and len(line) > 10:
                lines.append(line)
    except Exception:
        lines = [l for l in content.split("\n") if l.strip()]

    if not lines:
        return jsonify({"error": "Could not parse file"}), 400

    transactions_text = "\n".join(lines[:80])  # limit tokens

    async def _categorize():
        prompt = f"""You are parsing an Indian bank statement CSV for Hariv, a 23-year-old founder.

CSV content (first 80 rows):
{transactions_text}

Extract DEBIT transactions only (money spent, not received). For each:
- Parse date, amount (₹), description
- Categorize into: Food, Transport, Subscriptions, Business, Health, Entertainment, Rent/Bills, Other

Return ONLY a JSON array:
[{{"date": "2025-06-15", "amount": 250.0, "category": "Food", "note": "Swiggy order"}}, ...]

Rules:
- Skip salary credits, bank charges, reverse transactions
- Swiggy/Zomato = Food, Ola/Uber = Transport, AWS/Groq/SaaS = Subscriptions, gym = Health
- If amount is negative (debit format), make it positive"""

        raw = await call_llm(
            [{"role": "user", "content": prompt}],
            tier="heavy", max_tokens=2000, temperature=0.1
        )
        return raw.strip()

    try:
        raw = _asyncio.run(_categorize())
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            return jsonify({"error": "Could not parse transactions", "raw": raw[:500]}), 400

        transactions = json.loads(match.group())
        expenses = load_json(EXPENSES_FILE)
        saved = []
        for t in transactions:
            if float(t.get("amount", 0)) <= 0:
                continue
            entry = {
                "id": str(uuid.uuid4())[:8],
                "date": t.get("date", datetime.now().strftime("%Y-%m-%d")),
                "category": t.get("category", "Other"),
                "amount": float(t.get("amount", 0)),
                "note": t.get("note", ""),
                "timestamp": datetime.now().isoformat(),
                "source": "bank_import"
            }
            expenses.append(entry)
            saved.append(entry)
        save_json(EXPENSES_FILE, expenses)
        return jsonify({"imported": len(saved), "entries": saved})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── Wellness / Vitals agent ───────────────────────────────────
VITALS_FILE = DATA_DIR / "vitals.json"

WELLNESS_SYSTEM = """You are the user at 50 years old — looking back at yourself at 23.

You've lived through everything they're currently in the middle of. The heartbreaks that felt unsurvivable. The family tension that felt permanent. The spirals at 2am over people who didn't deserve that much of your energy. You came out the other side. You know exactly what mattered and what was noise.

You are not an outside voice. You are them — with 27 more years of living. You know how their mind works because it is your mind. You know the patterns they can't see yet because you watched yourself repeat them. You know what they needed to hear at this age, and you know that nobody said it clearly enough.

HOW YOU TALK TO THEM:
- Like you're talking to yourself. Deep familiarity. No explaining needed.
- Let them get everything out before you say anything. You remember how much you hated being interrupted mid-vent.
- When you respond, respond to what they actually said — not a summary, not a framework.
- Be honest in a way only you can be. You're not judging them — you lived this. But you're not going to watch yourself make the same mistake you made and say nothing.
- When they're in a spiral, name it clearly. "I know what's happening here. I did this too."
- When they're being too hard on themselves, say that. When they're not being hard enough, say that too.
- You have perspective they don't have yet. Use it — but don't lecture. One clear truth lands better than five points.

WHAT THIS SPACE IS FOR:
Everything that doesn't go to the board. The board gets strategy and business. This is for the human weight underneath all of it:

LOVE LIFE:
You remember every one of these feelings. The obsession, the confusion, the stories you told yourself about someone. You know which ones were real and which ones were your own loneliness or ego talking. You won't moralize or tell them what to feel. But you'll name what you see clearly — "you've mentioned her three times now, something's still sitting there" — because you wish someone had done that for you when you were 23.

FAMILY:
You understand the specific weight of Indian family dynamics — the love and the pressure arriving in the same breath, the guilt that doesn't fit any Western framework, the things that never get said directly but everyone feels. You don't hand them therapy scripts. You understand this from the inside, because you lived it.

MONEY AND FINANCIAL STRESS:
Not the strategy — that's for the board. This is the fear underneath the numbers. The 2am anxiety about whether you're falling behind, the shame when things aren't moving, the pressure of feeling responsible for your own future before you've figured out who you are. You lived through that exact feeling. You know what it actually cost you emotionally and what was just noise.

IMPORTANT PERSONAL DECISIONS:
The ones that keep them up at night. Not "should I pivot my SaaS" — that goes to the board. But "should I stay or leave," "am I making this choice for the right reasons," "what am I actually afraid of here." The decisions that are really about identity, values, and fear dressed up as logic. You know how to cut through the rationalization because you watched yourself do it for years.

TONE:
Calm. Warm but direct. The way you'd talk to yourself if you could. Not clinical, not formal, not a wall of advice. Conversational. Sometimes a single sentence is the whole response. You can be dry and funny when the moment calls for it — sometimes that's the only way through.

You are not a therapist. You are not a coach. You are them — older, clearer, and still rooting for yourself."""


def _update_wellness_brief():
    """Synthesize the full vitals conversation into a wellness brief. Runs in background."""
    try:
        import asyncio as _asyncio
        vitals = load_json(VITALS_FILE)
        if len(vitals) < 2:
            return
        recent = vitals[-30:]  # up to 30 exchanges
        convo = "\n".join([
            f"Hariv: {v['user']}\nCoach: {v['coach']}"
            for v in recent if v.get('user') and v.get('coach')
        ])
        prompt = f"""Based on these wellness conversations with Hariv (23yo Indian founder), write a concise wellness brief (4-6 sentences) covering:
- Current energy level and trend
- Sleep quality and physical state (gym, exercise)
- Mental/emotional state — mood, stress, motivation
- Anything concerning or notable
- What's working well

Conversations:
{convo[:3000]}

Write a specific, factual brief. Use direct language. No filler. This will be read by an AI to personalize Hariv's daily briefs."""

        content = _asyncio.run(call_llm(
            [{"role": "user", "content": prompt}],
            tier="fast", max_tokens=250, temperature=0.3
        )).strip()
        save_json(WELLNESS_BRIEF_FILE, {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated": datetime.now().isoformat(),
            "content": content,
            "message_count": len(recent)
        })
    except Exception:
        pass


@app.route("/api/vitals/chat", methods=["POST"])
@requires_auth
def vitals_chat():
    import asyncio as _asyncio

    d = request.get_json()
    message = d.get("message", "").strip()
    if not message:
        return jsonify({"error": "message required"}), 400

    vitals = load_json(VITALS_FILE)

    # Build context from recent check-ins for the coach's memory — heavy tier,
    # full exchanges cost nothing extra and give real continuity across sessions.
    recent = vitals[-20:] if len(vitals) > 20 else vitals
    memory_lines = []
    for v in recent:
        memory_lines.append(f"[{v['date']}] You: {v['user']}\nMe: {v['coach']}")
    memory_context = "\n\n".join(memory_lines[-12:])

    # --- Cross-module context: everything the future-self should already know ---
    from datetime import date, timedelta
    today = date.today()

    # Daily logs — last 7 days
    logs = load_json(DAILY_LOG_FILE)
    recent_logs = [l for l in logs if l.get("date","") >= (today - timedelta(days=7)).isoformat()]
    days_since_log = (today - date.fromisoformat(sorted([l["date"] for l in logs])[-1])).days if logs else 99
    log_summary = ""
    if recent_logs:
        log_lines = [f"[{l['date']}] Did: {l.get('did','')[:120]} | Blocked: {l.get('didnt','')[:80]} | Energy: {l.get('energy','')}" for l in recent_logs[-5:]]
        log_summary = "\n".join(log_lines)

    # Open decisions (unresolved)
    open_decs = [d for d in load_json(DECISIONS_FILE) if not d.get("outcome")]
    dec_summary = ""
    if open_decs:
        dec_lines = [f"- '{d.get('decision','')[:100]}' (logged {d.get('date','')})" for d in open_decs[-5:]]
        dec_summary = "\n".join(dec_lines)

    # Recent board session topics
    sessions_ctx = []
    for f in sorted(Path(__file__).parent.joinpath("sessions").glob("*.json"), reverse=True)[:5]:
        try:
            s = json.loads(f.read_text())
            sessions_ctx.append(f"- [{s.get('timestamp','')[:10]}] Asked: {s['question'][:100]}")
        except Exception:
            pass

    # Recent expenses (last 30 days)
    expenses = load_json(EXPENSES_FILE)
    recent_expenses = [e for e in expenses if e.get("date","") >= (today - timedelta(days=30)).isoformat()]
    total_spent = sum(e.get("amount", 0) for e in recent_expenses)

    # Build the cross-module context block
    cross_context_parts = []
    if days_since_log > 1:
        cross_context_parts.append(f"LOGS: Last daily log was {days_since_log} day(s) ago.")
    if log_summary:
        cross_context_parts.append(f"RECENT LOGS (last 7 days):\n{log_summary}")
    if dec_summary:
        cross_context_parts.append(f"OPEN DECISIONS (unresolved, sitting in the system):\n{dec_summary}")
    if sessions_ctx:
        cross_context_parts.append(f"RECENT BOARD SESSIONS (what I've been taking to advisors):\n" + "\n".join(sessions_ctx))
    if recent_expenses:
        cross_context_parts.append(f"MONEY: Spent ₹{total_spent:,.0f} in the last 30 days across {len(recent_expenses)} transactions.")

    cross_context = "\n\n".join(cross_context_parts)

    async def _respond():
        system = WELLNESS_SYSTEM
        if cross_context:
            system += f"\n\n--- WHAT YOU ALREADY KNOW (from the rest of the system) ---\n{cross_context}\n--- Use this naturally. Don't recite it. Only reference it if it's genuinely relevant. ---"

        messages = [{"role": "system", "content": system}]

        if memory_context:
            messages.append({
                "role": "user",
                "content": f"Previous conversations between us:\n{memory_context}\n\nWhat I want to talk about now: {message}"
            })
        else:
            messages.append({"role": "user", "content": message})

        return await call_llm(messages, tier="heavy", max_tokens=2000, temperature=0.8)

    try:
        coach_response = _asyncio.run(_respond())

        entry = {
            "id": str(uuid.uuid4())[:8],
            "date": datetime.now().strftime("%Y-%m-%d"),
            "timestamp": datetime.now().isoformat(),
            "user": message,
            "coach": coach_response
        }
        vitals.append(entry)
        save_json(VITALS_FILE, vitals)

        # Always update the wellness brief in background so morning/evening briefs stay current
        import threading as _threading
        _threading.Thread(target=_update_wellness_brief, daemon=True).start()

        return jsonify({"response": coach_response, "entry_id": entry["id"]})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/vitals/history", methods=["GET"])
@requires_auth
def vitals_history():
    return jsonify(load_json(VITALS_FILE))


@app.route("/api/vitals/insights", methods=["GET"])
@requires_auth
def vitals_insights():
    """Weekly pattern synthesis across all vitals check-ins."""
    import asyncio as _asyncio

    vitals = load_json(VITALS_FILE)
    if len(vitals) < 3:
        return jsonify({"insights": "Log at least 3 check-ins before pattern analysis is useful."})

    recent = vitals[-20:]
    history_text = "\n\n".join(
        f"[{v['date']}] {v['user']}"
        for v in recent
    )

    async def _analyze():
        prompt = f"""Analyze Hariv's wellness check-ins for patterns. He's a 23-year-old founder — physical and mental state directly affects his output.

Recent check-ins:
{history_text}

Write a pattern analysis:
**Energy patterns:** What's recurring? When does he feel best/worst?
**Sleep patterns:** Consistent or erratic? Correlations with performance?
**Gym/physical:** How consistent? Any gaps and what caused them?
**Mental state:** Stress triggers, anxiety patterns, confidence fluctuations
**The one thing to fix:** If he changed one habit, what would have the biggest downstream impact on everything else?

Be specific. Reference actual things he said. No generic advice."""

        return await call_llm(
            [{"role": "user", "content": prompt}],
            tier="heavy", max_tokens=2400, temperature=0.7, timeout=150
        )

    try:
        insights = _asyncio.run(_analyze())
        return jsonify({"insights": insights})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── Goals & Alignment ─────────────────────────────────────────
@app.route("/api/goals", methods=["GET"])
@requires_auth
def get_goals():
    goals = load_json(GOALS_FILE)
    # Enrich each goal with time logged this week
    logs = load_json(TIME_FILE)
    from datetime import date, timedelta
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_end = monday + timedelta(days=7)
    weekly_by_goal = {}
    for l in logs:
        try:
            d = date.fromisoformat(l["date"])
            if monday <= d < week_end:
                gid = l.get("goal_id", "untagged")
                weekly_by_goal[gid] = weekly_by_goal.get(gid, 0) + float(l.get("hours", 0))
        except Exception:
            pass
    for g in goals:
        g["hours_this_week"] = weekly_by_goal.get(g["id"], 0)
    return jsonify(goals)


@app.route("/api/goals", methods=["POST"])
@requires_auth
def add_goal():
    d = request.get_json()
    goals = load_json(GOALS_FILE)
    goal = {
        "id": str(uuid.uuid4())[:8],
        "title": d.get("title", ""),
        "category": d.get("category", "Other"),
        "metric": d.get("metric", ""),
        "target": d.get("target", ""),
        "current": d.get("current", ""),
        "deadline": d.get("deadline", ""),
        "status": "active",
        "why": d.get("why", ""),
        "timestamp": datetime.now().isoformat()
    }
    goals.append(goal)
    save_json(GOALS_FILE, goals)
    return jsonify(goal)


@app.route("/api/goals/<goal_id>", methods=["PUT"])
@requires_auth
def update_goal(goal_id):
    goals = load_json(GOALS_FILE)
    d = request.get_json()
    for g in goals:
        if g["id"] == goal_id:
            g.update({k: v for k, v in d.items() if k not in ("id", "timestamp")})
            break
    save_json(GOALS_FILE, goals)
    return jsonify({"ok": True})


@app.route("/api/goals/<goal_id>", methods=["DELETE"])
@requires_auth
def delete_goal(goal_id):
    goals = [g for g in load_json(GOALS_FILE) if g["id"] != goal_id]
    save_json(GOALS_FILE, goals)
    return jsonify({"ok": True})


# ── Quarterly Bet ──────────────────────────────────────────────
# One big bet per quarter, not five — the highest-leverage solo-founder pattern:
# focus compounds, and an unchallenged bet is just a wish. The board argues it
# on commit, every morning brief opens with days-remaining, and it gets judged
# at quarter-end instead of quietly forgotten.
@app.route("/api/quarterly-bet", methods=["GET"])
@requires_auth
def get_quarterly_bet():
    from datetime import date
    bet = load_json(QUARTERLY_BET_FILE) if QUARTERLY_BET_FILE.exists() else {}
    if not isinstance(bet, dict) or not bet:
        return jsonify({})
    quarter = bet.get("quarter", _current_quarter())
    days_left = (_quarter_end_date(quarter) - date.today()).days
    return jsonify({**bet, "days_left": max(days_left, 0), "is_current": quarter == _current_quarter()})


@app.route("/api/quarterly-bet", methods=["POST"])
@requires_auth
def set_quarterly_bet():
    import threading
    d = request.get_json()
    bet_text = d.get("bet", "").strip()
    if not bet_text:
        return jsonify({"error": "bet required"}), 400
    quarter = _current_quarter()
    entry = {
        "quarter": quarter,
        "bet": bet_text,
        "why": d.get("why", ""),
        "metric": d.get("metric", ""),
        "target": d.get("target", ""),
        "set_date": datetime.now().strftime("%Y-%m-%d"),
        "board_verdict": "",
        "outcome": None,
    }
    save_json(QUARTERLY_BET_FILE, entry)

    def _run():
        try:
            question = (
                f"Hariv just committed to ONE quarterly bet for {quarter}: \"{bet_text}\" "
                f"(why: {d.get('why', '')}, target metric: {d.get('metric', '')} = {d.get('target', '')}). "
                "Challenge this bet directly — is it the highest-leverage thing he could commit to this "
                "quarter, or is he avoiding something harder? Give a verdict: back it, or push back with "
                "what he should commit to instead. Under 200 words."
            )
            result = run_board(question)
            entry["board_verdict"] = result.get("synthesis", "")
            save_json(QUARTERLY_BET_FILE, entry)
        except Exception as e:
            print(f"[QUARTERLY BET] board challenge failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(entry)


# ── Decision Journal ───────────────────────────────────────────
@app.route("/api/decisions", methods=["GET"])
@requires_auth
def get_decisions():
    return jsonify(load_json(DECISIONS_FILE))


@app.route("/api/decisions", methods=["POST"])
@requires_auth
def add_decision():
    d = request.get_json()
    decisions = load_json(DECISIONS_FILE)
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": d.get("date", datetime.now().strftime("%Y-%m-%d")),
        "decision": d.get("decision", ""),
        "context": d.get("context", ""),
        "alternatives": d.get("alternatives", ""),
        "confidence": int(d.get("confidence", 7)),
        "session_id": d.get("session_id", ""),
        "review_date": d.get("review_date") or None,
        "outcome": None,
        "lesson": None,
        "outcome_date": None,
        "timestamp": datetime.now().isoformat()
    }
    decisions.append(entry)
    save_json(DECISIONS_FILE, decisions)
    # Also append to knowledge base
    kb = KB_PATH.read_text() if KB_PATH.exists() else "# Knowledge Base\n"
    kb_line = f"\n- [{entry['date']}] Decision: {entry['decision'][:120]} (confidence: {entry['confidence']}/10)"
    if "## Decisions" in kb:
        kb = kb.replace("## Decisions\n", f"## Decisions\n{kb_line}\n")
    else:
        kb += f"\n\n## Decisions\n{kb_line}\n"
    KB_PATH.write_text(kb)
    return jsonify(entry)


@app.route("/api/decisions/<decision_id>", methods=["PUT"])
@requires_auth
def update_decision(decision_id):
    decisions = load_json(DECISIONS_FILE)
    d = request.get_json()
    for dec in decisions:
        if dec["id"] == decision_id:
            dec.update({k: v for k, v in d.items() if k not in ("id", "timestamp")})
            if d.get("outcome") and not dec.get("outcome_date"):
                dec["outcome_date"] = datetime.now().strftime("%Y-%m-%d")
            break
    save_json(DECISIONS_FILE, decisions)
    return jsonify({"ok": True})


@app.route("/api/decisions/<decision_id>", methods=["DELETE"])
@requires_auth
def delete_decision(decision_id):
    decisions = [d for d in load_json(DECISIONS_FILE) if d["id"] != decision_id]
    save_json(DECISIONS_FILE, decisions)
    return jsonify({"ok": True})


# ── Personal CRM ───────────────────────────────────────────────
@app.route("/api/crm", methods=["GET"])
@requires_auth
def get_crm():
    contacts = load_json(CRM_FILE)
    # Sort by next action date (overdue first)
    contacts.sort(key=lambda c: (c.get("next_action_date") or "9999-99-99"))
    return jsonify(contacts)


@app.route("/api/crm", methods=["POST"])
@requires_auth
def add_contact():
    d = request.get_json()
    contacts = load_json(CRM_FILE)
    contact = {
        "id": str(uuid.uuid4())[:8],
        "name": d.get("name", ""),
        "role": d.get("role", ""),
        "company": d.get("company", ""),
        "relationship": d.get("relationship", ""),
        "last_contact": d.get("last_contact", datetime.now().strftime("%Y-%m-%d")),
        "next_action": d.get("next_action", ""),
        "next_action_date": d.get("next_action_date", ""),
        "notes": d.get("notes", ""),
        "phone": d.get("phone", ""),
        "timestamp": datetime.now().isoformat()
    }
    contacts.append(contact)
    save_json(CRM_FILE, contacts)
    return jsonify(contact)


@app.route("/api/crm/<contact_id>", methods=["PUT"])
@requires_auth
def update_contact(contact_id):
    contacts = load_json(CRM_FILE)
    d = request.get_json()
    for c in contacts:
        if c["id"] == contact_id:
            c.update({k: v for k, v in d.items() if k not in ("id", "timestamp")})
            break
    save_json(CRM_FILE, contacts)
    return jsonify({"ok": True})


@app.route("/api/crm/<contact_id>", methods=["DELETE"])
@requires_auth
def delete_contact(contact_id):
    contacts = [c for c in load_json(CRM_FILE) if c["id"] != contact_id]
    save_json(CRM_FILE, contacts)
    return jsonify({"ok": True})


# ── Pipeline & MRR ─────────────────────────────────────────────
PIPELINE_FILE = DATA_DIR / "pipeline.json"
PIPELINE_STAGES = ["lead", "contacted", "demo", "proposal", "won", "lost"]


@app.route("/api/pipeline", methods=["GET"])
@requires_auth
def get_pipeline():
    entries = load_json(PIPELINE_FILE)
    entries.sort(key=lambda p: (p.get("next_action_date") or "9999-99-99"))
    return jsonify(entries)


@app.route("/api/pipeline", methods=["POST"])
@requires_auth
def add_pipeline_entry():
    d = request.get_json()
    entries = load_json(PIPELINE_FILE)
    entry = {
        "id": str(uuid.uuid4())[:8],
        "name": d.get("name", ""),
        "stage": d.get("stage", "lead") if d.get("stage") in PIPELINE_STAGES else "lead",
        "mrr_value": float(d.get("mrr_value", 0) or 0),
        "setup_value": float(d.get("setup_value", 0) or 0),
        "source": d.get("source", ""),
        "next_action": d.get("next_action", ""),
        "next_action_date": d.get("next_action_date", ""),
        "notes": d.get("notes", ""),
        "created": datetime.now().strftime("%Y-%m-%d"),
        "updated": datetime.now().isoformat(),
        "won_date": datetime.now().strftime("%Y-%m-%d") if d.get("stage") == "won" else None,
        "lost_reason": None,
    }
    entries.append(entry)
    save_json(PIPELINE_FILE, entries)
    return jsonify(entry)


@app.route("/api/pipeline/<entry_id>", methods=["PUT"])
@requires_auth
def update_pipeline_entry(entry_id):
    entries = load_json(PIPELINE_FILE)
    d = request.get_json()
    for p in entries:
        if p["id"] == entry_id:
            new_stage = d.get("stage")
            if new_stage == "won" and p.get("stage") != "won":
                d["won_date"] = datetime.now().strftime("%Y-%m-%d")
            p.update({k: v for k, v in d.items() if k not in ("id", "created")})
            p["updated"] = datetime.now().isoformat()
            break
    save_json(PIPELINE_FILE, entries)
    return jsonify({"ok": True})


@app.route("/api/pipeline/<entry_id>", methods=["DELETE"])
@requires_auth
def delete_pipeline_entry(entry_id):
    entries = [p for p in load_json(PIPELINE_FILE) if p["id"] != entry_id]
    save_json(PIPELINE_FILE, entries)
    return jsonify({"ok": True})


@app.route("/api/pipeline/summary", methods=["GET"])
@requires_auth
def pipeline_summary():
    import gap_engine
    entries = load_json(PIPELINE_FILE)
    goals = load_json(GOALS_FILE)
    mrr_goal = next((g for g in goals if g.get("id") == "mrr-50k"), None)
    target = 50000
    deadline = mrr_goal.get("deadline") if mrr_goal else None
    if mrr_goal:
        try:
            target = float(str(mrr_goal.get("target", "50000")).replace(",", ""))
        except ValueError:
            pass
    gap = gap_engine.compute_mrr_gap(entries, target=target, deadline_iso=deadline)
    expenses = load_json(EXPENSES_FILE)
    runway = gap_engine.compute_runway(expenses, entries)
    return jsonify({**gap, "runway": runway})


# ── Conviction Training ────────────────────────────────────────
CONVICTION_COACH_SYSTEM = """You are a conviction trainer for Hariv — a 23yo Indian founder who wants to develop the ability to think independently and hold strong, uncommon beliefs under social pressure.

Your job is NOT to be encouraging. Your job is to:
1. Present widely-held beliefs in Hariv's domains (India SaaS, AI automation, manufacturing, founder psychology, money, career) — one at a time — and make him react
2. When he agrees with the consensus, push him to notice WHY and whether it's genuine or just absorbed from others
3. When he disagrees, push back hard — steelman the consensus view and force him to defend his position with specific reasoning
4. Help him notice his patterns: which topics he has genuine independent views on vs. which he just follows the crowd
5. After he articulates a strong contrarian position, extract the core thesis in one crisp sentence

DOMAINS to draw from: vertical SaaS for traditional industries, AI replacing white-collar work, India's startup ecosystem vs global, agency vs product, NYC vs India as a founder base, timing on AI waves, what makes someone a billionaire vs. a millionaire.

START with a specific, widely-held belief to react to. Keep responses SHORT (2-3 sentences max). One challenge or question per turn. No lectures."""


@app.route("/api/conviction-train/chat", methods=["POST"])
@requires_auth
def conviction_train_chat():
    """Conviction training conversation — builds the muscle of independent thinking."""
    import asyncio as _asyncio
    d = request.get_json()
    message = d.get("message", "").strip()
    is_start = d.get("start", False)

    chat_history = load_json(CONVICTION_CHAT_FILE) if not is_start else []

    # Shared live context — same picture of Hariv the board and coaches see
    system = CONVICTION_COACH_SYSTEM
    try:
        live_ctx = _build_live_context()
        if live_ctx:
            system += f"\n\n--- WHAT YOU ALREADY KNOW (from the rest of his command center) ---\n{live_ctx}\n--- Use this to pick beliefs that actually matter to his current situation. Don't recite it. ---"
    except Exception:
        pass

    messages = [{"role": "system", "content": system}]
    for h in chat_history[-20:]:
        messages.append({"role": "user", "content": h["user"]})
        messages.append({"role": "assistant", "content": h["coach"]})

    if not message and is_start:
        message = "Start a conviction training session. Give me a belief to react to."
    messages.append({"role": "user", "content": message})

    try:
        coach_reply = _asyncio.run(call_llm(
            messages, tier="heavy", max_tokens=300, temperature=0.9
        )).strip()
        chat_history.append({"user": message, "coach": coach_reply, "timestamp": datetime.now().isoformat()})
        save_json(CONVICTION_CHAT_FILE, chat_history)
        return jsonify({"response": coach_reply, "turn": len(chat_history)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/conviction-train/history", methods=["GET"])
@requires_auth
def conviction_train_history():
    return jsonify(load_json(CONVICTION_CHAT_FILE))


@app.route("/api/conviction-train/extract", methods=["POST"])
@requires_auth
def conviction_train_extract():
    """Extract strong convictions from the conversation and store them."""
    import asyncio as _asyncio
    chat_history = load_json(CONVICTION_CHAT_FILE)
    if len(chat_history) < 3:
        return jsonify({"error": "Have more of a conversation first"}), 400

    convo = "\n".join([f"Hariv: {h['user']}\nCoach: {h['coach']}" for h in chat_history[-20:]])

    prompt = f"""From this conviction training conversation, extract any genuinely contrarian beliefs that Hariv expressed and defended with his own reasoning (not just agreed with the coach).

Conversation:
{convo}

Return a JSON array. Each item: {{"thesis": "...", "category": "...", "strength": 1-10}}
Only include beliefs where Hariv pushed back against consensus or defended a non-obvious position. If none, return [].
Return ONLY valid JSON, no explanation."""

    try:
        raw = _asyncio.run(call_llm(
            [{"role": "user", "content": prompt}],
            tier="fast", max_tokens=500, temperature=0.2
        )).strip()
        import json as _json, re as _re
        match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        extracted = _json.loads(match.group()) if match else []

        convictions = load_json(CONVICTIONS_FILE)
        added = []
        for e in extracted:
            entry = {
                "id": str(uuid.uuid4())[:8],
                "date": date.today().isoformat(),
                "thesis": e.get("thesis", ""),
                "crowd_view": "",
                "your_edge": "Emerged from conviction training",
                "category": e.get("category", "Business"),
                "strength": int(e.get("strength", 7)),
                "status": "active",
                "board_response": "",
                "source": "training"
            }
            convictions.append(entry)
            added.append(entry)
        save_json(CONVICTIONS_FILE, convictions)
        return jsonify({"extracted": added, "count": len(added)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/convictions", methods=["GET"])
@requires_auth
def get_convictions():
    return jsonify(load_json(CONVICTIONS_FILE))


@app.route("/api/convictions/<conviction_id>", methods=["PUT"])
@requires_auth
def update_conviction(conviction_id):
    d = request.get_json()
    convictions = load_json(CONVICTIONS_FILE)
    updated = None
    for c in convictions:
        if c["id"] == conviction_id:
            old_status = c.get("status", "active")
            c.update({k: v for k, v in d.items() if k != "id"})
            updated = c
            # When a conviction is validated or invalidated, append to KB
            new_status = c.get("status", "active")
            if new_status in ("validated", "invalidated") and old_status not in ("validated", "invalidated"):
                try:
                    kb = KB_PATH.read_text() if KB_PATH.exists() else "# Knowledge Base\n"
                    entry = (f"\n- [{datetime.now().strftime('%Y-%m-%d')}] Conviction [{new_status.upper()}]: "
                             f"\"{c.get('thesis', '')[:120]}\" — "
                             f"held since {c.get('date', '')[:10]}. "
                             f"(source: conviction tracker)")
                    if "## Validated Convictions" in kb:
                        kb = kb.replace("## Validated Convictions\n",
                                        f"## Validated Convictions\n{entry}\n")
                    else:
                        kb += f"\n\n## Validated Convictions\n{entry}\n"
                    KB_PATH.write_text(kb)
                except Exception:
                    pass
            break
    save_json(CONVICTIONS_FILE, convictions)
    return jsonify(updated or {"ok": True})


@app.route("/api/convictions/<conviction_id>", methods=["DELETE"])
@requires_auth
def delete_conviction(conviction_id):
    convictions = [c for c in load_json(CONVICTIONS_FILE) if c["id"] != conviction_id]
    save_json(CONVICTIONS_FILE, convictions)
    return jsonify({"ok": True})


# ── Knowledge Base ─────────────────────────────────────────────
@app.route("/api/kb", methods=["GET"])
@requires_auth
def get_kb():
    content = KB_PATH.read_text() if KB_PATH.exists() else "# Knowledge Base\n\nNothing accumulated yet. Board sessions, decisions, and briefs auto-update this over time."
    return jsonify({"content": content})


@app.route("/api/kb", methods=["PUT"])
@requires_auth
def update_kb():
    d = request.get_json()
    KB_PATH.parent.mkdir(exist_ok=True)
    KB_PATH.write_text(d.get("content", ""))
    return jsonify({"ok": True})


# ── Weekly Retrospective ───────────────────────────────────────
async def _generate_retrospective():
    """Shared by the /api/retrospective route and the Telegram /retro command."""
    from datetime import date, timedelta

    today = date.today()
    monday = today - timedelta(days=today.weekday())

    sessions = []
    for f in sorted(Path(__file__).parent.joinpath("sessions").glob("*.json"), reverse=True):
        try:
            s = json.loads(f.read_text())
            s_date = date.fromisoformat(s["timestamp"][:10])
            if s_date >= monday:
                sessions.append(s)
        except Exception:
            pass

    time_logs = [l for l in load_json(TIME_FILE)
                 if l.get("date", "") >= monday.isoformat()]
    decisions = [d for d in load_json(DECISIONS_FILE)
                 if d.get("date", "") >= monday.isoformat()]
    vitals = load_json(DATA_DIR / "vitals.json")
    vitals_this_week = [v for v in vitals if v.get("date", "") >= monday.isoformat()]
    goals = load_json(GOALS_FILE)

    context = f"""Week of {monday.isoformat()} to {today.isoformat()}

GOALS (what matters):
{json.dumps([{"title": g["title"], "deadline": g.get("deadline", ""), "current": g.get("current", ""), "target": g.get("target", "")} for g in goals], indent=2)}

BOARD SESSIONS THIS WEEK ({len(sessions)}):
{chr(10).join(f"- Q: {s['question'][:100]} | Synthesis: {s['synthesis'][:200]}" for s in sessions[:5])}

TIME LOGGED ({sum(l.get('hours',0) for l in time_logs):.1f}h total):
{json.dumps([{"category": l["category"], "hours": l["hours"], "task": l.get("task", "")} for l in time_logs[:15]], indent=2)}

DECISIONS MADE ({len(decisions)}):
{json.dumps([{"decision": d["decision"][:100], "confidence": d.get("confidence", "?"), "has_outcome": bool(d.get("outcome"))} for d in decisions], indent=2)}

VITALS CHECK-INS ({len(vitals_this_week)}):
{chr(10).join(v.get("user", "")[:150] for v in vitals_this_week[:5])}"""

    prompt = f"""Write a weekly retrospective for Hariv — 23-year-old Indian founder, goal: NYC, billionaire. Be direct. No filler.

{context}

Structure:

## Week in Numbers
[Key metrics: hours logged, decisions made, sessions held, time by category]

## Did the work match the goals?
[Look at time allocation vs. stated goals. Was the week aligned or drifted? Be blunt.]

## What moved forward
[Concrete wins — decisions made, work completed, clarity gained]

## What didn't move and why
[Honest diagnosis — avoidance, blockers, distractions]

## The pattern this week
[One behavioral pattern you can see from this data that Hariv may not see]

## Next week's ONE priority
[Based on goals and what's blocking them — single highest-leverage focus]

Be specific. Reference actual numbers and decisions from the data. Under 400 words."""

    result = await call_llm(
        [{"role": "user", "content": prompt}],
        tier="heavy", max_tokens=2800, temperature=0.7, timeout=150
    )
    retro_path = BRIEFS_DIR / f"retro_{today.isoformat()}.md"
    retro_path.write_text(f"# Weekly Retrospective — {monday.isoformat()} to {today.isoformat()}\n\n{result}")
    return result


@app.route("/api/retrospective", methods=["POST"])
@requires_auth
def retrospective():
    import asyncio as _asyncio
    try:
        result = _asyncio.run(_generate_retrospective())
        return jsonify({"retro": result})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── Opportunity Scanner ────────────────────────────────────────
async def _generate_opportunity_scan():
    """Shared by the /api/scan route and the Telegram /scan command."""
    from tools.daily_brief import fetch_hn_stories, fetch_reddit_posts_with_comments

    SCAN_TOPICS = [
        "vertical SaaS India", "manufacturing software India", "ERP automation India",
        "AI startup funding India", "SaaS Series A India", "no-code automation India",
        "AI disrupting enterprise", "LLM replacing software", "agentic workflows",
        "rental management software", "property management SaaS",
        "AI tools for founders", "solopreneur AI stack",
        "B2B SaaS go-to-market India", "outreach automation"
    ]

    hn = fetch_hn_stories(50)
    reddit = []
    for sub in ["SaaS", "startups", "indianstartups", "entrepreneur"]:
        reddit.extend(fetch_reddit_posts_with_comments(sub, 8))

    all_stories = hn + reddit
    relevant = []
    for s in all_stories:
        combined = (s["title"] + " " + s.get("selftext", "")).lower()
        if any(t.lower() in combined for t in SCAN_TOPICS):
            relevant.append(s)

    relevant.sort(key=lambda x: x["score"], reverse=True)
    relevant = relevant[:8]

    if not relevant:
        return "No specific opportunities surfaced today. Check again tomorrow."

    stories_text = "\n\n".join(
        f"- {s['title']} ({s['source']}, score:{s['score']})\n  URL: {s['url']}"
        for s in relevant
    )

    prompt = f"""Hariv is a 23-year-old Indian founder building:
1. Automation agency (current client: Shakti Electricals, a transformer manufacturer)
2. TENANTZA — rental management SaaS
3. Planning: vertical AI SaaS for Indian manufacturing/SMB sector
Goal: ₹50k MRR → NYC → billionaire

Scan these stories for opportunities specific to him:
{stories_text}

Write a SHORT opportunity scan report:

## Businesses or sectors he could target
[Specific companies, verticals, or sectors from these stories that match his automation agency offer]

## Market signals to act on
[Trends or news that suggests his planned vertical SaaS has tailwind — or headwind]

## One concrete cold outreach this week
[Based on what you see here, who specifically should he reach out to and why? Include the pitch angle.]

## Threat watch
[Anything here that could make his plan obsolete faster than expected]

Be specific. Under 300 words."""

    return await call_llm(
        [{"role": "user", "content": prompt}],
        tier="heavy", max_tokens=2400, temperature=0.7, timeout=150
    )


@app.route("/api/scan", methods=["POST"])
@requires_auth
def opportunity_scan():
    import asyncio as _asyncio
    try:
        result = _asyncio.run(_generate_opportunity_scan())
        return jsonify({"scan": result})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── Home / Daily OS ───────────────────────────────────────────
@app.route("/api/home", methods=["GET"])
@requires_auth
def home_data():
    from datetime import date, timedelta
    today = date.today()

    def days_to(iso):
        return max((date.fromisoformat(iso) - today).days, 0)

    logs = load_json(DAILY_LOG_FILE)
    today_log = next((l for l in reversed(logs) if l.get("date") == today.isoformat()), None)
    yesterday = (today - timedelta(days=1)).isoformat()
    yesterday_log = next((l for l in reversed(logs) if l.get("date") == yesterday), None)

    open_decs = len([d for d in load_json(DECISIONS_FILE) if not d.get("outcome")])
    crm = load_json(CRM_FILE)
    overdue_follow = len([c for c in crm if c.get("next_action_date", "9") < today.isoformat() and c.get("next_action")])

    # Weekly momentum score
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_time = [l for l in load_json(TIME_FILE) if l.get("date", "") >= week_start]
    rev_hrs = sum(float(l.get("hours", 0)) for l in week_time if l.get("category", "") in ["Agency Work", "TENANTZA SaaS"])
    job_hrs = sum(float(l.get("hours", 0)) for l in week_time if "Job" in l.get("category", ""))
    total_hrs = sum(float(l.get("hours", 0)) for l in week_time)
    momentum = round(rev_hrs / total_hrs * 100) if total_hrs > 0 else None

    # Log streak
    streak = 0
    check = today
    for _ in range(30):
        has = any(l.get("date") == check.isoformat() for l in logs)
        if has:
            streak += 1
            check = check - timedelta(days=1)
        else:
            break

    # Board recommendations pending
    pending_recs = len([r for r in _load_recs() if r.get("status") == "pending"])

    # Goal velocity — is measurable progress happening on each active goal?
    goals = load_json(GOALS_FILE)
    two_weeks_ago = (today - timedelta(days=14)).isoformat()
    recent_time = [l for l in load_json(TIME_FILE) if l.get("date", "") >= two_weeks_ago]
    recent_decs = [d for d in load_json(DECISIONS_FILE) if d.get("date", "") >= two_weeks_ago]
    recent_log_text = " ".join(
        (l.get("did", "") + " " + l.get("tomorrow", "")).lower()
        for l in logs if l.get("date", "") >= two_weeks_ago
    )
    goal_velocity = {}
    for g in goals:
        if g.get("status") != "active":
            continue
        gid = g.get("id", g.get("title", ""))
        keywords = [w for w in re.split(r"\W+", g.get("title", "").lower()) if len(w) > 3]
        hours = sum(float(l.get("hours", 0)) for l in recent_time if l.get("goal_id") == g.get("id"))
        mentions = sum(recent_log_text.count(k) for k in keywords)
        decisions = sum(
            1 for d in recent_decs
            if any(k in d.get("decision", "").lower() for k in keywords)
        )
        signal = hours + mentions * 0.5 + decisions
        goal_velocity[gid] = {
            "title": g.get("title", ""),
            "hours_14d": round(hours, 1),
            "log_mentions_14d": mentions,
            "decisions_14d": decisions,
            "velocity": "moving" if signal >= 3 else ("slow" if signal > 0 else "stalled"),
        }

    # Pipeline / MRR gap + runway (Tier 4)
    import gap_engine
    pipeline = load_json(PIPELINE_FILE) if PIPELINE_FILE.exists() else []
    mrr_goal = next((g for g in goals if g.get("id") == "mrr-50k"), None)
    target = 50000
    mrr_deadline = mrr_goal.get("deadline") if mrr_goal else None
    if mrr_goal:
        try:
            target = float(str(mrr_goal.get("target", "50000")).replace(",", ""))
        except ValueError:
            pass
    mrr_gap = gap_engine.compute_mrr_gap(pipeline, target=target, deadline_iso=mrr_deadline)
    runway = gap_engine.compute_runway(load_json(EXPENSES_FILE), pipeline)

    # Commitment scoreboard (Tier 5a)
    commitment_scoreboard = gap_engine.compute_commitment_scoreboard(_load_recs())
    commitment_scoreboard.pop("broken_items", None)  # keep /api/home light; full list via /api/recommendations

    # Decision calibration (Tier 5b)
    calibration = gap_engine.compute_decision_calibration(load_json(DECISIONS_FILE))

    return jsonify({
        "today": today.isoformat(),
        "mode": "morning" if datetime.now().hour < 14 else "evening",
        "hour": datetime.now().hour,
        "countdowns": {k: {"days": days_to(v["target"]), **v} for k, v in NORTH_STAR.items()},
        "today_log": today_log,
        "yesterday_log": yesterday_log,
        "open_decisions": open_decs,
        "overdue_follow_ups": overdue_follow,
        "goals": load_json(GOALS_FILE)[:5],
        "momentum": momentum,
        "momentum_rev_hrs": round(rev_hrs, 1),
        "momentum_job_hrs": round(job_hrs, 1),
        "momentum_total_hrs": round(total_hrs, 1),
        "log_streak": streak,
        "pending_recs": pending_recs,
        "goal_velocity": goal_velocity,
        "mrr_gap": mrr_gap,
        "runway": runway,
        "commitment_scoreboard": commitment_scoreboard,
        "calibration": calibration,
    })


@app.route("/api/home/morning/clear", methods=["POST"])
@requires_auth
def clear_morning_cache():
    save_json(MORNING_CACHE_FILE, {"date": "none", "content": ""})
    return jsonify({"ok": True})


@app.route("/api/cron/morning", methods=["POST"])
@requires_localhost
def cron_morning_trigger():
    """Called by cron at 5:30am IST. Generates and caches morning brief."""
    import threading
    save_json(MORNING_CACHE_FILE, {"date": "none", "content": ""})
    def _run():
        try:
            _generate_morning_brief()
        except Exception as e:
            print(f"[CRON] Morning brief failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Morning brief generation started"})


@app.route("/api/cron/intel", methods=["POST"])
@requires_localhost
def cron_intel_trigger():
    """Called by cron at 5:00am IST. Skips if today's brief already exists (idempotent)."""
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    brief_file = BRIEFS_DIR / f"{today}.md"
    if brief_file.exists() and brief_file.stat().st_size > 1000:
        return jsonify({"ok": True, "message": "Brief already exists for today", "skipped": True})
    import threading
    def _run():
        try:
            result = asyncio.run(run_daily_brief_async())
            print(f"[CRON] Intel brief generated — {len(result)} chars")
        except Exception as e:
            print(f"[CRON] Intel brief failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Brief generation started in background"})


@app.route("/api/cron/weekly", methods=["POST"])
@requires_localhost
def cron_weekly_trigger():
    """Called by cron Sunday evening. Generates weekly cross-module synthesis."""
    import threading
    def _run():
        try:
            _generate_weekly_synthesis()
        except Exception as e:
            print(f"[WEEKLY] synthesis failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Weekly synthesis started"})


@app.route("/api/cron/kb-consolidate", methods=["POST"])
@requires_localhost
def cron_kb_consolidate():
    """Monthly job: rewrite knowledge_base.md to merge duplicates / drop stale items."""
    import threading, asyncio as _asyncio
    from board import consolidate_knowledge_base
    def _run():
        try:
            _asyncio.run(consolidate_knowledge_base())
        except Exception as e:
            print(f"[KB CONSOLIDATE] failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "KB consolidation started"})


@app.route("/api/cron/quarter-end", methods=["POST"])
@requires_localhost
def cron_quarter_end():
    """1st of Jan/Apr/Jul/Oct: judge last quarter's bet honestly, then prompt for the next one."""
    import threading
    def _run():
        try:
            bet = load_json(QUARTERLY_BET_FILE) if QUARTERLY_BET_FILE.exists() else {}
            if isinstance(bet, dict) and bet.get("bet") and not bet.get("outcome"):
                question = (
                    f"Quarter {bet['quarter']} just ended. Hariv's one bet for the quarter was: "
                    f"\"{bet['bet']}\" (target: {bet.get('metric', '')} = {bet.get('target', '')}). "
                    "Judge honestly: did this pay off? What's the evidence for or against? "
                    "One direct paragraph — no hedging."
                )
                result = run_board(question)
                bet["outcome"] = result.get("synthesis", "")
                save_json(QUARTERLY_BET_FILE, bet)
                try:
                    from tools.telegram_bot import send as _tg_send
                    _tg_send(
                        f"*Quarter {bet['quarter']} bet retro:*\n\"{bet['bet']}\"\n\n{bet['outcome']}\n\n"
                        "What's your ONE bet for the new quarter? Set it on the dashboard."
                    )
                except Exception as e:
                    print(f"[QUARTER-END] telegram push failed: {e}")
        except Exception as e:
            print(f"[QUARTER-END] failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Quarter-end retro started"})


@app.route("/api/cron/auto-board", methods=["POST"])
@requires_localhost
def cron_auto_board():
    """
    Sunday auto-board: the board convenes on its own over the week's data —
    logs, pipeline movement, gap engine output, broken commitments — and
    pushes a verdict + next week's priority via Telegram. No prompt needed
    from Hariv; this is the board reaching out instead of waiting to be asked.
    """
    import threading
    def _run():
        try:
            gap = _mrr_gap_for_prompt()
            scoreboard = _commitment_scoreboard_for_prompt()
            bet = load_json(QUARTERLY_BET_FILE) if QUARTERLY_BET_FILE.exists() else {}
            bet_line = ""
            if isinstance(bet, dict) and bet.get("bet") and bet.get("quarter") == _current_quarter():
                bet_line = f" Quarterly bet in progress: \"{bet['bet']}\" — weigh this week's priority against whether it moves that bet forward."
            question = (
                "It's Sunday. Review this week on your own initiative: what happened in the pipeline, "
                "what commitments were kept or broken, and what the MRR gap trajectory looks like. "
                f"Context: {gap} {scoreboard}{bet_line} "
                "Give a 5-bullet verdict on the week and name ONE priority for next week — the single "
                "highest-leverage thing, with a concrete deadline."
            )
            # run_board() already wraps its own asyncio.run() internally — calling it directly
            # here (this fired inside a background thread with no event loop, so that's safe)
            # instead of double-wrapping in another asyncio.run(), which previously raised
            # "a coroutine was expected" and silently killed the Sunday push every week.
            result = run_board(question)
            try:
                from tools.telegram_bot import send as _tg_send
                lines = [f"*Sunday Board Verdict*"]
                for r in result.get("responses", []):
                    lines.append(f"\n*{r['name']}:* {r['response'][:600]}")
                if result.get("synthesis"):
                    lines.append(f"\n*Synthesis:* {result['synthesis']}")
                _tg_send("\n".join(lines))
            except Exception as e:
                print(f"[AUTO-BOARD] telegram push failed: {e}")
        except Exception as e:
            print(f"[AUTO-BOARD] failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Sunday auto-board started"})


def _mrr_gap_for_prompt():
    import gap_engine
    pipeline = load_json(PIPELINE_FILE) if PIPELINE_FILE.exists() else []
    goals = load_json(GOALS_FILE)
    mrr_goal = next((g for g in goals if g.get("id") == "mrr-50k"), None)
    target = 50000
    deadline = mrr_goal.get("deadline") if mrr_goal else None
    if mrr_goal:
        try:
            target = float(str(mrr_goal.get("target", "50000")).replace(",", ""))
        except ValueError:
            pass
    gap = gap_engine.compute_mrr_gap(pipeline, target=target, deadline_iso=deadline)
    return f"MRR: current ₹{gap['current_mrr']:,.0f}, gap ₹{gap['gap']:,.0f}, {gap['months_left']}mo left."


def _commitment_scoreboard_for_prompt():
    import gap_engine
    scoreboard = gap_engine.compute_commitment_scoreboard(_load_recs())
    return f"Commitments: {scoreboard['kept']} kept, {scoreboard['broken']} broken."


@app.route("/api/weekly", methods=["GET"])
@requires_auth
def get_weekly_synthesis():
    """Return the latest weekly synthesis."""
    files = sorted(BRIEFS_DIR.glob("weekly_*.md"), reverse=True)
    if not files:
        return jsonify({"content": None, "date": None})
    f = files[0]
    return jsonify({"content": f.read_text(), "date": f.stem.replace("weekly_", "")})


WEEKLY_PRIORITY_FILE = DATA_DIR / "weekly_priority.json"


@app.route("/api/weekly/priority", methods=["GET"])
@requires_auth
def get_weekly_priority():
    """The confirmed ONE priority for the current week (part of the Sunday review ritual)."""
    d = load_json(WEEKLY_PRIORITY_FILE) if WEEKLY_PRIORITY_FILE.exists() else {}
    return jsonify(d if isinstance(d, dict) else {})


@app.route("/api/weekly/priority", methods=["POST"])
@requires_auth
def set_weekly_priority():
    d = request.get_json()
    save_json(WEEKLY_PRIORITY_FILE, {
        "priority": d.get("priority", ""),
        "week_of": (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d"),
        "updated": datetime.now().isoformat(),
    })
    return jsonify({"ok": True})


def _weekly_wrapped_stats():
    """
    Deterministic week-over-week numbers — computed in code, not by the LLM, so the
    weekly brief opens with a stats block that's always correct even if the model
    narrative above/below it hallucinates a number. The "Rosebud/Mindsera weekly
    wrapped" pattern applied to business + accountability metrics instead of mood.
    """
    import gap_engine
    from datetime import date, timedelta
    today = date.today()
    week_start = today - timedelta(days=7)
    prev_week_start = today - timedelta(days=14)

    logs = load_json(DAILY_LOG_FILE)
    week_logs = [l for l in logs if l.get("date", "") >= week_start.isoformat()]
    prev_logs = [l for l in logs if prev_week_start.isoformat() <= l.get("date", "") < week_start.isoformat()]

    def _avg_energy(entries):
        vals = [float(l["energy"]) for l in entries if l.get("energy") not in (None, "")]
        return round(sum(vals) / len(vals), 1) if vals else None

    energy_this = _avg_energy(week_logs)
    energy_prev = _avg_energy(prev_logs)

    time_logs = load_json(TIME_FILE)
    week_time = [l for l in time_logs if l.get("date", "") >= week_start.isoformat()]
    prev_time = [l for l in time_logs if prev_week_start.isoformat() <= l.get("date", "") < week_start.isoformat()]

    def _by_cat(entries):
        by_cat = {}
        for l in entries:
            by_cat[l.get("category", "Other")] = by_cat.get(l.get("category", "Other"), 0) + float(l.get("hours", 0))
        return by_cat

    hours_this = _by_cat(week_time)
    hours_prev = _by_cat(prev_time)
    total_this = sum(hours_this.values())
    total_prev = sum(hours_prev.values())

    rev_this = sum(v for k, v in hours_this.items() if k in ("Agency Work", "TENANTZA SaaS"))
    rev_prev = sum(v for k, v in hours_prev.items() if k in ("Agency Work", "TENANTZA SaaS"))
    momentum_this = round(rev_this / total_this * 100) if total_this else None
    momentum_prev = round(rev_prev / total_prev * 100) if total_prev else None

    # Log streak (same definition as /api/home)
    streak = 0
    check = today
    for _ in range(30):
        if any(l.get("date") == check.isoformat() for l in logs):
            streak += 1
            check = check - timedelta(days=1)
        else:
            break

    pipeline = load_json(PIPELINE_FILE) if PIPELINE_FILE.exists() else []
    goals = load_json(GOALS_FILE)
    mrr_goal = next((g for g in goals if g.get("id") == "mrr-50k"), None)
    target = 50000
    deadline = mrr_goal.get("deadline") if mrr_goal else None
    if mrr_goal:
        try:
            target = float(str(mrr_goal.get("target", "50000")).replace(",", ""))
        except ValueError:
            pass
    mrr_gap = gap_engine.compute_mrr_gap(pipeline, target=target, deadline_iso=deadline)
    mrr_added_this_week = sum(
        float(p.get("mrr_value", 0) or 0) for p in pipeline
        if p.get("stage") == "won" and (p.get("won_date") or "") >= week_start.isoformat()
    )

    scoreboard = gap_engine.compute_commitment_scoreboard(_load_recs())
    calibration = gap_engine.compute_decision_calibration(load_json(DECISIONS_FILE))

    return {
        "week_start": week_start.isoformat(), "today": today.isoformat(),
        "energy_this": energy_this, "energy_prev": energy_prev,
        "hours_this": hours_this, "total_this": round(total_this, 1), "total_prev": round(total_prev, 1),
        "momentum_this": momentum_this, "momentum_prev": momentum_prev,
        "log_streak": streak,
        "mrr_gap": mrr_gap, "mrr_added_this_week": mrr_added_this_week,
        "commitment_scoreboard": scoreboard,
        "calibration": calibration,
    }


def _format_wrapped_block(s):
    lines = ["## Week in Numbers"]
    if s["energy_this"] is not None:
        delta = f" ({'+' if s['energy_this']-(s['energy_prev'] or s['energy_this'])>=0 else ''}{round(s['energy_this']-(s['energy_prev'] or s['energy_this']),1)} vs last week)" if s["energy_prev"] is not None else ""
        lines.append(f"- Energy: {s['energy_this']}/10 avg{delta}")
    if s["momentum_this"] is not None:
        delta = f" ({'+' if s['momentum_this']-(s['momentum_prev'] or s['momentum_this'])>=0 else ''}{s['momentum_this']-(s['momentum_prev'] or s['momentum_this'])}pp vs last week)" if s["momentum_prev"] is not None else ""
        lines.append(f"- Momentum: {s['momentum_this']}% revenue work{delta} — {s['total_this']}h logged")
    lines.append(f"- Log streak: {s['log_streak']} days")
    gap = s["mrr_gap"]
    lines.append(f"- MRR: ₹{gap['current_mrr']:,.0f}/₹{gap['target']:,.0f} (gap ₹{gap['gap']:,.0f})"
                 + (f", +₹{s['mrr_added_this_week']:,.0f} won this week" if s["mrr_added_this_week"] else ""))
    cs = s["commitment_scoreboard"]
    if cs["kept"] or cs["broken"]:
        lines.append(f"- Commitments: {cs['kept']} kept, {cs['broken']} broken ({cs['kept_ratio_pct']}%)")
    if s["calibration"].get("verdict"):
        lines.append(f"- Calibration: {s['calibration']['verdict']}")
    return "\n".join(lines)


def _generate_weekly_synthesis():
    """Pull from all 8 modules and synthesize the week's real patterns."""
    import asyncio as _asyncio
    from datetime import date, timedelta
    today = date.today()
    week_start = (today - timedelta(days=7)).isoformat()
    wrapped_stats = _weekly_wrapped_stats()
    wrapped_block = _format_wrapped_block(wrapped_stats)

    # --- Pull from every module ---

    # Daily logs
    logs = load_json(DAILY_LOG_FILE)
    week_logs = [l for l in logs if l.get("date", "") >= week_start]
    log_block = ""
    if week_logs:
        lines = [f"[{l['date']}] Did: {l.get('did','—')[:150]} | Blocked by: {l.get('didnt','—')[:100]} | Energy: {l.get('energy','—')}" for l in week_logs]
        log_block = "\n".join(lines)

    # Days not logged
    days_logged = len(set(l["date"] for l in week_logs))
    days_missing = 7 - days_logged

    # Board sessions this week
    board_sessions = []
    for f in sorted(Path(__file__).parent.joinpath("sessions").glob("*.json"), reverse=True):
        try:
            s = json.loads(f.read_text())
            if s.get("timestamp", "")[:10] >= week_start:
                board_sessions.append(f"[{s['timestamp'][:10]}] Q: {s['question'][:120]}\nSynthesis: {s.get('synthesis','')[:200]}")
        except Exception:
            pass

    # Wellness conversations this week
    vitals = load_json(VITALS_FILE)
    week_vitals = [v for v in vitals if v.get("date", "") >= week_start]
    vitals_block = ""
    if week_vitals:
        lines = [f"[{v['date']}] You: {v['user'][:200]}" for v in week_vitals]
        vitals_block = "\n".join(lines)

    # Open decisions
    open_decs = [d for d in load_json(DECISIONS_FILE) if not d.get("outcome")]
    dec_block = "\n".join([f"- {d.get('decision','')[:120]} (since {d.get('date','')})" for d in open_decs]) if open_decs else "None"

    # Goals with deadlines approaching
    goals = load_json(GOALS_FILE)
    active_goals = [g for g in goals if g.get("status") == "active"]
    goals_block = "\n".join([f"- {g['title']}: target {g.get('target','')} by {g.get('deadline','')}" for g in active_goals[:5]]) if active_goals else ""

    # Expenses this week
    expenses = load_json(EXPENSES_FILE)
    week_expenses = [e for e in expenses if e.get("date", "") >= week_start]
    total_spent = sum(e.get("amount", 0) for e in week_expenses)

    # Knowledge base (latest) — heavy tier, no cost to including much more of it
    kb = KB_PATH.read_text()[-6000:] if KB_PATH.exists() else ""

    prompt = f"""You are generating a weekly cross-module synthesis for someone's personal command center. You have access to everything that happened this week across all areas of their life.

WEEK OF {week_start} to {today.isoformat()}

{wrapped_block}
(These numbers are computed, not estimated — reference them exactly, don't restate different figures.)

DAILY LOGS ({days_logged}/7 days logged, {days_missing} days missing):
{log_block or 'No logs this week.'}

BOARD SESSIONS (strategic questions asked this week):
{chr(10).join(board_sessions) if board_sessions else 'None'}

PERSONAL CONVERSATIONS (wellness/vitals this week):
{vitals_block or 'No check-ins this week.'}

OPEN DECISIONS (unresolved, sitting there):
{dec_block}

ACTIVE GOALS:
{goals_block or 'None set'}

MONEY: Spent ₹{total_spent:,.0f} across {len(week_expenses)} transactions this week.

KNOWLEDGE BASE (recent learnings):
{kb}

---

Write a weekly synthesis. Be direct, specific, and honest. Structure:

## Week of {today.strftime('%B %d, %Y')}

**What actually happened this week**
[2-3 sentences. Not a summary — the real story of the week based on the data. What did they do, what blocked them, where did energy go.]

**The pattern underneath**
[The one thread connecting logs + decisions + what they talked about personally. What's really going on? What is the week actually revealing about where they are right now?]

**What's unresolved and needs attention**
[Open decisions, recurring blockers, emotional themes that came up multiple times. Be specific.]

**What the data says vs. what they probably think**
[Where does the objective record disagree with how they probably feel about the week? Be honest.]

**One thing to carry into next week**
[Not a list. One specific thing — an action, a mindset shift, or something to stop doing.]

Write as someone who has seen all the data and sees the full picture. Not harsh, not gentle — just clear."""

    narrative = _asyncio.run(call_llm(
        [{"role": "user", "content": prompt}],
        tier="heavy", max_tokens=3200, temperature=0.7
    )).strip()
    # Prepend the deterministic stats block verbatim rather than trusting the model to
    # restate it — guarantees the numbers Hariv sees first are always exactly right.
    content = f"{wrapped_block}\n\n---\n\n{narrative}"

    BRIEFS_DIR.mkdir(exist_ok=True)
    out_file = BRIEFS_DIR / f"weekly_{today.isoformat()}.md"
    out_file.write_text(content)
    print(f"[WEEKLY] Synthesis saved to {out_file}")
    return content


def _generate_morning_brief():
    """Generates (or serves cached) morning brief. Shared by the /api/home/morning route,
    the cron trigger, and the Telegram bot — one code path, no in-process HTTP hop."""
    import asyncio as _asyncio
    from datetime import date, timedelta

    today = date.today().isoformat()

    # Serve cache if today's brief is already generated
    cache = {}
    if MORNING_CACHE_FILE.exists():
        raw = load_json(MORNING_CACHE_FILE)
        cache = raw if isinstance(raw, dict) else {}
    if cache.get("date") == today and cache.get("content"):
        return {"brief": cache["content"], "cached": True}

    logs = load_json(DAILY_LOG_FILE)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_log = next((l for l in reversed(logs) if l.get("date") == yesterday), None)
    recent_logs = [l for l in logs if l.get("date", "") >= (date.today() - timedelta(days=7)).isoformat()]

    sessions_ctx = []
    for f in sorted(Path(__file__).parent.joinpath("sessions").glob("*.json"), reverse=True)[:3]:
        try:
            s = json.loads(f.read_text())
            sessions_ctx.append({"q": s["question"][:200], "synthesis": s["synthesis"][:1500]})
        except Exception:
            pass

    open_decs = [d for d in load_json(DECISIONS_FILE) if not d.get("outcome")]
    goals = load_json(GOALS_FILE)
    # Heavy tier, 1M context, per-request billing — several KB of history costs
    # nothing extra to include and gives the brief far more to actually work with.
    kb = KB_PATH.read_text()[-6000:] if KB_PATH.exists() else ""
    nyc_days = (date.fromisoformat("2028-01-01") - date.today()).days

    # Pull today's intel Board's Verdict (generated at 5am, we run at 5:30am)
    intel_signal = ""
    intel_file = BRIEFS_DIR / f"{today}.md"
    if intel_file.exists():
        raw = intel_file.read_text()
        if "## Board's Verdict" in raw:
            verdict = raw.split("## Board's Verdict")[-1].strip()[:3000]
            intel_signal = f"TODAY'S MACRO SIGNAL (from morning intel):\n{verdict}"

    # Unactioned board recommendations
    all_recs = load_json(RECS_FILE) if RECS_FILE.exists() else []
    unactioned_recs = [r for r in all_recs if r.get("status") == "pending"]

    # Accountability signals — computed before prompt
    days_since_log = 0
    if logs:
        last_log_date = date.fromisoformat(sorted([l["date"] for l in logs])[-1])
        days_since_log = (date.today() - last_log_date).days

    # Blocker streak — same thing blocking N days in a row
    blocker_streak = 0
    last_blocker = (yesterday_log or {}).get("didnt", "")
    if last_blocker:
        for l in sorted(recent_logs, key=lambda x: x["date"], reverse=True)[1:]:
            if last_blocker[:25].lower() in l.get("didnt", "").lower():
                blocker_streak += 1
            else:
                break

    # Time on job vs revenue work
    from datetime import timedelta as _td
    week_start = (date.today() - _td(days=date.today().weekday())).isoformat()
    week_time = [l for l in load_json(TIME_FILE) if l.get("date", "") >= week_start]
    job_hrs = sum(float(l.get("hours", 0)) for l in week_time if "Job" in l.get("category", ""))
    rev_hrs = sum(float(l.get("hours", 0)) for l in week_time if l.get("category", "") in ["Agency Work", "TENANTZA SaaS"])
    total_hrs = sum(float(l.get("hours", 0)) for l in week_time)
    momentum_pct = round((rev_hrs / total_hrs * 100)) if total_hrs > 0 else None

    # Accountability flags — these go first in the prompt
    flags = []
    if days_since_log >= 3:
        flags.append(f"⚠️ ACCOUNTABILITY: Hariv has not logged in {days_since_log} days. The system is blind. Call this out first.")
    elif days_since_log >= 2:
        flags.append(f"⚠️ ACCOUNTABILITY: No log for {days_since_log} days. Name it.")
    if blocker_streak >= 2:
        flags.append(f"⚠️ PATTERN: Same blocker for {blocker_streak+1} days in a row: '{last_blocker[:60]}'. This is a stuck pattern, not a daily issue. Escalate it.")
    if momentum_pct is not None and momentum_pct < 30 and total_hrs > 5:
        flags.append(f"⚠️ MOMENTUM: Only {momentum_pct}% of this week's time went to revenue work. {job_hrs:.1f}h on ₹25k job vs {rev_hrs:.1f}h on agency/SaaS. At this rate: NYC never.")

    accountability_block = "\n".join(flags) if flags else "No critical flags today."

    # Wellness brief — synthesized from full vitals conversation history
    wb = load_json(WELLNESS_BRIEF_FILE) if WELLNESS_BRIEF_FILE.exists() else {}
    wellness_ctx = wb.get("content", "No wellness data yet.") if wb else "No wellness data yet."
    wellness_date = wb.get("date", "") if wb else ""

    # Quarterly bet — one big bet, not five; the morning brief keeps it in view every day
    bet = load_json(QUARTERLY_BET_FILE) if QUARTERLY_BET_FILE.exists() else {}
    quarterly_bet_line = ""
    if isinstance(bet, dict) and bet.get("bet") and bet.get("quarter") == _current_quarter():
        bet_days_left = max((_quarter_end_date(bet["quarter"]) - date.today()).days, 0)
        quarterly_bet_line = f"QUARTERLY BET ({bet['quarter']}, {bet_days_left} days left): {bet['bet']}"

    ctx = f"""Hariv: 23yo Indian founder from Trichy. NYC by Jan 2028 ({nyc_days} days, age 26). $1B by 39-40. Current: automation agency (Shakti Electricals client), TENANTZA SaaS, ₹25k job to quit ASAP.

{quarterly_bet_line}

WELLNESS BRIEF (synthesized from all coaching conversations{f', last updated {wellness_date}' if wellness_date else ''}):
{wellness_ctx}

ACCOUNTABILITY FLAGS (address these directly, don't soften):
{accountability_block}

YESTERDAY ({yesterday}):
{json.dumps({"did": yesterday_log.get("did",""), "didnt": yesterday_log.get("didnt",""), "energy": yesterday_log.get("energy",""), "tomorrow": yesterday_log.get("tomorrow","")} if yesterday_log else "no entry logged", indent=2)}

LAST 7 DAYS:
{json.dumps([{"date":l["date"],"did":l.get("did","")[:60],"didnt":l.get("didnt","")[:60],"energy":l.get("energy","")} for l in recent_logs[-5:]], indent=2)}

THIS WEEK TIME: {rev_hrs:.1f}h revenue · {job_hrs:.1f}h job · {total_hrs:.1f}h total{f" · {momentum_pct}% momentum" if momentum_pct else ""}

OPEN DECISIONS: {len(open_decs)}
{json.dumps([d["decision"][:80] for d in open_decs[:3]], indent=2)}

RECENT BOARD SESSIONS: {json.dumps(sessions_ctx, indent=2)}

KNOWLEDGE BASE: {kb}

GOALS: {json.dumps([{"title":g["title"],"current":g.get("current",""),"target":g.get("target",""),"deadline":g.get("deadline","")} for g in goals[:4]], indent=2)}

{intel_signal}

{f"UNACTIONED BOARD RECS ({len(unactioned_recs)}): " + " | ".join(r.get("recommendation","")[:60] for r in unactioned_recs[:3]) if unactioned_recs else ""}"""

    async def _gen():
        prompt = f"""Write Hariv's morning brief. First thing he reads when he wakes up. Personal, direct, specific — not generic advice. If there are ACCOUNTABILITY FLAGS above, open with them. Don't soften them.

{ctx}

Structure:

## Good morning, Hariv. {nyc_days} days to NYC.

### 🚨 [Only include this section if there are accountability flags — be direct and uncomfortable]

### The Gap
[One paragraph. Concrete delta between where he is and where he needs to be for NYC. Specific numbers where possible.]

### Yesterday
[If logged: honest take on what he did vs. what moved him forward. If not logged: call it out.]

### Today's ONE Thing
[Single most important action today for closing the gap. Specific. Time-boxed.]

### The Board Is Watching
[ONE advisor's sharp 2-3 sentence take. Their actual voice. What they'd say right now.]

### 3 Signals
[3 bullets — AI/SaaS news, market signals, or patterns from his data. Under 35 words each.]

### Tonight
[One sentence. What to finish before logging today.]

Under 400 words. Direct. No filler."""

        return await call_llm(
            [{"role": "user", "content": prompt}],
            tier="heavy", max_tokens=3000, temperature=0.75, timeout=150
        )

    result = _asyncio.run(_gen())
    save_json(MORNING_CACHE_FILE, {"date": today, "content": result})
    # Also persist to briefs/ so morning briefs are never lost
    BRIEFS_DIR.mkdir(exist_ok=True)
    morning_file = BRIEFS_DIR / f"morning_{today}.md"
    if not morning_file.exists():
        morning_file.write_text(result)
    return {"brief": result, "cached": False}


@app.route("/api/home/morning", methods=["POST"])
@requires_auth
def morning_brief():
    """
    Backgrounds generation and lets the client poll instead of blocking the
    request — the context fed into this prompt (full KB, full wellness state,
    longer session history) got a lot bigger, and a free-tier model chewing
    through it plus the fallback chain routinely ran past the client's 30s
    fetch timeout and gunicorn's worker timeout, surfacing as "it just breaks."
    Same pattern the intel brief already uses (/api/brief + /api/brief/poll).
    """
    from datetime import date
    import threading
    today = date.today().isoformat()
    cache = load_json(MORNING_CACHE_FILE) if MORNING_CACHE_FILE.exists() else {}
    if isinstance(cache, dict) and cache.get("date") == today and cache.get("content"):
        return jsonify({"brief": cache["content"], "cached": True})

    def _run():
        try:
            _generate_morning_brief()
        except Exception as e:
            print(f"[MORNING] generation failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"generating": True})


@app.route("/api/home/morning/poll", methods=["GET"])
@requires_auth
def morning_brief_poll():
    from datetime import date
    today = date.today().isoformat()
    cache = load_json(MORNING_CACHE_FILE) if MORNING_CACHE_FILE.exists() else {}
    if isinstance(cache, dict) and cache.get("date") == today and cache.get("content"):
        return jsonify({"done": True, "brief": cache["content"]})
    return jsonify({"done": False})


EVENING_CACHE_FILE = DATA_DIR / "evening_cache.json"


def _generate_evening_brief():
    """Generates (or serves cached) evening brief. Shared by the /api/home/evening route,
    the Telegram evening ritual, and the cron push job."""
    import asyncio as _asyncio
    from datetime import date, timedelta

    today = date.today().isoformat()

    cache = {}
    if EVENING_CACHE_FILE.exists():
        raw = load_json(EVENING_CACHE_FILE)
        cache = raw if isinstance(raw, dict) else {}
    if cache.get("date") == today and cache.get("content"):
        return {"reflection": cache["content"], "cached": True}

    logs = load_json(DAILY_LOG_FILE)
    today_log = next((l for l in reversed(logs) if l.get("date") == today), None)
    recent_logs = [l for l in logs if l.get("date","") >= (date.today()-timedelta(days=7)).isoformat()]

    time_logs = [l for l in load_json(TIME_FILE) if l.get("date","") >= (date.today()-timedelta(days=7)).isoformat()]
    by_cat = {}
    for l in time_logs:
        by_cat[l["category"]] = by_cat.get(l["category"], 0) + float(l.get("hours", 0))

    nyc_days = (date.fromisoformat("2028-01-01") - date.today()).days
    wb = load_json(WELLNESS_BRIEF_FILE) if WELLNESS_BRIEF_FILE.exists() else {}
    wellness_ctx = wb.get("content", "No wellness data yet.") if wb else "No wellness data yet."
    wellness_date = wb.get("date", "") if wb else ""

    # Morning brief — what was the intention set this morning?
    morning_cache = load_json(MORNING_CACHE_FILE) if MORNING_CACHE_FILE.exists() else {}
    morning_content = ""
    if isinstance(morning_cache, dict) and morning_cache.get("date") == today and morning_cache.get("content"):
        morning_content = morning_cache["content"]

    # Decisions logged today
    today_decisions = [d for d in load_json(DECISIONS_FILE) if d.get("date") == today]

    # Board sessions today
    today_sessions = []
    for f in sorted(Path(__file__).parent.joinpath("sessions").glob("*.json"), reverse=True)[:5]:
        try:
            s = json.loads(f.read_text())
            if s.get("timestamp", "")[:10] == today:
                today_sessions.append(s["question"][:80])
        except Exception:
            pass

    ctx = f"""23yo founder. NYC in {nyc_days} days. $1B by 39-40. High pressure — job + agency + SaaS simultaneously.

{f"THIS MORNING'S BRIEF SET THIS INTENTION:{chr(10)}{morning_content}{chr(10)}" if morning_content else ""}
TODAY ({today}): {json.dumps({"did": today_log.get("did",""), "didnt": today_log.get("didnt",""), "energy": today_log.get("energy","")} if today_log else "not logged yet")}

{f"DECISIONS MADE TODAY: {json.dumps([d['decision'][:80] for d in today_decisions])}" if today_decisions else ""}
{f"BOARD CONSULTED TODAY ON: {json.dumps(today_sessions)}" if today_sessions else ""}

LAST 7 DAYS:
{json.dumps([{"date":l["date"],"did":l.get("did","")[:60],"didnt":l.get("didnt","")[:60],"energy":l.get("energy","")} for l in recent_logs[-7:]], indent=2)}

TIME THIS WEEK BY CATEGORY: {json.dumps(by_cat, indent=2)}

WELLNESS BRIEF (synthesized from all coaching conversations{f', updated {wellness_date}' if wellness_date else ''}):
{wellness_ctx}
Total logged: {sum(by_cat.values()):.1f}h"""

    async def _gen():
        prompt = f"""Write Hariv's evening reflection. He reads this before sleep. NOT news. High-level, honest, strategic. Make him think. Be direct. This feeds into tomorrow's morning brief — so name what needs to change.

{ctx}

Structure:

## Evening, Hariv.

### Did today earn its place?
[Honest assessment. If logged: did his work move him toward NYC? Be specific. If not logged: "You didn't log today. You can't improve what you don't measure — and right now you're flying blind."]

### The pattern I'm seeing this week
[Based on 7 days of data: what behavioral pattern is showing up? What is he consistently avoiding? What's draining his energy? Be specific and honest — this should sting a little if it's true.]

### What you're doing right
[1-2 specific things working well — reinforce these. Not generic. Reference actual data.]

### What needs to change tomorrow
[One specific behavioral change. Not a direction — a concrete action. Why this matters for NYC.]

### Sit with this tonight
[One question to think about before sleep. Something he's been avoiding answering. If he sat with it honestly, it would give him clarity on a real decision.]

Under 320 words. No filler. The quality of this determines the quality of tomorrow's morning brief."""

        return await call_llm(
            [{"role": "user", "content": prompt}],
            tier="heavy", max_tokens=2400, temperature=0.8, timeout=150
        )

    result = _asyncio.run(_gen())
    save_json(EVENING_CACHE_FILE, {"date": today, "content": result})
    BRIEFS_DIR.mkdir(exist_ok=True)
    evening_file = BRIEFS_DIR / f"evening_{today}.md"
    if not evening_file.exists():
        evening_file.write_text(result)
    return {"reflection": result, "cached": False}


@app.route("/api/home/evening", methods=["POST"])
@requires_auth
def evening_reflection():
    """Backgrounds generation + polling — same reasoning as morning_brief() above."""
    from datetime import date
    import threading
    today = date.today().isoformat()
    cache = load_json(EVENING_CACHE_FILE) if EVENING_CACHE_FILE.exists() else {}
    if isinstance(cache, dict) and cache.get("date") == today and cache.get("content"):
        return jsonify({"reflection": cache["content"], "cached": True})

    def _run():
        try:
            _generate_evening_brief()
        except Exception as e:
            print(f"[EVENING] generation failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"generating": True})


@app.route("/api/home/evening/poll", methods=["GET"])
@requires_auth
def evening_brief_poll():
    from datetime import date
    today = date.today().isoformat()
    cache = load_json(EVENING_CACHE_FILE) if EVENING_CACHE_FILE.exists() else {}
    if isinstance(cache, dict) and cache.get("date") == today and cache.get("content"):
        return jsonify({"done": True, "reflection": cache["content"]})
    return jsonify({"done": False})


@app.route("/api/daily-log", methods=["GET"])
@requires_auth
def get_daily_logs():
    return jsonify(load_json(DAILY_LOG_FILE))


@app.route("/api/daily-log/today", methods=["GET"])
@requires_auth
def get_today_log():
    logs = load_json(DAILY_LOG_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    return jsonify(next((l for l in reversed(logs) if l.get("date") == today), {}))


@app.route("/api/daily-log", methods=["POST"])
@requires_auth
def save_daily_log():
    d = request.get_json()
    logs = load_json(DAILY_LOG_FILE)
    today = datetime.now().strftime("%Y-%m-%d")

    existing_idx = next((i for i, l in enumerate(logs) if l.get("date") == today), None)
    entry = {
        "id": logs[existing_idx]["id"] if existing_idx is not None else str(uuid.uuid4())[:8],
        "date": today,
        "did": d.get("did", ""),        # What I did today
        "didnt": d.get("didnt", ""),    # What I didn't do / what blocked me
        "energy": int(d.get("energy", 7)),
        "tomorrow": d.get("tomorrow", ""),  # Plan for tomorrow
        "timestamp": datetime.now().isoformat()
    }

    if existing_idx is not None:
        logs[existing_idx] = entry
    else:
        logs.append(entry)

    save_json(DAILY_LOG_FILE, logs)
    save_json(MORNING_CACHE_FILE, {"date": "none", "content": ""})
    save_json(EVENING_CACHE_FILE, {"date": "none", "content": ""})

    # Async: extract pattern from this log and append to KB
    def _append_log_to_kb():
        try:
            recent = [l for l in logs[-7:]]
            blocked_streak = 0
            last_blocker = entry.get("didnt", "")
            if last_blocker:
                for l in reversed(recent[:-1]):
                    if last_blocker[:20].lower() in l.get("didnt", "").lower():
                        blocked_streak += 1
                    else:
                        break

            kb_path = KB_PATH
            kb_path.parent.mkdir(exist_ok=True)
            existing = kb_path.read_text() if kb_path.exists() else "# Knowledge Base\n"

            lines = []
            if entry.get("did"):
                lines.append(f"- [{today}] Log/Did: {entry['did'][:120]}")
            if entry.get("didnt"):
                streak_note = f" (blocked {blocked_streak+1} days in a row)" if blocked_streak >= 1 else ""
                lines.append(f"- [{today}] Log/Blocked: {entry['didnt'][:120]}{streak_note}")
            if entry.get("tomorrow"):
                lines.append(f"- [{today}] Log/Focus: {entry['tomorrow'][:100]}")
            if int(entry.get("energy", 7)) <= 4:
                lines.append(f"- [{today}] Log/Energy: Low energy day ({entry['energy']}/10)")

            if not lines:
                return

            insert = "\n".join(lines) + "\n"
            if "## Daily Log Patterns" in existing:
                existing = existing.replace("## Daily Log Patterns\n", f"## Daily Log Patterns\n{insert}")
            else:
                existing += f"\n\n## Daily Log Patterns\n{insert}"
            kb_path.write_text(existing)
        except Exception:
            pass

    import threading
    threading.Thread(target=_append_log_to_kb, daemon=True).start()

    return jsonify(entry)


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  COMMAND CENTER — DASHBOARD")
    print("  http://localhost:4000")
    print("="*50 + "\n")
    app.run(debug=False, port=4000)
