# Codex Handoff Prompt

Build an internal single-user email outreach application in Python with FastAPI + LangGraph.

Requirements:
- Connect one Gmail account via OAuth.
- Upload a CSV of recipients/leads.
- Profile the CSV and infer:
  - primary email column
  - name/company columns
  - personalization fields
  - segmentation fields
  - send blockers such as opt-out / already-sent / do-not-contact
  - inferred campaign goal
- Generate a campaign plan and sample emails for review.
- Require campaign-level approval before any real send.
- Process one row at a time through a recipient graph:
  - normalize row
  - eligibility check
  - build personalization context
  - generate subject + plain text + HTML
  - validate output
  - optional human review if risky
  - send via Gmail
  - persist outcome
- Use LangGraph persistence and resumable thread IDs.
- Default to dry-run mode.
- Add idempotency so the same recipient/content is not sent twice.
- Add exports and audit logs.
- Build a minimal internal UI.

Technical stack:
- Python 3.11+
- FastAPI
- LangGraph
- Pydantic v2
- SQLAlchemy + Alembic
- SQLite for local dev, PostgreSQL-ready abstractions for production
- pandas for CSV handling
- google-api-python-client + google-auth-oauthlib + google-auth-httplib2 for Gmail
- tenacity for retries

Design constraints:
- v1 must use minimal Gmail access: connect Gmail for sending only.
- Do not read mailbox contents in v1.
- Do not create Gmail drafts in v1.
- Keep preview/approval inside the app.
- Persist all workflow state so campaigns survive restart.
- Implement campaign graph + recipient graph separately.
- Add unit + integration tests.
- Add README and sample fixtures.
- Make local startup straightforward.

Deliverables:
- complete codebase
- runnable local app
- DB models and migrations
- API endpoints
- minimal templates/UI
- tests
- README
- sample CSV
- .env.example
