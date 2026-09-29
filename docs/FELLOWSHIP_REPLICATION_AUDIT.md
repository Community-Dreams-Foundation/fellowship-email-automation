# Fellowship Email Automation — HR-Specific Code Audit

**Purpose:** Identify every place in the current HR Email Automation codebase that is
hardcoded or specific to HR, and note what a Fellowship version would need instead.
**Status:** Read-only audit. **No code was modified.**

---

## How to read this document

**This audit takes no position on the three open architecture questions** that Swayam is
confirming:

1. Separate Pinecone index for Fellowship, or shared index with metadata separation?
2. One shared configurable codebase, or a separate copy for Fellowship?
3. Same repository, or a new one?

Every row below describes **what has to be different for Fellowship**, not **how** to make
it different. A row like "needs a Fellowship-specific value" is true whether that value
arrives via a second `.env` file, a domain switch in one shared config, or a separate
deployment — so the row stays useful no matter how the three questions are answered.

Where something I found is *relevant* to one of those questions, I flag it as
`→ relevant to Q1/Q2/Q3` and state the technical fact only. **Section 8** collects those
flags in one place so they're easy to hand to Swayam without re-reading the tables.

Every claim cites `file:line` and quotes the actual line. Nothing here is from memory.

**Scale:** 107 entries across 9 areas, each with a `file:line` citation and a quoted line.

**Coverage:** the five areas requested (§1–§5) are covered in full. §6–§6c cover everything
else in the repository that carries an HR-specific value — deployment config, the Make.com
blueprint, and the remaining app files — so that "what would need to change" is complete
rather than limited to the five files named.

A small number of entries are marked **"reusable as-is"** — places I checked and found
*not* HR-specific. They are kept in the tables on purpose, so that the check is visible
rather than silently omitted.

---

## 1. `ingest.py` — ingestion, chunking, embeddings, vector store

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Pinecone index name default | `ingest.py:62` | `return os.environ.get("INDEX_NAME", "cdf-hr-policy-index").strip()` | A Fellowship index name. Note the HR name is baked in as the **fallback**, so an unset/typo'd `INDEX_NAME` silently writes to HR's index. `→ relevant to Q1` |
| Pinecone host default | `ingest.py:43` | `"https://cdf-hr-policy-index-b2rfj3w.svc.aped-4627-b74a.pinecone.io",` | The host that matches whatever index Fellowship uses. Same silent-fallback risk as above. `→ relevant to Q1` |
| Source document folder | `ingest.py:66` | `raw = (os.environ.get("PDF_FOLDER") or ".").strip()` | A Fellowship source folder. **Note:** the *code* default is `.` (repo root), not `HR FAQ` — the HR value comes from `.env` (see §4). `PINECONE_EMBEDDINGS.md:53` describes the default as `HR FAQ/`, which does not match the code. |
| Chroma collection name | `ingest.py:193`, `ingest.py:197` | `client.delete_collection("hr_pdfs")` / `name="hr_pdfs",` | A Fellowship collection name, or the two products will read and overwrite each other's local Chroma data. |
| Chroma collection description | `ingest.py:198` | `metadata={"description": "HR PDFs"},` | Fellowship wording. Cosmetic, but it appears in Chroma tooling. |
| Chroma read path | `ingest.py:292` | `collection = client.get_collection("hr_pdfs")` | Must match whatever the write side uses (above). |
| Extracted-text output folder | `ingest.py:396` | `out_txt = script_parent / "out"` | A Fellowship output folder. This path is **not** configurable by env var. `→ relevant to Q3` |
| Local Chroma path | `ingest.py:397` | `chroma_path = out_txt / "chroma_db"` | Derived from `out/` above, so it inherits the same collision. |
| Ingest wipes the whole index | `ingest.py:265` | `index.delete(delete_all=True)` | **Technical fact to be aware of:** every ingest run deletes *all* vectors in the target index before upserting, with no namespace or metadata scoping. `→ relevant to Q1` |
| File types read at ingest | `ingest.py:105-106` | `pdfs = sorted(pdf_dir.glob("*.pdf"))` / `docxs = sorted(p for p in pdf_dir.glob("*.docx") ...)` | Whatever formats the Fellowship knowledge base arrives in. Only `.pdf`/`.docx` are read, and `glob()` is **non-recursive** — subfolders and `.md`/`.json`/`.txt` files are invisible. |
| Docstring: index default | `ingest.py:12` | `  INDEX_NAME            (default: cdf-hr-policy-index)` | Fellowship wording. |
| Docstring: folder default | `ingest.py:14` | `  PDF_FOLDER            (default: .  → folder containing ingest.py)` | Fellowship wording. |

---

## 2. `web/app/pipeline.py` — classification, retrieval, drafting

