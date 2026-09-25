from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("fellowship_pipeline")

GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
GEMINI_EMBEDDING_DIM = int(os.getenv("GEMINI_EMBEDDING_DIM", "1536"))
GEMINI_REPLY_MODEL = os.getenv("GEMINI_REPLY_MODEL", "gemini-2.5-flash")
PINECONE_INDEX = os.getenv("INDEX_NAME", "cdf-fellowship-policy-index")
PINECONE_HOST = (os.getenv("PINECONE_HOST") or "").strip()
CHROMA_PATH = Path(
    os.getenv(
        "CHROMA_PATH",
        str((Path(__file__).resolve().parents[2] / "out" / "chroma_db")),
    )
)


@dataclass
class ProcessingResult:
    intent: str  # holds the category label (kept as `intent` for schema/back-compat)
    category: str
    category_strong: bool
    confidence: float
    quality_score: float
    draft_body: str
    snippets: list[dict[str, Any]]
    question_summary: str
    cleaned_body: str
    retrieval_backend: str
    recommended_action: str
    top_snippet_score: float
    citation_count: int
    risk_level: str
    risk_reasons: list[str]
    generation_mode: str
    escalation_reason: str | None = None
    matched_template: dict[str, Any] | None = None
    match_score: int = 0


def _pinecone_key() -> str | None:
    return (os.getenv("PINECONE_KEY") or os.getenv("PINECONE_API_KEY") or "").strip() or None


def _gemini_key() -> str | None:
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None


SENSITIVE_PATTERNS: dict[str, list[str]] = {
    "legal_threat": [r"\blawsuit\b", r"\battorney\b", r"\blegal action\b", r"\bdiscrimination\b"],
    "termination": [r"\btermination\b", r"\bfired\b", r"\blayoff\b"],
    "compensation_dispute": [r"\bunderpaid\b", r"\bpay dispute\b", r"\bwrong paycheck\b"],
    "medical_privacy": [r"\bmedical condition\b", r"\bdiagnosis\b", r"\bhipaa\b"],
}


