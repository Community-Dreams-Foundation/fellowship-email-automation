# CDF Fellowship Email Automation

An automated email-response system for the **Community Dreams Foundation (CDF) Fellowship program**.

This project is based on the existing CDF HR Email Automation system and is maintained as a **separate application, repository, knowledge base, and Pinecone index**. The initial implementation intentionally preserves the existing HR automation architecture as a baseline so the team can first reproduce a stable end-to-end workflow, then replace HR-specific configuration, knowledge, prompts, templates, categories, and integrations with Fellowship-specific equivalents.

> **Current status:** Initial Fellowship replication and configuration audit in progress. The system should not be treated as production-ready until the Fellowship knowledge base, workflow configuration, response logic, and end-to-end test suite have been validated.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Goals](#goals)
3. [Architecture Decision](#architecture-decision)
4. [System Workflow](#system-workflow)
5. [Technology Stack](#technology-stack)
6. [Repository Structure](#repository-structure)
7. [Core Components](#core-components)
8. [Knowledge Base and RAG](#knowledge-base-and-rag)
9. [Pinecone Configuration](#pinecone-configuration)
10. [Email Automation and Make.com](#email-automation-and-makecom)
11. [Database and Review Console](#database-and-review-console)
12. [Environment Configuration](#environment-configuration)
13. [Local Setup](#local-setup)
14. [Running the Application](#running-the-application)
15. [Knowledge Base Ingestion](#knowledge-base-ingestion)
16. [Development Workflow](#development-workflow)
17. [Team Ownership](#team-ownership)
18. [Testing and Validation](#testing-and-validation)
19. [Current Replication Checklist](#current-replication-checklist)
20. [Security and Secrets](#security-and-secrets)
21. [Deployment Notes](#deployment-notes)
22. [Known Work in Progress](#known-work-in-progress)
23. [Documentation](#documentation)

---

## Project Overview

The **CDF Fellowship Email Automation** project is intended to automate responses to incoming Fellowship-related emails using a retrieval-augmented generation (RAG) pipeline.

The system follows the architecture of the existing CDF HR Email Automation application:

1. An incoming email enters the automation workflow.
2. The application cleans and evaluates the email.
3. The email is classified into a supported category.
4. The system checks whether an appropriate curated template is available.
5. Relevant policy or program information is retrieved from the knowledge base when needed.
6. Gemini generates a grounded draft response.
7. The response is scored and routed according to the configured confidence/review rules.
8. The result is persisted for review, tracking, or downstream workflow actions.
9. Make.com coordinates the surrounding email automation flow.

The Fellowship implementation is deliberately separated from the HR implementation so that changes to Fellowship policies, prompts, knowledge, indexes, deployment configuration, or workflow behavior do not affect the production HR automation system.

---

## Goals

### Initial goal

The first milestone is to create a **working replica of the existing HR Email Automation architecture** for Fellowship use.

The initial pass focuses on:

- reproducing the existing application structure;
- creating a separate Fellowship repository;
- isolating Fellowship infrastructure from HR infrastructure;
- creating a separate Pinecone index;
- identifying all HR-specific or hardcoded configuration;
- preparing the system for the approved Fellowship knowledge base;
- validating the complete workflow before production use.

### Subsequent goal

After the baseline replica is stable, the team can progressively introduce Fellowship-specific improvements such as:

- Fellowship-specific categories;
- Fellowship-specific prompts and templates;
- stronger retrieval relevance checks;
- improved classification behavior;
- better confidence and review-routing logic;
- additional fallback handling;
- expanded test coverage;
- configuration-driven behavior where appropriate.

The initial replication and later improvements should be treated as separate stages so that architectural changes do not unnecessarily block creation of a working baseline.

---

## Architecture Decision

The Fellowship system uses the following confirmed structure:

| Component | Fellowship Approach |
| --- | --- |
| GitHub repository | Separate repository |
| Application code | Independent copy of HR automation baseline |
| Pinecone | Separate Fellowship index |
| Knowledge base | Fellowship-specific source documents |
| Environment configuration | Separate Fellowship configuration |
| Secrets | Separate environment/deployment secrets |
| Make.com workflow | Separate Fellowship configuration/workflow |
| Database/review data | Fellowship-specific |
| Deployment | Independent from HR deployment |

The HR repository remains the reference implementation during the replication phase, but Fellowship development should occur in this repository.

### Why keep the systems separate?

The two systems may eventually differ in:

- source documents;
- program policies;
- supported email categories;
- prompts;
- response templates;
- escalation/review rules;
- Pinecone contents;
- mailbox/workflow configuration;
- deployment settings.

Keeping them separate reduces the chance that Fellowship development accidentally changes the HR system.

---

## System Workflow

The baseline processing flow is:

```text
Incoming Fellowship Email
          |
          v
     Make.com Workflow
          |
          v
       FastAPI API
          |
          v
   Clean / Normalize Email
          |
          v
     Risk Evaluation
          |
          v
    Category Classification
          |
          v
    Template Matching
       /        \
      /          \
 Template       RAG Path
 Response          |
                   v
          Retrieve KB Context
                   |
                   v
            Gemini Draft
                   |
                   v
          Confidence / Quality
                   |
                   v
        Persist Response Result
                   |
                   v
       Review / Workflow Action
                   |
                   v
      Make.com / Email Handling
```

The exact review and auto-send behavior must be validated for the Fellowship workflow before production enablement.

---

## Technology Stack

### Backend

- **Python**
- **FastAPI**

### LLM / AI

- **Google Gemini** for response generation
- Gemini embedding model for document/query embeddings

### Retrieval

- **Pinecone** as the primary vector database
- Chroma-related fallback/local code may exist in the inherited baseline and should be reviewed during conversion

### Automation

- **Make.com**
- Email/mailbox integration

### Persistence

- SQLite/local database support in the baseline
- Production database configuration through environment variables where applicable

### Deployment

The inherited project contains deployment-related configuration that must be audited and converted to Fellowship-specific values before use.

---

## Repository Structure

```text
fellowship-email-automation/
│
├── main.py
├── pipeline.py
├── ingest.py
├── db.py
├── requirements.txt
├── __init__.py
│
├── .env.example
├── .gitignore
├── README.md
│
├── knowledge_base/
│   └── .gitkeep
│
└── docs/
    ├── IMPLEMENTATION_CHECKLIST.md
    ├── MAKE_SETUP.md
    └── TEAM_NOTES.md
```

Additional files and directories may be introduced as the Fellowship implementation is completed.

---

## Core Components

### `main.py`

`main.py` contains the FastAPI application and API-facing workflow.

Its responsibilities include:

- initializing the application;
- exposing the endpoints used by the automation workflow;
- receiving email-processing requests;
- invoking the response pipeline;
- supporting review-console behavior;
- applying configured thresholds and workflow controls;
- coordinating persistence and response actions.

The Fellowship copy uses Fellowship-specific application/session naming, but inherited HR-specific behavior still needs to be audited before production use.

---

### `pipeline.py`

`pipeline.py` contains the primary email-processing and response-generation logic.

The inherited flow includes:

```text
clean
  ↓
risk
  ↓
classify
  ↓
template
  ↓
retrieve
  ↓
Gemini draft
  ↓
score
  ↓
persist/action
```

This file is one of the primary areas being audited because the original implementation contains HR-specific categories, templates, prompts, terminology, and retrieval behavior.

During the initial replication phase, avoid redesigning the entire pipeline before a stable baseline is available. Fellowship-specific conversion should be performed deliberately and documented.

---

### `ingest.py`

`ingest.py` handles knowledge-base ingestion.

The baseline ingestion process includes:

1. reading supported source documents;
2. extracting text;
3. splitting content into chunks;
4. generating embeddings;
5. uploading vectors and metadata;
6. storing them in the configured vector index.

The inherited configuration uses approximately:

- **chunk size:** 1200 characters
- **chunk overlap:** 150 characters
- **embedding dimension:** 1536
- **retrieval top-k:** 5 in the existing pipeline

These values are inherited from the HR baseline and should be validated against the Fellowship corpus rather than assumed to be optimal.

---

### `db.py`

`db.py` provides persistence used by the application/review flow.

The Fellowship starter uses a separate local database name so local Fellowship development does not accidentally share the HR console database.

Any production database connection must be supplied through Fellowship-specific environment/deployment configuration.

---

## Knowledge Base and RAG

The system uses retrieval-augmented generation so generated responses can be grounded in approved source material instead of relying only on the language model's general knowledge.

Conceptually:

```text
User Email
    |
    v
Embedding
    |
    v
Pinecone Similarity Search
    |
    v
Relevant Fellowship Chunks
    |
    v
Prompt + Retrieved Context
    |
    v
Gemini
    |
    v
Grounded Draft Response
```

### Fellowship knowledge base

The Fellowship knowledge base must contain **approved Fellowship source documents only**.

Do not populate the Fellowship index by blindly copying the HR knowledge base. The application code is being replicated; the underlying policy/program content is separate.

Place approved source files in:

```text
knowledge_base/
```

The repository intentionally does not include live knowledge-base documents in the starter.

### Retrieval validation

Before production use, validate that:

- relevant queries retrieve relevant Fellowship content;
- unsupported questions do not retrieve misleading unrelated chunks;
- HR-specific content is not present in the Fellowship index;
- retrieval metadata is correct;
- category-aware retrieval is evaluated where appropriate;
- similarity/relevance behavior is tested using representative Fellowship emails.

---

## Pinecone Configuration

The Fellowship system must use its **own Pinecone index**.

The starter default index name is:

```text
cdf-fellowship-policy-index
```

The actual index configuration used by the organization should be supplied through environment configuration.

### Important

Never point the Fellowship application at the production HR index while testing Fellowship ingestion.

Configure:

```env
PINECONE_KEY=
INDEX_NAME=cdf-fellowship-policy-index
PINECONE_HOST=
```

`PINECONE_HOST` should be the host corresponding to the actual Fellowship index.

No production host or API key is committed to this repository.

---

## Email Automation and Make.com

Make.com is responsible for the external automation surrounding the API.

The HR workflow may be used as a **structural reference**, but the Fellowship workflow should have its own configuration.

The Fellowship workflow must be checked for:

- Fellowship mailbox/connection;
- Fellowship API/service endpoint;
- Fellowship webhook configuration;
- callback authentication;
- review/approval behavior;
- routing;
- labels;
- downstream email actions.

Do not modify the HR production workflow while setting up Fellowship.

See:

```text
docs/MAKE_SETUP.md
```

for the Fellowship workflow setup notes.

---

## Database and Review Console

The baseline application contains review/persistence behavior for storing email-processing results.

For Fellowship:

- use Fellowship-specific data;
- avoid mixing Fellowship and HR review records;
- validate review status behavior;
- confirm which messages are eligible for automated handling;
- confirm escalation/review rules before enabling automatic responses.

The local starter database is configured separately from the HR local database.

---

## Environment Configuration

Copy:

```text
.env.example
```

to:

```text
.env
```

for local development.

Example:

```env
GEMINI_API_KEY=
PINECONE_KEY=
INDEX_NAME=cdf-fellowship-policy-index
PINECONE_HOST=

APP_API_KEY=
MAKE_CALLBACK_SECRET=
MAKE_APPROVED_WEBHOOK_URL=

DATABASE_URL=

GEMINI_REPLY_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIM=1536

PDF_FOLDER=knowledge_base

ALLOW_AUTO_REPLY=false
DISABLE_AUTO_REPLY=true

PUBLIC_BASE_URL=http://localhost:8000

DASHBOARD_USER=
DASHBOARD_PASSWORD=
```

### Do not commit `.env`

`.env` contains secrets and is excluded by `.gitignore`.

Only `.env.example`, containing placeholders, belongs in the repository.

---

## Local Setup

### 1. Clone the repository

```bash
git clone <FELLOWSHIP_REPOSITORY_URL>
cd fellowship-email-automation
```

### 2. Create a virtual environment

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create local environment configuration

```bash
cp .env.example .env
```

On Windows, copy the file manually or use the equivalent command.

Populate `.env` with the **Fellowship-specific** configuration supplied through the approved secure channel.

### 5. Add approved Fellowship KB documents

Place approved source documents under:

```text
knowledge_base/
```

Do not add secrets or confidential credentials to this directory.

---

## Running the Application

After installing dependencies and configuring the environment, start the FastAPI application according to the application entry point used by the baseline project.

A typical local command is:

```bash
uvicorn main:app --reload
```

Then access the local service at the configured host/port.

Before connecting Make.com, test the API locally and confirm that it is using the Fellowship configuration.

---

## Knowledge Base Ingestion

Once the separate Fellowship Pinecone index exists and approved Fellowship source documents are available:

1. verify the Fellowship `.env`;
2. confirm `INDEX_NAME` and `PINECONE_HOST`;
3. verify the contents of `knowledge_base/`;
4. run the ingestion process;
5. inspect the resulting vectors/index;
6. perform retrieval tests before connecting the live workflow.

Example:

```bash
python ingest.py
```

Do not run ingestion until you have confirmed that the environment points to the Fellowship index.

---

## Development Workflow

Development should happen through feature branches and pull requests.

### Recommended branches

```text
feature/fellowship-rag-ingestion
feature/fellowship-prompts-categories
test/fellowship-validation
docs/fellowship-documentation
```

### Standard workflow

```bash
git checkout main
git pull origin main

git checkout -b feature/your-feature

# make changes

git add .
git commit -m "Describe the change"
git push -u origin feature/your-feature
```

Then open a pull request into `main`.

### Branch rules

- Do not push unfinished work directly to `main`.
- Pull the latest `main` before beginning major changes.
- Keep commits focused and descriptive.
- Document configuration changes.
- Do not commit secrets.
- Test changes before requesting merge.
- Use PRs so integration changes remain visible to the team.

---

## Team Ownership

The current Fellowship replication work is divided as follows.

### Akshaya — Configuration Audit, RAG and Ingestion

Primary areas:

- Fellowship configuration audit;
- identification of HR-specific/hardcoded values;
- knowledge-base ingestion;
- embeddings;
- Pinecone setup/integration;
- retrieval/RAG;
- response-generation flow;
- documenting configuration changes required for Fellowship.

The configuration audit currently covers the inherited codebase and supporting configuration, including areas such as:

- `ingest.py`
- `pipeline.py`
- `main.py`
- `.env` configuration
- `db.py`
- deployment configuration
- Make.com workflow/blueprint

The audit should serve as a verifiable checklist while converting the HR baseline to Fellowship.

---

### Nithish — Categories, Prompts and Templates

Primary areas:

- category-handling review;
- prompts;
- templates;
- fallbacks;
- related response logic;
- documenting improvement opportunities discovered in the HR implementation.

For the first replication pass, the priority is to get the copied system running reliably. Larger improvements to the inherited classification/template architecture should be documented and introduced deliberately rather than blocking the initial replica.

---

### Rudra — Testing and Validation

Primary areas:

- Fellowship test-case preparation;
- expected-vs-actual behavior;
- validation of the replicated flow;
- failure/gap tracking;
- integration testing;
- documentation of blockers and test results.

Testing should expand as Akshaya and Nithish complete their respective components.

---

### Integration / Coordination

Integration work includes:

- repository coordination;
- PR review and merging;
- ensuring work from different branches remains compatible;
- Make.com/workflow coordination;
- confirming documentation is updated alongside code;
- coordinating end-to-end validation.

---

## Testing and Validation

The Fellowship system should not be considered ready because the code runs successfully. The response quality and routing behavior must also be tested.

### Minimum validation areas

#### Classification

Verify:

- expected category;
- ambiguous wording;
- unsupported categories;
- overlapping category keywords;
- fallback behavior.

#### Retrieval

Verify:

- correct source retrieval;
- irrelevant retrieval;
- unsupported questions;
- retrieval similarity;
- category-specific retrieval where implemented;
- source metadata.

#### Response generation

Verify:

- factual grounding;
- completeness;
- unsupported claims;
- tone;
- use of retrieved information;
- behavior when the KB does not contain an answer.

#### Confidence and review routing

Verify:

- low-quality answers are not treated as high-confidence simply because the model produced them confidently;
- high-risk content follows the expected review path;
- confidence thresholds behave as intended;
- template responses follow the approved workflow;
- automatic sending remains disabled until explicitly approved.

#### End-to-end

Test:

```text
Test Email
   ↓
Automation
   ↓
API
   ↓
Classification
   ↓
Template/RAG
   ↓
Generated Response
   ↓
Confidence / Review
   ↓
Expected Workflow Result
```

For each case, record:

| Field | Example |
| --- | --- |
| Test ID | FEL-001 |
| Input email | Sample Fellowship query |
| Expected category | Expected classification |
| Actual category | Pipeline result |
| Expected behavior | Template / RAG / review |
| Actual behavior | Observed result |
| Retrieval quality | Relevant / Partial / Irrelevant |
| Response quality | Pass / Needs Review |
| Issue | Description |
| Status | Open / Fixed / Retest |

---

## Current Replication Checklist

### Repository and infrastructure

- [x] Separate Fellowship repository structure prepared
- [x] HR codebase used as baseline
- [x] `.env.example` created without secrets
- [x] Fellowship-specific local DB naming introduced
- [x] Fellowship Pinecone index default introduced
- [ ] Team repository access confirmed
- [ ] Separate Pinecone index provisioned/confirmed
- [ ] Deployment configuration converted
- [ ] Make.com Fellowship workflow configured

### Knowledge base

- [ ] Approved Fellowship source documents received
- [ ] Source-document scope confirmed
- [ ] Fellowship KB ingested
- [ ] Retrieval validation completed
- [ ] HR-only content excluded

### Application conversion

- [ ] Configuration audit incorporated
- [ ] HR-specific hardcoded values converted
- [ ] Fellowship categories finalized
- [ ] Fellowship prompts finalized
- [ ] Fellowship templates/fallbacks finalized
- [ ] Review behavior finalized
- [ ] Auto-send behavior approved
- [ ] Dashboard/application terminology converted

### Testing

- [ ] Synthetic Fellowship test cases created
- [ ] Classification tested
- [ ] Retrieval tested
- [ ] Response grounding tested
- [ ] Confidence/review routing tested
- [ ] End-to-end workflow tested
- [ ] Known issues documented
- [ ] Final stakeholder validation completed

---

## Security and Secrets

### Never commit

Do not commit:

- `.env`;
- Gemini API keys;
- Pinecone API keys;
- mailbox passwords;
- Google account credentials;
- Make.com connection credentials;
- webhook secrets;
- production database credentials;
- dashboard passwords;
- private credential handover documents;
- exported files containing live secrets.

The repository's `.gitignore` excludes common secret/local files, but developers are still responsible for reviewing commits before pushing them.

### Use secure channels

Credentials should be shared only through the organization's approved secure process.

If a secret is accidentally committed:

1. do not simply delete it in a later commit;
2. notify the appropriate project owner;
3. rotate/revoke the exposed credential;
4. remove it from repository history as required.

---

## Deployment Notes

The HR deployment configuration must **not** be reused blindly.

Before deploying Fellowship, verify:

- service/application name;
- environment variables;
- database configuration;
- Pinecone index and host;
- API keys;
- public base URL;
- Make.com callback/webhook configuration;
- mailbox connection;
- auto-send/review controls.

The Fellowship deployment must remain logically isolated from HR production.

---

## Known Work in Progress

The current repository is a **replication baseline**, not a completed Fellowship production system.

The following areas are intentionally still under active work:

1. **HR-specific configuration audit**  
   The inherited system contains hardcoded or HR-specific configuration that must be identified and converted.

2. **Fellowship knowledge base**  
   The approved Fellowship KB must be supplied, ingested, and validated independently.

3. **Categories and templates**  
   The HR categories/templates are inherited baseline logic and are not automatically valid Fellowship policy.

4. **Prompts**  
   Prompt content must be reviewed for Fellowship terminology, behavior, and escalation requirements.

5. **Retrieval quality**  
   The retrieval configuration should be tested against the actual Fellowship corpus.

6. **Confidence behavior**  
   Confidence must not be interpreted as a guarantee of response usefulness or policy correctness. Review behavior must be validated with test cases.

7. **Make.com integration**  
   Fellowship should use its own workflow/configuration and must not interfere with HR automation.

8. **Production auto-send**  
   Automatic sending should remain disabled until the complete Fellowship workflow has been tested and approved.

---

## Documentation

Project documentation lives under:

```text
docs/
```

Current files include:

### `IMPLEMENTATION_CHECKLIST.md`

Tracks Fellowship replication and conversion tasks.

### `MAKE_SETUP.md`

Contains notes for setting up the Fellowship-specific Make.com workflow.

### `TEAM_NOTES.md`

Provides a location for implementation notes, findings, handover information, and project documentation.

Team members should update documentation alongside implementation changes rather than waiting until the end of the project.

---

## Development Principle

The current priority is:

> **Replicate first, validate the baseline, then improve deliberately.**

The HR automation provides the starting architecture, but Fellowship should ultimately operate as its own independently configured and validated system.

Every Fellowship-specific change should be traceable, testable, and documented before production rollout.

---

## Community Dreams Foundation

This project is developed for **Community Dreams Foundation (CDF)** as part of the Fellowship Email Automation initiative.

For internal project questions, repository access, environment credentials, or production workflow changes, coordinate through the project's approved CDF communication channels rather than placing sensitive information in GitHub issues or repository files.
