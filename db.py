"""Persistence for review queue and audit trail.

Supports two backends:
  * SQLite — when DATABASE_URL is unset (local dev). DB at SQLITE_DIR or web/data/.
  * Postgres — when DATABASE_URL is set (production). Cloud Run → Neon.

The wrapper translates SQLite-style API (?, lastrowid, executescript) to Postgres
on the fly, so the rest of the app uses one API.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("fellowship_db")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
USE_POSTGRES = bool(DATABASE_URL)

_sqlite_dir_env = os.getenv("SQLITE_DIR")
if _sqlite_dir_env:
    DB_PATH = Path(_sqlite_dir_env) / "fellowship_console.db"
else:
    DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fellowship_console.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------- Postgres adapter ----------------------

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

    class _PgCursor:
        """Wraps a Postgres cursor to look like sqlite3.Cursor:
        - converts `?` placeholders to `%s`
        - exposes .lastrowid by appending RETURNING id to INSERTs
        - rows are RealDictRow (subscriptable by column name)
        """

        def __init__(self, cur):
            self._cur = cur
            self.lastrowid = None

        def execute(self, sql, params=()):
            sql_pg = self._translate(sql)
            is_insert = sql_pg.lstrip().upper().startswith("INSERT INTO")
            if is_insert and "RETURNING" not in sql_pg.upper():
                sql_pg = sql_pg.rstrip(";").rstrip() + " RETURNING id"
            try:
                self._cur.execute(sql_pg, params or ())
            except Exception:
                raise
            if is_insert:
                try:
                    row = self._cur.fetchone()
                    if row is not None:
                        self.lastrowid = row.get("id") if hasattr(row, "get") else row[0]
                except psycopg2.ProgrammingError:
                    self.lastrowid = None
            return self

        def executemany(self, sql, seq):
            sql_pg = self._translate(sql)
            self._cur.executemany(sql_pg, list(seq))
            return self

        def fetchone(self):
            return self._cur.fetchone()

        def fetchall(self):
            return self._cur.fetchall()

        @staticmethod
        def _translate(sql: str) -> str:
            # SQLite uses `?`, Postgres uses `%s`. Naïve but works for our queries
            # (no `?` ever appears in literals here).
            return sql.replace("?", "%s")

    class _PgConn:
        """Wraps a psycopg2 connection to look like sqlite3.Connection."""

        def __init__(self, dsn: str):
            self._conn = psycopg2.connect(dsn)
            self._conn.autocommit = False

        @property
        def row_factory(self):
            return None

        @row_factory.setter
        def row_factory(self, _):
            pass  # SQLite-only API; ignored

        def execute(self, sql, params=()):
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return _PgCursor(cur).execute(sql, params)

        def executemany(self, sql, seq):
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return _PgCursor(cur).executemany(sql, seq)

        def executescript(self, sql_script: str):
            # Postgres has no executescript; run statements separated by `;`.
            cur = self._conn.cursor()
            for stmt in (s.strip() for s in sql_script.split(";")):
                if stmt:
                    cur.execute(stmt)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            self._conn.close()


# ---------------------- Connection factory ----------------------

@contextmanager
def get_conn():
    if USE_POSTGRES:
        conn = _PgConn(DATABASE_URL)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


# ---------------------- Schema ----------------------

_CASES_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS cases (
    id SERIAL PRIMARY KEY,
    external_id TEXT UNIQUE,
    mailbox TEXT,
    subject TEXT NOT NULL,
    from_email TEXT NOT NULL,
    thread_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending_review',
    confidence DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    intent TEXT,
    recommended_action TEXT,
    retrieval_backend TEXT,
    top_snippet_score DOUBLE PRECISION,
    citation_count INTEGER,
    risk_level TEXT,
    draft_body TEXT,
    inbound_body TEXT,
    retrieved_snippets TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    case_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    trigger_phrases TEXT,
    auto_reply INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_thread ON cases(thread_id);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
CREATE INDEX IF NOT EXISTS idx_templates_category ON templates(category);
"""