### 2a. Configuration and identifiers

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Logger name | `pipeline.py:11` | `logger = logging.getLogger("hr_pipeline")` | A Fellowship logger name, so the two products' logs can be told apart. |
| Pinecone index default | `pipeline.py:16` | `PINECONE_INDEX = os.getenv("INDEX_NAME", "cdf-hr-policy-index")` | Same as `ingest.py:62`. The query side and build side each hardcode the HR fallback separately. `→ relevant to Q1` |
| Pinecone host default | `pipeline.py:19` | `"https://cdf-hr-policy-index-b2rfj3w.svc.aped-4627-b74a.pinecone.io",` | Same as `ingest.py:43`. |
| Chroma path default | `pipeline.py:22-25` | `str((Path(__file__).resolve().parents[2] / "out" / "chroma_db"))` | Must match whatever `ingest.py` writes. |
| Chroma collection name | `pipeline.py:262` | `collection = client.get_collection("hr_pdfs")` | Must match `ingest.py:197`. |

### 2b. `CATEGORIES` — the category taxonomy · **entirely HR-specific**

`pipeline.py:111-123`:

```python
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
```

All 11 describe the HR employment/volunteer lifecycle. The comment at `pipeline.py:109`
confirms their origin:

```python
# Category taxonomy — mirrors the Gmail labels the HR team uses to segregate the inbox.
```

**What Fellowship would need:** a completely new taxonomy derived from Fellowship's own
Gmail labels. None of the 11 transfers unchanged. This list is also **imported by the web
layer** at `main.py:29` (`from .pipeline import CATEGORIES, process_email`) and rendered in
the Templates page dropdown at `main.py:674`, so changing it changes the UI too.

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Default / catch-all category | `pipeline.py:124` | `DEFAULT_CATEGORY = "HR-Inbox"` | A Fellowship catch-all label. |
| `"HR-Inbox"` as a hardcoded sentinel | `main.py:777`, `main.py:971`, `main.py:1232`, `db.py:400` | `_topic = result.category if result.category and result.category != "HR-Inbox" else None` (`main.py:971`) | The same literal string is compared in **4 places outside** the `CATEGORIES` list. Renaming the catch-all requires changing all four, or they silently stop matching. |

### 2c. `_CATEGORY_RULES` — classification phrases · **entirely HR-specific**

`pipeline.py:141-153`. All 10 rules, quoted in full as proof:

```python
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
```

**What Fellowship would need:** every phrase replaced. These reference HR-only concepts —
US immigration forms (`i-983`, `uscis`, `stem opt`, `i-20`, `sevp`, `sevis`), background-check
vendors (`hireright`, `sterling`), employment documents (`relieving letter`, `experience
letter`, `offer letter`), and CDF's specific volunteer rules (`20 hours`, `hardship waiver`,
`leaving cdf`). Fellowship would need its own equivalents (likely application, cohort,
event, stipend, and project-submission vocabulary), plus the `strong`/`weak` distinction
re-derived for those phrases.

> **Carry-over note, not a new finding:** the matching logic itself has a confirmed bug
> documented in `RAG_RETRIEVAL_FINDINGS.md` §D1 — first-match-wins plus substring matching
> makes the `Resigned` category unreachable and produces false strong matches. A Fellowship
> copy built from this code inherits that bug regardless of which phrases are used.

### 2d. `SENSITIVE_PATTERNS` — you asked whether these are reusable

`pipeline.py:60-65`:

```python
SENSITIVE_PATTERNS: dict[str, list[str]] = {
    "legal_threat": [r"\blawsuit\b", r"\battorney\b", r"\blegal action\b", r"\bdiscrimination\b"],
    "termination": [r"\btermination\b", r"\bfired\b", r"\blayoff\b"],
    "compensation_dispute": [r"\bunderpaid\b", r"\bpay dispute\b", r"\bwrong paycheck\b"],
    "medical_privacy": [r"\bmedical condition\b", r"\bdiagnosis\b", r"\bhipaa\b"],
}
```

**Answer: 2 of the 4 groups are generic and reusable as-is; 2 are employment-specific.**

| Group | Reusable? | Reasoning |
|---|---|---|
| `legal_threat` | **Yes, as-is** | `lawsuit`, `attorney`, `legal action`, `discrimination` apply to any external correspondence, not just employment. |
| `medical_privacy` | **Yes, as-is** | `medical condition`, `diagnosis`, `hipaa` are generic privacy-sensitive terms. |
| `termination` | **No — needs Fellowship vocabulary** | `termination`, `fired`, `layoff` are employment words. A fellow is not "fired"; the equivalent events are being removed from a cohort, dropped from the program, or having an application rejected. |
| `compensation_dispute` | **No — needs Fellowship vocabulary** | `underpaid`, `wrong paycheck` assume payroll. Fellowship's money flows are likely stipends, grants, reimbursements, or fees — different words entirely. |

The *structure* (regex groups → `high` risk) is domain-neutral and transfers fine; only two
of the four word lists need rewriting. The surrounding function `detect_risk_flags()`
(`pipeline.py:96-106`) is generic.

