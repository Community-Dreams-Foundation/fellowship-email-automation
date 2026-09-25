from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Load project .env (one level above /web) so the app has API keys when run via uvicorn.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .pipeline import CATEGORIES, process_email

logger = logging.getLogger("fellowship_console")

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="Fellowship Email Console", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
AUTO_REPLY_THRESHOLD = float(os.getenv("AUTO_REPLY_CONFIDENCE_THRESHOLD", "0.75"))
# Global kill-switch for template auto-replies. Auto-reply is otherwise driven per
# category by the Templates page (a category auto-replies if it has an enabled template).
AUTO_REPLY_DISABLED = (os.getenv("DISABLE_AUTO_REPLY") or "").strip().lower() == "true"
# When no template matches, the AI draft auto-sends only if its confidence clears this bar.
AI_AUTOSEND_CONFIDENCE = float(os.getenv("AI_AUTOSEND_CONFIDENCE", "0.90"))
API_KEY = (os.getenv("APP_API_KEY") or "").strip()

# The CDF FAQ page (which also hosts the "CDF Ally" chatbot) — appended to every reply.
# Env-configurable so the URL can be changed without a code change.
FAQ_URL = (os.getenv("FAQ_URL") or "https://faq.cdreamstream.org").strip()
_FOOTER_MARKER = "— — —"  # sentinel so the footer is never added twice


def _compose_footer(auto_sent: bool, topic: str | None) -> str:
    """Build the reply footer. Auto-sent replies carry an explicit 'automated' notice so
    the recipient is never misled into thinking a person wrote it."""
    lines = [_FOOTER_MARKER]
    if auto_sent:
        lines.append(
            f"This is an automated email. Please check out our FAQ page or ask our chatbot (CDF Ally): {FAQ_URL}"
        )
    else:
        lines.append(
            f"For more details, please check out our FAQ page or ask our chatbot (CDF Ally): {FAQ_URL}"
        )
    return "\n".join(lines)


def _with_footer(body: str, *, auto_sent: bool, topic: str | None) -> str:
    """Insert the FAQ/chatbot footer before the closing signature (idempotent)."""
    footer = _compose_footer(auto_sent, topic)
    if not body:
        return footer
    if _FOOTER_MARKER in body:
        return body
    trimmed = body.rstrip()
    for sign in ("Best regards,", "Sincerely,", "Warm regards,", "Best,", "Thanks,", "Thank you,"):
        idx = trimmed.rfind(sign)
        if idx != -1:
            before = trimmed[:idx].rstrip()
            after = trimmed[idx:]
            return f"{before}\n\n{footer}\n\n{after}"
    return f"{trimmed}\n\n{footer}"

# Auto-reply is held for human review ONLY for legal threats and genuine emergencies.
# Every other category (RFE, supervisor requests, high-risk topics, etc.) auto-sends —
# per the go-live decision to answer volunteers as fast as possible.
_AUTO_REPLY_BLOCK_PHRASES = (
    # Legal threats
    "lawsuit", "law suit", "attorney", "legal action", "take legal", "discrimination",
    "harassment", "retaliation", "grievance", "lawyer", "sue you", "suing", "litigation",
    "defamation", "wrongful termination", "take to court",
    # Medical / family emergencies
    "medical emergency", "family emergency", "hospitalized", "in the hospital",
    "passed away", "death in the family", "suicide", "self-harm", "self harm",
)


def _auto_reply_blocked(subject: str, body: str) -> str | None:
    """Return the blocking phrase if this email must go to human review, else None."""
    hay = f"{subject}\n{body}".lower()
    for phrase in _AUTO_REPLY_BLOCK_PHRASES:
        if phrase in hay:
            return phrase
    return None


# Senders we never process/reply to — matched as substrings of the From address.
# Covers no-reply / system mailers, Slack & platform notifications, delivery daemons.
_NO_REPLY_SENDER_PATTERNS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon", "postmaster", "bounce@", "bounces@",
    "slack.com", "slackbot", "via slack",
    "notifications@", "notification@", "notify@", "alerts@", "alert@",
    "calendar-notification", "drive-shares", "forms-receipts", "comments-noreply",
    "docs.google.com", "resource.calendar.google.com", "automated@", "auto-confirm",
    "mailchimp", "sendgrid.net", "mailgun", "github.com", "atlassian.net", "jira@",
    "zoom.us", "calendly.com", "linkedin.com", "newsletter@", "unsubscribe@",
)

# Subject lines that mark an automated / bounce message (Slack is NOT here on purpose —
# a real volunteer asking about a "Slack invite" must still be processed).
_AUTOMATED_SUBJECT_MARKERS = (
    "out of office", "automatic reply", "auto-reply", "auto reply", "autoreply",
    "undeliverable", "delivery status notification", "mail delivery failed",
    "returned mail", "failure notice", "read receipt",
)
CALLBACK_SECRET = (os.getenv("MAKE_CALLBACK_SECRET") or "").strip()

# Dashboard HTTP Basic Auth — set DASHBOARD_USER / DASHBOARD_PASSWORD to enable.
DASHBOARD_USER = (os.getenv("DASHBOARD_USER") or "hr").strip()
DASHBOARD_PASSWORD = (os.getenv("DASHBOARD_PASSWORD") or "").strip()

# Slack incoming webhook for new-case notifications. If unset, no-op.
SLACK_WEBHOOK_URL = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()