def clean_email_body(text: str) -> str:
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"[ \t]+", " ", body)

    cut_markers = [
        r"^On .+wrote:$",
        r"^From:\s",
        r"^-{2,}\s*Original Message\s*-{2,}$",
        r"^Sent from my (iPhone|Android).*$",
        r"^Best regards,?$",
        r"^Thanks,?$",
    ]

    lines = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if any(re.match(marker, stripped, flags=re.IGNORECASE) for marker in cut_markers):
            break
        lines.append(line)

    body = "\n".join(lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body[:6000]


def detect_risk_flags(subject: str, body: str) -> tuple[str, list[str]]:
    haystack = f"{subject}\n{body}".lower()
    reasons: list[str] = []
    for key, patterns in SENSITIVE_PATTERNS.items():
        if any(re.search(pattern, haystack) for pattern in patterns):
            reasons.append(key)
    if reasons:
        return "high", reasons
    if any(token in haystack for token in ("urgent", "asap", "escalate")):
        return "medium", ["urgency_language"]
    return "low", []


# Category taxonomy — mirrors the Gmail labels the HR team uses to segregate the inbox.
# `HR-Inbox` is the catch-all / general bucket when nothing more specific matches.
CATEGORIES = [
    "Background Verification",
    "Ex-Payment Issue",
    "Experience Letter",
    "Leave of Absence",
    "Offboarding/Resign",
    "Onboarding",
    "Participation Status",
    "Relieving Letter",
    "Resigned",
    "RFE",
    "HR-Inbox",
]
DEFAULT_CATEGORY = "HR-Inbox"

# Generic auto-reply for emails that don't match a specific template (template-only mode).
# The FAQ link + "automated email" line are appended as the footer in main.py.
DEFAULT_REPLY_BODY = (
    "Hi,\n\n"
    "Thank you for reaching out to the Community Dreams Foundation HR team.\n\n"
    "Most volunteer questions are answered on our FAQ page and by our chatbot (linked below). "
    "If your question isn't covered there, please raise a support ticket on the CDF portal and "
    "our team will assist you.\n\n"
    "Best regards,\nHR Team"
)

# Ordered most-specific → least-specific. The first category with an `anchor` phrase
# wins as a STRONG match; a `weak`-only hit still classifies but is not "strong"
# (auto-reply requires a strong match). RFE is checked first so immigration cases
# never get swallowed by the more generic letter categories.
_CATEGORY_RULES: list[tuple[str, list[str], list[str]]] = [
    # category, anchor (high-signal) phrases, weak (supporting) keywords
    ("RFE", ["rfe", "request for evidence", "i-983", "i983", "uscis", "stem opt"], ["evidence", "supervisor details"]),
    ("Relieving Letter", ["relieving letter", "reliving letter", "relieve letter", "relieving document"], ["relieve", "relieving"]),
    ("Experience Letter", ["experience letter", "employment verification letter", "employee verification letter", "employment letter"], ["verification letter", "proof of employment", "employment proof"]),
    ("Background Verification", ["background verification", "background check", "hireright", "sterling", "accurate background", "verify my employment", "verify employment", "employment verification"], ["verification", "verify", "verifier"]),
    ("Offboarding/Resign", ["resignation", "resign from", "i resign", "want to resign", "submit my resignation", "offboarding", "offboard", "last working day", "withdraw my resignation"], ["resign", "notice period", "leaving cdf"]),
    ("Resigned", ["i have resigned", "already resigned", "after my resignation", "since i resigned", "post resignation", "i resigned"], ["resigned"]),
    ("Leave of Absence", ["leave of absence", "medical leave", "family emergency", "take a leave", "loa", "travel leave", "personal leave"], ["leave", "absence", "sabbatical"]),
    ("Ex-Payment Issue", ["membership fee", "paypal", "late fee", "autopay", "auto-pay", "hardship waiver", "refund", "double charged", "charged twice", "payment plan", "installment"], ["payment", "pay", "invoice", "due", "fee", "charged", "waiver"]),
    ("Participation Status", ["participation status", "am i active", "still active", "inactive status", "reduce my hours", "minimum hours", "20 hours", "weekly hours", "hours requirement"], ["participation", "active member", "inactive", "engagement", "hours per week"]),
    ("Onboarding", ["onboarding", "new hire", "orientation", "offer letter", "documents under review", "registration", "sevp", "sevis", "i-20", "i20"], ["onboard", "start date", "join cdf", "register", "document verification"]),
]


def classify_category(subject: str, body: str) -> tuple[str, bool]:
    """Classify an email into one of the Gmail-label categories.

    Returns (category, is_strong). `is_strong` is True when a high-signal anchor
    phrase matched — auto-reply is gated on strong matches only.
    """
    text = f"{subject}\n{body}".lower()
    for category, anchors, weak in _CATEGORY_RULES:
        if any(a in text for a in anchors):
            return category, True
        if any(w in text for w in weak):
            return category, False
    return DEFAULT_CATEGORY, False


def infer_intent(subject: str, body: str) -> str:
    """Back-compat shim: returns the category label only."""
    return classify_category(subject, body)[0]


def match_template(subject: str, body: str, templates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    """Specific-phrase match: find the template whose trigger phrases best fit the email.

    Each matched trigger contributes its word-count (longer, more specific phrases win).
    Returns (best_template, score). A match requires score >= 2 — i.e. one 2-word phrase
    (e.g. "stem opt") or a distinctive single token paired with another hit — so a lone
    generic word can't trigger an auto-reply.
    """
    text = f"{subject}\n{body}".lower()
    best: dict[str, Any] | None = None
    best_score = 0
    for t in templates:
        raw = t.get("trigger_phrases") or t.get("triggers") or ""
        phrases = [p.strip().lower() for p in raw.replace(",", "\n").split("\n") if p.strip()]
        score = 0
        for phrase in phrases:
            if phrase and phrase in text:
                # Distinctive short tokens (digits/hyphens like "w-2", "i-983") count as 2.
                words = phrase.split()
                score += len(words) if len(words) > 1 else (2 if any(c.isdigit() or c == "-" for c in phrase) else 1)
        if score > best_score:
            best_score = score
            best = t
    if best is not None and best_score >= 2:
        return best, best_score
    return None, best_score


_GREETING_PREFIXES = (
    "dear ", "hi ", "hi,", "hi.", "hello", "hey ", "hey,",
    "good morning", "good afternoon", "good evening",
    "greetings", "to whom", "team,",
    "i hope", "i'm writing", "i am writing",
    "thanks for", "thank you for",
)


def summarize_question(subject: str, body: str) -> str:
    """Pick the first substantive (non-greeting) line of the email.

    Prefers lines that contain a question mark or interrogatives.
    """
    interrogatives = ("how ", "what ", "when ", "where ", "why ",
                       "can ", "could ", "should ", "will ", "is ", "are ",
                       "do ", "does ", "i would like", "i need", "please")
    lines = [raw.strip() for raw in body.split("\n") if raw.strip()]
    non_greeting = []
    for line in lines:
        low = line.lower()
        if any(low.startswith(prefix) for prefix in _GREETING_PREFIXES):
            continue
        non_greeting.append(line)

    # Prefer a line that looks like a question
    for line in non_greeting:
        low = line.lower()
        if "?" in line or any(tok in low for tok in interrogatives):
            return (line[:220].rstrip() + "...") if len(line) > 220 else line

    if non_greeting:
        first = non_greeting[0]
        return (first[:220].rstrip() + "...") if len(first) > 220 else first
    return subject.strip() or "General HR question"


def build_retrieval_query(subject: str, body: str) -> str:
    """Pinecone gets MUCH more context than the one-line summary.

    Includes the subject + first ~1500 chars of body so multi-question emails
    can hit multiple policy snippets.
    """
    parts = [subject.strip()] if subject and subject.strip() else []
    parts.append(body[:1500].strip())
    return "\n".join(p for p in parts if p)


def _chroma_snippets(question: str, top_k: int) -> list[dict[str, Any]]:
    if not CHROMA_PATH.exists():
        return []
    try:
        import chromadb
    except Exception:
        return []

    try:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        collection = client.get_collection("fellowship_pdfs")
        result = collection.query(query_texts=[question], n_results=top_k)
    except Exception:
        return []

    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    snippets: list[dict[str, Any]] = []
    for doc, meta, dist in zip(docs, metas, dists):
        snippets.append(
            {
                "source": (meta or {}).get("source_file", "unknown"),
                "score": round(max(0.0, 1.0 - float(dist or 1.0)), 4),
                "excerpt": (doc or "")[:900],
            }
        )
    return snippets


def _pinecone_snippets(question: str, top_k: int) -> list[dict[str, Any]]:
    gkey = _gemini_key()
    pkey = _pinecone_key()
    if not gkey:
        logger.warning("Pinecone retrieval skipped: GEMINI_API_KEY missing")
        return []
    if not pkey:
        logger.warning("Pinecone retrieval skipped: PINECONE_KEY missing")
        return []

    try:
        from google import genai
        from google.genai import types
        from pinecone import Pinecone
    except Exception as exc:
        logger.warning("Pinecone retrieval skipped: import failed: %s", exc)
        return []

    try:
        client = genai.Client(api_key=gkey)
        emb_resp = client.models.embed_content(
            model=GEMINI_EMBEDDING_MODEL,
            contents=[question],
            config=types.EmbedContentConfig(
                output_dimensionality=GEMINI_EMBEDDING_DIM,
                task_type="RETRIEVAL_QUERY",
            ),
        )
        emb = list(emb_resp.embeddings[0].values)
        pc = Pinecone(api_key=pkey)
        index = pc.Index(PINECONE_INDEX, host=PINECONE_HOST)
        res = index.query(vector=emb, top_k=top_k, include_metadata=True)
    except Exception as exc:
        logger.warning("Pinecone retrieval failed: %s", exc)
        return []

    snippets: list[dict[str, Any]] = []
    for match in res.get("matches", []):
        meta = match.metadata or {}
        snippets.append(
            {
                "source": meta.get("source_file", "unknown"),
                "score": round(float(match.score or 0.0), 4),
                "excerpt": str(meta.get("text", ""))[:900],
            }
        )
    return snippets


def retrieve_snippets(question: str, top_k: int) -> tuple[list[dict[str, Any]], str]:
    snippets = _pinecone_snippets(question, top_k=top_k)
    if snippets:
        return snippets, "pinecone"

    snippets = _chroma_snippets(question, top_k=top_k)
    if snippets:
        return snippets, "chroma"

    return [], "none"


def _draft_prompt(
    subject: str,
    from_email: str,
    intent: str,
    question_summary: str,
    full_body: str,
    snippets: list[dict[str, Any]],
) -> str:
    context = "\n\n".join(
        f"Source: {s.get('source')} | score: {s.get('score')}\n{s.get('excerpt')}" for s in snippets
    )
    body_excerpt = (full_body or "").strip()
    if len(body_excerpt) > 4000:
        body_excerpt = body_excerpt[:4000] + "\n... [truncated]"
    return f"""You are an HR assistant for the Community Dreams Foundation (CDF) HR team.
Draft a clean, professional email reply that the recipient will read in Gmail.

FORMATTING RULES — STRICT:
- Plain text only. NO markdown. NO asterisks for bold (** **). NO underscores for italic.
  NO backticks. NO hash signs for headings. Do not use any character to denote formatting.
- Use blank lines between paragraphs for readability.
- If the email contains multiple distinct questions:
  - Use simple numbered headings on their own line like:
        1) Onboarding timeline
        2) Document verification
    (Use the digit + closing parenthesis. Do not bold or wrap them in symbols.)
  - Place the answer on the next line(s) below each heading.
  - Insert a blank line between each numbered block.
- Open with a brief greeting such as "Hi [name]," — use just the first name from the sender's
  address if available, otherwise "Hello,".
- Close with "Best regards," on its own line then "HR Team" on the next line.

CONTENT RULES:
- Use ONLY the provided policy excerpts when stating policy facts.
- If evidence is weak or missing for any question, explicitly say HR will confirm details
  for that specific point. Do not invent procedures, timelines, or contact paths.
- Keep the tone warm, concise, and direct.

Intent: {intent}
From: {from_email}
Subject: {subject}
Primary question (auto-extracted): {question_summary}

Full email body from sender:
\"\"\"
{body_excerpt or "(empty)"}
\"\"\"

Policy excerpts (retrieved from HR FAQs & guides):
{context or "No policy excerpts found."}

Return JSON with keys:
- draft_body (string) — the full reply, ready to send. Plain text, no markdown.
- confidence (0.0 to 1.0) — your confidence the reply is accurate per the policy excerpts
- citations (array of source filenames you used)
"""


_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MARKDOWN_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.DOTALL)
_MARKDOWN_UNDERLINE_BOLD = re.compile(r"__(.+?)__", re.DOTALL)
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_BACKTICK = re.compile(r"`([^`]+)`")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def strip_markdown_for_email(text: str) -> str:
    """Defense in depth: strip markdown that Gemini might emit despite the prompt.

    Gmail doesn't render markdown — asterisks would appear as literal characters.
    """
    if not text:
        return text
    out = text
    out = _MARKDOWN_BOLD.sub(r"\1", out)
    out = _MARKDOWN_UNDERLINE_BOLD.sub(r"\1", out)
    out = _MARKDOWN_ITALIC.sub(r"\1", out)
    out = _MARKDOWN_HEADING.sub("", out)
    out = _MARKDOWN_BACKTICK.sub(r"\1", out)
    out = _MARKDOWN_LINK.sub(r"\1 (\2)", out)
    out = _MULTI_BLANK_LINES.sub("\n\n", out)
    return out.strip()


def _parse_draft_json(raw: str) -> tuple[str, float, list[str]]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Gemini sometimes returns truncated JSON. Recover the draft_body via regex
        # so a long reply still gets through instead of falling back to deterministic.
        match = re.search(r'"draft_body"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, flags=re.DOTALL)
        if not match:
            raise
        draft_raw = match.group(1)
        try:
            draft = json.loads(f'"{draft_raw}"')  # decode JSON string escapes
        except json.JSONDecodeError:
            draft = draft_raw.encode().decode("unicode_escape", errors="replace")
        conf_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
        conf = float(conf_match.group(1)) if conf_match else 0.6
        logger.warning("Gemini draft JSON truncated; recovered via regex (len=%d)", len(draft))
        return strip_markdown_for_email(draft.strip()), max(0.0, min(1.0, conf)), []
    draft = strip_markdown_for_email(str(obj.get("draft_body") or "").strip())
    conf = float(obj.get("confidence") or 0.0)
    citations = [str(c) for c in (obj.get("citations") or []) if str(c).strip()]
    return draft, max(0.0, min(1.0, conf)), citations


def _draft_with_gemini(
    subject: str,
    from_email: str,
    intent: str,
    question_summary: str,
    full_body: str,
    snippets: list[dict[str, Any]],
) -> tuple[str, float, list[str]]:
    gkey = _gemini_key()
    if not gkey:
        logger.warning("Gemini draft skipped: GEMINI_API_KEY missing")
        return "", 0.0, []
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        logger.warning("Gemini draft skipped: import failed: %s", exc)
        return "", 0.0, []

    prompt = _draft_prompt(subject, from_email, intent, question_summary, full_body, snippets)
    client = genai.Client(api_key=gkey)
    config = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
        max_output_tokens=2048,
    )

    # Retry aggressively — Gemini APIs blip occasionally and falling back to deterministic
    # text in those moments produces low-quality HR replies. We retry every failure (with
    # short backoff) and try the lite model as the last attempt.
    import time
    attempts = [
        (GEMINI_REPLY_MODEL, 0.0),
        (GEMINI_REPLY_MODEL, 1.0),
        (GEMINI_REPLY_MODEL, 2.5),
        ("gemini-2.5-flash-lite", 0.5),
    ]
    # Errors that mean "don't bother retrying" — auth/quota issues that won't self-resolve.
    hard_fail_codes = ("UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT", "NOT_FOUND")
    last_err: Exception | None = None
    for model, backoff in attempts:
        if backoff:
            time.sleep(backoff)
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=config
            )
            raw = (response.text or "").strip()
            if not raw:
                logger.warning("Gemini draft empty response (model=%s)", model)
                last_err = RuntimeError(f"empty response from {model}")
                continue
            try:
                return _parse_draft_json(raw)
            except Exception as parse_exc:
                logger.warning(
                    "Gemini draft JSON parse failed (model=%s, len=%d): %s",
                    model, len(raw), str(parse_exc)[:200],
                )
                last_err = parse_exc
                continue
        except Exception as exc:
            last_err = exc
            msg = str(exc)
            if any(code in msg for code in hard_fail_codes):
                logger.warning("Gemini draft hard failure (model=%s): %s", model, msg[:200])
                break
            logger.warning("Gemini draft transient error (model=%s): %s", model, msg[:200])
            continue
    if last_err:
        logger.warning("Gemini draft exhausted retries: %s", str(last_err)[:200])
    return "", 0.0, []


