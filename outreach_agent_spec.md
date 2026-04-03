# Email Outreach Agent Spec
## Internal single-user MVP using LangGraph + Python

### 1) Executive summary

Build an internal email outreach application that connects **one Gmail account**, ingests a **CSV of leads/contacts**, infers the **structure and business logic of the CSV**, generates **personalized outbound emails**, allows the operator to **review/approve**, and then **sends email through Gmail**.

This should be implemented as a **Python backend** with **LangGraph** orchestrating long-running, stateful workflows and approvals. The first release should optimize for:
- reliability over autonomy
- minimal Gmail scope footprint
- clear auditability
- easy local/private deployment
- safe resume after crashes or partial failures

The implementation target is an internal MVP that Codex can build end-to-end.

---

### 2) Product assumptions

These are the assumptions the implementation should use unless I later override them:

1. This is a **single-user internal tool**, not a public SaaS product.
2. The app connects to **one Gmail sender account**.
3. The input CSV contains **one row per intended recipient**.
4. The system sends **one outbound email per valid row** in v1.
5. The app should **not read the user's mailbox** in v1 unless explicitly enabled later.
6. Human approval is required before any real send.
7. v1 does **not** include reply tracking, sequences, A/B testing, CRM sync, or attachments.
8. v1 should support both:
   - **dry-run mode**: generate drafts only
   - **send mode**: actually send mail through Gmail

---

### 3) Why this design

The main design constraint is Gmail scope management.

For v1, use **send-only Gmail integration** and keep all preview/review inside the app. Do **not** create Gmail drafts by default, and do **not** read prior emails, signature, inbox, or sent folder in v1.

Why:
- This keeps the scope footprint smaller.
- It reduces compliance and verification burden.
- It simplifies implementation.
- It still satisfies the core use case: “connect my Gmail, generate emails from a CSV, and send them.”

Use LangGraph because this workflow is naturally stateful and long-running:
- CSV profiling
- schema inference
- sample generation
- user approval
- per-row generation
- per-row validation
- sending
- retries / pause / resume

This is exactly the kind of process that benefits from graph state, checkpoints, and human-in-the-loop interrupts.

---

### 4) Goals and non-goals

#### Goals
1. Connect Gmail securely.
2. Upload and parse CSV files.
3. Infer the CSV schema and business logic.
4. Generate a campaign plan automatically.
5. Generate personalized email drafts per row.
6. Validate drafts before sending.
7. Pause for campaign-level approval.
8. Optionally pause for row-level approval when risk is high.
9. Send emails via Gmail.
10. Persist every step for audit and resume.
11. Export results and logs.

#### Non-goals for v1
1. Reading mailbox history or prior sent messages.
2. Reply detection or follow-up sequencing.
3. Deliverability analytics beyond basic send status.
4. Multi-tenant account management.
5. Marketing automation features.
6. Chrome extension / Gmail add-on.
7. Attachment handling.
8. Autonomous sending with zero approval.

---

### 5) High-level architecture

#### Backend
- **Python 3.11+**
- **FastAPI** for API layer
- **LangGraph** for orchestration
- **Pydantic v2** for all schemas and structured validation
- **SQLAlchemy + Alembic** for database layer
- **PostgreSQL** for production persistence
- **SQLite** for local development
- **pandas** for CSV ingestion/profiling
- **google-api-python-client**, **google-auth-oauthlib**, **google-auth-httplib2** for Gmail integration
- **tenacity** for retries
- **structlog** or standard JSON logging
- Optional: **LangSmith** for tracing

#### Frontend
Use a **minimal internal UI**:
- server-rendered templates with Jinja2, or
- FastAPI + HTMX

Avoid a heavy frontend in v1. The goal is operator workflows, not polished SaaS UX.

#### Storage
- Database for metadata and workflow state
- Local filesystem or object storage for uploaded CSVs
- Database/file-backed checkpointer for LangGraph

#### LLM layer
Implement an abstraction:
- `LLMClient` interface
- default provider configurable via environment variables
- support structured outputs / JSON schema mode when available
- local Pydantic validation is mandatory even if model-level structured output is enabled