# Public-facing base URL (used in Slack notifications). Falls back to None — Slack
# message will still include the case_id, just without a clickable link.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")


def _require_api_key(request: Request) -> None:
    if not API_KEY:
        return
    provided = (request.headers.get("x-api-key") or "").strip()
    if not provided or not hmac.compare_digest(provided, API_KEY):
        raise HTTPException(401, "Invalid API key")


SESSION_COOKIE = "cdf_fellowship_session"
SESSION_SECRET = (os.getenv("SESSION_SECRET") or DASHBOARD_PASSWORD or "dev-secret").strip()


def _session_token(user: str) -> str:
    """HMAC-signed session token: <user>.<hex-mac>."""
    mac = hmac.new(SESSION_SECRET.encode(), user.encode(), hashlib.sha256).hexdigest()
    return f"{user}.{mac}"


def _verify_session(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    user, _, mac = token.rpartition(".")
    expected = hmac.new(SESSION_SECRET.encode(), user.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(mac, expected):
        return user
    return None


class _RedirectToLogin(Exception):
    def __init__(self, next_path: str):
        self.next_path = next_path


def _require_dashboard_auth(request: Request) -> None:
    """Cookie session gate. Raises _RedirectToLogin for HTML, 401 for API."""
    if not DASHBOARD_PASSWORD:
        return
    token = request.cookies.get(SESSION_COOKIE)
    if _verify_session(token):
        return
    raise _RedirectToLogin(request.url.path)


def _post_slack(text: str, blocks: list | None = None) -> None:
    """Best-effort Slack notification. Silently no-op if webhook not configured."""
    if not SLACK_WEBHOOK_URL:
        return
    try:
        payload: dict = {"text": text}
        if blocks:
            payload["blocks"] = blocks
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.status >= 300:
                logger.warning("Slack webhook returned %s", resp.status)
    except Exception as exc:
        logger.warning("Slack notification failed: %s", exc)


def _minutes_pending(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return 0.0


def _format_pending(mins: float) -> str:
    """Human-friendly pending label: '3 min', '1h 20m', '2d 4h'."""
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{int(mins)} min"
    if mins < 1440:
        h = int(mins // 60)
        m = int(mins % 60)
        return f"{h}h {m}m" if m else f"{h}h"
    d = int(mins // 1440)
    h = int((mins % 1440) // 60)
    return f"{d}d {h}h" if h else f"{d}d"


def _confidence_color(conf: float | None) -> str:
    if conf is None:
        return "gray"
    if conf >= 0.85:
        return "green"
    if conf >= 0.65:
        return "yellow"
    return "red"


def _risk_color(risk: str | None) -> str:
    return {"high": "red", "medium": "yellow"}.get((risk or "").lower(), "green")


def _aging_color_mins(mins: float) -> str:
    """Yellow at 4h+ pending, red at 24h+ pending."""
    if mins >= 1440:
        return "red"
    if mins >= 240:
        return "yellow"
    return "green"


def _enrich_case_row(row: dict) -> dict:
    """Add aging + color fields to a case dict for template rendering."""
    mins = _minutes_pending(row.get("created_at"))
    row["minutes_pending"] = int(mins)
    row["pending_label"] = _format_pending(mins)
    row["aging_color"] = _aging_color_mins(mins)
    row["confidence_color"] = _confidence_color(row.get("confidence"))
    row["risk_color"] = _risk_color(row.get("risk_level"))
    return row


@app.on_event("startup")
def startup():
    db.init_db()
    # Seed the docx-sourced standard auto-reply templates (idempotent).
    db.seed_templates_if_empty()
    # Seed demo cases only when SEED_DEMO=true is set. Default off in production.
    if (os.getenv("SEED_DEMO") or "").strip().lower() == "true":
        db.seed_demo_if_empty()


@app.exception_handler(_RedirectToLogin)
async def _redirect_to_login_handler(request: Request, exc: _RedirectToLogin):
    next_path = exc.next_path or "/"
    return RedirectResponse(url=f"/login?next={next_path}", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = ""):
    # If already signed in, send straight to dashboard.
    if _verify_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse(url=next or "/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "title": "Sign in", "next": next or "/", "error": error},
    )


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    user_ok = hmac.compare_digest(username.strip(), DASHBOARD_USER)
    pass_ok = hmac.compare_digest(password.strip(), DASHBOARD_PASSWORD or "")
    if not (DASHBOARD_PASSWORD and user_ok and pass_ok):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "title": "Sign in",
                "next": next or "/",
                "error": "Incorrect username or password.",
            },
            status_code=401,
        )
    token = _session_token(username.strip())
    response = RedirectResponse(url=next or "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 12,  # 12-hour session
        path="/",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/logout")
def logout_get():
    return logout()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    _require_dashboard_auth(request)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                  - __import__("datetime").timedelta(days=6)).isoformat()

    with db.get_conn() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status = 'pending_review'"
        ).fetchone()["c"]
        sent_total = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status IN ('approved', 'auto_sent')"
        ).fetchone()["c"]
        auto_sent_total = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status = 'auto_sent'"
        ).fetchone()["c"]
        received_today = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE created_at >= ?",
            (today_start,),
        ).fetchone()["c"]
        sent_today = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status IN ('approved', 'auto_sent') AND updated_at >= ?",
            (today_start,),
        ).fetchone()["c"]
        avg_conf = conn.execute(
            "SELECT AVG(confidence) AS v FROM cases WHERE status = 'pending_review'"
        ).fetchone()["v"] or 0.0
        high_risk_pending = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status = 'pending_review' AND risk_level = 'high'"
        ).fetchone()["c"]
        by_intent = conn.execute(
            """
            SELECT intent, COUNT(*) AS total
            FROM cases
            WHERE created_at >= ?
            GROUP BY intent
            ORDER BY total DESC
            """,
            (week_start,),
        ).fetchall()

        # Top 5 pending cases (priority queue: high-risk first, then oldest)
        priority = conn.execute(
            """
            SELECT id, subject, from_email, confidence, risk_level, created_at, intent
            FROM cases
            WHERE status = 'pending_review'
            ORDER BY
              CASE WHEN risk_level = 'high' THEN 0 WHEN risk_level = 'medium' THEN 1 ELSE 2 END,
              created_at ASC
            LIMIT 5
            """
        ).fetchall()

        # Last 5 sent (recently approved)
        recent_sent = conn.execute(
            """
            SELECT id, subject, from_email, intent, status, updated_at
            FROM cases
            WHERE status IN ('approved', 'auto_sent')
            ORDER BY updated_at DESC LIMIT 5
            """
        ).fetchall()

        # Last 7 days received counts (for sparkline / week trend)
        week_trend = conn.execute(
            """
            SELECT date(created_at) AS day, COUNT(*) AS c
            FROM cases
            WHERE created_at >= ?
            GROUP BY date(created_at)
            """,
            (week_start,),
        ).fetchall()

    # Build a 7-day series (zero-fill missing days)
    trend_map = {row["day"]: row["c"] for row in week_trend}
    from datetime import timedelta
    trend_days = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        trend_days.append({"day": d, "count": trend_map.get(d, 0), "label": (now - timedelta(days=i)).strftime("%a")})
    trend_max = max((t["count"] for t in trend_days), default=1) or 1

    priority_rows = [_enrich_case_row(dict(r)) for r in priority]
    sent_rows = [dict(r) for r in recent_sent]
    for r in sent_rows:
        r["sent_label"] = _format_pending(_minutes_pending(r.get("updated_at")))

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "Dashboard",
            "pending": pending,
            "sent_total": sent_total,
            "auto_sent_total": auto_sent_total,
            "received_today": received_today,
            "sent_today": sent_today,
            "avg_conf_pct": int(round(avg_conf * 100)) if avg_conf else 0,
            "high_risk_pending": high_risk_pending,
            "by_intent": [dict(r) for r in by_intent],
            "priority": priority_rows,
            "recent_sent": sent_rows,
            "trend_days": trend_days,
            "trend_max": trend_max,
        },
    )


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, mailbox, subject, from_email, thread_id, status, confidence, quality_score,
                   intent, recommended_action, risk_level, draft_body, retrieved_snippets, created_at
            FROM cases WHERE status = 'pending_review' ORDER BY created_at ASC
            """
        ).fetchall()
    cases = []
    for r in rows:
        d = _enrich_case_row(dict(r))
        try:
            d["snippets"] = json.loads(d.pop("retrieved_snippets") or "[]")
        except json.JSONDecodeError:
            d["snippets"] = []
        cases.append(d)
    return templates.TemplateResponse(
        "queue.html",
        {"request": request, "title": "Review queue", "cases": cases},
    )


@app.get("/case/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: int):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    c = _enrich_case_row(dict(row))
    try:
        c["snippets"] = json.loads(c.get("retrieved_snippets") or "[]")
    except json.JSONDecodeError:
        c["snippets"] = []
    return templates.TemplateResponse("case.html", {"request": request, "title": c["subject"], "case": c})


@app.post("/case/{case_id}/approve")
def approve_case(request: Request, case_id: int, actor: str = Form("hr@company.com"), note: str = Form("")):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        r = conn.execute("SELECT id, status FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        if r["status"] != "pending_review":
            raise HTTPException(400, "Only pending cases can be approved")
        now = db._utc_now()
        conn.execute(
            "UPDATE cases SET status = 'approved', updated_at = ? WHERE id = ?",
            (now, case_id),
        )
    db.audit(case_id, "approve_and_send", actor, {"note": note})
    _notify_make_case_approved(case_id)
    return RedirectResponse(url="/queue", status_code=303)


@app.post("/case/{case_id}/delete")
def delete_case(request: Request, case_id: int, actor: str = Form("hr@cdreams.org")):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        r = conn.execute("SELECT id, subject FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        subject = r["subject"]
        conn.execute("DELETE FROM audit_log WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    logger.info("case deleted: id=%d subject=%r by=%s", case_id, subject, actor)
    return RedirectResponse(url="/queue", status_code=303)


@app.post("/case/{case_id}/edit-draft")
def edit_draft(request: Request, case_id: int, body: str = Form(...), actor: str = Form("hr@company.com")):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        r = conn.execute("SELECT id, status FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        now = db._utc_now()
        conn.execute(
            "UPDATE cases SET draft_body = ?, updated_at = ? WHERE id = ?",
            (body, now, case_id),
        )
    db.audit(case_id, "edit_draft", actor, {"chars": len(body)})
    return RedirectResponse(url=f"/case/{case_id}", status_code=303)


@app.post("/case/{case_id}/feedback")
def feedback_case(
    request: Request,
    case_id: int,
    actor: str = Form("hr@company.com"),
    rating: str = Form(...),
    note: str = Form(""),
):
    _require_dashboard_auth(request)
    if rating not in {"great", "good", "needs_work", "wrong"}:
        raise HTTPException(400, "Invalid rating")
    with db.get_conn() as conn:
        r = conn.execute("SELECT id FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not r:
            raise HTTPException(404)
    db.audit(case_id, "feedback", actor, {"rating": rating, "note": note})
    return RedirectResponse(url=f"/case/{case_id}", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.case_id, a.action, a.actor, a.details, a.created_at, c.subject
            FROM audit_log a JOIN cases c ON c.id = a.case_id
            ORDER BY a.created_at DESC LIMIT 100
            """
        ).fetchall()
    events = []
    for r in rows:
        d = dict(r)
        try:
            d["details_obj"] = json.loads(d.get("details") or "{}")
        except json.JSONDecodeError:
            d["details_obj"] = {}
        events.append(d)
    return templates.TemplateResponse(
        "audit.html",
        {"request": request, "title": "Audit log", "events": events},
    )


