# Email Outreach Application

Private, single-owner outreach app built with FastAPI, LangGraph and SQLite. Upload contacts, analyze campaigns, review generated drafts, and export results. AI analysis and drafting use the configured LLM provider.

**Migration mode: no real email sending.** Keep `GMAIL_CONNECTION_ENABLED=false`, `EMAIL_SENDING_ENABLED=false`, and `DRY_RUN_DEFAULT=true`. Do not connect live Gmail accounts or perform live send tests during migration.

## Local setup

Requires Python 3.11+. From the repository directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
chmod 600 .env
```

Edit `.env` before starting. Set the owner identity and session secret described below. Leave provider credentials blank unless needed for authorized provider use; startup and offline tests do not require LLM or Gmail credentials.

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, sign in as the configured owner, and keep campaigns in dry-run mode. Use synthetic contacts for migration checks. `/docs`, `/redoc`, and private APIs require the owner session. `/health` and `/ready` expose generic status; `/ready` also checks database connectivity.

## Owner sign-in and optional Gmail authorization

These are independent authentication flows:

| Purpose | Configuration | Behavior |
| --- | --- | --- |
| Private app access | `APP_GOOGLE_CLIENT_ID`, `APP_OWNER_EMAIL`, `APP_SESSION_SECRET`, `APP_BASE_URL`; keep `APP_LOGIN_REQUIRED=true` | Google Identity Services supplies a basic identity ID token. The server verifies it and accepts only the verified owner email, then issues an eight-hour signed, HttpOnly session cookie. No Gmail permission or persistent Google token storage is involved in owner sign-in. |
| Optional Gmail connection | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Separate OAuth authorization for mailbox access and sending. Disabled by `GMAIL_CONNECTION_ENABLED=false` during migration. |

For owner sign-in, configure a Google **web application** client with an authorized JavaScript origin exactly matching `APP_BASE_URL` (locally `http://127.0.0.1:8000`). Set that client ID explicitly in `APP_GOOGLE_CLIENT_ID`; the app does not fall back to `GOOGLE_CLIENT_ID`. Owner sign-in posts the ID token to `/login/google` and does not use the Gmail callback or require a Google client secret. Set `APP_OWNER_EMAIL` to the single owner's Google email.

`APP_BASE_URL` must be an origin without a path, query, or fragment. Use HTTPS in deployment; HTTP is accepted only on loopback. Missing or invalid owner configuration prevents app initialization. Disabling owner login is rejected in production.

For a later, separately authorized Gmail setup, enable the Gmail API for its Google project, configure the consent screen, and register the exact `GOOGLE_REDIRECT_URI` (for example, `https://outreach.example.com/auth/google/callback`). The current connection flow requests all four scopes:

- `https://www.googleapis.com/auth/gmail.send`
- `https://www.googleapis.com/auth/gmail.modify`
- `openid`
- `https://www.googleapis.com/auth/userinfo.email`

`gmail.modify` grants mailbox read/modify access; these permissions are broader than sending alone. Existing grants may require renewed consent for the current scopes. Configuring credentials or signing in as owner does not enable Gmail access or delivery. Keep both server flags false throughout migration.

## Secrets and server locks

For a new installation only, generate secrets once in a private terminal and store them in the private environment file or deployment secret store. For migration, reuse the existing secrets:

