"""Tool definitions and dispatch for Claude tool-use."""

import json
from app import syncro, todoist, gmail

TOOLS = [
    {
        "name": "syncro_list_tickets",
        "description": "List Syncro MSP tickets, optionally filtered by status or customer name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by ticket status, e.g. 'New', 'In Progress', 'Resolved', 'Closed'",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Filter by customer name (partial match supported)",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search tickets by keyword in subject or customer name",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Filter by assigned technician name, e.g. 'Jason' or 'Rex'",
                },
            },
        },
    },
    {
        "name": "syncro_get_ticket",
        "description": "Get full details of a specific Syncro ticket including comments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ref": {
                    "type": "integer",
                    "description": "The ticket number as shown in Syncro (e.g. 116627)",
                },
            },
            "required": ["ticket_ref"],
        },
    },
    {
        "name": "syncro_create_ticket",
        "description": "Create a new Syncro MSP ticket for a customer. Provide either customer_name (for lookup) or customer_id (if you already know it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Customer name to search for (partial match supported)",
                },
                "customer_id": {
                    "type": "integer",
                    "description": "Syncro customer ID — use this directly if you already know it, skips name lookup",
                },
                "contact_name": {
                    "type": "string",
                    "description": "Contact name within the customer — this person will receive ticket email notifications",
                },
                "subject": {
                    "type": "string",
                    "description": "Brief summary of the issue",
                },
                "description": {
                    "type": "string",
                    "description": "Full description of the issue",
                },
                "issue_type": {
                    "type": "string",
                    "description": "Issue/problem type, e.g. 'Remote Break/Fix', 'On-Site Visit'",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Technician name to assign the ticket to, e.g. 'Jason' or 'Rex'",
                },
            },
            "required": ["subject"],
        },
    },
    {
        "name": "syncro_update_ticket",
        "description": "Update a Syncro ticket's status, subject, or assigned technician.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ref": {
                    "type": "integer",
                    "description": "The ticket number as shown in Syncro (e.g. 116627)",
                },
                "status": {
                    "type": "string",
                    "description": "New status: 'New', 'In Progress', 'Resolved', 'Closed', 'Waiting on Customer'",
                },
                "subject": {
                    "type": "string",
                    "description": "New subject/title for the ticket",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Technician name to assign the ticket to, e.g. 'Jason' or 'Rex'",
                },
            },
            "required": ["ticket_ref"],
        },
    },
    {
        "name": "syncro_add_comment",
        "description": "Add a comment or internal note to a Syncro ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ref": {
                    "type": "integer",
                    "description": "The ticket number as shown in Syncro (e.g. 116627)",
                },
                "body": {
                    "type": "string",
                    "description": "The comment text",
                },
                "hidden": {
                    "type": "boolean",
                    "description": "If true, this is an internal note not visible to the customer",
                },
            },
            "required": ["ticket_ref", "body"],
        },
    },
    {
        "name": "todoist_list_tasks",
        "description": "List Todoist tasks. Use Todoist filter syntax to narrow results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Todoist filter string, e.g. 'today', 'overdue', 'p1', '#Work', '@label'",
                },
            },
        },
    },
    {
        "name": "todoist_create_task",
        "description": "Create a new Todoist task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Task title/content",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer description or notes",
                },
                "due_string": {
                    "type": "string",
                    "description": "Natural language due date, e.g. 'today', 'tomorrow', 'next Monday'",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority: 4=urgent/p1, 3=high/p2, 2=medium/p3, 1=normal/p4",
                    "enum": [1, 2, 3, 4],
                },
                "project_id": {
                    "type": "string",
                    "description": "Todoist project ID to add the task to",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of label names to apply",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "todoist_complete_task",
        "description": "Mark a Todoist task as complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The Todoist task ID",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "todoist_list_projects",
        "description": "List all Todoist projects.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "syncro_delete_ticket",
        "description": "Permanently delete a Syncro ticket. This cannot be undone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ref": {
                    "type": "integer",
                    "description": "The ticket number as shown in Syncro (e.g. 116627)",
                },
            },
            "required": ["ticket_ref"],
        },
    },
    {
        "name": "syncro_log_time",
        "description": "Log labor/time on a Syncro ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ref": {
                    "type": "integer",
                    "description": "Ticket number (e.g. 116627) or internal ID",
                },
                "hours": {
                    "type": "number",
                    "description": "Hours worked, e.g. 1.5",
                },
                "notes": {
                    "type": "string",
                    "description": "Description of work performed",
                },
                "billable": {
                    "type": "boolean",
                    "description": "Whether to bill the customer (default true)",
                },
            },
            "required": ["ticket_ref", "hours"],
        },
    },
    {
        "name": "syncro_create_invoice",
        "description": "Create an invoice for a Syncro ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ref": {
                    "type": "integer",
                    "description": "Ticket number (e.g. 116627) or internal ID",
                },
            },
            "required": ["ticket_ref"],
        },
    },
    {
        "name": "check_emails",
        "description": "Check recent unread emails from Jason's personal (jlh1825@gmail.com) and/or business (jason.holland@hollandit.biz) Gmail inboxes. Returns emails from the last 24 hours by default. Use your judgment to surface only important ones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "enum": ["personal", "business", "both"],
                    "description": "Which inbox to check. Default: both",
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to look. Default: 24",
                },
            },
        },
    },
]


