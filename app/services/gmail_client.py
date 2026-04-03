"""Gmail client service."""

import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_token, encrypt_token

logger = get_logger(__name__)

# Gmail API scopes - minimal send-only scope
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def create_auth_flow() -> Flow:
    """Create OAuth flow for Gmail authentication."""
    client_secrets_path = settings.GOOGLE_CLIENT_SECRETS_PATH
    
    if not os.path.exists(client_secrets_path):
        raise FileNotFoundError(
            f"Client secrets file not found: {client_secrets_path}. "
            "Please download from Google Cloud Console."
        )
    
    flow = Flow.from_client_secrets_file(
        client_secrets_path,
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
    return auth_url, state


def exchange_code_for_credentials(code: str) -> dict:
    """Exchange authorization code for credentials."""
    flow = create_auth_flow()
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
) -> MIMEMultipart:
    """Create MIME message for email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    
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
    ) -> dict:
        """Send email via Gmail API."""
        try:
            service = self._get_service()
            
            # Create MIME message
            msg = create_mime_message(sender, to, subject, plain_text, html_body)
            
            # Encode and send
            encoded_msg = encode_message(msg)
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
