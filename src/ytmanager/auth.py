"""OAuth: get valid credentials and build the YouTube service."""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Full access to manage your YouTube account (needed to edit playlists).
# Use "https://www.googleapis.com/auth/youtube.readonly" if you only read.
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def get_credentials(client_secret: Path, token_path: Path) -> Credentials:
    """Load saved credentials, refresh them, or run the browser login once."""
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not client_secret.exists():
            raise FileNotFoundError(
                f"{client_secret} not found. See README: 'Google Cloud setup'."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    return creds


def build_service(client_secret: Path, token_path: Path):
    """Return an authenticated YouTube Data API v3 service."""
    creds = get_credentials(client_secret, token_path)
    return build("youtube", "v3", credentials=creds)
