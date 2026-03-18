#!/usr/bin/env python3
"""monthly_report.py — Holland IT Monthly Invoicing Reminder

Pulls Syncro reports, generates PDF, sends email, creates Todoist task
and Google Calendar event.

Usage:
  python monthly_report.py              # run the report
  python monthly_report.py --setup-gcal # one-time Google Calendar auth
"""

from __future__ import annotations
import os, sys, json, smtplib, tempfile
from datetime import datetime, date, timedelta
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────────────────────
SYNCRO_BASE   = "https://hollandit.syncromsp.com/api/v1"
TODOIST_BASE  = "https://api.todoist.com/api/v1"
GMAIL_USER    = "jason.holland@hollandit.biz"
TO_EMAIL      = "jason.holland@hollandit.biz"
GCAL_ID       = "jason.holland@hollandit.biz"
SCRIPT_DIR    = Path(__file__).parent
TOKEN_FILE    = SCRIPT_DIR / "gcal_token.json"
CREDS_FILE    = SCRIPT_DIR / "gcal_credentials.json"
SCOPES        = ["https://www.googleapis.com/auth/calendar.events"]


# ── Syncro API ─────────────────────────────────────────────────────────────────
def _syncro_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['SYNCRO_API_TOKEN']}",
        "Accept": "application/json",
    }


def _get_invoices_paged(params: dict) -> list[dict]:
    results = []
    page = 1
    with httpx.Client(timeout=30) as client:
        while True:
            r = client.get(
                f"{SYNCRO_BASE}/invoices",
                params={**params, "page": page},
                headers=_syncro_headers(),
            )
            r.raise_for_status()
            data = r.json()
            batch = data.get("invoices", [])
            results.extend(batch)
            per_page = data.get("meta", {}).get("per_page", 100)
            if len(batch) < per_page:
                break
            page += 1
    return results


def get_aging_invoices() -> list[dict]:
    """All unpaid invoices, sorted by days overdue descending."""
    today = date.today()
    raw = _get_invoices_paged({"status": "Unpaid"})
    result = []
    for inv in raw:
        due_str = inv.get("due_date") or inv.get("date") or ""
        try:
            due = date.fromisoformat(due_str[:10]) if due_str else None
        except ValueError:
            due = None
        days_overdue = (today - due).days if due else None
        result.append({
            "number":      inv.get("number"),
            "customer":    (inv.get("customer_business_name") or str(inv.get("customer_id", ""))).strip(),
            "balance":     float(inv.get("balance_due") or inv.get("total") or 0),
            "date":        (inv.get("date") or "")[:10],
            "due_date":    due_str[:10] if due_str else "",
            "days_overdue": days_overdue,
        })
    result.sort(key=lambda x: x.get("days_overdue") or 0, reverse=True)
    return result


def get_monthly_invoices() -> list[dict]:
    """All invoices created this calendar month."""
    today = date.today()
    start = today.replace(day=1).isoformat()
    raw = _get_invoices_paged({"start_date": start})
    result = []
    for inv in raw:
        inv_date = (inv.get("date") or "")[:10]
        if inv_date >= start:
            result.append({
                "number":   inv.get("number"),
                "customer": (inv.get("customer_business_name") or str(inv.get("customer_id", ""))).strip(),
                "total":    float(inv.get("total") or 0),
                "status":   inv.get("status") or "",
                "date":     inv_date,
            })
    result.sort(key=lambda x: x["date"])
    return result


def get_tickets_without_charges() -> list[dict]:
    """Open tickets with no line items — work that may not have been billed."""
    result = []
    with httpx.Client(timeout=60) as client:
        r = client.get(
            f"{SYNCRO_BASE}/tickets",
            params={"status[]": ["New", "In Progress", "Waiting on Customer", "Waiting on Parts"]},
            headers=_syncro_headers(),
        )
        r.raise_for_status()
        tickets = r.json().get("tickets", [])

        for t in tickets[:60]:
            dr = client.get(f"{SYNCRO_BASE}/tickets/{t['id']}", headers=_syncro_headers())
            if dr.status_code != 200:
                continue
            ticket = dr.json().get("ticket", {})
            line_items = ticket.get("line_items") or []
            charges    = ticket.get("charges") or []
            if not line_items and not charges:
                result.append({
                    "number":     ticket.get("number"),
                    "customer":   (ticket.get("customer_business_name") or "").strip(),
                    "subject":    (ticket.get("subject") or "")[:60],
                    "status":     ticket.get("status") or "",
                    "created_at": (ticket.get("created_at") or "")[:10],
                })
    return result