TODOIST_TOOL_NAMES = {"todoist_list_tasks", "todoist_create_task", "todoist_complete_task", "todoist_list_projects"}
EMAIL_TOOL_NAMES = {"check_emails"}


def get_tools(include_todoist: bool = True, include_email: bool = True) -> list:
    excluded = set()
    if not include_todoist:
        excluded |= TODOIST_TOOL_NAMES
    if not include_email:
        excluded |= EMAIL_TOOL_NAMES
    if not excluded:
        return TOOLS
    return [t for t in TOOLS if t["name"] not in excluded]


async def dispatch_tool(name: str, input: dict) -> str:
    try:
        if name == "syncro_list_tickets":
            result = await syncro.list_tickets(
                status=input.get("status"),
                customer_name=input.get("customer_name"),
                keyword=input.get("keyword"),
                assigned_to=input.get("assigned_to"),
            )
        elif name == "syncro_get_ticket":
            result = await syncro.get_ticket(input["ticket_id"] if "ticket_id" in input else input["ticket_ref"])
        elif name == "syncro_create_ticket":
            result = await syncro.create_ticket(
                customer_name=input.get("customer_name"),
                customer_id=input.get("customer_id"),
                contact_name=input.get("contact_name"),
                subject=input["subject"],
                description=input.get("description"),
                issue_type=input.get("issue_type", "Remote Break/Fix"),
                assigned_to=input.get("assigned_to"),
            )
        elif name == "syncro_update_ticket":
            ref = input.get("ticket_ref") or input.get("ticket_id")
            result = await syncro.update_ticket(
                ticket_ref=ref,
                status=input.get("status"),
                subject=input.get("subject"),
                assigned_to=input.get("assigned_to"),
            )
        elif name == "syncro_add_comment":
            ref = input.get("ticket_ref") or input.get("ticket_id")
            result = await syncro.add_comment(
                ticket_ref=ref,
                body=input["body"],
                hidden=input.get("hidden", False),
            )
        elif name == "syncro_delete_ticket":
            result = await syncro.delete_ticket(input["ticket_ref"])
        elif name == "syncro_log_time":
            result = await syncro.log_time(
                ticket_ref=input["ticket_ref"],
                hours=input["hours"],
                notes=input.get("notes", ""),
                billable=input.get("billable", True),
            )
        elif name == "syncro_create_invoice":
            result = await syncro.create_invoice(ticket_ref=input["ticket_ref"])
        elif name == "todoist_list_tasks":
            result = await todoist.list_tasks(filter=input.get("filter"))
        elif name == "todoist_create_task":
            result = await todoist.create_task(
                content=input["content"],
                description=input.get("description"),
                due_string=input.get("due_string"),
                priority=input.get("priority"),
                project_id=input.get("project_id"),
                labels=input.get("labels"),
            )
        elif name == "todoist_complete_task":
            result = await todoist.complete_task(input["task_id"])
        elif name == "todoist_list_projects":
            result = await todoist.list_projects()
        elif name == "check_emails":
            result = await gmail.fetch_emails(
                account=input.get("account", "both"),
                hours=input.get("hours", 24),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
    except ValueError as e:
        result = {"error": str(e)}
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {str(e)}"}

    return json.dumps(result)