---

### 6) Core product flow

1. User opens app.
2. User connects Gmail.
3. User uploads CSV.
4. App profiles CSV and infers:
   - recipient email column
   - name/company columns
   - personalization fields
   - segmentation columns
   - send/no-send logic
   - possible offer / campaign objective
5. App produces a proposed campaign plan.
6. App generates sample emails for a handful of rows.
7. User reviews inferred mapping and sample drafts.
8. User approves campaign.
9. System processes all eligible rows:
   - normalize row
   - check eligibility
   - generate email
   - validate
   - approve if needed
   - send
   - record result
10. User watches progress in dashboard.
11. User exports a result CSV / audit log.

---

### 7) Recommended Gmail strategy

#### v1 scope strategy
Use **send-only** integration by default.

Do not request mailbox-reading scopes in v1.
Do not create Gmail drafts in v1.
Do not attempt mailbox watch/reply ingestion in v1.

#### Local/internal auth strategy
For local/private MVP, support a desktop-style OAuth flow during development and testing.

#### Production auth strategy
For a deployed internal service, implement proper server-side OAuth handling and secure token storage.
Do not store OAuth tokens in plaintext files in production.

#### Token storage
Store OAuth credentials encrypted or protected by the runtime environment:
- local dev: file-based token store is acceptable
- production: encrypted DB field or secret-backed storage

#### Sender identity
Use the authenticated Gmail account as the sender.
No alias management in v1.

---

### 8) LangGraph design

Use **two graphs**:

#### A. Campaign graph
Responsible for:
- CSV ingestion and profiling
- schema inference
- campaign plan generation
- sample draft generation
- campaign-level approval
- dispatching recipient jobs
- monitoring progress
- final campaign completion

#### B. Recipient graph
Responsible for one row at a time:
- normalize row
- infer personalization context
- generate draft
- validate draft
- optional human review interrupt
- send via Gmail
- persist result

This split prevents one giant state blob for large CSVs and makes retries/resume much cleaner.

---

### 9) Threading and persistence strategy

Use deterministic thread IDs.

#### Campaign graph thread_id
`campaign:{campaign_id}`

#### Recipient graph thread_id
`campaign:{campaign_id}:recipient:{recipient_id}`

Why:
- Every unit of work becomes resumable.
- Operator can inspect one failed row without replaying the entire campaign.
- Resume logic is simple and deterministic.

#### Checkpointer strategy
- local dev: SQLite checkpointer
- production: PostgreSQL checkpointer

All graph state must be JSON-serializable.

---

### 10) Campaign graph state

Use a Pydantic model like:

```python
class CampaignState(BaseModel):
    campaign_id: str
    gmail_account_id: str | None = None
    csv_path: str
    csv_profile: dict | None = None
    inferred_schema: dict | None = None
    schema_confidence: float | None = None
    campaign_plan: dict | None = None
    sample_drafts: list[dict] = []
    approval_status: Literal["pending", "approved", "rejected"] = "pending"
    row_ids: list[str] = []
    dispatch_cursor: int = 0
    totals: dict = {}
    errors: list[str] = []
    status: Literal[
        "created",
        "profiling",
        "awaiting_schema_review",
        "awaiting_campaign_approval",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
    ] = "created"
```

---

### 11) Recipient graph state

```python
class RecipientState(BaseModel):
    campaign_id: str
    recipient_id: str
    row_number: int
    raw_row: dict
    normalized_row: dict | None = None
    eligibility: dict | None = None
    personalization_context: dict | None = None
    generated_email: dict | None = None
    validation_report: dict | None = None
    review_required: bool = False
    approval_status: Literal["pending", "approved", "rejected", "not_required"] = "pending"
    send_result: dict | None = None
    retries: int = 0
    status: Literal[
        "queued",
        "normalized",
        "ineligible",
        "generated",
        "validated",
        "awaiting_review",
        "sending",
        "sent",
        "failed",
        "skipped",
    ] = "queued"
    errors: list[str] = []
```

---

