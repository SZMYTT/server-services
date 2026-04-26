"""
One-time OAuth2 setup for Google Drive access.
Run this once on the server to authenticate NNLOS with your Google account.
Saves a token to config/drive_token.json — after that the worker uses it silently.

Usage:
    python3 mcp/drive_auth.py

You'll get a URL to open in a browser. Log in with the Google account
that owns the Drive folder. Paste the code back in the terminal.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDS_PATH = Path(os.environ.get(
    "GOOGLE_OAUTH_CREDENTIALS_JSON",
    Path(__file__).parent.parent / "config" / "drive_oauth_credentials.json",
))
TOKEN_PATH = Path(os.environ.get(
    "GOOGLE_DRIVE_TOKEN_JSON",
    Path(__file__).parent.parent / "config" / "drive_token.json",
))


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    if not CREDS_PATH.exists():
        print(f"\nCredentials file not found: {CREDS_PATH}")
        print("\nTo get it:")
        print("  1. Go to console.cloud.google.com")
        print("  2. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID")
        print("  3. Application type: Desktop app")
        print("  4. Download JSON → save to:", CREDS_PATH)
        sys.exit(1)

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            print("Token refreshed.")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            # run_console() works over SSH — no local browser needed
            creds = flow.run_console()

        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
        print(f"\nToken saved to: {TOKEN_PATH}")

    print("\nAuthenticated. Testing Drive access...")
    from googleapiclient.discovery import build
    service = build("drive", "v3", credentials=creds)
    about = service.about().get(fields="user").execute()
    print(f"Signed in as: {about['user']['emailAddress']}")
    print("\nDrive auth is ready. NNLOS will use this token automatically.")


if __name__ == "__main__":
    main()