_CASES_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    mailbox TEXT,
    subject TEXT NOT NULL,
    from_email TEXT NOT NULL,
    thread_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending_review',
    confidence REAL,
    quality_score REAL,
    intent TEXT,
    recommended_action TEXT,
    retrieval_backend TEXT,
    top_snippet_score REAL,
    citation_count INTEGER,
    risk_level TEXT,
    draft_body TEXT,
    inbound_body TEXT,
    retrieved_snippets TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    trigger_phrases TEXT,
    auto_reply INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_thread ON cases(thread_id);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
CREATE INDEX IF NOT EXISTS idx_templates_category ON templates(category);
"""


# Standard auto-reply responses, sourced from "Documents Email Responses.docx" and
# "Immediate Responses.pdf". Each template carries its own `triggers` (one phrase per
# line) used for specific-phrase matching. Seeded once per title; HR edits/toggles them
# on the Templates page afterward. Tuple = (category, title, body, auto_reply, triggers).
_SEED_TEMPLATES: list[tuple[str, str, str, int, str]] = [
    (
        "Relieving Letter",
        "Relieving letter — standard response",
        "Hi,\n\n"
        "Thank you for reaching out about your relieving letter.\n\n"
        "Your relieving letter is available on the CDF portal. If you are unable to download it "
        "from the main section, you can also find it under the Relieving Letter section of your portal.\n\n"
        "Please note: once a relieving letter has been issued, the end date on it cannot be changed.\n\n"
        "If you have already raised a ticket for this, please check your portal for status updates.\n\n"
        "Best regards,\nHR Team",
        1,
        "relieving letter\nreliving letter\nrelieve letter\nrelieving document\nrelieving certificate\n"
        "download my relieving\nneed my relieving\nrequest relieving letter\nrequest for relieving\n"
        "relieving letter status\nrelieving letter not received\nwhere is my relieving letter\n"
        "get my relieving letter\nmy relieving letter\nissue my relieving letter",
    ),
    (
        "Experience Letter",
        "Experience / employee verification letter — standard response",
        "Hi,\n\n"
        "Thank you for reaching out regarding your experience / employee verification letter.\n\n"
        "If you are resigning, please raise a ticket for these documents on the CDF portal:\n\n"
        "Support Tickets > +Create Ticket > Type of request (HR Letters > Letter type: Experience Letter)\n"
        "Support Tickets > +Create Ticket > Type of request (HR Letters > Letter type: Employee Verification Letter)\n\n"
        "If you did not raise the ticket while your portal was active and your portal access has since "
        "been revoked, please reply to this email with the following details so we can process your request:\n\n"
        "Name:\nRole:\nStart Date:\nEnd Date:\nHours worked:\nTeam Name:\nContributions:\n"
        "Work location:\nReason for Employment Verification:\nReporting Manager:\n\n"
        "Best regards,\nHR Team",
        1,
        "experience letter\nemployment verification letter\nemployee verification letter\nemployment letter\n"
        "work experience letter\nexperience certificate\nverification letter\nproof of employment\nemployment proof\n"
        "need an experience letter\nrequest experience letter\nrequest for experience letter\nexperience letter for\n"
        "need a verification letter\nemployment verification document",
    ),
    (
        "Onboarding",
        "Welcome email — new applicant interest",
        "Dear Applicant,\n\n"
        "Thank you for reaching out and expressing your interest in volunteering with our organization. "
        "We truly appreciate your enthusiasm and willingness to contribute your skills.\n\n"
        "To learn more about our organization, please visit our website and review our Team Overview and FAQ. "
        "Please note that everything is done through our portal, so creating an account there is necessary.\n\n"
        "To ensure a smooth and expedited onboarding process, please follow these steps:\n\n"
        "- Visit our website at cdreamstream.org to begin the volunteer application process by joining our portal.\n"
        "- Upon account creation and review, you will receive an offer letter and agreement within 24-48 hours.\n"
        "- Once you sign and upload the documents, you will see the next steps within the portal.\n"
        "- Refer your friends and peers to join the organization. We offer fee waivers for referrals, which can "
        "be seen on the Membership tab of the portal.\n\n"
        "Please note: the guidance below applies only to individuals seeking full-time/part-time employment under OPT or CPT.\n\n"
        "- If you are seeking OPT employment, please register only if your OPT has been approved and you have your EAD in hand.\n"
        "- If you are seeking CPT employment, please ensure your CPT eligibility and approved start date have been "
        "confirmed by your DSO before registering.\n\n"
        "We're excited to welcome you to the team!\n\n"
        "Sincerely,\nOne Team, One Dream!\nHuman Resources\nCommunity Dreams Foundation",
        1,
        "interested in volunteering\ninterested in volunteer\ninterested to volunteer\nwant to volunteer\n"
        "would like to volunteer\nlike to volunteer\nkeen to volunteer\nhow do i become a volunteer\n"
        "how to become a volunteer\njoin as a volunteer\napply as a volunteer\nvolunteer opportunity\n"
        "volunteer opportunities\nvolunteering opportunity\nvolunteering opportunities\nvolunteer application\n"
        "volunteer with cdf\nvolunteer with your organization\nexpress my interest\ninterested in contributing\n"
        "how do i join cdf\njoin community dreams",
    ),
    (
        "Onboarding",
        "Upon documents receiving — onboarding next steps",
        "Dear Applicant,\n\n"
        "Thank you for returning your signed documents! We're thrilled to have you join the team.\n\n"
        "Next Steps: Complete Your Onboarding\n\n"
        "1. Join Our Slack Workspace\n"
        "You will receive a separate invitation (within 2-3 business days) to log in to Slack. The link expires "
        "in 7 days, so please join promptly. After joining, update your Slack profile with your full name, start date, "
        "job title, profile photo, and status.\n\n"
        "2. Enter the #onboarding Channel\n"
        "This channel contains essential documentation and instructions for the Team Selection Process. Please "
        "introduce yourself in the channel and mention your role, skills, and relevant details so we can align you "
        "with the right projects.\n\n"
        "3. Register and Log in to TaskVerse\n"
        "TaskVerse is where you will manage and track your assigned work. Go to the CDF Portal Home Page, navigate "
        "to the Tasks section, and open your TaskVerse account. If prompted for a code, use CDF and log in with your "
        "personal email and password. Then explore the Community Projects section to find work that aligns with your role.\n\n"
        "If you have not received your offer letter yet, please reply to this email immediately and we will expedite the process.\n\n"
        "We appreciate your patience and are excited to have you on board!\n\n"
        "Best regards,\nHR Team",
        1,
        "signed documents\nsigned the documents\nuploaded my documents\nreturned my signed\n"
        "signed the offer letter\nsigned my offer letter\nsubmitted my offer letter\nuploaded the signed\n"
        "completed my registration\nfinished my registration\nwhat are the next steps\nnext steps after signing\n"
        "what happens after i sign\ni signed and uploaded\nnext step in onboarding",
    ),
    (
        "Offboarding/Resign",
        "Resignation — portal process",
        "Hello,\n\n"
        "Thank you for your email.\n\n"
        "To proceed with your resignation and receive your relieving letter, please complete the following steps "
        "through the CDF Portal, as we no longer accept email submissions:\n\n"
        "1. Log in to the CDF Portal and navigate to Support Tickets > Resignation.\n"
        "2. Support Tickets > +Create Ticket > Type of request (HR Letters > Letter type: Experience Letter).\n"
        "3. Submit your formal request through the portal. A mandatory one-week notice period is required to ensure a "
        "smooth transition and the timely issuance of your relieving letter. Your last working day will be the final "
        "day of this notice period.\n"
        "4. If you wish to have an early relieving date, click the earliest date available in the date bar, then select "
        "an early relieving date and submit it for approval.\n\n"
        "Additionally, please ensure you:\n"
        "- Inform your team leader of your resignation.\n"
        "- Complete outstanding tasks and return all access, tools, and drives to your lead.\n"
        "- Log out of all CDF platforms on your final day.\n\n"
        "For any future background verification requests, please use verification@cdreams.org.\n\n"
        "Please contact the HR Team if you have any questions.\n\n"
        "Best regards,\nHR Team",
        1,
        "i want to resign\ni would like to resign\nwant to resign\nwish to resign\nplanning to resign\n"
        "submit my resignation\nhow do i resign\nhow to resign\ntender my resignation\nmy resignation\n"
        "resignation process\nresignation request\nprocess my resignation\nstepping down\n"
        "discontinue volunteering\nstop volunteering\nleaving the organization\nresign from cdf\nresign from my role",
    ),
    (
        "Onboarding",
        "Date change — cannot change start date/role/hours",
        "Thank you for your email.\n\n"
        "Please note that at Community Dreams Foundation, we remain compliant with the start date, role, and hours you "
        "selected during your registration. We offer the option to choose a reliable date, role, and hours to avoid these "
        "issues, but as a result we are unable to make any changes to your current offer letter.\n\n"
        "Best regards,\nHR Team",
        1,
        "change my start date\nchange the start date\nupdate my start date\ndifferent start date\n"
        "change start date\nmodify my start date\nreschedule my start date\npostpone my start date\n"
        "push my start date\nchange my joining date\nchange my offer letter date\nchange the date on my offer",
    ),
    (
        "Onboarding",
        "EAD delay — offer letter voided, re-register",
        "Thank you for the update regarding your EAD status.\n\n"
        "Please note that we are unable to change the dates on your current offer letter. Since you have not yet received "
        "your EAD card, your existing offer letter will be voided.\n\n"
        "Once you have your EAD in hand, please re-register with Community Dreams Foundation by following the same steps "
        "as before. We will then issue a new offer letter with updated dates.\n\n"
        "Best regards,\nHR Team",
        1,
        "ead delay\nead is delayed\nhaven't received my ead\nhave not received my ead\nead not yet\n"
        "ead not received\nwaiting for my ead\nstill waiting for ead\nead card\nead pending\ndelay in ead\n"
        "ead approval delay\nmy ead is delayed\nyet to receive ead\nead is not here yet",
    ),
    (
        "HR-Inbox",
        "Address change — update records",
        "Thank you for providing your new address.\n\n"
        "Please take the following actions to update your employment records:\n\n"
        "1. Update your address on your Agreement Document and return the revised document to us via a reply to this email.\n"
        "2. Update your address on the Community Dreams Foundation (CDF) portal, if applicable.\n"
        "3. Submit a ticket through the CDF portal to formally request the address update so our team can process it accordingly.\n\n"
        "Let us know if you have any questions.\n\n"
        "Best regards,\nHR Team",
        1,
        "change my address\nupdate my address\nnew address\naddress change\nupdate my mailing address\n"
        "changed my address\nmoved to a new address\nupdate my address on file\ncorrect my address\n"
        "address update\nmy new address\nupdate my residential address\nchange of address",
    ),
    (
        "Ex-Payment Issue",
        "Ex / fellowship payment issue — contact fellowship team",
        "Hi,\n\n"
        "For this payment matter, please reach out to cdffellowship@cdreams.org and the team will assist you.\n\n"
        "Best regards,\nHR Team",
        1,
        "fellowship payment\nfellowship fee\ncdffellowship\nfellowship membership",
    ),
    (
        "Ex-Payment Issue",
        "Payments (new hires) — pay via portal",
        "Hi,\n\n"
        "Please note that all payments are now handled through the CDF portal. Kindly log in and complete your payment "
        "under the Membership section.\n\n"
        "Best regards,\nHR Team",
        1,
        "how do i pay\nwhere do i pay\nhow to make payment\nhow to make the payment\nmake my first payment\n"
        "first payment\nwhere is the payment option\npay my membership\nhow do i pay my membership\n"
        "how do i make my payment\nhow to pay the membership fee\nmake a payment\nwhere to pay\n"
        "complete my payment\nmake my membership payment\nhow can i pay",
    ),
    (
        "Ex-Payment Issue",
        "Payment queries — raise a ticket",
        "Hi,\n\n"
        "Thank you for reaching out.\n\n"
        "For any payment-related queries, please raise a ticket through the CDF portal. You can do this by navigating to "
        "Support Ticket > + New Ticket > Payment issue.\n\n"
        "Best regards,\nHR Team",
        1,
        "payment issue\npayment query\npayment problem\noverdue payment\npayment overdue\nshows overdue\n"
        "still overdue\nlate fee\ndouble charged\ncharged twice\npayment not reflecting\npayment not showing\n"
        "payment is not showing\npayment isn't showing\nnot showing in the portal\nnot reflected\nnot reflecting\n"
        "refund\npayment failed\npayment error\nincorrect payment\npayment discrepancy\nalready paid\n"
        "payment status\nmembership fee issue\nautopay\nauto pay\npayment not updated\nissue with my payment\n"
        "problem with payment\ntrouble with payment\npaid but still\ncompleted my payment but",
    ),
    (
        "RFE",
        "STEM OPT — not supported",
        "Hi,\n\n"
        "Thank you for reaching out and for your interest in contributing to Community Dreams Foundation.\n\n"
        "While CDF is enrolled in E-Verify, we do not participate in the F-1 STEM OPT extension program and therefore "
        "cannot complete Form I-983 or any related STEM OPT training plan documentation.\n\n"
        "We truly appreciate your interest in supporting our mission and encourage you to explore volunteer opportunities "
        "with us that do not involve STEM OPT requirements.\n\n"
        "Thank you for your understanding.\n\n"
        "Best regards,\nHR Team",
        1,
        "stem opt\ni-983\ni983\nform i-983\nstem opt extension\nstem opt employment\nstem opt verification\n"
        "support stem opt\ndo you support stem opt\nstem opt training plan\ntraining plan for stem",
    ),
    (
        "RFE",
        "W-2 form — not issued (volunteer org)",
        "Thank you for your email.\n\n"
        "Community Dreams Foundation operates as a volunteer-based organization. As such, we do not issue W-2 forms and "
        "are unable to provide W-2 documentation for STEM OPT verification purposes.\n\n"
        "Please let us know if you require any general confirmation of volunteer association within the scope of our organization.\n\n"
        "Best regards,\nHR Team",
        1,
        "w-2\nw2 form\nw-2 form\nw2 for tax\nw-2 for stem\ntax form w2\nw2 document\nw-2 document\n"
        "issue w2\nneed my w2\nneed my w-2\nw2 statement\nprovide w2\nw2 for verification\nrequest for w2",
    ),
    (
        "Onboarding",
        "Portal invite — allow 3-4 business days",
        "Hi,\n\n"
        "Please allow 3-4 business days for a new Portal invite link to be generated. This will be sent directly to your "
        "email; please monitor your inbox for this update.\n\n"
        "Best regards,\nHR Team",
        1,
        "portal invite\nportal invitation\nnew portal link\nportal link expired\ndidn't receive portal invite\n"
        "did not receive portal invite\nportal invite link\nresend portal invite\nportal login link\n"
        "portal access link\nportal link not working\nregenerate portal invite\nportal invitation link",
    ),
    (
        "Onboarding",
        "Slack invite — allow 3-4 business days",
        "Hi,\n\n"
        "Please allow 3-4 business days for a new Slack invite link to be generated. This will be sent directly to your "
        "email; please monitor your inbox for this update.\n\n"
        "Best regards,\nHR Team",
        1,
        "slack invite\nslack invitation\nslack link expired\nnew slack invite\ndidn't receive slack invite\n"
        "did not receive slack invite\nslack invite link\njoin slack\nslack access\nresend slack invite\n"
        "slack link not working\ncan't join slack\ncannot join slack\nslack invite expired\nneed slack access\n"
        "access to slack\nadd me to slack\nslack workspace invite",
    ),
    (
        "Onboarding",
        "Backdate offer letter — not permitted",
        "Thank you for your email.\n\n"
        "Please note that a backdated offer letter cannot be provided, as this is against compliance requirements. You "
        "will receive an offer letter reflecting the date on which you submit your application.\n\n"
        "Best regards,\nHR Team",
        1,
        "backdated offer letter\nbackdate my offer\nback date the offer\nbackdate offer\n"
        "earlier date on offer letter\nbackdate my offer letter\nbackdated letter\noffer letter with a past date\n"
        "offer letter with an earlier date\ncan you backdate\nbackdate the offer letter",
    ),
    (
        "Onboarding",
        "Scanned copies — not accepted",
        "Hi,\n\n"
        "Thank you for sending the documents. Please note that scanned, signed copies of the offer letter cannot be "
        "accepted. Kindly sign the electronic document and submit it through the system and via email.\n\n"
        "Best regards,\nHR Team",
        1,
        "scanned copy\nscanned signed\nscanned copies\nsent a scanned\nscanned offer letter\n"
        "scan of my offer letter\nuploaded a scanned\nphysical signed copy\nprinted and signed\nhandwritten signature",
    ),
    (
        "Onboarding",
        "Updated offer letter — provide updated start date",
        "Hi,\n\n"
        "Please provide your updated start date. Once received, a new offer letter will be sent to you within 24-48 hours. "
        "Please upload the updated offer letter with all required signatures.\n\n"
        "Best regards,\nHR Team",
        0,  # review-only: contradicts the Date Change policy, so a human confirms first
        "updated offer letter\nrevised offer letter\nreissue my offer letter\nre-issue offer letter\n"
        "resend my offer letter\ncorrected offer letter\noffer letter with the updated date",
    ),
    (
        "Ex-Payment Issue",
        "Waiver — fee waiver information",
        "Hi,\n\n"
        "Information regarding fee waivers can be found on the Membership tab of the portal or the volunteer agreement form "
        "sent during your onboarding.\n\n"
        "Please review the agreement included in your onboarding documents to determine which waiver you would like to apply "
        "for. Once you have identified the correct waiver, please log in to the CDF portal and submit a ticket. You will find "
        "an option for a membership waiver within the ticketing section.\n\n"
        "Best regards,\nHR Team",
        1,
        "fee waiver\nmembership waiver\napply for waiver\napply for a waiver\nhardship waiver\nleadership waiver\n"
        "sweat equity waiver\nreferral waiver\nwaiver request\nfee waiver request\nwaive my fee\nwaive the fee\n"
        "waive my membership\nwaive membership fee\nrequest a waiver\nwaiver for membership\nqualify for a waiver",
    ),
]


def seed_templates_if_empty() -> None:
    """Insert any standard responses that aren't already present (idempotent by title)."""
    with get_conn() as conn:
        existing = {
            r["title"]
            for r in conn.execute("SELECT title FROM templates").fetchall()
        }
        now = _utc_now()
        to_add = [
            (cat, title, body, triggers, auto, now)
            for (cat, title, body, auto, triggers) in _SEED_TEMPLATES
            if title not in existing
        ]
        if to_add:
            conn.executemany(
                """
                INSERT INTO templates (category, title, body, trigger_phrases, auto_reply, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                to_add,
            )

        # Trigger phrases and the on/off flag are managed in code (this seed list) and
        # synced to every seed template on startup, so broadened matching + auto-reply
        # toggles ship with a deploy. Template BODY wording edited in the UI is preserved.
        synced = 0
        for cat, title, body, auto, triggers in _SEED_TEMPLATES:
            row = conn.execute(
                "SELECT id, trigger_phrases, auto_reply FROM templates WHERE title = ? ORDER BY id LIMIT 1",
                (title,),
            ).fetchone()
            if row is None:
                continue
            if (row["trigger_phrases"] or "") != triggers or int(row["auto_reply"] or 0) != auto:
                conn.execute(
                    "UPDATE templates SET trigger_phrases = ?, auto_reply = ?, updated_at = ? WHERE id = ?",
                    (triggers, auto, now, row["id"]),
                )
                synced += 1
        if synced:
            logger.info("templates: synced trigger_phrases/auto_reply for %d template(s)", synced)


def get_auto_reply_templates() -> list[dict]:
    """Return all auto-reply-enabled templates (for specific-phrase matching)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, category, title, body, trigger_phrases, auto_reply FROM templates "
            "WHERE auto_reply = 1 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_CASES_SCHEMA_PG if USE_POSTGRES else _CASES_SCHEMA_SQLITE)
        if USE_POSTGRES:
            # Add columns missing in older schemas (idempotent via IF NOT EXISTS).
            for col, ddl in (
                ("mailbox", "TEXT"),
                ("quality_score", "DOUBLE PRECISION"),
                ("recommended_action", "TEXT"),
                ("retrieval_backend", "TEXT"),
                ("top_snippet_score", "DOUBLE PRECISION"),
                ("citation_count", "INTEGER"),
                ("risk_level", "TEXT"),
                ("inbound_body", "TEXT"),
                ("intent", "TEXT"),
            ):
                conn.execute(f"ALTER TABLE cases ADD COLUMN IF NOT EXISTS {col} {ddl}")
        else:
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(cases)").fetchall()
            }
            for col, ddl in (
                ("mailbox", "TEXT"),
                ("quality_score", "REAL"),
                ("recommended_action", "TEXT"),
                ("retrieval_backend", "TEXT"),
                ("top_snippet_score", "REAL"),
                ("citation_count", "INTEGER"),
                ("risk_level", "TEXT"),
                ("inbound_body", "TEXT"),
                ("intent", "TEXT"),
            ):
                if col not in cols:
                    conn.execute(f"ALTER TABLE cases ADD COLUMN {col} {ddl}")

        # trigger_phrases column (renamed from the original "triggers", which silently
        # dropped writes on the production Postgres instance).
        if USE_POSTGRES:
            conn.execute("ALTER TABLE templates ADD COLUMN IF NOT EXISTS trigger_phrases TEXT")
        else:
            tcols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(templates)").fetchall()
            }
            if "trigger_phrases" not in tcols:
                conn.execute("ALTER TABLE templates ADD COLUMN trigger_phrases TEXT")