# ── PDF ────────────────────────────────────────────────────────────────────────
def generate_pdf(aging: list, monthly: list, uncharged: list, output_path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    today = date.today()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        topMargin=0.75*inch, bottomMargin=0.75*inch,
        leftMargin=inch, rightMargin=inch,
    )
    story = []

    def header(text):
        return Paragraph(text, styles["Heading2"])

    def body(text):
        return Paragraph(text, styles["Normal"])

    def gap(n=0.25):
        return Spacer(1, n*inch)

    def make_table(rows, col_widths, header_color):
        tbl = Table(rows, colWidths=col_widths)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), header_color),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ]))
        return tbl

    # Title
    story.append(Paragraph("Holland IT — Monthly Invoicing Report", styles["Title"]))
    story.append(body(today.strftime("%B %Y") + "  |  Generated " + today.strftime("%B %d, %Y")))
    story.append(gap(0.3))

    # Checklist
    story.append(header("Monthly Invoicing Checklist"))
    story.append(body("1. Review aging invoices below — follow up on anything 30+ days overdue"))
    story.append(body("2. Check tickets without charges — make sure all work was billed"))
    story.append(body("3. Go to QuickBooks — create and send monthly invoices"))
    story.append(gap(0.3))

    # Aging invoices
    total_overdue = sum(i["balance"] for i in aging)
    story.append(header(f"Aging Invoices — {len(aging)} unpaid  (${total_overdue:,.2f} outstanding)"))
    if aging:
        rows = [["Customer", "Invoice #", "Balance Due", "Due Date", "Days Overdue"]]
        for i in aging:
            rows.append([
                str(i["customer"])[:32],
                str(i["number"] or ""),
                f"${i['balance']:,.2f}",
                str(i["due_date"]),
                str(i["days_overdue"]) if i["days_overdue"] is not None else "—",
            ])
        story.append(make_table(rows, [2.3*inch, 0.9*inch, 1.0*inch, 1.0*inch, 1.0*inch],
                                colors.HexColor("#1D4ED8")))
    else:
        story.append(body("No unpaid invoices."))
    story.append(gap())

    # Tickets without charges
    story.append(header(f"Tickets Without Charges — {len(uncharged)} tickets"))
    if uncharged:
        rows = [["Customer", "Ticket #", "Subject", "Status", "Opened"]]
        for t in uncharged:
            rows.append([
                str(t["customer"])[:25],
                str(t["number"] or ""),
                str(t["subject"])[:38],
                str(t["status"]),
                str(t["created_at"]),
            ])
        story.append(make_table(rows, [1.8*inch, 0.75*inch, 2.3*inch, 1.0*inch, 0.85*inch],
                                colors.HexColor("#B91C1C")))
    else:
        story.append(body("No open tickets without charges."))
    story.append(gap())

    # Invoice export
    total_month = sum(i["total"] for i in monthly)
    story.append(header(f"Invoices This Month — {len(monthly)} invoices  (${total_month:,.2f})"))
    if monthly:
        rows = [["Customer", "Invoice #", "Date", "Total", "Status"]]
        for i in monthly:
            rows.append([
                str(i["customer"])[:32],
                str(i["number"] or ""),
                str(i["date"]),
                f"${i['total']:,.2f}",
                str(i["status"]),
            ])
        rows.append(["", "", "TOTAL", f"${total_month:,.2f}", ""])
        tbl = Table(rows, colWidths=[2.3*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.8*inch])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0),  (-1, 0),  colors.HexColor("#047857")),
            ("TEXTCOLOR",     (0, 0),  (-1, 0),  colors.white),
            ("FONTNAME",      (0, 0),  (-1, 0),  "Helvetica-Bold"),
            ("FONTNAME",      (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND",    (0, -1), (-1, -1), colors.HexColor("#D1FAE5")),
            ("FONTSIZE",      (0, 0),  (-1, -1), 9),
            ("ROWBACKGROUNDS",(0, 1),  (-1, -2), [colors.white, colors.HexColor("#F0FDF4")]),
            ("GRID",          (0, 0),  (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",    (0, 0),  (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0),  (-1, -1), 4),
            ("LEFTPADDING",   (0, 0),  (-1, -1), 5),
        ]))
        story.append(tbl)
    else:
        story.append(body("No invoices created this month yet."))

    doc.build(story)


# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(pdf_path: Path, aging: list, uncharged: list, monthly: list) -> None:
    today = date.today()
    month_name = today.strftime("%B %Y")
    total_overdue = sum(i["balance"] for i in aging)
    total_month   = sum(i["total"]   for i in monthly)

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL
    msg["Subject"] = f"[ACTION] Monthly Invoicing — {month_name}"

    body = f"""\
Time to do monthly invoicing.

SUMMARY
  Unpaid invoices:          {len(aging):>3}  (${total_overdue:,.2f} outstanding)
  Tickets without charges:  {len(uncharged):>3}
  Invoices created this month: {len(monthly):>3}  (${total_month:,.2f})

CHECKLIST
  [ ] Review aging invoices — follow up on anything 30+ days overdue
  [ ] Check tickets without charges — bill anything missed
  [ ] QuickBooks — create and send monthly invoices

Full Syncro report attached.

— Automated reminder, Holland IT
"""

    msg.attach(MIMEText(body, "plain"))

    with open(pdf_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{pdf_path.name}"')
    msg.attach(part)

    app_pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, app_pw)
        server.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())


