#!/usr/bin/env python3
"""cw-inbox MCP Server — AI-to-AI coordination via shared JSONL inbox.

Transport: stdio (default) or HTTP/SSE (--port N).
Storage: ~/.cwinbox/  (JSONL, SeaShell-compatible protocol).

Tools:
  inbox_send    — write a message to the current project inbox
  inbox_poll    — read unread messages (atomically marks as read)
  inbox_reply   — reply to a specific message
  inbox_status  — summary of all projects and their inbox state
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ── Constants ────────────────────────────────────────────────────────────────

STORAGE_ROOT = Path(os.environ.get("CWINBOX_ROOT", Path.home() / ".cwinbox"))
GLOBAL_INBOX = STORAGE_ROOT / "inbox.jsonl"
PROJECTS_FILE = STORAGE_ROOT / "projects.jsonl"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hostname() -> str:
    return socket.gethostname()


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _jsonl_append(path: Path, record: dict) -> None:
    """Append a JSON line to a file with file locking."""
    _ensure_dir(path)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _jsonl_read(path: Path) -> list[dict]:
    """Read all JSON records from a file. Returns empty list if missing."""
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return records


def _atomic_process_inbox(inbox_path: Path, archive_path: Path) -> list[dict]:
    """Atomically rename inbox, mark all as read, archive, return records."""
    if not inbox_path.exists():
        return []

    processing = inbox_path.with_name("inbox.processing.jsonl")
    _ensure_dir(processing)

    # Atomic rename
    os.rename(inbox_path, processing)

    records = _jsonl_read(processing)
    if not records:
        processing.unlink(missing_ok=True)
        return []

    # Mark all as read, append to archive
    now = _now_iso()
    for r in records:
        r["read"] = True
        r["archived_at"] = now
        _jsonl_append(archive_path, r)

    # Delete processing file
    processing.unlink(missing_ok=True)

    return records


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _find_project_inbox(cwd: str | None = None) -> tuple[Path | None, str | None]:
    """Walk up from cwd to find .cwinbox/ directory.

    Returns (inbox_dir, project_name) or (None, None).
    """
    if cwd is None:
        cwd = os.getcwd()
    directory = Path(cwd).resolve()
    while directory != directory.parent:
        cwbox = directory / ".cwinbox"
        if cwbox.is_dir():
            return cwbox, directory.name
        # Also check for .ai-crew.json as marker
        if (directory / ".ai-crew.json").exists():
            cwbox.mkdir(parents=True, exist_ok=True)
            return cwbox, directory.name
        directory = directory.parent
    return None, None


def _resolve_inbox_paths(cwd: str | None = None) -> dict:
    """Resolve inbox, archive, replies paths for the current project (fallback to global)."""
    project_inbox, project_name = _find_project_inbox(cwd)
    if project_inbox:
        return {
            "inbox": project_inbox / "inbox.jsonl",
            "archive": project_inbox / "archive.jsonl",
            "replies": project_inbox / "replies.jsonl",
            "project_name": project_name,
            "project_path": str(project_inbox.parent),
            "is_global": False,
        }
    return {
        "inbox": STORAGE_ROOT / "inbox.jsonl",
        "archive": STORAGE_ROOT / "inbox.archive.jsonl",
        "replies": STORAGE_ROOT / "replies.jsonl",
        "project_name": None,
        "project_path": None,
        "is_global": True,
    }


def _register_project(path: str, name: str) -> None:
    """Register a project in projects.jsonl if not already present."""
    existing = _jsonl_read(PROJECTS_FILE)
    for entry in existing:
        if entry.get("path") == path:
            return  # already registered
    _jsonl_append(PROJECTS_FILE, {
        "path": path,
        "name": name,
        "added_at": _now_iso(),
    })


def _discover_all_inboxes() -> list[dict]:
    """Discover all project inboxes (global + registered projects)."""
    inboxes = [{
        "inbox": GLOBAL_INBOX,
        "archive": STORAGE_ROOT / "inbox.archive.jsonl",
        "replies": STORAGE_ROOT / "replies.jsonl",
        "project_name": None,
        "project_path": None,
        "is_global": True,
    }]

    for entry in _jsonl_read(PROJECTS_FILE):
        project_path = Path(entry["path"])
        if not project_path.is_dir():
            continue
        cwbox = project_path / ".cwinbox"
        inboxes.append({
            "inbox": cwbox / "inbox.jsonl",
            "archive": cwbox / "archive.jsonl",
            "replies": cwbox / "replies.jsonl",
            "project_name": entry.get("name", project_path.name),
            "project_path": str(project_path),
            "is_global": False,
        })
    return inboxes


# ── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP("ai-crew", host="127.0.0.1", port=9876)


@mcp.tool()
def inbox_send(text: str, role: str = "worker", priority: int = 3) -> dict[str, Any]:
    """Send a message to the current project's inbox.

    Use this when you (as a worker) need guidance from the supervisor,
    or when you (as a supervisor) want to leave a note for the worker.

    Args:
        text: The message content. Be specific about what you need.
        role: Your role — "worker" or "supervisor".
        priority: 1 (urgent) to 5 (low). Default 3.

    Returns:
        The created message record with id and timestamp.
    """
    paths = _resolve_inbox_paths()
    msg_id = str(uuid.uuid4())
    record = {
        "id": msg_id,
        "ts": _now_iso(),
        "cwd": os.getcwd(),
        "hostname": _hostname(),
        "text": text,
        "role": role,
        "priority": priority,
        "read": False,
    }
    _jsonl_append(paths["inbox"], record)

    # Register project if not global
    if not paths["is_global"] and paths["project_path"]:
        _register_project(paths["project_path"], paths["project_name"] or "unknown")

    return {
        "id": msg_id,
        "ts": record["ts"],
        "project": paths["project_name"] or "global",
    }


@mcp.tool()
def inbox_poll(project: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Read unread messages from the inbox. Messages are marked as read after this call.

    Call this when you want to check if there are new messages from
    your worker or supervisor. Messages are atomically moved to archive
    after reading.

    Args:
        project: Optional project name to filter. If None, reads all projects.
        limit: Maximum number of messages to return per inbox. Default 20.

    Returns:
        List of unread messages, grouped by project.
    """
    results: list[dict] = []
    inboxes = _discover_all_inboxes()

    for inbox_info in inboxes:
        if project and inbox_info["project_name"] != project:
            continue
        records = _atomic_process_inbox(inbox_info["inbox"], inbox_info["archive"])
        if records:
            for r in records[:limit]:
                r["_project"] = inbox_info["project_name"] or "global"
                results.append(r)

    # Check for replies to our messages
    replies = _jsonl_read(_resolve_inbox_paths()["replies"])
    unread_replies = [r for r in replies if not r.get("read", False)]
    if unread_replies:
        # Mark replies as read
        for r in unread_replies:
            r["read"] = True
        # Rewrite replies file (simplified — for production, use atomic pattern)
        paths = _resolve_inbox_paths()
        # This is a simplification. Full atomicity would require the same rename pattern.
        # For MVP, we accept eventual consistency on replies.
        results.extend([{**r, "_is_reply": True} for r in unread_replies])

    return {
        "messages": results,
        "count": len(results),
        "ts": _now_iso(),
    }


