"""FastAPI application entrypoint."""

import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.ai import chat, chat_stream

app = FastAPI(title="IT Assistant")
app.mount("/static", StaticFiles(directory="static"), name="static")


class ChatRequest(BaseModel):
    messages: list[dict]


class BatchDeleteRequest(BaseModel):
    ticket_ids: list[int]  # internal IDs from ticket URLs


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug/syncro")
async def debug_syncro():
    import os, httpx
    token = os.environ.get("SYNCRO_API_TOKEN", "NOT SET")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://hollandit.syncromsp.com/api/v1/tickets",
            params={"status": "New"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        return {"status": resp.status_code, "token_prefix": token[:10] + "...", "body": resp.text[:500]}


@app.get("/debug/syncro/charge/{ticket_number}")
async def debug_charge(ticket_number: int):
    import os, httpx
    from datetime import datetime, timezone, timedelta
    token = os.environ.get("SYNCRO_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
    base = "https://hollandit.syncromsp.com/api/v1"
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{base}/tickets", params={"number": ticket_number}, headers=headers)
        tickets = r.json().get("tickets", [])
        if not tickets:
            return {"error": "ticket not found"}
        internal_id = tickets[0]["id"]

        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=30)
        # Create timer entry
        create_resp = await client.post(f"{base}/tickets/{internal_id}/timer_entry", json={"start_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "duration_minutes": 30, "notes": "charge debug test"}, headers=headers)
        entry = create_resp.json()
        timer_id = entry.get("id")

        # Try to charge it
        charge_resp = await client.post(f"{base}/tickets/{internal_id}/charge_timer_entry", json={"timer_entry_id": timer_id}, headers=headers)
        return {
            "internal_id": internal_id,
            "timer_created": {"status": create_resp.status_code, "id": timer_id, "body": create_resp.text[:300]},
            "charge": {"status": charge_resp.status_code, "body": charge_resp.text[:400]},
        }


@app.get("/debug/syncro/timer/{ticket_number}")
async def debug_timer(ticket_number: int):
    import os, httpx
    from datetime import datetime, timezone, timedelta
    token = os.environ.get("SYNCRO_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        # Resolve ticket number to internal ID
        r = await client.get("https://hollandit.syncromsp.com/api/v1/tickets", params={"number": ticket_number}, headers=headers)
        tickets = r.json().get("tickets", [])
        if not tickets:
            return {"error": "ticket not found"}
        internal_id = tickets[0]["id"]

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=1)
        base = "https://hollandit.syncromsp.com/api/v1"
        results = {}

        # Try flat payload on ticket_timers
        flat = {"ticket_id": internal_id, "start_time": start.strftime("%Y-%m-%dT%H:%M:%S"), "end_time": now.strftime("%Y-%m-%dT%H:%M:%S"), "notes": "debug test", "billable": True}
        r2 = await client.post(f"{base}/ticket_timers", json=flat, headers=headers)
        results["flat_ticket_timers"] = {"status": r2.status_code, "body": r2.text[:300]}

        # Try line_items on ticket
        line_payload = {"line_item": {"item": "Labor", "name": "Remote Support", "quantity": 1, "price": 0, "ticket_id": internal_id}}
        r3 = await client.post(f"{base}/line_items", json=line_payload, headers=headers)
        results["line_items"] = {"status": r3.status_code, "body": r3.text[:300]}

        # Try GET on ticket_timers to check what fields exist
        r4 = await client.get(f"{base}/ticket_timers", params={"ticket_id": internal_id}, headers=headers)
        results["get_ticket_timers"] = {"status": r4.status_code, "body": r4.text[:300]}

        return {"internal_id": internal_id, "results": results}


@app.get("/debug/syncro/tickets")
async def debug_syncro_tickets():
    import os, httpx
    token = os.environ.get("SYNCRO_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://hollandit.syncromsp.com/api/v1/tickets", params={"status": "New"}, headers=headers)
        tickets = resp.json().get("tickets", [])[:3]
        return [{"id": t.get("id"), "number": t.get("number"), "subject": t.get("subject"), "customer_id": t.get("customer_id")} for t in tickets]


@app.get("/debug/todoist")
async def debug_todoist():
    import os, httpx
    token = os.environ.get("TODOIST_API_TOKEN", "")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.todoist.com/api/v1/tasks",
            headers={"Authorization": f"Bearer {token}"},
        )
        return {"status": resp.status_code, "body": resp.json()}


@app.post("/tickets/batch-delete")
async def batch_delete_tickets(req: BatchDeleteRequest):
    import asyncio
    import httpx
    from app.syncro import _headers, BASE_URL

    async def _delete_one(tid: int):
        async with httpx.AsyncClient() as client:
            resp = await client.delete(f"{BASE_URL}/tickets/{tid}", headers=_headers())
            resp.raise_for_status()

    results = await asyncio.gather(
        *[_delete_one(tid) for tid in req.ticket_ids],
        return_exceptions=True,
    )
    out = []
    for tid, res in zip(req.ticket_ids, results):
        if isinstance(res, Exception):
            out.append({"ticket_id": tid, "success": False, "error": str(res)})
        else:
            out.append({"ticket_id": tid, "success": True})
    return {"results": out}


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    try:
        reply = await chat(req.messages)
        return {"role": "assistant", "content": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    async def event_generator():
        try:
            async for chunk in chat_stream(req.messages):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield "data: {\"done\": true}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "IT Assistant",
        "short_name": "IT Asst",
        "description": "Manage Syncro tickets and Todoist tasks",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1a1a2e",
        "theme_color": "#16213e",
        "orientation": "portrait",
        "icons": [
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🖥️</text></svg>",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    })


@app.get("/sw.js", response_class=PlainTextResponse)
async def service_worker():
    js = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
self.addEventListener('fetch', e => {
  // Network-first strategy — no offline caching needed
  e.respondWith(fetch(e.request));
});
"""
    return PlainTextResponse(js, media_type="application/javascript")


@app.get("/")
async def index():
    return FileResponse("static/app.html", headers={"Cache-Control": "no-store"})