# ── Todoist ────────────────────────────────────────────────────────────────────
def create_todoist_task(aging: list, uncharged: list) -> None:
    today = date.today()
    month_name = today.strftime("%B %Y")
    total_overdue = sum(i["balance"] for i in aging)

    description = (
        f"**Aging invoices:** {len(aging)} unpaid (${total_overdue:,.2f})\n"
        f"**Tickets without charges:** {len(uncharged)}\n\n"
        "1. Review aging invoices in Syncro\n"
        "2. Bill any uncharged tickets\n"
        "3. Create & send invoices in QuickBooks"
    )

    with httpx.Client(timeout=15) as client:
        headers = {
            "Authorization": f"Bearer {os.environ['TODOIST_API_TOKEN']}",
            "Content-Type": "application/json",
        }

        # Find Work project
        r = client.get(f"{TODOIST_BASE}/projects", headers=headers)
        r.raise_for_status()
        data = r.json()
        projects = data.get("results", data) if isinstance(data, dict) else data
        work = next((p for p in projects if p["name"].lower() == "work"), None)

        payload = {
            "content":     f"Monthly Invoicing — {month_name}",
            "description": description,
            "priority":    4,  # P1 urgent
            "due_date":    today.isoformat(),
        }
        if work:
            payload["project_id"] = work["id"]

        r2 = client.post(f"{TODOIST_BASE}/tasks", json=payload, headers=headers)
        r2.raise_for_status()


# ── Google Calendar ─────────────────────────────────────────────────────────────
def create_calendar_event() -> None:
    if not TOKEN_FILE.exists():
        print("  [WARN] Google Calendar token not found. Run --setup-gcal first.")
        return

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("  [WARN] Google API libs not installed. Skipping calendar event.")
        return

    today = date.today()
    month_name = today.strftime("%B %Y")

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    event_date = today.isoformat()
    event = {
        "summary": f"Monthly Invoicing — {month_name}",
        "description": (
            "Time to do monthly invoicing.\n\n"
            "1. Review Syncro aging invoices\n"
            "2. Bill any uncharged tickets\n"
            "3. Create & send invoices in QuickBooks\n\n"
            "Full report sent to email."
        ),
        "start": {"dateTime": f"{event_date}T09:00:00", "timeZone": "America/New_York"},
        "end":   {"dateTime": f"{event_date}T09:30:00", "timeZone": "America/New_York"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 0},
                {"method": "popup", "minutes": 60},
            ],
        },
    }

    service.events().insert(calendarId=GCAL_ID, body=event).execute()


# ── Google Calendar setup (one-time) ──────────────────────────────────────────
def setup_gcal() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: Run setup first:  bash run_monthly_report.sh --install")
        return

    if not CREDS_FILE.exists():
        print(f"""
Google Calendar credentials not found at:
  {CREDS_FILE}

To create them:
  1. Go to https://console.cloud.google.com
  2. Create or select a project (e.g. "Holland IT Automation")
  3. Enable "Google Calendar API"
  4. OAuth consent screen → Internal → App name: Holland IT Automation
  5. Credentials → Create → OAuth 2.0 Client ID → Desktop App
  6. Download the JSON file and save it as:
     {CREDS_FILE}

Then re-run:  python monthly_report.py --setup-gcal
""")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    print(f"Google Calendar authorized. Token saved to {TOKEN_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    if "--setup-gcal" in sys.argv:
        setup_gcal()
        return

    today = date.today()
    month_name = today.strftime("%B %Y")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Monthly report — {month_name}")

    print("  Pulling aging invoices...")
    aging = get_aging_invoices()

    print("  Pulling monthly invoices...")
    monthly = get_monthly_invoices()

    print("  Pulling tickets without charges (this may take a minute)...")
    uncharged = get_tickets_without_charges()

    print(f"  Aging: {len(aging)}  |  Monthly invoices: {len(monthly)}  |  Uncharged tickets: {len(uncharged)}")

    tmp_dir = Path(tempfile.mkdtemp())
    pdf_path = tmp_dir / f"HollandIT_Report_{today.strftime('%Y-%m')}.pdf"
    print(f"  Generating PDF...")
    generate_pdf(aging, monthly, uncharged, pdf_path)

    print("  Sending email...")
    send_email(pdf_path, aging, uncharged, monthly)

    print("  Creating Todoist task...")
    create_todoist_task(aging, uncharged)

    print("  Creating Google Calendar event...")
    create_calendar_event()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Done.")


if __name__ == "__main__":
    main()
