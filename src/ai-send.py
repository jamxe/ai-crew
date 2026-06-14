#!/usr/bin/env python3
"""Standalone inbox writer — used by ai-msg and ai-ask shell commands."""
import json, os, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

root = Path.home() / ".cwinbox"
cwd = Path.cwd().resolve()
inbox_dir = root
proj = None

for d in [cwd] + list(cwd.parents):
    cb = d / ".cwinbox"
    if cb.is_dir() or (d / ".ai-crew.json").exists():
        cb.mkdir(parents=True, exist_ok=True)
        inbox_dir = cb
        proj = d.name
        break

inbox_dir.mkdir(parents=True, exist_ok=True)
inbox_file = inbox_dir / "inbox.jsonl"

text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
role = sys.argv[2] if len(sys.argv) > 2 else "worker"
priority = int(sys.argv[3]) if len(sys.argv) > 3 else 3
reply_token = sys.argv[4] if len(sys.argv) > 4 else ""

mid = str(uuid.uuid4())
rec = {
    "id": mid,
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "cwd": str(cwd),
    "hostname": os.uname().nodename,
    "text": text,
    "role": role,
    "priority": priority,
    "read": False,
}
if reply_token:
    rec["reply_token"] = reply_token

with open(inbox_file, "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(json.dumps({"id": mid, "project": proj or "global"}))
