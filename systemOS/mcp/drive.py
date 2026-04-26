"""
Google Drive read/write via the Drive API v3.

Primarily used by nnlos to read MRP Easy CSV exports that Google Sheets drops
into Drive, and by researchOS to save reports to Drive.

Import from any project:
    from systemOS.mcp.drive import read_file, read_csv, list_files, create_file, find_file

Auth setup (one-time per project):
    Option A — Service account (recommended for server):
        1. Create service account in Google Cloud Console
        2. Download JSON key, save to <project>/config/google_service_account.json
        3. Set GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/key.json in .env
        4. Share the Drive folder with the service account email

    Option B — OAuth2 user credentials:
        1. Download credentials.json from Google Cloud Console
        2. Set GOOGLE_CREDENTIALS_FILE=/path/to/credentials.json in .env
        3. First run triggers browser OAuth flow, token saved to token.json

Usage:
    # Find the most recent MRP Easy export in a Drive folder
    files = await list_files(folder_id="1abc...", query="name contains 'VENDORS'")
    file_id = files[0]["id"]

    # Read CSV content as list of dicts
    rows = await read_csv(file_id)
    for row in rows:
        print(row["Name"], row["Lead Time"])

    # Read any file as text
    text = await read_file(file_id)

    # Save a report to Drive
    new_id = await create_file(
        name="vendor_report_2026-04.md",
        content=report_text,
        folder_id="1abc...",
        mime_type="text/markdown",
    )

    # Find a file by name
    f = await find_file("VENDORS_export_2026.csv", folder_id="1abc...")
    if f:
        rows = await read_csv(f["id"])
"""

import csv
import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "config/google_token.json")

_service = None


def _get_service():
    global _service
    if _service is not None:
        return _service

    from googleapiclient.discovery import build

    if SERVICE_ACCOUNT_FILE and Path(SERVICE_ACCOUNT_FILE).exists():
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
    elif CREDENTIALS_FILE and Path(CREDENTIALS_FILE).exists():
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = Path(TOKEN_FILE)
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, _SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())
    else:
        raise RuntimeError(
            "No Google auth configured. Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_CREDENTIALS_FILE in .env"
        )

    _service = build("drive", "v3", credentials=creds)
    return _service


async def list_files(
    folder_id: str | None = None,
    query: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    List files in Drive, optionally filtered by folder and/or query string.

    query syntax: https://developers.google.com/drive/api/guides/search-files
    e.g. query="name contains 'VENDORS' and mimeType != 'application/vnd.google-apps.folder'"

    Returns list of {id, name, mimeType, modifiedTime, size} dicts, newest first.
    """
    try:
        service = _get_service()
        parts = ["trashed = false"]
        if folder_id:
            parts.append(f"'{folder_id}' in parents")
        if query:
            parts.append(query)
        q = " and ".join(parts)

        result = service.files().list(
            q=q,
            pageSize=limit,
            orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,size)",
        ).execute()
        return result.get("files", [])
    except Exception as e:
        logger.error("[DRIVE] list_files failed: %s", e)
        return []


async def find_file(name: str, folder_id: str | None = None) -> dict | None:
    """Find a file by exact name. Returns file dict or None."""
    files = await list_files(folder_id=folder_id, query=f"name = '{name}'", limit=1)
    return files[0] if files else None


async def read_file(file_id: str) -> str:
    """
    Download a file and return its content as a string.
    Google Docs/Sheets are exported as plain text / CSV automatically.
    """
    try:
        service = _get_service()
        meta = service.files().get(fileId=file_id, fields="mimeType,name").execute()
        mime = meta.get("mimeType", "")

        if mime == "application/vnd.google-apps.document":
            resp = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        elif mime == "application/vnd.google-apps.spreadsheet":
            resp = service.files().export(fileId=file_id, mimeType="text/csv").execute()
        else:
            resp = service.files().get_media(fileId=file_id).execute()

        if isinstance(resp, bytes):
            return resp.decode("utf-8", errors="replace")
        return resp
    except Exception as e:
        logger.error("[DRIVE] read_file failed for %s: %s", file_id, e)
        return ""


async def read_csv(file_id: str) -> list[dict]:
    """
    Download a file and parse as CSV. Returns list of row dicts keyed by header.
    Works with both native CSV files and Google Sheets (auto-exported as CSV).
    """
    text = await read_file(file_id)
    if not text:
        return []
    try:
        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader]
    except Exception as e:
        logger.error("[DRIVE] read_csv parse failed: %s", e)
        return []


async def create_file(
    name: str,
    content: str,
    folder_id: str | None = None,
    mime_type: str = "text/plain",
) -> str | None:
    """
    Create or overwrite a file in Drive. Returns the file ID on success.
    If a file with the same name already exists in the folder, it is replaced.
    """
    try:
        service = _get_service()
        metadata: dict = {"name": name}
        if folder_id:
            metadata["parents"] = [folder_id]

        media_body = _media_body(content, mime_type)

        # Check if file already exists (to update instead of duplicate)
        existing = await find_file(name, folder_id=folder_id)
        if existing:
            service.files().update(
                fileId=existing["id"],
                media_body=media_body,
            ).execute()
            logger.info("[DRIVE] updated file %s (%s)", name, existing["id"])
            return existing["id"]

        result = service.files().create(
            body=metadata,
            media_body=media_body,
            fields="id",
        ).execute()
        file_id = result.get("id")
        logger.info("[DRIVE] created file %s (%s)", name, file_id)
        return file_id
    except Exception as e:
        logger.error("[DRIVE] create_file failed for %s: %s", name, e)
        return None


def _media_body(content: str, mime_type: str):
    from googleapiclient.http import MediaIoBaseUpload
    return MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype=mime_type,
        resumable=False,
    )