### 2e. The Gemini drafting prompt · **HR-specific throughout**

This is the item you flagged as critical, and it is the most HR-bound text in the codebase.
`_draft_prompt()` spans `pipeline.py:343-398`. Every HR reference, quoted:

| Location | Current HR-specific text | What Fellowship would need |
|---|---|---|
| `pipeline.py:357` | `return f"""You are an HR assistant for the Community Dreams Foundation (CDF) HR team.` | The role identity rewritten — "HR assistant" and "HR team" both appear in one line. |
| `pipeline.py:373` | `- Close with "Best regards," on its own line then "HR Team" on the next line.` | The literal signature block written into every reply. |
| `pipeline.py:377-378` | `- If evidence is weak or missing for any question, explicitly say HR will confirm details`<br>`  for that specific point. Do not invent procedures, timelines, or contact paths.` | The named fallback authority. This drives the hedging behaviour in every low-evidence reply. |
| `pipeline.py:391` | `Policy excerpts (retrieved from HR FAQs & guides):` | How the retrieved context is labelled to the model. |
| `pipeline.py:381` | `Intent: {intent}` | Not HR-worded itself, but the value injected comes from the HR `CATEGORIES` list (§2b). |

The remaining prompt content — the formatting rules at `pipeline.py:360-373` (plain text, no
markdown, numbered headings) and the JSON output contract at `pipeline.py:394-398` — is
**domain-neutral and reusable as-is**.

### 2f. Other HR-worded reply text in `pipeline.py`

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Generic no-match reply | `pipeline.py:130-132` | `"Thank you for reaching out to the Community Dreams Foundation HR team.\n\n"`<br>`"Most volunteer questions are answered on our FAQ page and by our chatbot (linked below). "`<br>`"If your question isn't covered there, please raise a support ticket on the CDF portal and "` | Fellowship wording, Fellowship FAQ, Fellowship support route. Names the team, the audience ("volunteer"), and the portal. |
| Fallback draft text | `pipeline.py:549` | `"If you need immediate confirmation, HR will follow up directly."` | Fellowship's escalation contact. |
| Fallback draft text (no snippets) | `pipeline.py:558` | `"HR will follow up shortly."` | Fellowship's escalation contact. |

---

## 3. `web/app/main.py` — API, auto-send decision, dashboard

### 3a. Thresholds you asked about

| Item | Location | Current value | Fellowship consideration |
|---|---|---|---|
| Soft review threshold | `main.py:38` | `AUTO_REPLY_THRESHOLD = float(os.getenv("AUTO_REPLY_CONFIDENCE_THRESHOLD", "0.75"))` | Already env-configurable. The number itself is **not** HR-specific in meaning, but it was tuned against HR's corpus and traffic, so it should be re-validated rather than assumed correct for Fellowship. |
| Auto-send threshold | `main.py:43` | `AI_AUTOSEND_CONFIDENCE = float(os.getenv("AI_AUTOSEND_CONFIDENCE", "0.90"))` | Same: env-configurable, but tuned for HR. This is the gate that decides whether a reply reaches a human. |
| Global kill switch | `main.py:41` | `AUTO_REPLY_DISABLED = (os.getenv("DISABLE_AUTO_REPLY") or "").strip().lower() == "true"` | Generic. Needs its own value per product so one can be paused without pausing the other. |

Both thresholds are **already externalised** — no code change is required to give Fellowship
different numbers, only a different value source.

### 3b. `_AUTO_REPLY_BLOCK_PHRASES` — largely reusable

`main.py:86-94`:

```python
_AUTO_REPLY_BLOCK_PHRASES = (
    # Legal threats
    "lawsuit", "law suit", "attorney", "legal action", "take legal", "discrimination",
    "harassment", "retaliation", "grievance", "lawyer", "sue you", "suing", "litigation",
    "defamation", "wrongful termination", "take to court",
    # Medical / family emergencies
    "medical emergency", "family emergency", "hospitalized", "in the hospital",
    "passed away", "death in the family", "suicide", "self-harm", "self harm",
)
```

**Assessment: 24 of 25 phrases are generic safety terms** that apply to any external
correspondence. Only `"wrongful termination"` is employment-specific. Fellowship could reuse
this list almost unchanged, and would want to *add* any Fellowship-specific escalation
triggers rather than remove much.

> **Carry-over note:** `RAG_RETRIEVAL_FINDINGS.md` §E1 documents that this list and
> `SENSITIVE_PATTERNS` (§2d) disagree with each other, and that a template match auto-sends
> even when risk is high. A Fellowship copy inherits both issues.