@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM cases").fetchone()["c"]
        avg_conf = conn.execute("SELECT AVG(confidence) AS v FROM cases").fetchone()["v"] or 0.0
        avg_quality = conn.execute("SELECT AVG(quality_score) AS v FROM cases").fetchone()["v"] or 0.0
        auto = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status = 'auto_sent'"
        ).fetchone()["c"]
        draft = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status IN ('pending_review', 'approved')"
        ).fetchone()["c"]
        by_intent = conn.execute(
            """
            SELECT intent, COUNT(*) AS total, AVG(confidence) AS avg_conf, AVG(quality_score) AS avg_quality
            FROM cases
            GROUP BY intent
            ORDER BY total DESC
            """
        ).fetchall()
        feedback_events = conn.execute(
            "SELECT details FROM audit_log WHERE action = 'feedback'"
        ).fetchall()

    feedback_counts = {"great": 0, "good": 0, "needs_work": 0, "wrong": 0}
    for event in feedback_events:
        try:
            rating = (json.loads(event["details"] or "{}").get("rating") or "").strip()
        except json.JSONDecodeError:
            rating = ""
        if rating in feedback_counts:
            feedback_counts[rating] += 1

    total_actions = max(total, 1)
    auto_rate = auto / total_actions
    quality_badge = (
        "Policy Pro"
        if avg_quality >= 0.8
        else "Rising Analyst"
        if avg_quality >= 0.65
        else "Needs Tuning"
    )
    speed_badge = "Fast Resolver" if auto_rate >= 0.6 else "Careful Reviewer"

    return templates.TemplateResponse(
        "metrics.html",
        {
            "request": request,
            "title": "Metrics & Gamification",
            "total_cases": total,
            "avg_conf": avg_conf,
            "avg_quality": avg_quality,
            "auto": auto,
            "draft": draft,
            "auto_rate": auto_rate,
            "by_intent": [dict(r) for r in by_intent],
            "feedback": feedback_counts,
            "quality_badge": quality_badge,
            "speed_badge": speed_badge,
        },
    )


