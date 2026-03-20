"""Async Syncro MSP API client."""

from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
import httpx

BASE_URL = "https://hollandit.syncromsp.com/api/v1"
SYNCRO_BASE = "https://hollandit.syncromsp.com"


def _headers() -> dict:
    token = os.environ["SYNCRO_API_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def customer_display_name(c: dict) -> str:
    biz = (c.get("business_name") or "").strip()
    first = (c.get("firstname") or "").strip()
    last = (c.get("lastname") or "").strip()
    full = f"{first} {last}".strip()
    return biz if biz and biz.lower() not in ("none", "") else full


def ticket_url(ticket_id: int) -> str:
    return f"{SYNCRO_BASE}/tickets/{ticket_id}"


async def _find_user_id(client: httpx.AsyncClient, name: str) -> int:
    resp = await client.get(f"{BASE_URL}/users", headers=_headers())
    resp.raise_for_status()
    # API returns [[id, name], ...] format
    users = resp.json().get("users", [])
    query = name.lower()
    matches = [u for u in users if query in str(u[1]).lower()]
    if not matches:
        raise ValueError(f"No user found matching '{name}'")
    if len(matches) > 1:
        options = [{"id": u[0], "name": u[1]} for u in matches]
        raise ValueError(f"Multiple users found — be more specific: {options}")
    return matches[0][0]


import re as _re

def _tokenize(name: str) -> list[str]:
    """Split on spaces/hyphens AND camelCase, return lowercase words >= 3 chars."""
    # Insert space before uppercase letters that follow lowercase (camelCase split)
    spaced = _re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return [w for w in spaced.lower().replace("-", " ").split() if len(w) >= 3]


async def _find_customer_id(client: httpx.AsyncClient, name: str) -> int:
    query_words = _tokenize(name)

    async def _search_and_filter(params: dict) -> list:
        resp = await client.get(f"{BASE_URL}/customers", params=params, headers=_headers())
        resp.raise_for_status()
        customers = resp.json().get("customers", [])
        return [
            c for c in customers
            if all(w in customer_display_name(c).lower() for w in query_words)
            and customer_display_name(c).lower() not in ("", "none none")
        ]

    # Try API search first with client-side word filter applied
    candidates = await _search_and_filter({"name": name})

    # Fall back to full paginated fetch if no match
    if not candidates:
        all_c: list = []
        page = 1
        while True:
            resp = await client.get(
                f"{BASE_URL}/customers",
                params={"page": page, "per_page": 100},
                headers=_headers(),
            )
            resp.raise_for_status()
            batch = resp.json().get("customers", [])
            all_c.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        candidates = [
            c for c in all_c
            if all(w in customer_display_name(c).lower() for w in query_words)
            and customer_display_name(c).lower() not in ("", "none none")
        ]

    if not candidates:
        raise ValueError(f"No customer found matching '{name}'")
    if len(candidates) > 1:
        # Prefer exact substring match (e.g. "Pro Georgia" inside "Pro Georgia (ProGa / PGA)")
        query_lower = " ".join(_tokenize(name))
        exact = [c for c in candidates if query_lower in customer_display_name(c).lower()]
        if len(exact) == 1:
            return exact[0]["id"]
        matches = [{"id": c["id"], "name": customer_display_name(c)} for c in candidates]
        raise ValueError(f"Multiple customers found — specify which one: {matches}")
    return candidates[0]["id"]


async def _resolve_ticket_id(client: httpx.AsyncClient, ticket_ref: int) -> tuple[int, int]:
    """Given a ticket number OR internal id, return (internal_id, customer_id).
    Tries direct ID lookup first; falls back to number search."""
    resp = await client.get(f"{BASE_URL}/tickets/{ticket_ref}", headers=_headers())
    if resp.status_code == 200:
        t = resp.json().get("ticket", {})
        return t["id"], t["customer_id"]

    # Try searching by ticket number across all statuses (closed tickets won't appear in default list)
    for status in [None, "Resolved", "Closed"]:
        params: dict = {"number": ticket_ref}
        if status:
            params["status"] = status
        resp2 = await client.get(f"{BASE_URL}/tickets", params=params, headers=_headers())
        resp2.raise_for_status()
        tickets = resp2.json().get("tickets", [])
        if tickets:
            return tickets[0]["id"], tickets[0]["customer_id"]
    raise ValueError(f"No ticket found with number or id '{ticket_ref}'")


async def list_tickets(
    status: str | None = None,
    customer_name: str | None = None,
    keyword: str | None = None,
    assigned_to: str | None = None,
) -> dict:
    async with httpx.AsyncClient() as client:
        params = {}
        if status:
            params["status"] = status
        if customer_name:
            customer_id = await _find_customer_id(client, customer_name)
            params["customer_id"] = customer_id
        if keyword:
            params["q"] = keyword
        if assigned_to:
            user_id = await _find_user_id(client, assigned_to)
            params["user_id"] = user_id

        resp = await client.get(f"{BASE_URL}/tickets", params=params, headers=_headers())
        resp.raise_for_status()
        tickets = resp.json().get("tickets", [])

        # Client-side keyword filter as fallback if API doesn't support ?q=
        if keyword and tickets:
            kw = keyword.lower()
            filtered = [t for t in tickets if kw in (t.get("subject") or "").lower()
                        or kw in (t.get("customer_business_name") or "").lower()]
            if filtered:
                tickets = filtered

        # Always exclude resolved/closed unless explicitly requested
        if not status or status.lower() not in ("resolved", "closed"):
            tickets = [t for t in tickets if t.get("status") not in ("Resolved", "Closed")]

        MAX = 15
        summary = [
            {
                "number": t.get("number"),
                "url": ticket_url(t.get("id")),
                "subject": t.get("subject"),
                "status": t.get("status"),
                "customer": t.get("customer_business_name") or t.get("customer_id"),
                "updated_at": t.get("updated_at"),
                "customer_reply": t.get("customer_reply", False),
            }
            for t in tickets[:MAX]
        ]
        return {"count": len(tickets), "showing": len(summary), "tickets": summary}


async def get_ticket(ticket_ref: int) -> dict:
    async with httpx.AsyncClient() as client:
        internal_id, customer_id = await _resolve_ticket_id(client, ticket_ref)
        resp = await client.get(f"{BASE_URL}/tickets/{internal_id}", headers=_headers())
        resp.raise_for_status()
        ticket = resp.json().get("ticket", resp.json())

        # Fetch recent tickets from same customer for context
        recent = []
        if customer_id:
            hist_resp = await client.get(
                f"{BASE_URL}/tickets",
                params={"customer_id": customer_id},
                headers=_headers(),
            )
            if hist_resp.status_code == 200:
                all_tickets = hist_resp.json().get("tickets", [])
                recent = [
                    {
                        "number": t.get("number"),
                        "url": ticket_url(t.get("id")),
                        "subject": t.get("subject"),
                        "status": t.get("status"),
                        "created_at": t.get("created_at"),
                    }
                    for t in all_tickets
                    if t.get("id") != internal_id
                ][:4]

        return {
            "id": ticket.get("id"),
            "number": ticket.get("number"),
            "url": ticket_url(ticket.get("id")),
            "subject": ticket.get("subject"),
            "status": ticket.get("status"),
            "customer": ticket.get("customer_business_name"),
            "description": ticket.get("problem_type") or ticket.get("description"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "comments": [
                {
                    "id": c.get("id"),
                    "body": c.get("body"),
                    "hidden": c.get("hidden"),
                    "created_at": c.get("created_at"),
                }
                for c in ticket.get("comments", [])
            ],
            "recent_customer_tickets": recent,
        }


async def _find_contact_id(client: httpx.AsyncClient, customer_id: int, contact_name: str) -> int | None:
    resp = await client.get(f"{BASE_URL}/customers/{customer_id}", headers=_headers())
    resp.raise_for_status()
    contacts = resp.json().get("customer", {}).get("contacts", [])
    words = _tokenize(contact_name)

    # Try full substring match first, then any-word match as fallback
    for matcher in [
        lambda name: contact_name.lower() in name,
        lambda name: any(w in name for w in words),
    ]:
        matches = [c for c in contacts if matcher(c.get("name", "").lower())]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            names = [c["name"] for c in matches]
            raise ValueError(f"Multiple contacts matched '{contact_name}': {names}")
    return None


async def create_ticket(
    subject: str,
    customer_name: str | None = None,
    customer_id: int | None = None,
    contact_name: str | None = None,
    description: str | None = None,
    issue_type: str = "Remote Break/Fix",
    assigned_to: str | None = None,
) -> dict:
    async with httpx.AsyncClient() as client:
        if customer_id is None:
            if not customer_name:
                raise ValueError("Either customer_name or customer_id is required")
            customer_id = await _find_customer_id(client, customer_name)
        payload = {
            "customer_id": customer_id,
            "subject": subject,
            "problem_type": issue_type,
        }
        if description:
            payload["description"] = description
        if contact_name:
            contact_id = await _find_contact_id(client, customer_id, contact_name)
            if contact_id:
                payload["contact_id"] = contact_id
        if assigned_to:
            user_id = await _find_user_id(client, assigned_to)
            payload["user_id"] = user_id

        resp = await client.post(f"{BASE_URL}/tickets", json=payload, headers=_headers())
        resp.raise_for_status()
        ticket = resp.json().get("ticket", resp.json())
        internal_id = ticket.get("id")

        # Syncro doesn't support a description field on create — post it as the first comment
        if description and internal_id:
            comment_payload = {
                "subject": "Issue Description",
                "body": description,
                "hidden": False,
                "do_not_email": True,
            }
            await client.post(
                f"{BASE_URL}/tickets/{internal_id}/comment",
                json=comment_payload,
                headers=_headers(),
            )

        return {
            "success": True,
            "message": "Ticket created",
            "id": internal_id,
            "number": ticket.get("number"),
            "url": ticket_url(internal_id),
            "subject": ticket.get("subject"),
            "customer": ticket.get("customer_business_name"),
            "contact_id": ticket.get("contact_id"),
        }


async def update_ticket(
    ticket_ref: int,
    status: str | None = None,
    subject: str | None = None,
    assigned_to: str | None = None,
) -> dict:
    payload = {}
    if status:
        payload["status"] = status
    if subject:
        payload["subject"] = subject
    if not payload and not assigned_to:
        raise ValueError("No update fields provided. Specify status, subject, or assigned_to.")

    async with httpx.AsyncClient() as client:
        if assigned_to:
            user_id = await _find_user_id(client, assigned_to)
            payload["user_id"] = user_id
        internal_id, _ = await _resolve_ticket_id(client, ticket_ref)
        resp = await client.put(f"{BASE_URL}/tickets/{internal_id}", json=payload, headers=_headers())
        resp.raise_for_status()
        ticket = resp.json().get("ticket", resp.json())
        return {
            "success": True,
            "message": "Ticket updated",
            "id": ticket.get("id"),
            "number": ticket.get("number"),
            "url": ticket_url(ticket.get("id")),
            "status": ticket.get("status"),
            "subject": ticket.get("subject"),
        }


async def add_comment(ticket_ref: int, body: str, hidden: bool = False) -> dict:
    payload = {
        "subject": "Internal Note" if hidden else "Comment",
        "body": body,
        "hidden": hidden,
        "do_not_email": hidden,
    }
    async with httpx.AsyncClient() as client:
        internal_id, _ = await _resolve_ticket_id(client, ticket_ref)
        resp = await client.post(
            f"{BASE_URL}/tickets/{internal_id}/comment",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        comment = resp.json().get("comment", resp.json())

        # If this is a customer-visible reply, clear the customer_reply flag
        if not hidden:
            await client.put(
                f"{BASE_URL}/tickets/{internal_id}",
                json={"customer_reply": False},
                headers=_headers(),
            )

        return {
            "success": True,
            "message": "Comment added",
            "comment_id": comment.get("id"),
            "ticket_id": internal_id,
            "hidden": hidden,
        }


async def log_time(
    ticket_ref: int,
    hours: float,
    notes: str = "",
    billable: bool = True,
) -> dict:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    payload = {
        "start_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_minutes": int(hours * 60),
        "notes": notes,
    }
    async with httpx.AsyncClient() as client:
        internal_id, _ = await _resolve_ticket_id(client, ticket_ref)
        resp = await client.post(
            f"{BASE_URL}/tickets/{internal_id}/timer_entry",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        entry = resp.json()
        timer_id = entry.get("id")

        # Charge the timer entry so it appears as a billable line item
        charge_resp = await client.post(
            f"{BASE_URL}/tickets/{internal_id}/charge_timer_entry",
            json={"timer_entry_id": timer_id},
            headers=_headers(),
        )
        charged = charge_resp.status_code in (200, 201)
        charge_detail = charge_resp.text[:200] if not charged else ""

        return {
            "success": True,
            "message": f"Logged and charged {hours}h on ticket {ticket_ref}",
            "timer_id": timer_id,
            "charged": charged,
            "charge_status": charge_resp.status_code,
            "charge_error": charge_detail,
            "hours": hours,
            "notes": notes,
            "url": ticket_url(internal_id),
        }


async def delete_ticket(ticket_ref: int) -> dict:
    async with httpx.AsyncClient() as client:
        internal_id, _ = await _resolve_ticket_id(client, ticket_ref)
        resp = await client.delete(f"{BASE_URL}/tickets/{internal_id}", headers=_headers())
        resp.raise_for_status()
        return {
            "success": True,
            "message": f"Ticket {ticket_ref} deleted",
            "ticket_id": internal_id,
        }


async def create_invoice(ticket_ref: int) -> dict:
    async with httpx.AsyncClient() as client:
        internal_id, customer_id = await _resolve_ticket_id(client, ticket_ref)
        payload = {
            "customer_id": customer_id,
            "ticket_ids": [internal_id],
        }
        resp = await client.post(f"{BASE_URL}/invoices", json=payload, headers=_headers())
        resp.raise_for_status()
        invoice = resp.json().get("invoice", resp.json())
        invoice_id = invoice.get("id")
        return {
            "success": True,
            "message": "Invoice created",
            "invoice_id": invoice_id,
            "invoice_number": invoice.get("number"),
            "url": f"{SYNCRO_BASE}/invoices/{invoice_id}" if invoice_id else None,
            "total": invoice.get("total"),
        }