```bash
# APP_SESSION_SECRET: a random signing secret, at least 32 characters
python -c "import secrets; print(secrets.token_urlsafe(48))"

# ENCRYPTION_KEY: a valid Fernet key for stored Gmail credentials
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Preserve existing secrets when migrating. `ENCRYPTION_KEY` is supplied by the operator, never generated per session. Production requires a valid key even while Gmail is disabled. Gmail access/refresh tokens are encrypted in SQLite with this key; replacing or losing it makes existing credentials unreadable. Keep the matching key backed up securely alongside the database backup plan, outside source control. Do not regenerate it on restart or redeploy.

Keep `APP_SESSION_SECRET` stable across restarts; changing it invalidates owner sessions. Treat it separately from `ENCRYPTION_KEY`. Never commit `.env` files, provider secrets, tokens, or real keys into the example file or image.

| Setting | Current behavior |
| --- | --- |
| `GMAIL_CONNECTION_ENABLED=false` | Blocks Gmail authorization, credential exchange, refresh, and Gmail service access. |
| `EMAIL_SENDING_ENABLED=false` | Blocks real campaign and follow-up delivery at server entry points and the sending service/client. |
| `DRY_RUN_DEFAULT=true` | Sets the sending service's default to simulated delivery. Campaign/follow-up request defaults are also true. Campaign toggles or request fields cannot override the server locks. |

Dry-run delivery does not send Gmail messages, but AI analysis/drafting can still call a configured LLM provider. Leave `FIREWORKS_API_KEY` and `OPENAI_API_KEY` blank for provider-free checks. With `LLM_PROVIDER=fireworks`, OpenAI fallback is available only when `OPENAI_API_KEY` is configured; `LLM_PROVIDER=openai` uses OpenAI directly. Model settings and other defaults are listed in `.env.example`.

## Production container and persistent data

The Dockerfile runs as **UID/GID 10001:10001**, uses one worker, and defaults to `APP_ENV=production` with `/data` paths and both provider locks false. Keep one worker: Gmail OAuth flow state and campaign task coordination include in-memory state.

Create a private `.env.production` from `.env.example`, set the owner configuration and stable secrets above, and apply these overrides. The example's local paths override Docker image defaults if passed unchanged.

```dotenv
APP_ENV=production
APP_LOGIN_REQUIRED=true
APP_BASE_URL=https://outreach.example.com
DATA_DIR=/data
DATABASE_URL=sqlite:////data/outreach.db
CHECKPOINT_DATABASE_URL=sqlite:////data/checkpoints.db
UPLOAD_DIR=/data/uploads
GMAIL_CONNECTION_ENABLED=false
EMAIL_SENDING_ENABLED=false
DRY_RUN_DEFAULT=true
```

Use the actual public origin in `APP_BASE_URL` and the owner's Google client settings. On the deployment host, prepare durable storage writable by the container user. Example deployment commands for an authorized deployment:

```bash
chmod 600 .env.production
sudo install -d -o 10001 -g 10001 -m 0700 /srv/email-outreach/data
sudo install -d -o 10001 -g 10001 -m 0700 /srv/email-outreach/data/uploads
docker build -t email-outreach:local .
docker run -d --name email-outreach --restart unless-stopped \
  --env-file .env.production \
  --mount type=bind,src=/srv/email-outreach/data,dst=/data \
  -p 127.0.0.1:8000:8000 email-outreach:local
```

Place an HTTPS reverse proxy in front of the loopback port. Persist and back up `/data`, and preserve the matching encryption key in secure secret storage. The image creates `/data` directories but does not provision a durable host volume; the mount is an operator responsibility.

In production, startup requires an existing absolute `DATA_DIR`, owner login, a valid encryption key, and absolute SQLite database/checkpoint and upload paths contained inside `DATA_DIR`. The deployment validator rejects other database backends, in-memory SQLite, and paths escaping that directory. The mount must also allow UID 10001 to create/update files. Local development instead defaults to `./outreach.db` and `./uploads`; startup creates the main database tables and upload directory.

## Persistence limits

Campaigns, recipients, drafts, and send events are stored in the main SQLite database. Both LangGraph workflows currently compile **without a checkpointer**. `CHECKPOINT_DATABASE_URL` is configured and validated, but does not wire up durable graph state or automatically create a working checkpoint database. Existing pause/rerun paths use application database records; interrupted background work is not guaranteed to resume from its exact graph step after a restart.

## Offline verification

```bash
pytest
git diff --check -- README.md .env.example
```

The test configuration uses disposable local storage, synthetic owner settings, disabled Gmail/sending flags, and a network-blocking fixture. It does not validate a real Google login, mailbox connection, delivery, or deployment. Do not use live accounts for migration verification.

## Troubleshooting

- **Owner configuration error or rejected login:** verify the explicit `APP_GOOGLE_CLIENT_ID`, exact verified owner email, matching JavaScript origin and `APP_BASE_URL`, and a session secret of at least 32 characters.
- **Production startup failure:** check the stable Fernet key, persistent mount, absolute `/data` paths, and UID/GID 10001 write permissions. Keep `APP_LOGIN_REQUIRED=true`.
- **Gmail/sending unavailable:** expected while either server lock is false. Keep migration checks in dry-run mode.
- **No LLM client available:** provider credentials are needed for AI analysis/drafting, not startup or offline tests.
- **Stored credentials cannot be decrypted:** restore the encryption key that belongs to the migrated database; generating another key cannot decrypt old tokens.

## License

MIT License
