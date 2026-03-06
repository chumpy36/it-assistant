"""Async Todoist REST API v2 client."""

from __future__ import annotations
import os
import httpx

BASE_URL = "https://api.todoist.com/api/v1"


def _headers() -> dict:
    token = os.environ.get("TODOIST_API_TOKEN", "")
    if not token:
        raise ValueError("TODOIST_API_TOKEN is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def list_projects() -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/projects", headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        projects = data.get("results", data) if isinstance(data, dict) else data
        return {
            "count": len(projects),
            "projects": [
                {"id": p["id"], "name": p["name"], "color": p.get("color")}
                for p in projects
            ],
        }


async def list_tasks(filter: str | None = None) -> dict:
    async with httpx.AsyncClient() as client:
        params = {}
        if filter:
            params["filter"] = filter
        resp = await client.get(f"{BASE_URL}/tasks", params=params, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        tasks = data.get("results", data) if isinstance(data, dict) else data
        return {
            "count": len(tasks),
            "tasks": [
                {
                    "id": t["id"],
                    "content": t["content"],
                    "description": t.get("description", ""),
                    "priority": t.get("priority", 1),  # 4=urgent(p1), 1=normal(p4)
                    "due": t.get("due"),
                    "project_id": t.get("project_id"),
                    "labels": t.get("labels", []),
                }
                for t in tasks
            ],
        }


async def create_task(
    content: str,
    description: str | None = None,
    due_string: str | None = None,
    priority: int | None = None,
    project_id: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    payload: dict = {"content": content}
    if description:
        payload["description"] = description
    if due_string:
        payload["due_string"] = due_string
    if priority is not None:
        payload["priority"] = priority
    if project_id:
        payload["project_id"] = project_id
    if labels:
        payload["labels"] = labels

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/tasks", json=payload, headers=_headers())
        resp.raise_for_status()
        task = resp.json()
        return {
            "success": True,
            "message": "Task created",
            "id": task["id"],
            "content": task["content"],
            "due": task.get("due"),
            "priority": task.get("priority"),
        }


async def complete_task(task_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/tasks/{task_id}/close",
            headers=_headers(),
        )
        resp.raise_for_status()  # returns 204 No Content
        return {"success": True, "message": f"Task {task_id} completed"}