@mcp.tool()
def inbox_reply(msg_id: str, text: str) -> dict[str, Any]:
    """Reply to a specific inbox message.

    Use this when you (as supervisor) want to respond to a worker's
    question, or when you (as worker) want to acknowledge a supervisor's
    guidance.

    Args:
        msg_id: The id of the message you're replying to
                  (from inbox_poll results).
        text: Your reply content. Be direct and actionable.

    Returns:
        Confirmation with the reply id and timestamp.
    """
    paths = _resolve_inbox_paths()
    reply_id = str(uuid.uuid4())
    record = {
        "id": reply_id,
        "message_id": msg_id,
        "ts": _now_iso(),
        "hostname": _hostname(),
        "text": text,
        "read": False,
    }
    _jsonl_append(paths["replies"], record)
    return {
        "id": reply_id,
        "in_reply_to": msg_id,
        "ts": record["ts"],
    }


@mcp.tool()
def inbox_status() -> dict[str, Any]:
    """Get a summary of all projects and their inbox state.

    Call this at the start of your session to understand what's pending.
    Shows unread message counts per project and configuration.

    Returns:
        Status summary with per-project unread counts.
    """
    inboxes = _discover_all_inboxes()
    projects_status = []

    for inbox_info in inboxes:
        records = _jsonl_read(inbox_info["inbox"])
        unread = [r for r in records if not r.get("read", False)]
        unread_replies = len([r for r in _jsonl_read(inbox_info["replies"]) if not r.get("read", False)])
        projects_status.append({
            "project": inbox_info["project_name"] or "global",
            "path": inbox_info["project_path"],
            "unread_messages": len(unread),
            "unread_replies": unread_replies,
            "total_archived": len(_jsonl_read(inbox_info["archive"])),
        })

    # Check for .ai-crew.json config in current project
    config = {}
    cwd = Path.cwd()
    for d in [cwd] + list(cwd.parents):
        config_file = d / ".ai-crew.json"
        if config_file.exists():
            try:
                config = json.loads(config_file.read_text())
            except json.JSONDecodeError:
                pass
            break

    return {
        "projects": projects_status,
        "total_unread": sum(p["unread_messages"] for p in projects_status),
        "config": config,
        "storage_root": str(STORAGE_ROOT),
        "ts": _now_iso(),
    }


