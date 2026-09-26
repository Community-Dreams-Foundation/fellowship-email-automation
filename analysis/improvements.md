# Improvements: areas I think need work

My running list. Each entry: what's wrong, why it matters, how I'd fix it.
Analysis only. Nothing here is built yet.

---

## 1. Category matching stops at the first rule that hits anything. Order decides the winner

**Where:** `web/app/pipeline.py`, `classify_category()` / `_CATEGORY_RULES`

**What's wrong:**
- The function walks the category list top to bottom. For the first category whose
  anchor list matches, it returns immediately (strong). If a category's anchor
  doesn't match but its weak list does, it returns immediately too (weak), without
  ever checking the categories listed after it.
- So a **weak** keyword hit in an early category can block a **strong** anchor match
  in a later category from ever being checked.

**Example:** email says "when can I take leave for orientation, still finishing my
onboarding documents." `Leave of Absence` (weak keyword `"leave"`) comes before
`Onboarding` (anchor `"onboarding"`) in the list. The function returns `Leave of
Absence` (weak) and never even looks at `Onboarding`, even though that's the real
topic and has a strong anchor match.

**Why it matters:**
- List order should not decide the category. Right now it silently does.
- This directly hurts coverage: emails get mis-filed or downgraded to "weak" (which
  blocks auto-reply) purely because of where a rule happens to sit in the list.

**Suggested improvement:** three options, easy to hard.

**Easy: fix the algorithm, no AI.** Check anchors and weak keywords in two separate
passes instead of one combined pass per category:
1. Loop through all categories, checking only anchor lists. If any category's anchor
   hits, return it as strong. Done.
2. Only if no anchor hit anywhere, loop through all categories again checking weak
   lists. Return the first weak hit.

This guarantees any strong match anywhere beats any weak match anywhere, regardless
of list order. Small code change, no cost, no new infra. Worth doing regardless of
whether we also do the options below.

**Medium: AI classifies using few-shot examples, falls back to default if unsure.**
Ask the AI to pick the best-fitting category, giving it example phrases per category
as context (few-shot / in-context learning). Use
`knowledge_base/automation_design/email_intent_catalog.json`'s `examples` field as the
source of those examples, not the old anchor/weak lists. The catalog is the newer,
team-provided taxonomy, so training the AI on the old lists would just repeat the
gaps we already found. If the AI isn't confident, fall back to the default category
(same as today).
- Needs a handful of labeled example emails per category to sanity-check it, but not
  a large dataset.
- Costs an extra AI call per email, so consider only calling it when the (fixed)
  keyword pass from the Easy option comes back weak or empty. Keep the free keyword
  pass as the fast path for obvious cases.

**Hard: train a neural network classifier on labeled emails.** Would need hundreds+
labeled examples per category to train well. We don't have a labeled dataset at all
right now, so this means building that dataset first regardless. Given the Medium
option gets most of the same benefit without training/hosting a model, I'd park this
one rather than pursue it now.

**Hybrid: Easy + Medium together.** Loop through all categories checking only anchor
lists (the free, instant Easy pass). If any anchor hits, use that category, done. If
no anchor hits anywhere, don't bother with the weak-keyword list at all, send the
email straight to the Medium AI classifier instead. This skips the unreliable weak
list entirely and only pays for an AI call on the emails the keyword pass genuinely
can't handle.

**My take:** do the Hybrid. It's Easy for the free, obvious cases and Medium for
everything else, so we get the coverage benefit without paying AI cost on every
email. Hard is not worth it at our current data/scale.

---

## 2. Template matching score doesn't measure real fit, it measures phrase-list luck

**Where:** `web/app/pipeline.py`, `match_template()`

**Current state:**
- Each template has a list of trigger phrases. The code checks the email text for
  each phrase as a plain substring, and adds up points for every phrase that hits
  (multi-word phrases and phrases with digits/hyphens score more, single plain words
  score 1). Whichever template ends up with the highest total wins, as long as it's
  at least 2.
- Two problems with summing hits like this:
  1. **Overlapping phrases double count.** A template's phrase list often has
     shorter and longer versions of the same request, e.g. `"relieving letter"` and
     `"where is my relieving letter"`. If the email says the longer one, it also
     contains the shorter one, so both phrases match and both add points, even
     though the email only said one thing.
  2. **List size gives an unfair advantage.** A template with 30 trigger phrases has
     far more chances to rack up points than a template with 4, regardless of which
     one actually fits the email better. The score reflects how many phrases someone
     happened to write for that template, not real relevance.

**My proposal:** replace the substring-and-sum scoring with embeddings and cosine
similarity.
- Turn each template's trigger phrases into vectors (one vector per phrase, using
  the same embedding model already used for policy retrieval), then average them
  into one vector per template. Use the phrases, not the template's reply body, the
  body is full of generic boilerplate ("Hi,", "Thank you for reaching out",
  "Best regards, HR Team") that's nearly identical across templates and would dilute
  the topic signal.
- Turn the incoming email into a vector the same way. This can reuse the same
  embedding call already made for the policy-retrieval step, no need to embed the
  email twice.
- Compare the email's vector against every template's vector with cosine similarity.
  Whichever template is closest wins, if it clears a similarity threshold.
