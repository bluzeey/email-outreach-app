# Email Outreach Application

An internal single-user email outreach application built with FastAPI + LangGraph.

## Features

- **Gmail OAuth Integration**: Connect one Gmail account for sending
- **CSV Upload & Profiling**: Upload leads/contacts and automatically infer schema
- **AI-Powered Personalization**: Generate personalized emails per recipient
- **Campaign Management**: Plan, approve, and execute email campaigns
- **Human-in-the-Loop**: Campaign-level and row-level approval workflows
- **Dry-Run Mode**: Preview emails before sending (default mode)
- **Idempotency**: Prevent duplicate sends
- **Audit Logging**: Complete trail of all actions
- **Resumable Workflows**: Pause/resume campaigns with LangGraph persistence

## Tech Stack

- Python 3.11+
- FastAPI (web framework)
- LangGraph (workflow orchestration)
- Pydantic v2 (data validation)
- SQLAlchemy + Alembic (database)
- SQLite (auto-created locally)
- pandas (CSV handling)
- Google API Client (Gmail integration)
- Fireworks AI / OpenAI (LLM providers)

## Quick Start

### 1. Clone & Install

```bash
# Clone the repository
git clone <your-repo-url>
cd email-outreach-app

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### 2. Get Your API Keys

You need **3 API keys** to run this app:

| Key | Where to Get | Cost |
|-----|-------------|------|
| **FIREWORKS_API_KEY** | [fireworks.ai](https://fireworks.ai) | Free tier available |
| **GOOGLE_CLIENT_ID** | [Google Cloud Console](https://console.cloud.google.com/) | Free |
| **GOOGLE_CLIENT_SECRET** | [Google Cloud Console](https://console.cloud.google.com/) | Free |

**Optional**: `OPENAI_API_KEY` - only if you want OpenAI as a fallback

### 3. Setup Gmail OAuth (5 minutes)

**Video Guides** (watch either one):
- [How to Create Google OAuth Credentials](https://www.youtube.com/watch?v=StamvXkNly4) - General OAuth setup
- [Search YouTube: "Gmail API OAuth setup web application scopes"](https://www.youtube.com/results?search_query=gmail+api+oauth+setup+web+application+scopes) - Detailed walkthrough with scopes

Or follow these detailed steps:

#### Step 1: Create a Project & Enable Gmail API
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (click the project dropdown → "New Project", name it "Email Outreach")
3. Once created, make sure your new project is selected
4. Go to **"APIs & Services"** → **"Library"** in the left sidebar
5. Search for **"Gmail API"** → Click on it → Click **"Enable"**

#### Step 2: Configure OAuth Consent Screen & Add Scopes
1. Go to **"APIs & Services"** → **"OAuth consent screen"**
2. Select **"External"** as User Type → Click **"Create"**
3. Fill in the app information:
   - App name: **"Email Outreach"**
   - User support email: **your email**
   - Developer contact information: **your email**
   - Click **"Save and Continue"**
4. **IMPORTANT - Add ALL Required Scopes**:
   - Click **"Add or Remove Scopes"**
   - Search for and check these 3 scopes:
     - `https://www.googleapis.com/auth/gmail.send` - Send emails
     - `openid` - Get user identity
     - `https://www.googleapis.com/auth/userinfo.email` - Get user email
   - Click **"Update"**
   - Click **"Save and Continue"**
5. Skip the "Test users" step for now (you can add yourself later if needed)
6. Click **"Back to Dashboard"**

#### Step 3: Create OAuth Credentials
1. Go to **"APIs & Services"** → **"Credentials"**
2. Click **"Create Credentials"** → Select **"OAuth client ID"**
3. Configure the OAuth client:
   - Application type: **"Web application"**
   - Name: **"Email Outreach Web"**
   - **Authorized JavaScript origins**: Click **"Add URI"** → Enter `http://localhost:8000`
   - **Authorized redirect URIs**: Click **"Add URI"** → Enter `http://localhost:8000/auth/google/callback`
   - Click **"Create"**
4. A popup will show your **Client ID** and **Client Secret**
   - Click the copy icon for each and save them for the next step
   - ⚠️ **Note**: You can always retrieve these later from the Credentials page

> **Important**: If you run the app on a different port (e.g., `8001`), update the origins and redirect URIs accordingly (use `http://localhost:8001` instead).