### 12) Graph nodes

#### Campaign graph nodes
1. `load_csv`
2. `profile_csv`
3. `infer_schema`
4. `infer_campaign_plan`
5. `generate_sample_drafts`
6. `campaign_review_interrupt`
7. `prepare_recipient_records`
8. `dispatch_recipient_runs`
9. `aggregate_progress`
10. `finalize_campaign`

#### Recipient graph nodes
1. `normalize_row`
2. `eligibility_check`
3. `build_personalization_context`
4. `generate_email_draft`
5. `validate_email_draft`
6. `row_review_interrupt_if_needed`
7. `send_email_via_gmail`
8. `persist_send_outcome`

---

### 13) CSV “logic understanding” requirements

This is the most important part of the product.

The system must not just read the CSV — it must infer the logic of how the CSV should drive email generation and sending.

Implement this as a hybrid of deterministic analysis + LLM inference.

#### Step 1: deterministic profiling
For every column compute:
- name
- inferred primitive type
- null percentage
- unique count
- representative samples
- regex matches:
  - email
  - url/domain
  - phone
  - date
  - currency
  - boolean-like strings
- semantic hints from header names

#### Step 2: candidate role detection
Infer likely column roles:
- primary email
- first name / last name / full name
- company / domain / website
- job title / persona
- industry / segment / region
- custom personalization fields
- campaign blockers:
  - opt-out
  - unsubscribed
  - do-not-contact
  - already_sent
  - status not ready

#### Step 3: LLM schema inference
Feed the profile + sample rows into a schema inference prompt and return strict JSON:
- primary recipient email column
- personalization columns ranked by usefulness
- segmentation columns
- row eligibility rules
- inferred campaign objective
- confidence score
- unresolved questions

#### Step 4: low-confidence fallback
If confidence is below threshold or there are multiple possible email columns, force human review before any generation.

---

### 14) CSV schema contract

```python
class FilterRule(BaseModel):
    column: str
    operator: Literal["eq", "neq", "in", "not_in", "is_null", "not_null", "truthy", "falsy"]
    value: str | list[str] | None = None
    reason: str

class CsvSchemaInference(BaseModel):
    primary_email_column: str
    recipient_name_columns: list[str] = []
    company_columns: list[str] = []
    personalization_columns: list[str] = []
    segmentation_columns: list[str] = []
    blocker_rules: list[FilterRule] = []
    send_rules: list[FilterRule] = []
    inferred_goal: str
    confidence: float
    unresolved_questions: list[str] = []
```

---

### 15) Campaign plan contract

```python
class CampaignPlan(BaseModel):
    campaign_name: str
    inferred_goal: str
    target_audience: str
    tone: str
    style_constraints: list[str] = []
    cta: str
    subject_style: str
    personalization_priority: list[str] = []
    do_not_claim: list[str] = []
    review_policy: dict
    sending_policy: dict
```

The plan should be editable by the operator before execution.

---

### 16) Personalization logic

Per row, build a personalization context object.

#### Rules
1. Use only data available in:
   - the row
   - explicit user campaign settings
   - static templates/settings configured in the app
2. Do not fabricate achievements, metrics, or personal facts.
3. If a field is missing, gracefully degrade.
4. Do not use blank placeholders in output.
5. Track exactly which columns were used in the generated draft.

#### Example personalization sources
- first_name
- company
- title
- industry
- pain_point
- product_interest
- location
- website/domain

---

### 17) Draft generation contract

```python
class GeneratedEmail(BaseModel):
    subject: str
    plain_text_body: str
    html_body: str
    personalization_fields_used: list[str]
    key_claims_used: list[str] = []
    confidence: float
    needs_human_review: bool = False
    review_reasons: list[str] = []
```

#### Generation requirements
- Always produce both plain text and HTML.
- Keep tone concise and professional unless overridden.
- Body should be short enough for cold outreach.
- No unresolved placeholders.
- No invented facts.
- No mention of fields not present in row context.
- Include a configurable signature block.

---

### 18) Validation layer

Every generated email must pass **deterministic** validation before send.

