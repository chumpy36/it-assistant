"""Claude AI orchestration with agentic tool-use loop."""

import os
import time
import asyncio
import anthropic
from app.tools import get_tools, dispatch_tool

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
4. Summarize: new tickets first, then any In Progress tickets not updated in 2+ days, then Waiting on Customer tickets. For Waiting on Customer tickets, show each ticket on its own line — if customer_reply=true, prepend 🔔 to that ticket's row (e.g. "🔔 [#116959](url) | subject | customer"). Do NOT use 🔔 as a section header. Do NOT show 🔔 on New or In Progress tickets.
5. Ticket aging: calculate days since updated_at for each ticket. Append ⚠️ Xd to any New ticket not updated in 3+ days, or any In Progress ticket not updated in 7+ days. E.g. "| [#116627](url) | Subject | Customer | ⚠️ 9d |"
6. Also mention Todoist tasks due today if relevant

## Customer Ticket History
- When showing a ticket, if recent_customer_tickets is present, show a brief "Recent tickets from this customer" section with the last few tickets as links
- This gives context — e.g. if the same customer has had recurring issues

## Technicians
- "my tickets" / "Jason's tickets" / "mine" → assigned_to="Jason", no status filter (Syncro default returns active tickets only)
- "Rex's tickets" → assigned_to="Rex"
- Always use assigned_to filter when a specific technician is mentioned or implied
- "assign to me" / "assign to Jason" → use assigned_to="Jason" in create or update ticket
- "assign to Rex" → use assigned_to="Rex" in create or update ticket
- Never show Resolved or Closed tickets in results unless the user explicitly asks for them — filter them out of any list before displaying
- When listing tickets for a technician (e.g. "my tickets", "Jason's tickets"), prepend 🔔 to each individual ticket row that has customer_reply=true — do not use it as a section header

## Ticket Search
- "Find tickets about VPN" → use keyword search
- "Show all Welch tickets" → use customer_name filter

## Customer Name Lookup
- Pass customer names exactly as the user provides them — do NOT normalize, concatenate, or remove spaces (e.g. "Pro Georgia" must stay "Pro Georgia", never "ProGeorgia")
- If a search fails, retry with alternate spellings the user mentioned (e.g. "PGA", "Pro-Georgia") before asking for clarification
- The search supports partial and fuzzy matching, so pass the most natural form of the name

## Personality
- You have a dry, sardonic wit — use it when the situation calls for it, but keep it brief
- One-liners are fine, novels are not
- Don't force it — let it happen naturally

## General
- Be concise — short responses are better than long ones
- Ticket lists: ALWAYS show number (as link), subject, AND customer — never omit the customer column
- Briefings: top 5 most important items max, not exhaustive lists
- When an action succeeds, one line confirmation is enough
- If a customer name matches multiple customers, list the matches and ask for clarification
- For ambiguous requests, ask one focused clarifying question
- No raw JSON, no unnecessary formatting
- When a tool returns an error, ALWAYS report the exact error message to the user — never say it "failed silently" or omit the details. Show the full error text so the issue can be diagnosed.
"""


def _trim_history(messages: list[dict], keep: int = 4) -> list[dict]:
    """Keep only the last `keep` messages to avoid ballooning context."""
    if len(messages) <= keep:
        return messages
    # Always keep an even number to preserve user/assistant pairs
    trimmed = messages[-keep:]
    # Ensure first message is from user
    while trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]
    return trimmed


async def chat(messages: list[dict], include_todoist: bool = True) -> str:
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    total_start = time.time()
    loop = 0
    tools = get_tools(include_todoist)

    # Trim history to keep context small
    messages = _trim_history(messages)

    while True:
        loop += 1
        t0 = time.time()
        response = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
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


async def chat_stream(messages: list[dict], include_todoist: bool = True):
    """Stream the final text response as an async generator of text chunks."""
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages = _trim_history(messages)
    tools = get_tools(include_todoist)
    loop = 0

    while True:
        loop += 1
        t0 = time.time()

        async with client.messages.stream(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        ) as stream:
            # Yields text chunks in real-time for end_turn responses;
            # tool_use responses produce no text so this loop exits immediately.
            async for chunk in stream.text_stream:
                yield chunk

            final_msg = await stream.get_final_message()

        print(f"[timing] stream loop={loop} stop={final_msg.stop_reason} {time.time()-t0:.2f}s", flush=True)

        if final_msg.stop_reason == "end_turn":
            return

        if final_msg.stop_reason == "tool_use":
            messages = messages + [{"role": "assistant", "content": final_msg.content}]
            tool_blocks = [b for b in final_msg.content if b.type == "tool_use"]

            async def run_tool(block):
                t1 = time.time()
                result = await dispatch_tool(block.name, block.input)
                print(f"[timing]   tool={block.name} {time.time()-t1:.2f}s", flush=True)
                return {"type": "tool_result", "tool_use_id": block.id, "content": result}

            tool_results = await asyncio.gather(*[run_tool(b) for b in tool_blocks])
            messages = messages + [{"role": "user", "content": list(tool_results)}]
        else:
            yield f"\n[Stopped: {final_msg.stop_reason}]"
            return