def seed_demo_if_empty() -> None:
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM cases").fetchone()["c"]
        if n > 0:
            return
        now = _utc_now()
        sample_body_1 = (
            "Hi HR Team,\n\n"
            "I registered as a volunteer with CDF last week and submitted my documents. "
            "My portal still shows \"Documents Under Review\" after several days. "
            "Can you let me know how long onboarding typically takes and whether my application is on track?\n\n"
            "Thanks!\nEmployee"
        )
        samples = [
            (
                "demo-onboarding-1",
                "hr@cdreams.org",
                "Question about onboarding timeline",
                "employee@example.com",
                "thread-demo-1",
                "pending_review",
                0.42,
                0.38,
                "onboarding",
                "queue_for_review",
                "chroma",
                0.42,
                1,
                "low",
                "Hi there,\n\nThanks for reaching out about onboarding. New hires typically complete orientation within 2–5 business days of submitting all required documents. HR will confirm your specific status.\n\nBest,\nHR Team",
                sample_body_1,
                json.dumps(
                    [
                        {"source": "Employee Handbook.pdf", "score": 0.42, "excerpt": "New hires complete orientation within 2–5 business days."},
                    ]
                ),
                now,
                now,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO cases (
                external_id, mailbox, subject, from_email, thread_id, status, confidence, quality_score,
                intent, recommended_action, retrieval_backend, top_snippet_score, citation_count, risk_level,
                draft_body, inbound_body, retrieved_snippets, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            samples,
        )


def audit(case_id: int, action: str, actor: str, details: dict | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO audit_log (case_id, action, actor, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, action, actor, json.dumps(details or {}), _utc_now()),
        )