#### Deterministic validators
- subject exists
- body exists
- no unresolved placeholders like `{{first_name}}`
- recipient email valid
- row is eligible
- campaign approved
- content length within thresholds
- duplicate send not already recorded
- blocked row conditions not triggered

#### LLM-assisted validators
Use a second validation pass to score:
- hallucination risk
- mismatch with row data
- awkward/overly generic phrasing
- prohibited claim detection
- personalization quality
- CTA quality

#### Validation output
```python
class ValidationReport(BaseModel):
    passed: bool
    risk_score: float
    issues: list[str] = []
    suggested_fixes: list[str] = []
    requires_human_review: bool = False
```

If deterministic validation fails, do not send.
If risk score is high, route to human review interrupt.

---

### 19) Human-in-the-loop policy

This workflow must not auto-send without explicit campaign approval.

#### Mandatory approval points
1. After schema inference + campaign plan generation
2. After sample drafts are generated
3. Before the first real send batch

#### Conditional row-level approval
Require per-row approval if:
- validation risk score > threshold
- required personalization fields are missing
- output includes sensitive claims
- schema confidence was low
- row was repaired after validation failure

#### Resume behavior
After approval, LangGraph should resume from the exact checkpoint without re-running already completed steps.

---

### 20) Gmail send implementation requirements

#### Transport
Use Gmail API for sending.

#### Sending method
Use direct send in v1, not draft creation by default.

#### MIME
Build an RFC-compliant MIME message containing:
- To
- Subject
- multipart/alternative
  - text/plain
  - text/html

#### Encoding
Encode the MIME message as base64url and send as the `raw` payload.

#### Idempotency
Before send, compute:
`idempotency_key = sha256(campaign_id + recipient_email + subject + plain_text_body)`

Store successful sends and skip duplicates unless a force-resend flag is explicitly set.

#### Send result persistence
Persist:
- recipient_id
- campaign_id
- timestamp
- idempotency_key
- Gmail API response payload
- send status
- error if any

---

### 21) Sending policy and throttling

Default sending behavior:
- bounded concurrency
- configurable rate limit
- exponential backoff with jitter on transient errors
- pause campaign on auth failures
- pause campaign if repeated send failures exceed threshold

Recommended MVP defaults:
- generation concurrency: 3 to 5
- send concurrency: 1 to 3
- rate limit: configurable per minute
- automatic retry for transient API failures only

---

### 22) Database schema

Create these tables:

#### `gmail_accounts`
- id
- email
- provider
- scopes
- token_ref / encrypted_token_blob
- connected_at
- status

#### `campaigns`
- id
- name
- gmail_account_id
- status
- csv_filename
- csv_storage_path
- inferred_schema_json
- campaign_plan_json
- totals_json
- created_at
- updated_at

#### `campaign_rows`
- id
- campaign_id
- row_number
- raw_row_json
- normalized_row_json
- recipient_email
- status
- validation_report_json
- error_message
- created_at
- updated_at

#### `email_drafts`
- id
- campaign_row_id
- subject
- plain_text_body
- html_body
- generation_meta_json
- created_at

#### `send_events`
- id
- campaign_row_id
- idempotency_key
- provider
- provider_message_id
- provider_response_json
- sent_at
- status
- error_message

#### `approval_events`
- id
- campaign_id
- campaign_row_id nullable
- decision
- reviewer
- notes
- created_at

---

### 23) API surface

#### Auth
- `GET /auth/google/start`
- `GET /auth/google/callback`
- `POST /auth/google/disconnect`

#### Campaign lifecycle
- `POST /campaigns`
- `POST /campaigns/{campaign_id}/upload-csv`
- `POST /campaigns/{campaign_id}/analyze`
- `POST /campaigns/{campaign_id}/approve`
- `POST /campaigns/{campaign_id}/reject`
- `POST /campaigns/{campaign_id}/run`
- `POST /campaigns/{campaign_id}/pause`
- `POST /campaigns/{campaign_id}/resume`
- `POST /campaigns/{campaign_id}/cancel`
- `GET /campaigns/{campaign_id}`
- `GET /campaigns/{campaign_id}/rows`
- `GET /campaigns/{campaign_id}/export`

