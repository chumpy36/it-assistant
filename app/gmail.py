"""Gmail integration — read recent unread emails via Gmail API."""

import asyncio
import os
import re
import urllib.request
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "gcal_credentials.json")
TOKEN_PERSONAL = os.path.join(BASE_DIR, "gmail_token_personal.json")
TOKEN_BIZ = os.path.join(BASE_DIR, "gmail_token_biz.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

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


def _fetch_sync(token_file: str, hours: int, max_results: int, account_email: str = "") -> list[dict]:
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
            "url": f"https://mail.google.com/mail/?authuser={account_email}#all/{msg['id']}",
        })

    return emails


def _trash_by_query_sync(token_file: str, query: str) -> dict:
    """Trash all messages matching a Gmail search query. Returns count trashed."""
    service = _get_service(token_file)

    trashed = 0
    page_token = None
    while True:
        kwargs = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.users().messages().list(**kwargs).execute()
        messages = result.get("messages", [])
        if not messages:
            break
        # Batch trash
        ids = [m["id"] for m in messages]
        service.users().messages().batchModify(
            userId="me",
            body={"ids": ids, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
        ).execute()
        trashed += len(ids)
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return {"trashed": trashed, "query": query}


def _unsubscribe_sync(token_file: str, message_id: str) -> dict:
    """Unsubscribe using the List-Unsubscribe header from a specific message."""
    service = _get_service(token_file)

    detail = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post", "From", "Subject"],
    ).execute()

    headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
    unsub_header = headers.get("List-Unsubscribe", "")
    unsub_post = headers.get("List-Unsubscribe-Post", "")

    if not unsub_header:
        return {"error": "No List-Unsubscribe header found in this email."}

    # Extract URLs and mailto links
    urls = re.findall(r'<(https?://[^>]+)>', unsub_header)
    mailto = re.findall(r'<(mailto:[^>]+)>', unsub_header)

    # Prefer one-click HTTP unsubscribe (RFC 8058)
    if urls and unsub_post and "List-Unsubscribe=One-Click" in unsub_post:
        url = urls[0]
        try:
            req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"unsubscribed": True, "method": "one-click POST", "url": url, "status": resp.status}
        except Exception as e:
            pass  # Fall through to URL return

    # Return the unsubscribe URL for the user to visit if we can't automate it
    if urls:
        return {"unsubscribed": False, "method": "url", "url": urls[0],
                "message": "Visit this URL to unsubscribe (couldn't automate it)."}
    if mailto:
        return {"unsubscribed": False, "method": "mailto", "url": mailto[0],
                "message": "Send an email to this address to unsubscribe."}

    return {"error": "Could not parse unsubscribe method from header.", "header": unsub_header}


async def fetch_emails(account: str = "both", hours: int = 24, max_results: int = 20) -> dict:
    """Fetch recent unread emails from one or both Gmail accounts."""

    async def fetch_one(token_file: str, account_email: str = "") -> dict:
        try:
            emails = await asyncio.to_thread(_fetch_sync, token_file, hours, max_results, account_email)
            return {"emails": emails, "count": len(emails)}
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)}"}

    keys = [k for k in ("personal", "business") if account in (k, "both")]
    results = await asyncio.gather(*[fetch_one(ACCOUNTS[k][1], ACCOUNTS[k][0]) for k in keys])
    return dict(zip(keys, results))


async def trash_emails(account: str, query: str) -> dict:
    """Trash all emails matching a query in the specified account."""
    if account not in ACCOUNTS:
        return {"error": f"Unknown account '{account}'. Use 'personal' or 'business'."}
    token_file = ACCOUNTS[account][1]
    try:
        return await asyncio.to_thread(_trash_by_query_sync, token_file, query)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}


async def unsubscribe(account: str, message_id: str) -> dict:
    """Unsubscribe using the List-Unsubscribe header from a specific message."""
    if account not in ACCOUNTS:
        return {"error": f"Unknown account '{account}'. Use 'personal' or 'business'."}
    token_file = ACCOUNTS[account][1]
    try:
        return await asyncio.to_thread(_unsubscribe_sync, token_file, message_id)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}
