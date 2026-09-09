# personal-agent/tools/google_auth.py
import os
import logging
from typing import Optional, Any
import config

logger = logging.getLogger(__name__)

# Minimum scope for Google Calendar read/create events along with Gmail scopes
DEFAULT_SCOPES = getattr(
    config,
    "GOOGLE_SCOPES",
    [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.events",
    ],
)

CREDENTIALS_FILE = getattr(config, "GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = getattr(config, "GOOGLE_TOKEN_FILE", "token.json")


def _sanitize_auth_error(err: Exception) -> str:
    """Sanitize exception string to avoid exposing secrets or raw tokens."""
    if not err:
        return "Authentication failure"
    return "Authentication error: Unable to authenticate with Google API."


def get_google_credentials(
    scopes: Optional[list[str]] = None,
    credentials_file: Optional[str] = None,
    token_file: Optional[str] = None,
) -> Any:
    """
    Unified Google OAuth credential retrieval helper.
    Loads existing credentials from token.json or initiates OAuth consent flow
    using client credentials.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        logger.error("Required Google auth libraries are not installed.")
        raise RuntimeError("Google auth libraries missing.") from e

    target_scopes = scopes or DEFAULT_SCOPES
    cred_path = credentials_file or CREDENTIALS_FILE
    tok_path = token_file or TOKEN_FILE

    creds = None
    if os.path.exists(tok_path):
        try:
            creds = Credentials.from_authorized_user_file(tok_path, target_scopes)
        except Exception as e:
            logger.warning("Failed to load credentials from token file: %s", _sanitize_auth_error(e))
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning("Failed to refresh token: %s", _sanitize_auth_error(e))
                creds = None

        if not creds:
            if not os.path.exists(cred_path):
                raise FileNotFoundError(
                    f"OAuth client credentials file '{cred_path}' not found."
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(cred_path, target_scopes)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                raise RuntimeError(_sanitize_auth_error(e)) from e

            # Save the credentials for future runs
            try:
                with open(tok_path, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                logger.warning("Failed to save credentials token file: %s", _sanitize_auth_error(e))

    return creds


def get_calendar_service(
    scopes: Optional[list[str]] = None,
    credentials_file: Optional[str] = None,
    token_file: Optional[str] = None,
) -> Any:
    """
    Construct and return an authenticated Google Calendar API service client (v3).
    """
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        logger.error("google-api-python-client is not installed.")
        raise RuntimeError("google-api-python-client library missing.") from e

    creds = get_google_credentials(
        scopes=scopes,
        credentials_file=credentials_file,
        token_file=token_file,
    )
    try:
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        raise RuntimeError(_sanitize_auth_error(e)) from e


def get_gmail_service(
    scopes: Optional[list[str]] = None,
    credentials_file: Optional[str] = None,
    token_file: Optional[str] = None,
) -> Any:
    """
    Construct and return an authenticated Gmail API service client (v1).
    """
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        logger.error("google-api-python-client is not installed.")
        raise RuntimeError("google-api-python-client library missing.") from e

    creds = get_google_credentials(
        scopes=scopes,
        credentials_file=credentials_file,
        token_file=token_file,
    )
    try:
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        raise RuntimeError(_sanitize_auth_error(e)) from e
