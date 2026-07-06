"""
Deterministic trajectory math — the WAT principle applied to the goal itself:
code computes the numbers, the LLM only narrates them. Every function here is
pure (no I/O, no API calls) so it's cheap to call from every brief/board session
and trivially testable.
"""

from datetime import date, datetime, timedelta


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def pipeline_mrr(pipeline, stage="won"):
    """Sum of mrr_value for entries in a given stage. stage=None sums everything."""
    return sum(
        float(p.get("mrr_value", 0) or 0)
        for p in pipeline
        if stage is None or p.get("stage") == stage
    )


def compute_mrr_gap(pipeline, target=50000, deadline_iso=None, today=None):
    """
    Core trajectory math for the ₹50k MRR goal.
    Returns: current MRR, gap, months left, required new MRR/month,
    pipeline value sitting in proposal/demo (not yet won), and a required
    close rate given open pipeline value vs the remaining gap.
    """
    today = today or date.today()
    current_mrr = pipeline_mrr(pipeline, stage="won")
    gap = max(target - current_mrr, 0)

    deadline = _parse_date(deadline_iso) if deadline_iso else None
    months_left = None
    required_per_month = None
    if deadline:
        days_left = (deadline - today).days
        months_left = round(days_left / 30.44, 1)
        if months_left > 0:
            required_per_month = round(gap / months_left)

    open_stages = ("contacted", "demo", "proposal")
    open_value = sum(
        float(p.get("mrr_value", 0) or 0)
        for p in pipeline
        if p.get("stage") in open_stages
    )
    proposal_value = pipeline_mrr(pipeline, stage="proposal")

    required_close_rate = None
    if gap > 0 and open_value > 0:
        required_close_rate = round(min(gap / open_value, 1.0) * 100)

    return {
        "current_mrr": current_mrr,
        "target": target,
        "gap": gap,
        "months_left": months_left,
        "required_new_mrr_per_month": required_per_month,
        "open_pipeline_value": open_value,
        "proposal_value": proposal_value,
        "required_close_rate_pct": required_close_rate,
    }


def compute_runway(expenses, pipeline, months_lookback=3):
    """
    Monthly burn (avg of last N months' expenses) vs monthly income (won MRR
    + any one-off setup fees logged this period). Returns runway in months
    if burn > income, else None (runway is infinite / growing).
    """
    today = date.today()
    cutoff = (today.replace(day=1) - timedelta(days=months_lookback * 31)).isoformat()
    recent_expenses = [e for e in expenses if e.get("date", "") >= cutoff]
    total_spent = sum(float(e.get("amount", 0) or 0) for e in recent_expenses)
    avg_monthly_burn = round(total_spent / months_lookback, 2) if recent_expenses else 0

    monthly_income = pipeline_mrr(pipeline, stage="won")

    net_monthly = monthly_income - avg_monthly_burn
    return {
        "avg_monthly_burn": avg_monthly_burn,
        "monthly_income": monthly_income,
        "net_monthly": round(net_monthly, 2),
        "trend": "growing" if net_monthly >= 0 else "burning",
    }


def compute_commitment_scoreboard(recommendations):
    """
    Kept vs broken ratio over accepted board commitments.
    A rec is "kept" if actioned before/at its implied deadline, "broken" if
    still pending and past deadline, "open" if pending and not yet due.
    """
    today = date.today()
    kept, broken, open_ = 0, 0, 0
    broken_items = []
    for r in recommendations:
        if r.get("status") == "actioned":
            kept += 1
            continue
        deadline = _parse_date(r.get("deadline") or r.get("date"))
        if deadline and deadline < today:
            broken += 1
            broken_items.append(r)
        else:
            open_ += 1
    total_scored = kept + broken
    ratio = round(kept / total_scored * 100) if total_scored else None
    return {
        "kept": kept, "broken": broken, "open": open_,
        "kept_ratio_pct": ratio, "broken_items": broken_items,
    }


def compute_decision_calibration(decisions):
    """
    For decisions with both a confidence score and a recorded outcome,
    compare stated confidence to actual success rate. Buckets: low (1-4),
    mid (5-7), high (8-10). Surfaces systematic over/under-confidence.
    """
    buckets = {"low": [], "mid": [], "high": []}
    for d in decisions:
        outcome = d.get("outcome")
        conf = d.get("confidence")
        if outcome is None or conf is None:
            continue
        try:
            conf = int(conf)
        except (TypeError, ValueError):
            continue
        succeeded = str(outcome).lower() in ("success", "worked", "good", "yes", "true")
        key = "low" if conf <= 4 else ("mid" if conf <= 7 else "high")
        buckets[key].append(succeeded)

    result = {}
    for key, outcomes in buckets.items():
        if outcomes:
            result[key] = {
                "n": len(outcomes),
                "success_rate_pct": round(sum(outcomes) / len(outcomes) * 100),
            }
    if result.get("high") and result["high"]["success_rate_pct"] < 60 and result["high"]["n"] >= 3:
        result["verdict"] = (
            f"Overconfident: {result['high']['n']} calls at 8+/10 confidence succeeded only "
            f"{result['high']['success_rate_pct']}% of the time."
        )
    return result