def _draft_with_llm(
    subject: str,
    from_email: str,
    intent: str,
    question_summary: str,
    full_body: str,
    snippets: list[dict[str, Any]],
) -> tuple[str, float, list[str], str]:
    draft, conf, citations = _draft_with_gemini(
        subject, from_email, intent, question_summary, full_body, snippets
    )
    if draft:
        return draft, conf, citations, "gemini"
    return "", 0.0, [], "none"


def _fallback_draft(question_summary: str, intent: str, snippets: list[dict[str, Any]]) -> tuple[str, float, list[str]]:
    if snippets:
        source = snippets[0].get("source", "policy document")
        draft = (
            "Thanks for your email.\n\n"
            f"We reviewed your question ({question_summary}) and found relevant information in {source}. "
            "Please review this draft and confirm details before sending.\n\n"
            "If you need immediate confirmation, HR will follow up directly."
        )
        confidence = min(0.75, max(0.45, float(snippets[0].get("score", 0.5))))
        citations = [str(source)]
    else:
        draft = (
            "Thanks for reaching out.\n\n"
            f"We received your {intent} question ({question_summary}). "
            "I could not find enough policy context automatically, so this has been routed to HR review.\n\n"
            "HR will follow up shortly."
        )
        confidence = 0.25
        citations = []
    return draft, confidence, citations


