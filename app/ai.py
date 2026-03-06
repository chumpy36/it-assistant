"""Claude AI orchestration with agentic tool-use loop."""

import os
import time
import asyncio
import anthropic
from app.tools import TOOLS, dispatch_tool

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are an IT professional assistant for Holland IT, helping manage Syncro MSP tickets and Todoist tasks via natural language.

## Syncro MSP
- Valid ticket statuses: "New", "In Progress", "Resolved", "Closed", "Waiting on Customer", "Waiting on Parts"
- "resolve" or "mark resolved" → set status to "Resolved"
- "close" → set status to "Closed"
- "internal note" or "private note" → add_comment with hidden=true (not visible to customer)
- Regular comment / reply to customer → add_comment with hidden=false (emails the customer)
- ALWAYS format ticket numbers as markdown links: [#116627](https://hollandit.syncromsp.com/tickets/INTERNAL_ID)
- When you have the URL from tool results, use it. When listing multiple tickets, every ticket number must be a link.
- Format: [#NUMBER](URL) — e.g. [#116627](https://hollandit.syncromsp.com/tickets/107248794)
- After logging time or creating an invoice, confirm with the URL so Jason can review it
- When listing tickets, show a concise table with ticket number (as link), subject, and customer

## Todoist Priority Mapping
- "urgent" / "p1" → priority 4 (API value)
- "high" / "p2" → priority 3
- "medium" / "p3" → priority 2
- "normal" / "p4" → priority 1
- Todoist API priority is INVERTED from the display label

## Morning Briefing / "What needs attention?"
When asked what needs attention, what to work on, or for a morning briefing:
1. Call syncro_list_tickets with status="New" — these need triage
2. Call syncro_list_tickets with status="In Progress" — check for stalled tickets
3. Call syncro_list_tickets with status="Waiting on Customer" — check for customer replies
4. Summarize: new tickets first, then any In Progress tickets not updated in 2+ days, then Waiting on Customer tickets where customer has replied (updated_at is recent)
5. Also mention Todoist tasks due today if relevant

## Customer Ticket History
- When showing a ticket, if recent_customer_tickets is present, show a brief "Recent tickets from this customer" section with the last few tickets as links
- This gives context — e.g. if the same customer has had recurring issues

## Ticket Search
- "Find tickets about VPN" → use keyword search
- "Show all Welch tickets" → use customer_name filter

## General
- Be concise — short responses are better than long ones
- Ticket lists: show number (as link), subject, customer only — no timestamps unless asked
- Briefings: top 5 most important items max, not exhaustive lists
- When an action succeeds, one line confirmation is enough
- If a customer name matches multiple customers, list the matches and ask for clarification
- For ambiguous requests, ask one focused clarifying question
- No raw JSON, no unnecessary formatting
"""


def _trim_history(messages: list[dict], keep: int = 6) -> list[dict]:
    """Keep only the last `keep` messages to avoid ballooning context."""
    if len(messages) <= keep:
        return messages
    # Always keep an even number to preserve user/assistant pairs
    trimmed = messages[-keep:]
    # Ensure first message is from user
    while trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]
    return trimmed


async def chat(messages: list[dict]) -> str:
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    total_start = time.time()
    loop = 0

    # Trim history to keep context small
    messages = _trim_history(messages)

    while True:
        loop += 1
        t0 = time.time()
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        print(f"[timing] loop={loop} claude={time.time()-t0:.2f}s stop={response.stop_reason}", flush=True)

        if response.stop_reason == "end_turn":
            print(f"[timing] total={time.time()-total_start:.2f}s", flush=True)
            text_parts = [
                block.text for block in response.content if hasattr(block, "text")
            ]
            return "\n".join(text_parts)

        if response.stop_reason == "tool_use":
            messages = messages + [{"role": "assistant", "content": response.content}]

            # Dispatch all tool calls concurrently
            tool_blocks = [b for b in response.content if b.type == "tool_use"]

            async def run_tool(block):
                t1 = time.time()
                result = await dispatch_tool(block.name, block.input)
                print(f"[timing]   tool={block.name} {time.time()-t1:.2f}s", flush=True)
                return {"type": "tool_result", "tool_use_id": block.id, "content": result}

            tool_results = await asyncio.gather(*[run_tool(b) for b in tool_blocks])
            messages = messages + [{"role": "user", "content": list(tool_results)}]
        else:
            text_parts = [
                block.text for block in response.content if hasattr(block, "text")
            ]
            return "\n".join(text_parts) or f"[Stopped: {response.stop_reason}]"