#### Review endpoints
- `GET /campaigns/{campaign_id}/samples`
- `POST /campaigns/{campaign_id}/rows/{row_id}/approve`
- `POST /campaigns/{campaign_id}/rows/{row_id}/reject`
- `POST /campaigns/{campaign_id}/rows/{row_id}/regenerate`

---

### 24) UI pages

Build a simple internal operator UI with these pages:

1. **Connect Gmail**
2. **Upload CSV**
3. **Review inferred schema**
4. **Review campaign plan**
5. **Preview sample emails**
6. **Run campaign**
7. **Live progress dashboard**
8. **Row-level review page**
9. **Results/export page**

No need for a complex design system in v1.

---

### 25) Folder structure

```text
app/
  api/
    auth.py
    campaigns.py
    reviews.py
  core/
    config.py
    logging.py
    security.py
  db/
    base.py
    models.py
    session.py
    migrations/
  graphs/
    campaign_graph.py
    recipient_graph.py
    state.py
    nodes/
      campaign_nodes.py
      recipient_nodes.py
  schemas/
    auth.py
    campaign.py
    csv_inference.py
    draft.py
    validation.py
  services/
    gmail_client.py
    csv_loader.py
    csv_profiler.py
    schema_inference_service.py
    plan_generation_service.py
    draft_generation_service.py
    validation_service.py
    send_service.py
    idempotency_service.py
  templates/
  static/
  tests/
    unit/
    integration/
  main.py
pyproject.toml
README.md
.env.example
```

---

### 26) Service responsibilities

#### `csv_loader.py`
- read CSV with encoding fallback
- normalize headers
- preserve original row data
- fail clearly on malformed input

#### `csv_profiler.py`
- compute column stats
- detect likely semantic roles
- return profile JSON for inference

#### `schema_inference_service.py`
- call LLM with structured output contract
- return `CsvSchemaInference`
- attach confidence and unresolved questions

#### `plan_generation_service.py`
- create `CampaignPlan` from inferred schema + sample rows

#### `draft_generation_service.py`
- create per-row `GeneratedEmail`

#### `validation_service.py`
- deterministic checks first
- optional LLM validation second
- emit `ValidationReport`

#### `gmail_client.py`
- OAuth auth flow
- token refresh
- MIME builder
- `send_message()`

#### `send_service.py`
- idempotency checks
- throttling
- retry policy
- send outcome persistence

---

### 27) Prompting requirements

All LLM prompts should be stored as versioned templates.

#### Prompt families
1. schema inference
2. campaign plan generation
3. sample draft generation
4. recipient draft generation
5. validation / critique
6. repair / regeneration

#### Prompt rules
- Include only the minimum required row fields.
- Include explicit do-not-invent instructions.
- Include output schema.
- Include validation criteria.
- Track prompt version in metadata for every generated draft.

---

### 28) Model output discipline

The implementation must treat model output as untrusted until validated.

#### Required rules
1. Use structured output / JSON schema mode when available.
2. Parse into Pydantic models.
3. On parse failure:
   - retry once with repair prompt
   - if still failing, route to review
4. Never let raw model text directly trigger send.

---

### 29) Dry-run mode

Dry-run mode is required.

#### Dry-run behavior
- no Gmail send calls
- full CSV analysis
- full draft generation
- full validation
- full approval workflow
- dashboard and export work normally
- send status is recorded as `dry_run_preview`

This mode should be the default until operator explicitly enables real send.

---

### 30) Error handling

#### CSV errors
- invalid encoding
- missing email column
- empty file
- malformed delimiters
- duplicate headers

#### LLM errors
- timeout
- malformed structured output
- low-confidence inference
- missing subject/body

#### Gmail errors
- auth expired
- revoked token
- insufficient scope
- rate-limited
- transient 5xx
- malformed MIME

#### System behavior
- never lose campaign state
- pause instead of silently failing
- expose user-readable error reason
- keep raw technical error in logs

