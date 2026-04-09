"""Gmail client service."""

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Gmail API scopes - includes gmail.modify for thread management
# Note: Users will need to reconnect their Gmail account to get the new scope
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",  # For thread management and reply tracking
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# In-memory storage for OAuth flows (keyed by state)
# Note: This only works for single-user apps. For multi-user, use Redis/DB
_oauth_flows = {}


def get_client_config() -> dict:
    """Build OAuth client configuration from settings."""
    return {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }


def create_auth_flow() -> Flow:
    """Create OAuth flow for Gmail authentication."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "Google OAuth credentials not configured. "
            "Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file."
        )

    client_config = get_client_config()
    flow = Flow.from_client_config(
        client_config,
        scopes=GMAIL_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )
    return flow


def get_authorization_url() -> tuple[str, str]:
    """Get authorization URL and state for OAuth flow."""
    flow = create_auth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    
    # Store the flow instance by state for later retrieval
    _oauth_flows[state] = flow
    
    return auth_url, state


def exchange_code_for_credentials(code: str, state: str) -> dict:
    """Exchange authorization code for credentials."""
    # Retrieve the flow instance from storage
    flow = _oauth_flows.pop(state, None)
    if not flow:
        raise ValueError(
            "OAuth flow not found or expired. Please restart the authentication process."
        )
    
    flow.fetch_token(code=code)

    credentials = flow.credentials

    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }


def credentials_to_dict(credentials: Credentials) -> dict:
    """Convert credentials to dictionary."""
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


def dict_to_credentials(cred_dict: dict) -> Credentials:
    """Convert dictionary to credentials."""
    return Credentials(
        token=cred_dict["token"],
        refresh_token=cred_dict.get("refresh_token"),
        token_uri=cred_dict["token_uri"],
        client_id=cred_dict["client_id"],
        client_secret=cred_dict["client_secret"],
        scopes=cred_dict["scopes"],
    )


def refresh_credentials_if_needed(credentials: Credentials) -> Credentials:
    """Refresh credentials if expired."""
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        logger.info("Refreshed expired OAuth token")
    return credentials


def build_gmail_service(credentials: Credentials):
    """Build Gmail API service."""
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def create_mime_message(
    sender: str,
    to: str,
    subject: str,
    plain_text: str,
    html_body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEMultipart:
    """Create MIME message for email.
    
    Args:
        sender: From email address
        to: To email address  
        subject: Email subject
        plain_text: Plain text body
        html_body: HTML body
        in_reply_to: Message ID for In-Reply-To header (for threading)
        references: Message IDs for References header (for threading)
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    
    # Add threading headers for followups
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    # Add plain text part
    part1 = MIMEText(plain_text, "plain")
    msg.attach(part1)

    # Add HTML part
    part2 = MIMEText(html_body, "html")
    msg.attach(part2)

    return msg


def encode_message(msg: MIMEMultipart) -> dict:
    """Encode MIME message for Gmail API."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


class GmailClient:
    """Gmail client for sending emails."""

    def __init__(self, credentials_dict: dict):
        self.credentials_dict = credentials_dict
        self.credentials = dict_to_credentials(credentials_dict)
        self.service = None

    def _get_service(self):
        """Get or create Gmail service."""
        if self.service is None:
            self.credentials = refresh_credentials_if_needed(self.credentials)
            self.service = build_gmail_service(self.credentials)
        return self.service

    def send_email(
        self,
        sender: str,
        to: str,
        subject: str,
        plain_text: str,
        html_body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> dict:
        """Send email via Gmail API.
        
        Args:
            sender: From email address
            to: To email address
            subject: Email subject
            plain_text: Plain text body
            html_body: HTML body
            thread_id: Gmail thread ID for threading (for followups)
            in_reply_to: Message ID for In-Reply-To header
            references: Message IDs for References header
        """
        try:
            service = self._get_service()

            # Create MIME message
            msg = create_mime_message(
                sender, to, subject, plain_text, html_body,
                in_reply_to=in_reply_to,
                references=references,
            )

            # Encode
            encoded_msg = encode_message(msg)
            
            # Add threadId if provided (for followups in same thread)
            if thread_id:
                encoded_msg["threadId"] = thread_id
            
            result = (
                service.users()
                .messages()
                .send(userId="me", body=encoded_msg)
                .execute()
            )

            logger.info(
                "Email sent successfully",
                message_id=result.get("id"),
                recipient=to,
                subject=subject,
            )

            return {
                "success": True,
                "message_id": result.get("id"),
                "thread_id": result.get("threadId"),
                "label_ids": result.get("labelIds", []),
                "raw_response": result,
            }

        except HttpError as e:
            logger.error(
                "Gmail API error",
                error=str(e),
                recipient=to,
                status_code=e.resp.status if hasattr(e, "resp") else None,
            )
            raise
        except Exception as e:
            logger.error(
                "Failed to send email",
                error=str(e),
                recipient=to,
            )
            raise

    def get_profile(self) -> dict:
        """Get Gmail profile."""
        service = self._get_service()
        profile = service.users().getProfile(userId="me").execute()
        return {
            "email": profile.get("emailAddress"),
            "messages_total": profile.get("messagesTotal"),
            "threads_total": profile.get("threadsTotal"),
            "history_id": profile.get("historyId"),
        }
