# 🐚 AI Crew

> **Inbox-based AI-to-AI coordination for CodeWhale + OpenCode.**

Let one AI supervise another — worker asks for guidance, supervisor gives it, all through a shared inbox on disk. No server, no WebSocket, just JSONL files.

Inspired by [SeaShell](https://github.com/M-Pineapple/seashell)'s inbox protocol, reimagined for the DeepSeek/OpenCode ecosystem instead of Claude/Wave.

---

## What it does

```
┌──────────────────────────────────────────────────────────┐
│  Worker (CodeWhale / OpenCode)     Supervisor (CodeWhale) │
│  ┌─────────────────────┐          ┌─────────────────────┐ │
│  │ Refactoring payment  │          │ Polls inbox         │ │
│  │ module...            │          │ Reads worker's msg  │ │
│  │                      │          │                     │ │
│  │ inbox_send("Strategy │          │ inbox_reply("Go     │ │
│  │   vs if/else?")      │──inbox──→│   with Strategy")   │ │
│  │                      │←─reply──│                     │ │
│  │ inbox_poll() → reply │          │                     │ │
│  │ Continues with plan  │          │                     │ │
│  └─────────────────────┘          └─────────────────────┘ │
│                                                           │
│              ~/.cwinbox/  (shared JSONL files)             │
└──────────────────────────────────────────────────────────┘
```

**Core idea**: worker AIs post decisions/questions to an inbox; supervisor AIs read and give direction. Both can be CodeWhale, OpenCode, or any MCP-compatible client. Messages persist on disk — cross-session, cross-day, cross-model.

## Prerequisites

- **Python 3.10+** — `python3 --version`
- **uv** (recommended) or **pip** — `brew install uv` or `pip install mcp`
- **CodeWhale** and/or **OpenCode** — at least one AI client that supports MCP

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/jamxe/ai-crew.git ~/CascadeProjects/ai-crew
cd ~/CascadeProjects/ai-crew
uv venv && source .venv/bin/activate && uv pip install mcp
```

### 2. Register MCP server with CodeWhale

```bash
codewhale mcp add ai-crew \
  --command ~/CascadeProjects/ai-crew/.venv/bin/python3 \
  --arg ~/CascadeProjects/ai-crew/src/server.py
```

### 3. Install shell commands

```bash
for cmd in ai-msg ai-ask ai-continue ai-sessions ai-crew-register; do
    cp src/$cmd ~/.local/bin/$cmd && chmod +x ~/.local/bin/$cmd
done
```

### 4. Try it

```bash
# Send a message from your terminal
ai-msg "hey supervisor, Strategy pattern vs if/else for payment module?"

# The supervisor AI (in CodeWhale) polls the inbox
# → calls inbox_poll(), sees the message, calls inbox_reply()

# Worker checks back later
ai-ask "what did supervisor say?"
```

## Architecture

```
src/
├── server.py           ← MCP server (stdio + HTTP/SSE), 4 tools
├── ai-send.py          ← inbox write helper
├── ai-msg              ← shell: one-way message
├── ai-ask              ← shell: send + block for reply
├── ai-continue         ← shell: resume sessions by name
├── ai-sessions         ← shell: list all sessions + inbox status
└── ai-crew-register    ← shell: register with OpenCode (HTTP mode)
```

### MCP Tools

| Tool | Description |
|---|---|
| `inbox_send(text, role, priority)` | Write message to project inbox |
| `inbox_poll(project?, limit?)` | Read unread messages (atomic, marks as read) |
| `inbox_reply(msg_id, text)` | Reply to a specific message |
| `inbox_status()` | Summary of all projects, unread counts, config |

### Storage

```
~/.cwinbox/
├── projects.jsonl                 ← registered projects
├── inbox.jsonl                    ← global inbox
└── <project-path>/.cwinbox/
    ├── inbox.jsonl                ← per-project inbox
    ├── archive.jsonl              ← processed messages
    └── replies.jsonl              ← replies
```

All files are JSONL (one JSON object per line). Compatible with SeaShell's format — you can `cat`, `grep`, `jq` them directly.

## Shell Commands

| Command | What it does |
|---|---|
| `ai-msg "text"` | Send a message to the nearest project inbox |
| `ai-msg -r supervisor "text"` | Send as supervisor role |
| `cmd \| ai-msg "context"` | Pipe output as message |
| `ai-ask "question"` | Send + block until reply (300s timeout) |
| `ai-ask -t 60 "urgent?"` | Custom timeout |
| `ai-continue` | Resume latest CodeWhale session |
| `ai-continue myproject` | Fuzzy-match project name, resume |
| `ai-continue --tool opencode` | Resume latest OpenCode session |
| `ai-continue --list` | List all sessions |
| `ai-sessions` | List sessions + inbox status |
| `ai-crew-register` | Register with OpenCode (HTTP MCP) |
| `ai-crew-register --status` | Check registration status |

## Configuration

Create `.ai-crew.json` in any project root:

```json
{
  "supervisor": "codewhale",
  "worker": "opencode",
  "auto_poll_interval": 30
}
```

The MCP server reads this to report the current crew setup. `ai-continue` uses it to auto-detect which backend to resume.

## Worker / Supervisor System Prompts

### Worker prompt

```
You are a WORKER AI. When you encounter:
- Architecture decisions (multiple valid approaches)
- Uncertain API usage
- Task completion (request next steps)

Call inbox_send() to report to your supervisor.
Then call inbox_poll() to wait for guidance before continuing.
```

### Supervisor prompt

```
You are a SUPERVISOR AI. At the start of each turn, call inbox_status().
For each unread worker message, give clear directional guidance.
Do NOT write code yourself — guide the worker to do it.
Be specific: name approaches, list constraints, define interfaces.
```

## End-to-End Test (verified)

```
Session A (Worker #1, codewhale exec):
  → inbox_send: "Strategy pattern vs if/else? 3 payment providers..."
  → inbox_poll: no reply yet

Session B (Supervisor, codewhale TUI):
  → Read archive
  → inbox_reply: "Go with Strategy. 4 guidelines: ..."

Session C (Worker #2, codewhale exec):
  → inbox_poll → received supervisor's reply
  → Reports: "Next: draft PaymentProvider interface for sign-off"
```

Three independent CodeWhale sessions, zero shared state except JSONL files on disk. ✅

## Compatibility

| Client | Transport | Status |
|---|---|---|
| CodeWhale TUI | stdio MCP | ✅ Tested |
| CodeWhale exec (headless) | stdio MCP | ✅ Tested |
| OpenCode | HTTP/SSE MCP (`--port 9876`) | ✅ Ready |

## Troubleshooting

**`codewhale mcp add` says "command not found"**  
Use the absolute path to the venv Python:
```bash
codewhale mcp add ai-crew --command $(pwd)/.venv/bin/python3 --arg $(pwd)/src/server.py
```

**`ai-msg` says "FAILED" or "python3: command not found"**  
The script needs `python3` on PATH. Install it: `brew install python@3.14`.  
Or manually set `PYTHON` in the script to your venv path.

**Shell scripts can't find `ai-send.py`**  
The scripts look for `ai-send.py` next to themselves (same directory). Install them together:
```bash
cp src/ai-{msg,ask,send.py} ~/.local/bin/
```

**`uv` not installed**  
macOS: `brew install uv`  
Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`  
Or use pip: `python3 -m venv .venv && source .venv/bin/activate && pip install mcp`

**OpenCode can't find the MCP server**  
Run `ai-crew-register` to start the HTTP server and register with OpenCode in one step.

## Credits

**Protocol design** — The inbox JSONL format and atomic rename→process→archive pattern are based on [SeaShell](https://github.com/M-Pineapple/seashell) by [Pineapple 🍍](https://github.com/M-Pineapple) (MIT licensed). SeaShell pioneered the inbox-based AI coordination model for terminal environments. If you find AI Crew useful, consider starring SeaShell too.

**Implementation** — All code in this repo is original Python/shell, written for CodeWhale + OpenCode. No Swift or Claude-specific code was ported.

## License

MIT — see [LICENSE](LICENSE).