@app.get("/policies", response_class=HTMLResponse)
def policies(request: Request):
    _require_dashboard_auth(request)
    return templates.TemplateResponse("policies.html", {"request": request, "title": "Policies & knowledge"})


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    _require_dashboard_auth(request)
    return templates.TemplateResponse("setup.html", {"request": request, "title": "What we need from you"})


@app.get("/templates", response_class=HTMLResponse)
def templates_page(request: Request):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, category, title, body, trigger_phrases, auto_reply, updated_at FROM templates ORDER BY category, id"
        ).fetchall()
    items = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "templates.html",
        {
            "request": request,
            "title": "Auto-reply templates",
            "templates": items,
            "categories": CATEGORIES,
            "auto_reply_disabled": AUTO_REPLY_DISABLED,
        },
    )


@app.post("/templates/new")
def template_create(
    request: Request,
    category: str = Form(...),
    title: str = Form(...),
    body: str = Form(...),
    triggers: str = Form(""),
    auto_reply: str = Form(""),
):
    _require_dashboard_auth(request)
    auto = 1 if auto_reply in ("1", "true", "on", "yes") else 0
    now = db._utc_now()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO templates (category, title, body, trigger_phrases, auto_reply, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (category.strip(), title.strip(), body, triggers.strip(), auto, now),
        )
    return RedirectResponse(url="/templates", status_code=303)


