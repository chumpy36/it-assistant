"""Gmail integration — read recent unread emails via Gmail API."""

import asyncio
import os
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "gcal_credentials.json")
TOKEN_PERSONAL = os.path.join(BASE_DIR, "gmail_token_personal.json")
TOKEN_BIZ = os.path.join(BASE_DIR, "gmail_token_biz.json")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

ACCOUNTS = {
    "personal": ("jlh1825@gmail.com", TOKEN_PERSONAL),
    "business": ("jason.holland@hollandit.biz", TOKEN_BIZ),
}


def _get_service(token_file: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not os.path.exists(token_file):
        raise FileNotFoundError(
            f"Gmail token not found at {token_file}. Run setup_gmail_oauth.py first."
        )

    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _fetch_sync(token_file: str, hours: int, max_results: int) -> list[dict]:
    service = _get_service(token_file)

    after_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    query = f"is:unread after:{after_ts}"

    result = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    emails = []
    for msg in result.get("messages", []):
        detail = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        emails.append({
            "id": msg["id"],
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "snippet": detail.get("snippet", ""),
        })

    return emails


async def fetch_emails(account: str = "both", hours: int = 24, max_results: int = 20) -> dict:
    """Fetch recent unread emails from one or both Gmail accounts."""

    async def fetch_one(token_file: str) -> dict:
        try:
            emails = await asyncio.to_thread(_fetch_sync, token_file, hours, max_results)
            return {"emails": emails, "count": len(emails)}
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)}"}

    keys = [k for k in ("personal", "business") if account in (k, "both")]
    results = await asyncio.gather(*[fetch_one(ACCOUNTS[k][1]) for k in keys])
    return dict(zip(keys, results))