def _compute_quality_score(
    confidence: float,
    snippets: list[dict[str, Any]],
    citation_count: int,
    risk_level: str,
) -> float:
    top_score = float(snippets[0].get("score", 0.0)) if snippets else 0.0
    snippet_factor = min(1.0, len(snippets) / 5.0)
    citation_factor = min(1.0, citation_count / 2.0)
    risk_penalty = 0.2 if risk_level == "high" else 0.1 if risk_level == "medium" else 0.0
    quality = (confidence * 0.5) + (top_score * 0.3) + (snippet_factor * 0.1) + (citation_factor * 0.1) - risk_penalty
    return round(max(0.0, min(1.0, quality)), 4)


def process_email(
    subject: str,
    from_email: str,
    body: str,
    *,
    templates: list[dict[str, Any]] | None = None,
    top_k: int = 5,
    auto_reply_threshold: float = 0.75,
) -> ProcessingResult:
    cleaned = clean_email_body(body)
    category, category_strong = classify_category(subject, cleaned)
    intent = category
    risk_level, risk_reasons = detect_risk_flags(subject, cleaned)
    summary = summarize_question(subject, cleaned)

    # TEMPLATE-FIRST (primary path): a trigger-phrase match short-circuits here — the
    # standard response is used verbatim and NO Gemini API call is made. Gemini is only
    # the fallback below when nothing matches, keeping API usage (and cost) minimal.
    if templates:
        matched, match_score = match_template(subject, cleaned, templates)
        if matched:
            return ProcessingResult(
                intent=matched["category"],
                category=matched["category"],
                category_strong=True,
                confidence=1.0,
                quality_score=1.0,
                draft_body=matched["body"],
                snippets=[],
                question_summary=summary,
                cleaned_body=cleaned,
                retrieval_backend="none",
                recommended_action="auto_reply",
                top_snippet_score=0.0,
                citation_count=0,
                risk_level=risk_level,
                risk_reasons=risk_reasons,
                generation_mode="template",
                escalation_reason=None,
                matched_template=matched,
                match_score=match_score,
            )

    # TEMPLATE-ONLY MODE: when TEMPLATE_ONLY=true, never call Gemini. Unmatched emails get
    # a generic FAQ/ticket reply and still auto-send (legal/emergency are held upstream in
    # main.py). Nothing waits on Gemini; no cost, no 429 delays.
    if (os.getenv("TEMPLATE_ONLY") or "").strip().lower() == "true":
        return ProcessingResult(
            intent=category,
            category=category,
            category_strong=category_strong,
            confidence=1.0,
            quality_score=1.0,
            draft_body=DEFAULT_REPLY_BODY,
            snippets=[],
            question_summary=summary,
            cleaned_body=cleaned,
            retrieval_backend="none",
            recommended_action="auto_reply",
            top_snippet_score=0.0,
            citation_count=0,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            generation_mode="default_template",
            escalation_reason=None,
        )

    # FALLBACK (no template matched): use the Gemini pipeline — retrieve policy + draft.
    retrieval_query = build_retrieval_query(subject, cleaned)
    snippets, backend = retrieve_snippets(retrieval_query, top_k=top_k)
    snippets_sorted = sorted(snippets, key=lambda s: float(s.get("score", 0.0)), reverse=True)
    top_snippet_score = float(snippets_sorted[0].get("score", 0.0)) if snippets_sorted else 0.0

    draft, conf, citations, generation_mode = _draft_with_llm(
        subject, from_email, intent, summary, cleaned, snippets_sorted
    )
    if not draft:
        draft, conf, citations = _fallback_draft(summary, intent, snippets_sorted)
        generation_mode = "fallback"

    escalation_reason = None
    if conf < 0.65:
        escalation_reason = "low_confidence"
    elif not snippets_sorted:
        escalation_reason = "no_retrieval_context"
    elif risk_level == "high":
        escalation_reason = "sensitive_topic"

    citation_count = len(set(citations))
    quality_score = _compute_quality_score(conf, snippets_sorted, citation_count, risk_level)

    # Hard guardrails before automatic replies.
    if risk_level == "high":
        conf = min(conf, 0.6)
    if top_snippet_score < 0.55:
        conf = min(conf, 0.7)
        if escalation_reason is None:
            escalation_reason = "weak_retrieval_match"
    if citation_count == 0 and snippets_sorted:
        if escalation_reason is None:
            escalation_reason = "missing_citation"

    # Every email goes to the dashboard for HR review and "Approve & Send".
    # Auto-reply path is kept in code but only enabled when ALLOW_AUTO_REPLY=true.
    allow_auto_reply = (os.getenv("ALLOW_AUTO_REPLY") or "").strip().lower() == "true"
    if allow_auto_reply and conf >= auto_reply_threshold and escalation_reason is None:
        recommended_action = "reply_in_thread"
    else:
        recommended_action = "queue_for_review"

    return ProcessingResult(
        intent=intent,
        category=category,
        category_strong=category_strong,
        confidence=round(conf, 4),
        quality_score=quality_score,
        draft_body=draft,
        snippets=snippets_sorted,
        question_summary=summary,
        cleaned_body=cleaned,
        retrieval_backend=backend,
        recommended_action=recommended_action,
        top_snippet_score=round(top_snippet_score, 4),
        citation_count=citation_count,
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        generation_mode=generation_mode,
        escalation_reason=escalation_reason,
    )