@app.post("/templates/{template_id}/edit")
def template_edit(
    request: Request,
    template_id: int,
    title: str = Form(...),
    body: str = Form(...),
    category: str = Form(...),
    triggers: str = Form(""),
):
    _require_dashboard_auth(request)
    now = db._utc_now()
    with db.get_conn() as conn:
        r = conn.execute("SELECT id FROM templates WHERE id = ?", (template_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        conn.execute(
            "UPDATE templates SET title = ?, body = ?, category = ?, trigger_phrases = ?, updated_at = ? WHERE id = ?",
            (title.strip(), body, category.strip(), triggers.strip(), now, template_id),
        )
    return RedirectResponse(url="/templates", status_code=303)


@app.post("/templates/{template_id}/toggle")
def template_toggle(request: Request, template_id: int):
    _require_dashboard_auth(request)
    now = db._utc_now()
    with db.get_conn() as conn:
        r = conn.execute("SELECT auto_reply FROM templates WHERE id = ?", (template_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        new_val = 0 if r["auto_reply"] else 1
        conn.execute(
            "UPDATE templates SET auto_reply = ?, updated_at = ? WHERE id = ?",
            (new_val, now, template_id),
        )
    return RedirectResponse(url="/templates", status_code=303)


@app.post("/templates/{template_id}/delete")
def template_delete(request: Request, template_id: int):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    return RedirectResponse(url="/templates", status_code=303)


@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request):
    _require_dashboard_auth(request)
    with db.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM cases").fetchone()["c"]
        auto_total = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status = 'auto_sent'"
        ).fetchone()["c"]
        rows = conn.execute(
            """
            SELECT intent AS category,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'auto_sent' THEN 1 ELSE 0 END) AS auto_sent,
                   SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved
            FROM cases
            GROUP BY intent
            ORDER BY total DESC
            """
        ).fetchall()
        # Which categories currently have an auto-reply-enabled template.
        auto_cats = {
            r["category"]
            for r in conn.execute(
                "SELECT DISTINCT category FROM templates WHERE auto_reply = 1"
            ).fetchall()
        }

    max_total = max((r["total"] for r in rows), default=1) or 1
    cats = []
    for r in rows:
        d = dict(r)
        d["category"] = d["category"] or "HR-Inbox"
        d["auto_sent"] = d["auto_sent"] or 0
        d["pending"] = d["pending"] or 0
        d["approved"] = d["approved"] or 0
        d["bar_pct"] = int(round(d["total"] / max_total * 100))
        d["auto_rate"] = int(round((d["auto_sent"] / d["total"]) * 100)) if d["total"] else 0
        d["auto_enabled"] = d["category"] in auto_cats
        cats.append(d)

    auto_rate = int(round((auto_total / total) * 100)) if total else 0
    return templates.TemplateResponse(
        "categories.html",
        {
            "request": request,
            "title": "Category statistics",
            "total": total,
            "auto_total": auto_total,
            "auto_rate": auto_rate,
            "cats": cats,
        },
    )


def _notify_make_case_approved(case_id: int) -> None:
    """Best-effort callback for Make.com after HR approval."""
    callback_url = (os.getenv("MAKE_APPROVED_WEBHOOK_URL") or "").strip()
    if not callback_url:
        return

    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, external_id, mailbox, subject, from_email, thread_id, status, confidence, intent, draft_body, retrieved_snippets, updated_at
            FROM cases WHERE id = ?
            """,
            (case_id,),
        ).fetchone()
    if not row:
        return

    payload = dict(row)
    try:
        payload["retrieved_snippets"] = json.loads(payload.get("retrieved_snippets") or "[]")
    except json.JSONDecodeError:
        payload["retrieved_snippets"] = []

    req = urllib.request.Request(
        callback_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if CALLBACK_SECRET:
        digest = hmac.new(
            CALLBACK_SECRET.encode("utf-8"),
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        req.add_header("X-Callback-Signature", f"sha256={digest}")
    try:
        with urllib.request.urlopen(req, timeout=8):
            pass
        db.audit(case_id, "make_callback_sent", "system", {"url": callback_url})
    except Exception as exc:
        db.audit(case_id, "make_callback_failed", "system", {"error": str(exc)})


@app.post("/api/process-email")
async def api_process_email(request: Request):
    """
    End-to-end email processing for Make.com.
    Accepts JSON body OR form-data (multipart / urlencoded) so Make.com doesn't
    need to JSON-escape arbitrary email bodies.
    """
    _require_api_key(request)
    payload: dict | None = None

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = None

    if payload is None:
        try:
            form = await request.form()
            if form:
                payload = {k: (v if isinstance(v, str) else str(v)) for k, v in form.items()}
        except Exception:
            payload = None

    if payload is None:
        # Last attempt: parse raw bytes as JSON, regardless of content-type header.
        raw = await request.body()
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                payload = None

    if not isinstance(payload, dict) or not payload:
        raise HTTPException(400, "JSON or form body required")

    subject = payload.get("subject") or "(no subject)"
    from_email = payload.get("from") or payload.get("from_email") or "unknown@example.com"
    mailbox = payload.get("mailbox") or payload.get("mailbox_id") or ""
    thread_id = payload.get("thread_id") or ""
    body = payload.get("body") or payload.get("text") or payload.get("content") or ""
    top_k = int(payload.get("top_k") or 5)
    ext = payload.get("external_id") or f"process-{db._utc_now()}"

    # Only genuine human emails get processed. We skip our own address, no-reply / system
    # senders, Slack & other platform notifications, and auto-responders. This is a safety
    # net in addition to the Gmail-side filter in Make — nothing here should ever get a reply.
    fe = (from_email or "").strip().lower()
    mb = (mailbox or "").strip().lower()
    sub = (subject or "").strip().lower()
    skip_reason = None
    if fe and mb and fe == mb:
        skip_reason = "self_email"
    elif "humanresources@cdreams.org" in fe or "@cdreams.org" in fe:
        # Never reply to our own org addresses (self / internal automated senders).
        skip_reason = "internal_sender"
    elif any(p in fe for p in _NO_REPLY_SENDER_PATTERNS):
        skip_reason = "no_reply_sender"
    elif any(m in sub for m in _AUTOMATED_SUBJECT_MARKERS):
        skip_reason = "automated_subject"
    if skip_reason:
        return JSONResponse(
            {
                "ok": False,
                "skipped": True,
                "skip_reason": skip_reason,
                "recommended_action": "ignore",
                "mailbox": mailbox,
                "from": from_email,
                "subject": subject,
            },
            status_code=200,
        )

    # Template-first: hand the enabled templates to the pipeline so a trigger match
    # short-circuits before any Gemini call (cost-efficient). Gemini runs only on no-match.
    result = process_email(
        subject,
        from_email,
        body,
        templates=db.get_auto_reply_templates(),
        top_k=top_k,
        auto_reply_threshold=AUTO_REPLY_THRESHOLD,
    )
    now = db._utc_now()

    # Follow-up detection: if there's already a pending_review case on this thread,
    # update it in-place with the new draft instead of creating a duplicate.
    is_follow_up = False
    existing_case_id: int | None = None
    if thread_id:
        with db.get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM cases WHERE thread_id = ? AND status = 'pending_review' ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if existing:
                existing_case_id = int(existing["id"])
                is_follow_up = True

    # ---- Auto-reply decision -------------------------------------------------
    # A brand-new email auto-sends (any category) unless it's a legal threat / emergency
    # (auto_block_reason) or auto-reply is globally disabled, AND either:
    #   * the pipeline matched a template (generation_mode == "template"), OR
    #   * the Gemini fallback draft's confidence is >= AI_AUTOSEND_CONFIDENCE.
    # Everything else (no match + low confidence, legal/emergency, follow-ups) → review.
    is_template = result.generation_mode in ("template", "default_template")
    auto_template: dict | None = result.matched_template
    auto_match_score = result.match_score
    final_draft = result.draft_body
    final_status = "pending_review"
    final_action = result.recommended_action if not is_template else "queue_for_review"
    autosend_reason: str | None = None
    auto_block_reason = _auto_reply_blocked(subject, body)
    if not is_follow_up and not AUTO_REPLY_DISABLED and not auto_block_reason:
        if is_template:
            final_status = "auto_sent"
            final_action = "auto_reply"
            autosend_reason = "template_match"
        elif result.confidence >= AI_AUTOSEND_CONFIDENCE:
            final_status = "auto_sent"
            final_action = "auto_reply"
            autosend_reason = "high_confidence"

    # Every outgoing reply carries the FAQ + chatbot footer; auto-sends also carry the
    # explicit "automated reply" disclosure.
    _topic = result.category if result.category and result.category != "HR-Inbox" else None
    final_draft = _with_footer(final_draft, auto_sent=(final_status == "auto_sent"), topic=_topic)

    with db.get_conn() as conn:
        if is_follow_up and existing_case_id is not None:
            conn.execute(
                """
                UPDATE cases SET
                    subject = ?, confidence = ?, quality_score = ?, intent = ?,
                    recommended_action = ?, retrieval_backend = ?, top_snippet_score = ?,
                    citation_count = ?, risk_level = ?, draft_body = ?, retrieved_snippets = ?,
                    inbound_body = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    subject,
                    result.confidence,
                    result.quality_score,
                    result.intent,
                    result.recommended_action,
                    result.retrieval_backend,
                    result.top_snippet_score,
                    result.citation_count,
                    result.risk_level,
                    _with_footer(result.draft_body, auto_sent=False, topic=_topic),
                    json.dumps(result.snippets),
                    result.cleaned_body,
                    now,
                    existing_case_id,
                ),
            )
            case_id = existing_case_id
        else:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO cases (
                        external_id, mailbox, subject, from_email, thread_id, status, confidence, quality_score,
                        intent, recommended_action, retrieval_backend, top_snippet_score, citation_count, risk_level,
                        draft_body, retrieved_snippets, inbound_body, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ext,
                        mailbox,
                        subject,
                        from_email,
                        thread_id,
                        final_status,
                        result.confidence,
                        result.quality_score,
                        result.intent,
                        final_action,
                        result.retrieval_backend,
                        result.top_snippet_score,
                        result.citation_count,
                        result.risk_level,
                        final_draft,
                        json.dumps(result.snippets),
                        result.cleaned_body,
                        now,
                        now,
                    ),
                )
                case_id = cur.lastrowid
            except sqlite3.IntegrityError:
                raise HTTPException(409, "external_id already exists")

    auto_sent = final_status == "auto_sent"
    if auto_sent:
        audit_action = "auto_replied"
    elif is_follow_up:
        audit_action = "follow_up_received"
    else:
        audit_action = "process_email"
    db.audit(
        case_id,
        audit_action,
        "system",
        {
            "external_id": ext,
            "mailbox": mailbox,
            "category": result.category,
            "category_strong": result.category_strong,
            "auto_sent": auto_sent,
            "autosend_reason": autosend_reason,
            "auto_template_id": (auto_template or {}).get("id") if auto_sent else None,
            "auto_template_title": (auto_template or {}).get("title") if auto_sent else None,
            "auto_match_score": auto_match_score,
            "auto_block_reason": auto_block_reason,
            "question_summary": result.question_summary,
            "retrieval_backend": result.retrieval_backend,
            "escalation_reason": result.escalation_reason,
            "risk_level": result.risk_level,
            "risk_reasons": result.risk_reasons,
            "quality_score": result.quality_score,
            "generation_mode": result.generation_mode,
            "is_follow_up": is_follow_up,
        },
    )

    # For an auto-reply, push the standard response to Make (Scenario 2) so the Gmail
    # reply goes out immediately — same callback the Approve & Send button uses.
    if auto_sent:
        _notify_make_case_approved(case_id)

    # Slack notification: announce new cases, follow-ups, and auto-sends.
    case_link = f"{PUBLIC_BASE_URL}/case/{case_id}" if PUBLIC_BASE_URL else f"case #{case_id}"
    risk_marker = " ⚠️ HIGH-RISK" if (result.risk_level or "").lower() == "high" else ""
    follow_marker = " ↪️ FOLLOW-UP" if is_follow_up else ""
    if auto_sent:
        headline = f"Auto-replied 🤖 [{result.category}]"
        action_line = "*Auto-sent:* standard template reply went out"
    else:
        headline = f"New HR case{follow_marker}{risk_marker}"
        action_line = f"*Review:* {case_link}"
    _post_slack(
        text=f"{headline}: *{subject}* from `{from_email}` — {case_link}",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{headline}*\n"
                        f"*Subject:* {subject}\n"
                        f"*From:* `{from_email}`\n"
                        f"*Category:* {result.category} · *Confidence:* {result.confidence:.0%} · *Risk:* {result.risk_level or 'low'}\n"
                        f"{action_line}"
                    ),
                },
            }
        ],
    )

    return JSONResponse(
        {
            "ok": True,
            "case_id": case_id,
            "mailbox": mailbox,
            "status": final_status,
            "auto_replied": auto_sent,
            "confidence": result.confidence,
            "quality_score": result.quality_score,
            "auto_reply_threshold": AUTO_REPLY_THRESHOLD,
            "recommended_action": final_action,
            "intent": result.intent,
            "category": result.category,
            "gmail_label": result.category,
            "category_strong": result.category_strong,
            "auto_block_reason": auto_block_reason,
            "question_summary": result.question_summary,
            "draft_body": final_draft,
            "retrieved_snippets": result.snippets,
            "retrieval_backend": result.retrieval_backend,
            "top_snippet_score": result.top_snippet_score,
            "citation_count": result.citation_count,
            "risk_level": result.risk_level,
            "risk_reasons": result.risk_reasons,
            "generation_mode": result.generation_mode,
            "escalation_reason": result.escalation_reason,
        }
    )