### 3c. Branding, copy, and identity strings

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| App title | `main.py:36` | `app = FastAPI(title="HR Email Console", version="0.1.0")` | Fellowship product name (appears in OpenAPI docs). |
| Logger name | `main.py:31` | `logger = logging.getLogger("hr_console")` | A distinguishable logger name. |
| Session cookie name | `main.py:148` | `SESSION_COOKIE = "cdf_hr_session"` | A Fellowship cookie name. If both consoles are ever served from the same domain, an identical cookie name means one login overwrites the other. `→ relevant to Q3` |
| Default dashboard user | `main.py:129` | `DASHBOARD_USER = (os.getenv("DASHBOARD_USER") or "hr").strip()` | A Fellowship default username. |
| FAQ URL default | `main.py:48` | `FAQ_URL = (os.getenv("FAQ_URL") or "https://faq.cdreamstream.org").strip()` | Fellowship's FAQ page. Env-configurable, HR value hardcoded as fallback. |
| Email footer (auto-sent) | `main.py:58` | `f"This is an automated email. Please check out our FAQ page or ask our chatbot (CDF Ally): {FAQ_URL}"` | Fellowship wording. Names the CDF Ally chatbot specifically. |
| Email footer (reviewed) | `main.py:62` | `f"For more details, please check out our FAQ page or ask our chatbot (CDF Ally): {FAQ_URL}"` | Same. |
| Slack notification headline | `main.py:1086` | `headline = f"New HR case{follow_marker}{risk_marker}"` | Fellowship wording, so a shared Slack channel can distinguish the two. |
| Approve action default actor | `main.py:491` | `actor: str = Form("hr@company.com")` | Fellowship's actor identity. Note this is a placeholder domain (`company.com`), inconsistent with the others. |
| Delete action default actor | `main.py:510` | `actor: str = Form("hr@cdreams.org")` | Fellowship's actor identity. |
| Edit-draft default actor | `main.py:524` | `actor: str = Form("hr@company.com")` | Fellowship's actor identity. |
| Feedback default actor | `main.py:543` | `actor: str = Form("hr@company.com"),` | Fellowship's actor identity. |

### 3d. `api_process_email` endpoint logic

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Internal-sender skip rule | `main.py:898` | `elif "humanresources@cdreams.org" in fe or "@cdreams.org" in fe:` | **Needs review before reuse.** The rule skips any email from an `@cdreams.org` address. HR's own mailbox is named explicitly. Since Fellowship's team address is also on `@cdreams.org` (see `db.py:418`), a Fellowship deployment would skip mail from CDF colleagues — including anything HR forwards to Fellowship. |
| Catch-all category comparison | `main.py:971` | `_topic = result.category if result.category and result.category != "HR-Inbox" else None` | See §2b — hardcoded literal, must track `DEFAULT_CATEGORY`. |
| Same comparison in redraft | `main.py:1232` | `topic = result.category if result.category and result.category != "HR-Inbox" else None` | Same. |
| Category fallback in stats page | `main.py:777` | `d["category"] = d["category"] or "HR-Inbox"` | Same. |
| Categories imported for the UI | `main.py:29` | `from .pipeline import CATEGORIES, process_email` | The Templates page dropdown is populated from the HR taxonomy (`main.py:674`). |
| No-reply sender patterns | `main.py:108-117` | `"noreply", "no-reply", ... "zoom.us", "calendly.com", "linkedin.com", "newsletter@", "unsubscribe@",` | **Generic and reusable as-is** — these are platform/bounce patterns, not HR concepts. |
| Automated subject markers | `main.py:121-125` | `"out of office", "automatic reply", ... "read receipt",` | **Generic and reusable as-is.** |
| Test-address allowlist | `main.py:1137-1138` | `_TEST_ADDR_SUFFIXES = ("@example.com", "@example.org", "@e.com", "@ex.com", "@test.com")` | Generic, but note it drives **deletion** of matching cases at `main.py:1212-1216`. |

### 3e. Dashboard HTML templates