- This also fixes the overlap problem for free: `"relieving letter"` and `"where is
  my relieving letter"` mean nearly the same thing, so their vectors are nearly
  identical. Averaging them doesn't inflate the result the way summing did.

**Drawbacks of my idea:**
- Cosine similarity is fuzzier than exact keyword matching. Two genuinely different
  templates (say, Experience Letter and Background Verification) both involve
  "confirming someone's employment," so their vectors could end up closer together
  than they should be. Plain keyword matching, despite its bugs, at least required
  the literal phrase to appear, which kept lookalike topics apart more reliably.
- It introduces a new threshold ("how similar counts as similar enough to auto-send",
  e.g. 0.75 cosine) to replace today's `score >= 2`. That's the same "where did this
  number come from" problem flagged in point 5 below. It doesn't go away, it just
  moves to a different number.

---

## 3. Category and template matching are completely independent, category doesn't narrow the template search

**Where:** `web/app/pipeline.py`, `process_email()`, the order of `classify_category()`
then `match_template()`

**Current state:**
- `classify_category()` runs first and picks a category, but that result is never
  used to narrow the template search. `match_template()` then checks the email
  against **every** template regardless of category, and whichever template scores
  best wins. If a template matches, its own `category` field overwrites whatever
  `classify_category()` picked in the first place.
- So right now the two systems don't talk to each other at all. Category tagging and
  template selection are two fully separate guesses, each looking at the whole email.

**My proposal:** use the category to narrow the template search. Once
`classify_category()` picks a category, only check that category's templates instead
of all of them. Fewer templates to score, and the reply topic and the assigned
category would always agree.

**Drawback of my idea (this is why I'm not fully sold on it yet):** point 1 above
already showed `classify_category()` gets it wrong sometimes. Right now, because the
two systems are independent, a wrong category guess can't stop a good template from
still matching on its own keywords. If I make template search depend on category
first, a misclassified email would lose access to the correct template too, since it
would never even look outside the (wrong) category. That would make things worse,
not better, until the category fix from point 1 is actually in place. I'd only do
this after point 1 is fixed, not before.

---

## 4. Policy lookup only sees the first part of the email, and only one question

**Where:** `web/app/pipeline.py`, `build_retrieval_query()` and `summarize_question()`

**What's wrong:**
- Before the system searches the policy database, it builds a search query from the
  email. That query uses only the **first 1500 characters** of the email body
  (`body[:1500]`).
- Separately, `summarize_question()` picks just **one line** of the email as "the
  main question."
- So if an email is long, or has a second or third question further down, those extra
  questions are never used to search the policy database.

**Why it matters:**
- The AI still gets shown more of the email when it writes the reply (up to 4000
  chars), but the **policy text to back up those later questions was never pulled in.**
- Result: for the later questions the AI has no source material, so it either says
  "HR will confirm" or guesses. Both hurt answer quality and the coverage goal.
- Multi-question emails are common in HR (e.g. "how do I resign AND get my relieving
  letter AND what about my last payment?").

**Suggested improvement:**

Split the email into its separate questions before searching for policy text, and
search once for each question.

Steps:
1. Send the full cleaned email body to the AI and ask it to return a list of the
   distinct questions in the email.
2. For each question in that list, run its own policy-database search.
3. Combine all the results, remove duplicates, and give that combined set to the AI
   to write the reply.

To keep cost down, only run this when it is worth it. Most HR emails are short and
ask one thing. So: if the email is short and has a single question, keep the current
single search. Only do the split-and-search-per-question step when the email is long
or clearly contains several questions (for example, it has two or more question
marks). This way the extra AI call only happens on emails that actually need it.

Add a cap (for example, at most 5 questions) so one huge email cannot create endless
searches.

This also matches the new knowledge base guidance in
`knowledge_base/automation_design/03_retrieval_and_answer_composition.md`, which says
to "retrieve one document per materially distinct question."

**Trade-offs to check:**
- The extra AI call costs money and adds a small delay (only on the emails that need it).
- Need to decide the rule for "this email has multiple questions" (length? number of
  question marks? a quick AI check?).

---

## 5. The auto-reply thresholds look arbitrary

**Where:** `web/app/pipeline.py`, the guardrail block near the end of `process_email()`

**What's wrong:**
- Whether a reply is sent automatically depends on a confidence score (`conf`, a
  number from 0.0 to 1.0).
- That score is the **AI rating its own answer**. The AI is asked in the prompt to
  return its own confidence.
- The code then checks that score against a lot of hardcoded numbers: send only if
  confidence is at least 0.75; if the topic is high-risk, force confidence down to
  0.6; if the best policy match was weak, force it down to 0.7; escalate if
  confidence is under 0.65; and so on.
- There is no comment or record explaining where any of these numbers came from. It
  doesn't look like anyone measured them.

**Why it matters:**
- An AI rating its own work is a weak signal. Models are usually overconfident, so a
  self-reported 0.8 does not really mean "80% likely correct." The whole safety gate
  leans on this.
- There are many different cutoffs (0.75, 0.65, 0.6, 0.55, 0.7) doing similar jobs.
  Hard to reason about, easy to get inconsistent.
- Nobody is tracking "auto-sent, then HR had to correct it," so there is no way to
  know if 0.75 is too loose or too strict.

**Suggested improvement:** TBD. Come back to this once we understand how the team
wants to set and measure thresholds. For now this is just flagged as a concern.
