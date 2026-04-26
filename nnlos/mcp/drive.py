"""Google Drive client for NNLOS — fetches and archives MRP Easy CSV exports."""

import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _build_service():
    """
    Build Drive service using OAuth2 token (run mcp/drive_auth.py once to set up).
    Falls back to service account JSON if token not found and SA path is configured.
    """
    from googleapiclient.discovery import build
    from pathlib import Path

    token_path = Path(os.environ.get(
        "GOOGLE_DRIVE_TOKEN_JSON",
        Path(__file__).parent.parent / "config" / "drive_token.json",
    ))

    # OAuth2 path (preferred — run mcp/drive_auth.py once)
    if token_path.exists():
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(
            str(token_path), ["https://www.googleapis.com/auth/drive"]
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    # Service account fallback (if configured)
    sa_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_path and Path(sa_path).exists():
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    raise RuntimeError(
        "No Google Drive credentials found. "
        "Run: python3 mcp/drive_auth.py"
    )


def list_files(folder_id: str) -> list[dict]:
    """Return all non-trashed files in a Drive folder, newest first."""
    service = _build_service()
    results = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, createdTime)",
                pageToken=page_token,
                orderBy="createdTime desc",
            )
            .execute()
        )
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def get_latest_file(
    folder_id: str,
    prefix: str,
    contains: Optional[str] = None,
    excludes: Optional[str] = None,
) -> Optional[dict]:
    """Find the most recently created file matching prefix/contains/excludes filters."""
    for f in list_files(folder_id):
        name = f["name"]
        if not name.startswith(prefix):
            continue
        if contains and contains not in name:
            continue
        if excludes and excludes in name:
            continue
        return f  # list is newest-first, so first match wins
    return None


def download_text(file_id: str, encoding: str = "utf-8") -> str:
    """Download a Drive file and return its text content."""
    from googleapiclient.http import MediaIoBaseDownload

    service = _build_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode(encoding, errors="replace")


def move_to_folder(file_id: str, new_folder_id: str) -> None:
    """Move a file to a different folder (used to archive after ingest)."""
    service = _build_service()
    f = service.files().get(fileId=file_id, fields="parents").execute()
    current_parents = ",".join(f.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=new_folder_id,
        removeParents=current_parents,
        fields="id, parents",
    ).execute()
    logger.info("Archived Drive file %s → folder %s", file_id, new_folder_id)