Branding and HR copy live in the Jinja2 templates, not just Python:

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Page title (all pages) | `base.html:8` | `<title>{{ title }} · CDF HR Console</title>` | Fellowship product name. |
| Nav brand | `base.html:14-15` | `<span class="brand-mark">CDF</span>` / `<span class="brand-sub">HR Console</span>` | Fellowship branding. |
| Sign-out chip | `base.html:25` | `...>hr@cdreams · sign out</button>` | Hardcoded identity in the UI, not the logged-in user. |
| Login page title | `login.html:8` | `<title>Sign in · CDF HR Console</title>` | Fellowship branding. |
| Login heading | `login.html:17` | `<p class="login-title">HR Email Console</p>` | Fellowship branding. |
| Login footer | `login.html:39` | `<p class="login-foot">Community Dreams Foundation · HR Console</p>` | Fellowship branding. |
| Case action actors (×4) | `case.html:31,60,79,87` | `<input type="hidden" name="actor" value="hr@cdreams.org">` | Fellowship actor identity, hardcoded in four separate forms. |
| Empty-queue copy | `queue.html:35` | `<p>No cases waiting for HR review. New emails will appear here as they arrive.</p>` | Fellowship wording. |
| Dashboard subtitle | `dashboard.html:8` | `...{{ 'case' if pending == 1 else 'cases' }} awaiting HR...` | Fellowship wording. |
| Metrics labels (×3) | `metrics.html:21,23,29` | `<p class="label">HR feedback rate</p>` · `<p class="sub">cases with HR rating</p>` · `<h2>How HR rates the AI drafts</h2>` | Fellowship wording. |
| Metrics audience copy | `metrics.html:58` | `...>Where volunteer questions cluster</p>` | Fellowship audience term. |
| Categories page copy | `categories.html:24` | `...>What kinds of emails the HR inbox receives — segregated by the Gmail labels.</p>` | Fellowship wording. |
| Policies page copy | `policies.html:8` | `<li>Point Make.com (or a worker) at the same index your HR assistant queries.</li>` | Fellowship wording. |
| Setup page examples | `setup.html:26,30,40` | `"mailbox": "humanresources@cdreams.org",` · `"body": "Hi HR, can you confirm what our 401k match is and how vesting works?"` | Fellowship example payloads. |

---

## 4. `.env` / `.env.example` — environment variables

Values below are from `.env.example` (committed). The live `.env` matches for all
HR-specific entries; secrets are omitted.

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Index name | `.env.example:31` | `INDEX_NAME=cdf-hr-policy-index` | A Fellowship value. `→ relevant to Q1` |
| Pinecone host | `.env.example:32` | `PINECONE_HOST=https://cdf-hr-policy-index-b2rfj3w.svc.aped-4627-b74a.pinecone.io` | Must match the index above. `→ relevant to Q1` |
| Source folder | `.env.example:37` | `PDF_FOLDER=HR FAQ` | The Fellowship knowledge-base folder (content not yet delivered by HR). |
| Dashboard user | `.env.example:56` | `DASHBOARD_USER=hr` | A Fellowship value. |
| FAQ URL | `.env.example:80` | `FAQ_URL=https://faq.cdreamstream.org` | Fellowship's FAQ page. |
| SQLite path comment | `.env.example:71` | `# Postgres connection string. If unset, the app uses local SQLite (web/data/hr_console.db).` | Reflects the hardcoded HR DB filename (see §5). |
| SQLite directory | `.env.example:74` | `SQLITE_DIR=` | Directory is configurable; the **filename** is not (see `db.py:28`). `→ relevant to Q3` |
| Header comment | `.env.example:2` | `#  CDF HR Email Automation — environment template` | Fellowship wording. |
| Example value in comment | `.env.example:7` | `#    - Quote values with spaces  e.g. PDF_FOLDER="HR FAQ"` | Fellowship wording. |
| Auto-send thresholds | `.env.example:44-45` | `AUTO_REPLY_CONFIDENCE_THRESHOLD=0.75` / `AI_AUTOSEND_CONFIDENCE=0.90` | Not HR-worded, but HR-tuned — needs its own Fellowship value, even if the number ends up identical. |

**Not HR-specific (reusable as-is):** `LLM_PROVIDER`, `GEMINI_API_KEY`,
`GEMINI_REPLY_MODEL`, `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIM`, `PINECONE_KEY`,
`APP_API_KEY`, `DISABLE_AUTO_REPLY`, `TEMPLATE_ONLY`, `ALLOW_AUTO_REPLY`,
`DASHBOARD_PASSWORD`, `SESSION_SECRET`, `MAKE_CALLBACK_SECRET`, `SLACK_WEBHOOK_URL`,
`PUBLIC_BASE_URL`, `SEED_DEMO`. These are credentials or behaviour flags that each
deployment needs its own copy of regardless of domain.

---

## 5. `web/app/db.py` and the SQLite schema

### 5a. Your specific question: would the tables need a `domain` / `product` column?

**Verified fact: no such column exists today, on any of the three tables.** Read directly
from the live database at `web/data/hr_console.db`:

```
cases:      id external_id mailbox subject from_email thread_id status confidence
            quality_score intent recommended_action retrieval_backend top_snippet_score
            citation_count risk_level draft_body inbound_body retrieved_snippets
            created_at updated_at
audit_log:  id case_id action actor details created_at
templates:  id category title body trigger_phrases auto_reply updated_at
```

A repo-wide grep for any existing multi-tenancy concept returns nothing:

```
$ grep -rn "domain\|tenant\|product_id\|workspace" web/app/*.py ingest.py
web/app/db.py:502:  "access to slack\nadd me to slack\nslack workspace invite",   # (a trigger phrase, unrelated)
```

**Decision-agnostic answer — stated as a conditional, taking no position:**

- **If the database ends up separate per product:** all three tables are **fine as-is**. No
  schema change is needed. Every query in the codebase assumes a single product and would
  keep working unchanged.