---

### 31) Observability and audit

Implement:
- structured logs with campaign_id and recipient_id
- per-node timing
- send counts
- success/failure counts
- exportable audit trail
- operator decision history

If LangSmith is enabled, trace both campaign and recipient flows.

---

### 32) Security requirements

1. Never log OAuth tokens or email bodies at debug level by default.
2. Mask secrets in logs.
3. Store tokens securely.
4. Restrict Gmail scopes to the minimum needed.
5. Keep uploaded CSV access limited to authenticated operator.
6. Add CSRF/session protection if using cookie-based auth.
7. Provide a delete campaign action that removes uploaded CSV and generated drafts.

---

### 33) Acceptance criteria

The implementation is complete when all of the following are true:

1. I can connect a Gmail account.
2. I can upload a CSV and see inferred schema/mapping.
3. The app detects the likely primary email column automatically.
4. The app proposes a campaign plan and sample drafts.
5. I can approve or reject the campaign.
6. In dry-run mode, the app processes all rows and exports results without sending.
7. In send mode, the app sends email via Gmail for all eligible approved rows.
8. Invalid rows are skipped with clear reasons.
9. Duplicate sends are prevented by idempotency checks.
10. The workflow survives restart and resumes from persisted state.
11. I can review row-level failures and retry them.
12. I can export campaign results as CSV/JSON.

---

### 34) Test plan

#### Unit tests
- CSV parsing and profiling
- header normalization
- email column detection
- blocker rule application
- prompt input builders
- generated draft parsing
- validation logic
- MIME builder
- idempotency service

#### Integration tests
- OAuth callback handler (mocked)
- LangGraph campaign flow in dry-run mode
- recipient flow with success path
- recipient flow with validation failure
- send service with mocked Gmail API
- pause/resume recovery

#### End-to-end tests
- sample CSV with 10 rows
- approve campaign
- process dry-run
- process real-send against mocked Gmail transport

---

### 35) Recommended implementation phases

#### Phase 1
- project scaffold
- DB models
- Gmail auth
- CSV upload
- CSV profiler
- dry-run campaign graph

#### Phase 2
- schema inference
- campaign plan generation
- sample draft generation
- campaign approval UI

#### Phase 3
- recipient graph
- validation
- Gmail send service
- progress dashboard

#### Phase 4
- retries
- pause/resume
- exports
- polish / observability

---

### 36) Explicit build instructions for Codex

Codex should:
1. create the full project scaffold
2. implement local dev using SQLite and file storage
3. implement production-ready abstractions for PostgreSQL and secure token storage
4. build the LangGraph campaign and recipient graphs
5. implement Gmail OAuth and Gmail send transport
6. implement dry-run as the default execution mode
7. add a minimal operator UI
8. add tests and a README
9. include sample CSV fixtures
10. make the app runnable locally with a single command

---

### 37) Copy-paste prompt for Codex

Use this prompt verbatim if needed:

> Build an internal single-user email outreach app in Python using FastAPI, LangGraph, Pydantic, SQLAlchemy, and Gmail API. The app must connect one Gmail account, upload a CSV, infer the CSV schema and send logic, generate personalized emails per row, allow campaign-level approval, optionally require row-level review for risky rows, and send via Gmail. Use a campaign graph plus recipient graph. Default to dry-run mode. Use minimal Gmail scopes and do not read the mailbox in v1. Persist workflow state so the system can pause/resume after restart. Create a minimal operator UI, full database models, structured logging, tests, and README. Include idempotency, validation, and export of results.

---

### 38) Future roadmap (not in v1)

1. Gmail draft mode
2. Signature extraction from Gmail
3. Reply monitoring / bounce handling
4. Follow-up sequences
5. CRM sync
6. multi-user auth
7. template libraries
8. A/B testing
9. deliverability analytics
10. inbox watch / PubSub notifications

---

### 39) Final implementation note

The most important v1 discipline is this:

**Use AI for inference and generation, but keep sending deterministic, auditable, resumable, and approval-gated.**

That principle should drive every implementation choice.