_TEST_ADDR_SUFFIXES = ("@example.com", "@example.org", "@e.com", "@ex.com", "@test.com")
_TEST_ADDRS = {"v@e.com", "t@e.com", "a@e.com", "b@e.com", "v@ex.com"}


def _is_test_case(from_email: str | None) -> bool:
    fe = (from_email or "").lower().strip()
    return fe.endswith(_TEST_ADDR_SUFFIXES) or fe in _TEST_ADDRS


def _thread_replyable(thread_id: str | None) -> bool:
    """A real Gmail thread id is a long token with no spaces. Short/empty ones (leftover
    test data) make Scenario 2's 'Reply to an email' fail with '[400] Invalid id'."""
    tid = (thread_id or "").strip()
    return len(tid) >= 12 and " " not in tid


@app.post("/api/redraft-queue")
async def redraft_queue(request: Request, limit: int = 5, dry_run: bool = False,
                        cleanup_test: bool = False, before: str = ""):
    """Re-process pending_review cases through the full pipeline now that Gemini is back.
    Auto-sends ONLY cases that are confident AND have a replyable Gmail thread; otherwise
    updates the draft and leaves it in review. Legal/emergency stay in review. Small batches
    keep each call under the request timeout.

    Modes: ?dry_run=true (report only, no changes), ?cleanup_test=true (delete leftover
    test-address cases), default (?limit=N) re-draft that many oldest pending cases."""
    _require_api_key(request)

    if cleanup_test:
        deleted = 0
        with db.get_conn() as conn:
            for r in conn.execute("SELECT id, from_email FROM cases WHERE status = 'pending_review'").fetchall():
                if _is_test_case(r["from_email"]):
                    conn.execute("DELETE FROM audit_log WHERE case_id = ?", (r["id"],))
                    conn.execute("DELETE FROM cases WHERE id = ?", (r["id"],))
                    deleted += 1
        return JSONResponse({"deleted_test_cases": deleted})

    if dry_run:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT from_email, thread_id, inbound_body FROM cases WHERE status = 'pending_review'"
            ).fetchall()
        total = len(rows)
        test = sum(1 for r in rows if _is_test_case(r["from_email"]))
        replyable = sum(1 for r in rows if _thread_replyable(r["thread_id"]) and not _is_test_case(r["from_email"]))
        no_body = sum(1 for r in rows if not (r["inbound_body"] or "").strip())
        return JSONResponse({
            "pending_total": total, "test_junk": test,
            "real_replyable_thread": replyable, "real_unreplyable_thread": total - test - replyable,
            "no_body": no_body,
        })

    # `before` (ISO timestamp) makes each case get processed exactly once: a re-drafted
    # case gets updated_at=now (>= before), so it drops out of the next batch instead of
    # being re-processed forever. The client passes the drain's start time.
    with db.get_conn() as conn:
        if before:
            rows = conn.execute(
                "SELECT id, subject, from_email, thread_id, inbound_body FROM cases "
                "WHERE status = 'pending_review' AND updated_at < ? ORDER BY id LIMIT ?",
                (before, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, subject, from_email, thread_id, inbound_body FROM cases "
                "WHERE status = 'pending_review' ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
    cases = [dict(r) for r in rows]
    templates = db.get_auto_reply_templates()
    summary = {"processed": 0, "auto_sent": 0, "redrafted_review": 0,
               "deleted_test": 0, "skipped_no_body": 0, "errors": 0}
    for c in cases:
        try:
            if _is_test_case(c["from_email"]):
                with db.get_conn() as conn:
                    conn.execute("DELETE FROM audit_log WHERE case_id = ?", (c["id"],))
                    conn.execute("DELETE FROM cases WHERE id = ?", (c["id"],))
                summary["deleted_test"] += 1
                continue
            subject = c["subject"] or "(no subject)"
            body = c["inbound_body"] or ""
            if not body.strip():
                summary["skipped_no_body"] += 1
                continue
            result = process_email(
                subject, c["from_email"] or "", body,
                templates=templates, top_k=5, auto_reply_threshold=AUTO_REPLY_THRESHOLD,
            )
            is_template = result.generation_mode in ("template", "default_template")
            auto_block_reason = _auto_reply_blocked(subject, body)
            confident = is_template or result.confidence >= AI_AUTOSEND_CONFIDENCE
            # Only auto-send when confident, not blocked, AND actually replyable in-thread.
            autosend = confident and not auto_block_reason and _thread_replyable(c["thread_id"])
            topic = result.category if result.category and result.category != "HR-Inbox" else None
            draft = _with_footer(result.draft_body, auto_sent=autosend, topic=topic)
            now = db._utc_now()
            new_status = "auto_sent" if autosend else "pending_review"
            with db.get_conn() as conn:
                conn.execute(
                    """
                    UPDATE cases SET draft_body = ?, intent = ?, confidence = ?, quality_score = ?,
                        recommended_action = ?, retrieval_backend = ?, top_snippet_score = ?,
                        citation_count = ?, risk_level = ?, retrieved_snippets = ?, status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        draft, result.intent, result.confidence, result.quality_score,
                        "auto_reply" if autosend else "queue_for_review", result.retrieval_backend,
                        result.top_snippet_score, result.citation_count, result.risk_level,
                        json.dumps(result.snippets), new_status, now, c["id"],
                    ),
                )
            if autosend:
                db.audit(c["id"], "auto_replied", "system", {"via": "redraft", "category": result.category})
                _notify_make_case_approved(c["id"])
                summary["auto_sent"] += 1
            else:
                db.audit(c["id"], "redraft", "system", {
                    "category": result.category, "confidence": result.confidence,
                    "confident": confident, "replyable": _thread_replyable(c["thread_id"]),
                    "block": auto_block_reason,
                })
                summary["redrafted_review"] += 1
            summary["processed"] += 1
        except Exception as exc:
            logger.warning("redraft failed for case %s: %s", c.get("id"), str(exc)[:200])
            summary["errors"] += 1
    with db.get_conn() as conn:
        summary["remaining_pending"] = conn.execute(
            "SELECT COUNT(*) AS c FROM cases WHERE status = 'pending_review'"
        ).fetchone()["c"]
        if before:
            summary["remaining_unprocessed"] = conn.execute(
                "SELECT COUNT(*) AS c FROM cases WHERE status = 'pending_review' AND updated_at < ?",
                (before,),
            ).fetchone()["c"]
    return JSONResponse(summary)


@app.post("/api/webhook/inbound")
async def webhook_inbound(request: Request):
    """Stub: Make.com or your worker can POST JSON or form-data here to create a pending case."""
    _require_api_key(request)
    payload: dict | None = None
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = None
    if payload is None:
        try:
            form = await request.form()
            if form:
                payload = {k: (v if isinstance(v, str) else str(v)) for k, v in form.items()}
        except Exception:
            payload = None
    if payload is None:
        raw = await request.body()
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                payload = None
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(400, "JSON or form body required")
    subject = payload.get("subject") or "(no subject)"
    from_email = payload.get("from") or payload.get("from_email") or "unknown@example.com"
    mailbox = payload.get("mailbox") or payload.get("mailbox_id") or ""
    thread_id = payload.get("thread_id") or ""
    confidence = float(payload.get("confidence") or 0)
    intent = payload.get("intent") or ""
    draft_body = payload.get("draft_body") or ""
    snippets = payload.get("retrieved_snippets") or payload.get("snippets") or []
    quality_score = float(payload.get("quality_score") or 0.0)
    recommended_action = payload.get("recommended_action") or "create_thread_draft"
    retrieval_backend = payload.get("retrieval_backend") or "external"
    top_snippet_score = float(payload.get("top_snippet_score") or 0.0)
    citation_count = int(payload.get("citation_count") or 0)
    risk_level = payload.get("risk_level") or "low"
    ext = payload.get("external_id") or f"webhook-{db._utc_now()}"
    now = db._utc_now()
    with db.get_conn() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO cases (
                    external_id, mailbox, subject, from_email, thread_id, status, confidence, quality_score,
                    intent, recommended_action, retrieval_backend, top_snippet_score, citation_count, risk_level,
                    draft_body, retrieved_snippets, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ext,
                    mailbox,
                    subject,
                    from_email,
                    thread_id,
                    confidence,
                    quality_score,
                    intent,
                    recommended_action,
                    retrieval_backend,
                    top_snippet_score,
                    citation_count,
                    risk_level,
                    draft_body,
                    json.dumps(snippets),
                    now,
                    now,
                ),
            )
            case_id = cur.lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "external_id already exists")
    db.audit(case_id, "webhook_inbound", "system", {"external_id": ext, "mailbox": mailbox})
    return JSONResponse({"ok": True, "case_id": case_id})