- **If the database ends up shared:** all three tables would need a discriminator column,
  and **every** query would need a corresponding filter. The scale of that is concrete and
  countable — these queries currently have no product filter:
  - `cases` is queried without any product filter in **13 places**: `main.py:347`, `350`,
    `353`, `356`, `361`, `364`, `367`, `370`, `382`, `395`, `405`, `454`, `479`
  - plus `main.py:587-603` (metrics aggregates), `main.py:749-771` (category stats),
    `main.py:938` (thread follow-up lookup), `main.py:1168`, `1177`, `1194`, `1201`, `1266`
  - `templates` is read without a filter at `db.py:602-605` in `get_auto_reply_templates()`,
    which feeds the matcher for every inbound email
  - `audit_log` is joined to `cases` at `main.py:563-567`

  Two specific collision points worth knowing either way:
  - **`external_id` is globally unique** (`db.py:205`: `external_id TEXT UNIQUE`). Both
    products build it from a Gmail message id (`main.py:887`: `ext = payload.get("external_id") or f"process-{db._utc_now()}"`), so a shared table means one namespace for both.
  - **Template seeding is keyed on `title` alone** (`db.py:559-567`, `db.py:583-586`:
    `"SELECT id, trigger_phrases, auto_reply FROM templates WHERE title = ? ORDER BY id LIMIT 1"`).
    Two products with a same-named template would collide on startup.

  `→ relevant to Q2 and Q3`

### 5b. Other HR-specific items in `db.py`

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| SQLite filename (env path) | `db.py:28` | `DB_PATH = Path(_sqlite_dir_env) / "hr_console.db"` | A Fellowship filename. `SQLITE_DIR` sets the directory, but the **filename is hardcoded** — pointing both products at one directory gives them the same file. `→ relevant to Q3` |
| SQLite filename (default path) | `db.py:30` | `DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hr_console.db"` | Same, for the local-dev default. |
| Logger name | `db.py:21` | `logger = logging.getLogger("hr_db")` | A distinguishable logger name. |
| Seed templates | `db.py:254-553` | `_SEED_TEMPLATES: list[tuple[str, str, str, int, str]] = [` — **19 templates, ~300 lines**, all HR content | A complete Fellowship replacement. Every body, trigger phrase, and category is HR-specific (relieving letters, EAD/OPT, offer letters, membership fees, W-2, STEM OPT). None transfers. |
| Seed template category values | `db.py:256`, `272`, `291`, `319`, `346`, `373`, `386`, `400`, `415`, `424`, `437`, `453`, `468`, `480`, `492`, `505`, `517`, `528`, `539` | e.g. `db.py:256` → `        "Relieving Letter",` · `db.py:400` → `        "HR-Inbox",` · `db.py:453` → `        "RFE",` | Must match whatever Fellowship's `CATEGORIES` list becomes (§2b), or templates won't map to categories. |
| Demo seed mailbox | `db.py:674` | `"hr@cdreams.org",` | Fellowship mailbox. Dev-only (`SEED_DEMO=true`). |
| Demo seed content | `db.py:664-695` | `"I registered as a volunteer with CDF last week and submitted my documents. "` | Fellowship example content. |

### 5c. An existing HR↔Fellowship boundary already in the code

Worth knowing before designing anything: the HR system **already routes Fellowship payment
questions away to the Fellowship team**. `db.py:415-421`:

```python
"Ex-Payment Issue",
"Ex / fellowship payment issue — contact fellowship team",
"Hi,\n\n"
"For this payment matter, please reach out to cdffellowship@cdreams.org and the team will assist you.\n\n"
"Best regards,\nHR Team",
1,
"fellowship payment\nfellowship fee\ncdffellowship\nfellowship membership",
```

This template is **live** (`auto_reply = 1`). Two practical consequences to be aware of:

1. Once Fellowship has its own automation, this hand-off template may need revisiting so the
   two systems don't bounce a volunteer back and forth.
2. The trigger phrases `fellowship payment`, `fellowship fee`, `cdffellowship`, and
   `fellowship membership` are currently claimed by the **HR** matcher. If Fellowship ends up
   sharing any matching surface with HR, these overlap. `→ relevant to Q2`

---

## 6. Also found — deployment config (beyond the five areas you listed)