### 4. Configure Environment

```bash
# Copy example file
cp .env.example .env

# Edit .env with your keys
nano .env  # or use your favorite editor
```

Your `.env` file should look like this:

```bash
# LLM API (get from fireworks.ai)
FIREWORKS_API_KEY=fw-your-fireworks-key-here

# Optional: OpenAI fallback
OPENAI_API_KEY=sk-your-openai-key-here

# Gmail OAuth (from Google Cloud Console)
GOOGLE_CLIENT_ID=123456789-abc123.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your-secret-here
```

**Note**: 
- Databases (`outreach.db`, `checkpoints.db`) are **auto-created** when you first run the app
- OAuth encryption keys are auto-generated per session
- Uploads folder is created automatically

### 5. Run the Application

```bash
# Start the server
python -m app.main

# Or with uvicorn
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

## How to Use

1. **Connect Gmail**: Click "Connect Gmail" and authenticate
2. **Create Campaign**: Click "New Campaign", give it a name
3. **Upload CSV**: Upload your leads file (see sample format below)
4. **Review & Analyze**: The app detects email columns and generates a campaign plan
5. **Preview**: Review sample emails generated by AI
6. **Approve**: Click "Approve Campaign" to start processing
7. **Monitor**: Watch progress in real-time
8. **Export**: Download results when done

### Sample CSV Format

```csv
first_name,last_name,email,company,title
John,Doe,john@example.com,Acme Inc,CEO
Jane,Smith,jane@example.com,TechCorp,CTO
```

See `tests/fixtures/sample_leads.csv` for a complete example.

## Configuration Reference

| Variable | Description | Required |
|----------|-------------|----------|
| `FIREWORKS_API_KEY` | Fireworks AI API key | **Yes** |
| `OPENAI_API_KEY` | OpenAI API key (fallback) | No |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | **Yes** |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | **Yes** |
| `DRY_RUN_DEFAULT` | Start in dry-run mode | No (default: true) |
| `LOG_LEVEL` | Logging verbosity | No (default: INFO) |

### LLM Provider Options

**Option 1: Fireworks AI with Kimi K2.5 Turbo (Default)**
```bash
FIREWORKS_API_KEY=your_key_here
OPENAI_API_KEY=your_openai_key_here  # Optional fallback
```

**Option 2: OpenAI Only**
```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

When Fireworks fails (rate limits, etc.), the app automatically falls back to OpenAI.

## Project Structure

```
email-outreach-app/
├── app/
│   ├── api/              # API endpoints (auth, campaigns, reviews)
│   ├── core/             # Config, logging, security
│   ├── db/               # Database models
│   ├── graphs/           # LangGraph workflows
│   ├── schemas/          # Pydantic models
│   ├── services/         # Business logic
│   ├── templates/        # HTML templates
│   └── static/           # CSS styles
├── tests/                # Unit & integration tests
├── uploads/              # Auto-created: CSV uploads
├── outreach.db           # Auto-created: Main database
└── checkpoints.db        # Auto-created: LangGraph state
```

## API Documentation

Once running, access:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run specific test
pytest tests/unit/test_csv_loader.py -v
```

## Troubleshooting

### "No LLM client available"
- Check your `FIREWORKS_API_KEY` is set correctly
- Verify the API key is active in your Fireworks dashboard

### "Failed to authenticate with Google"
- Ensure you've enabled **Gmail API** in Google Cloud Console
- Make sure you selected **"Web application"** as the application type
- Verify your **Authorized JavaScript origins** includes `http://localhost:8000`
- Verify your **Authorized redirect URIs** includes `http://localhost:8000/auth/google/callback`
- Check that `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are correctly set in your `.env` file

### "Database locked" errors
- SQLite doesn't support concurrent writes well
- Stop the app and restart if you see this

## Gmail Permissions

This app uses **minimal** Gmail scopes:
- `https://www.googleapis.com/auth/gmail.send` - Send emails only
- `openid` - Authenticate your identity
- `https://www.googleapis.com/auth/userinfo.email` - Get your email address

**We do NOT access**:
- ❌ Your inbox (reading emails)
- ❌ Your contacts
- ❌ Your sent folder
- ❌ Drafts

## License

MIT License

---

**Questions?** Open an issue on GitHub or check the API docs at `/docs` when running locally.