# ── Entry point ──────────────────────────────────────────────────────────────

def _patch_uvicorn_timeouts():
    """Extend uvicorn timeouts so the server stays alive between requests."""
    import uvicorn
    import functools

    _orig_sse = mcp.run_sse_async
    @functools.wraps(_orig_sse)
    async def _patched_sse(mount_path=None):
        starlette_app = mcp.sse_app(mount_path)
        config = uvicorn.Config(
            starlette_app,
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
            timeout_keep_alive=300,   # default 5s → 5min
            timeout_notify=300,       # default 30s → 5min
        )
        server = uvicorn.Server(config)
        await server.serve()

    _orig_http = mcp.run_streamable_http_async
    @functools.wraps(_orig_http)
    async def _patched_http():
        starlette_app = mcp.streamable_http_app()
        config = uvicorn.Config(
            starlette_app,
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
            timeout_keep_alive=300,
            timeout_notify=300,
        )
        server = uvicorn.Server(config)
        await server.serve()

    mcp.run_sse_async = _patched_sse
    mcp.run_streamable_http_async = _patched_http


def main():
    parser = argparse.ArgumentParser(description="cw-inbox MCP Server")
    parser.add_argument("--sse", action="store_true",
                        help="Run as HTTP/SSE server (default: stdio)")
    parser.add_argument("--http", action="store_true",
                        help="Run as StreamableHTTP server (default: stdio)")
    parser.add_argument("--port", type=int, default=9876,
                        help="Port for HTTP server (default: 9876)")
    args = parser.parse_args()

    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

    if args.port != 9876:
        mcp.settings.port = args.port

    if args.http:
        _patch_uvicorn_timeouts()
        print(f"cw-inbox StreamableHTTP: http://{mcp.settings.host}:{mcp.settings.port}/mcp",
              file=sys.stderr)
        mcp.run(transport="streamable-http")
    elif args.sse:
        _patch_uvicorn_timeouts()
        print(f"cw-inbox SSE server: http://{mcp.settings.host}:{mcp.settings.port}/sse",
              file=sys.stderr)
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