Not in your list, but it is hardcoded HR and would block a Fellowship deploy, so flagging it:

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Render service name | `render.yaml:3` | `    name: cdf-hr-email` | A Fellowship service name. |
| Render index name | `render.yaml:25` | `        value: cdf-hr-policy-index` | Fellowship index. `→ relevant to Q1` |
| Render Pinecone host | `render.yaml:27` | `        value: https://cdf-hr-policy-index-b2rfj3w.svc.aped-4627-b74a.pinecone.io` | Fellowship host. |
| Cloud Run service name | `deploy_gcp.sh:28` | `SERVICE_NAME="${GCP_SERVICE_NAME:-cdf-hr-email}"` | A Fellowship service name. |
| Cloud Run env block | `deploy_gcp.sh:125` | `--set-env-vars "...INDEX_NAME=cdf-hr-policy-index,PINECONE_HOST=https://cdf-hr-policy-index-...,PUBLIC_BASE_URL=https://cdf-hr-email-mjfe3btpwq-uc.a.run.app,...FAQ_URL=https://faq.cdreamstream.org..."` | A full Fellowship set — index, host, public URL, and FAQ URL are all HR values in one line. |
| Fly app name | `deploy_fly.sh:24` | `APP_NAME="${FLY_APP_NAME:-cdf-hr-email}"` | A Fellowship app name. |
| Fly volume name | `deploy_fly.sh:35` | `fly volumes create hr_data --region "$REGION" --size 1 --yes ...` | A Fellowship volume name. |
| Fly app name (committed config) | `web/fly.toml:1` | `app = "cdf-hr-email"` | A Fellowship app name. Separate from `deploy_fly.sh:24` — both must change. |
| Fly index name | `web/fly.toml:11` | `  INDEX_NAME = "cdf-hr-policy-index"` | Fellowship index. `→ relevant to Q1` |
| Fly Pinecone host | `web/fly.toml:12` | `  PINECONE_HOST = "https://cdf-hr-policy-index-b2rfj3w.svc.aped-4627-b74a.pinecone.io"` | Fellowship host. |
| Fly threshold | `web/fly.toml:13` | `  AUTO_REPLY_CONFIDENCE_THRESHOLD = "0.75"` | HR-tuned value baked into deploy config. |
| Fly volume mount | `web/fly.toml:29` | `  source = "hr_data"` | A Fellowship volume name. |

**Clean — no changes needed:** `web/Dockerfile`, `web/Procfile`
(`web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`), `web/runtime.txt`,
`requirements.txt`. Verified by grep; no HR-specific strings.

---

## 6b. Make.com scenario blueprint

`make_blueprint_scenario1.json` is a committed, importable Make.com configuration — a
config artifact, not documentation, so a Fellowship version would need its own.

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Scenario name | `make_blueprint_scenario1.json:2` | `  "name": "CDF HR Auto-Reply (Gmail → FastAPI → Gmail)",` | A Fellowship scenario name. |
| **Hardcoded mailbox in the POST body** | `make_blueprint_scenario1.json:45` | `\"mailbox\": \"swathyragupathy@gmail.com\",` | The Fellowship mailbox. **Worth flagging separately:** this is a personal Gmail address baked into a committed blueprint, and it is the value the app receives as `mailbox` — which drives the self-email skip check at `main.py:896`. |
| Review draft subject prefix | `make_blueprint_scenario1.json:111` | `                "subject": "[HR REVIEW] Re: {{1.subject}}",` | A Fellowship prefix, so the two products' review drafts are distinguishable in Gmail. |
| Review draft body label | `make_blueprint_scenario1.json:112` | `<p><strong>HR REVIEW NEEDED</strong> — confidence={{2.confidence}} · case_id={{2.case_id}} · intent={{2.intent}}</p>` | Fellowship wording. |

---

## 6c. Remaining app files

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Package docstring | `web/app/__init__.py:1` | `# HR Email Console web package` | Fellowship wording. (This is the file's entire content.) |
| Stylesheet header comment | `web/app/static/app.css:1` | `/* CDF HR Email Console — refined light theme.` | Fellowship wording. |
| Stylesheet section comment | `web/app/static/app.css:534` | `/* Quote box (volunteer message) */` | Fellowship audience term. |

**Otherwise clean:** `app.css` is 20,585 bytes and contains only these **2** HR/CDF
references — both comments. No selectors, colours, or layout are HR-specific, so the
entire stylesheet is reusable as-is.

| Item | Location | Current HR-specific value | What Fellowship would need |
|---|---|---|---|
| Org reference file | `cdf_core_info.txt` (whole file, 7 lines) | `Contact Email: humanresources@cdreams.org` | A Fellowship equivalent **if** it is ever used. **Verified: not referenced by any code** — `grep -rn "cdf_core_info"` across `.py/.sh/.yaml/.toml/.json` returns nothing. It appears to be a loose reference file, so it is listed for completeness rather than as a required change. |

---

## 7. Summary by area

| Area | Entries | Where they are | Reusable as-is |
|---|---|---|---|
| `ingest.py` | 12 | §1 | Chunking, embedding, and upsert logic — all domain-neutral |
| `pipeline.py` | 18 | §2a–2f | Email cleaning, question summarising, markdown stripping, JSON parsing, retry logic, prompt formatting rules, prompt output contract, 2 of 4 `SENSITIVE_PATTERNS` groups |
| `main.py` (Python) | 24 | §3a–3d | Auth, session handling, aging/colour helpers, no-reply and automated-subject filters, 24 of 25 block phrases |
| `main.py` (HTML) | 14 | §3e | Layout, CSS, all table/chart structure |
| `.env` / `.env.example` | 10 | §4 | 16 credential and behaviour-flag variables |
| `db.py` / schema | 9 | §5a–5c | The Postgres/SQLite adapter, connection factory, audit helper, and all three table structures |
| Deploy config | 12 | §6 | `Dockerfile`, `Procfile`, `runtime.txt`, `requirements.txt` — all verified clean |
| Make.com blueprint | 4 | §6b | The module wiring, router logic, and field mappings |
| Remaining app files | 4 | §6c | All of `app.css` except 2 comments; `cdf_core_info.txt` is unused by code |
| **Total** | **107** | | |

*Note on counting: the `pipeline.py` figure counts `CATEGORIES`, `_CATEGORY_RULES`, and
`SENSITIVE_PATTERNS` as one entry each, even though each contains many phrases — 11
categories, 10 rule sets, and 4 pattern groups respectively. The `db.py` figure likewise
counts `_SEED_TEMPLATES` as one entry covering all 19 templates.*

**Overall shape:** the *plumbing* is largely domain-neutral and transfers well — retrieval,
embedding, cleaning, scoring, persistence, and auth carry over with no content changes. What
is HR-bound is the **content and vocabulary layer**: the category taxonomy, the classification
phrases, the drafting prompt, the 19 seed templates, and the branding copy.

---

## 8. Items relevant to the three pending decisions

Collected here so they can be handed to Swayam directly. **These are stated as technical
facts, not recommendations — I am not taking a position on any of the three questions.**

**Q1 — separate index vs. shared index with metadata separation**

- The HR index name is hardcoded as a **fallback default** in two separate places
  (`ingest.py:62`, `pipeline.py:16`), as is the host (`ingest.py:43`, `pipeline.py:19`). An
  unset or misspelled `INDEX_NAME` resolves to HR's index rather than failing.
- Beyond those code defaults, the HR index name is also written into **four committed config
  files**: `.env.example:31`, `render.yaml:25`, `deploy_gcp.sh:125`, `web/fly.toml:11` — so
  six locations total would need to resolve to the right index.
- `ingest.py:265` runs `index.delete(delete_all=True)` before every upsert, with no namespace
  or metadata scoping.
- The query at `pipeline.py:313` passes no metadata filter, though `source_file`, `pdf_stem`,
  and `chunk_index` are written at `ingest.py:180`.

**Q2 — shared configurable codebase vs. separate copy**

- Template seeding is keyed on `title` alone (`db.py:583-586`).
- `get_auto_reply_templates()` (`db.py:599-606`) returns all enabled templates with no
  product filter, and feeds the matcher for every inbound email.
- The literal `"HR-Inbox"` is compared in 4 places outside the `CATEGORIES` list
  (`main.py:777`, `971`, `1232`, `db.py:400`).
- HR's live template set already claims the trigger phrases `fellowship payment`,
  `fellowship fee`, `cdffellowship`, `fellowship membership` (`db.py:421`).
- Any bug in the shared logic is inherited by both products — including the two documented in
  `RAG_RETRIEVAL_FINDINGS.md` (§D1 classification, §E1 risk-list divergence).

**Q3 — same repo vs. new repo**

- `ingest.py:396` writes extracted text to a fixed `out/` folder, not configurable by env var;
  `ingest.py:397` derives the Chroma path from it.
- The Chroma collection name `"hr_pdfs"` is hardcoded in three places (`ingest.py:193`, `197`,
  `pipeline.py:262`).
- The SQLite **filename** `hr_console.db` is hardcoded (`db.py:28`, `db.py:30`); only the
  directory is configurable via `SQLITE_DIR`.
- The session cookie name `"cdf_hr_session"` is hardcoded (`main.py:148`).

---

## 9. Verification

Every citation in this document can be checked with:

```bash
# Show any cited line with its number, e.g. pipeline.py:357
sed -n '357p' web/app/pipeline.py

# Confirm the HR-specific constant blocks and their start lines
grep -n "^SENSITIVE_PATTERNS\|^CATEGORIES\|^_CATEGORY_RULES\|^DEFAULT_CATEGORY\|^_SEED_TEMPLATES" \
  web/app/pipeline.py web/app/db.py

# Confirm no domain/product column exists on any table
for t in cases audit_log templates; do
  echo -n "$t: "; sqlite3 web/data/hr_console.db "PRAGMA table_info($t);" | cut -d'|' -f2 | tr '\n' ' '; echo
done

# Confirm the hardcoded index/host fallbacks
grep -n "cdf-hr-policy-index" ingest.py web/app/pipeline.py .env.example render.yaml deploy_gcp.sh

# Confirm the "HR-Inbox" sentinel usage
grep -rn '"HR-Inbox"' web/app/
```
