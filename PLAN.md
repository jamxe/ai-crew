# AI Crew — Inbox-based AI-to-AI Coordination

> **灵感来源**: [SeaShell](https://github.com/M-Pineapple/seashell) 的 inbox 协议  
> **目标环境**: CodeWhale TUI + OpenCode + Wave Terminal (macOS)  
> **核心价值**: 让多个 AI 会话通过共享 inbox 协作——worker 干活、supervisor 把关

## 1. 目标

构建一个 MCP server，让 CodeWhale 和 OpenCode 的 AI 会话可以通过文件系统的 inbox 互相发送消息、回复、协调工作。不依赖任何特定 AI 客户端，纯 JSONL 协议。

### 1.1 典型使用场景

```
场景 A: 大任务分解监督
  Worker (OpenCode YOLO)  →  重构代码，遇到架构决策
    → inbox_send("Payment module: Strategy vs Factory?")  
  Supervisor (CodeWhale)  →  poll inbox, 分析方案
    → inbox_reply("选 Strategy，但注意 LSP 原则")
  Worker  →  读回复，继续干活

场景 B: 便宜模型干活，贵模型 review
  Worker (DeepSeek V3)  →  批量生成代码 → inbox
  Supervisor (V4-Pro)   →  code review → inbox_reply

场景 C: 跨天上下文延续
  Day 1: Worker 干了 2 小时，所有决策点写入 inbox
  Day 2: Supervisor 打开，poll inbox 了解昨天进展
```

### 1.2 非目标

- ❌ 不是 Claude Desktop/Claude Code 适配器（那是 SeaShell 的事）
- ❌ 不是 Wave Terminal 深度集成（不做 widgets/presets 管理）
- ❌ 不是实时通信（是 poll-based 异步消息）

## 2. 架构

```
┌──────────────────────────────────────────────────────────┐
│                      Wave Terminal                        │
│                                                           │
│  Block 1: Supervisor               Block 2: Worker        │
│  ┌─────────────────────┐          ┌─────────────────────┐ │
│  │ CodeWhale TUI        │          │ CodeWhale exec       │ │
│  │ (或 OpenCode TUI)    │          │ (或 OpenCode run)    │ │
│  │ role: supervisor     │          │ role: worker         │ │
│  └──────────┬───────────┘          └──────────┬──────────┘ │
│             │                                  │            │
│             │   ┌──────────────────────────────┘            │
│             ▼   ▼  (MCP 协议 — 各自独立的 server 进程)       │
│  ┌──────────────────────────────────────────────┐          │
│  │          cw-inbox MCP Server (Python)         │          │
│  │          无状态，纯文件 IO                     │          │
│  │                                               │          │
│  │  tools: send / poll / reply / status           │          │
│  │  transport: stdio (CodeWhale) + HTTP (OpenCode)│          │
│  └──────────────────────┬───────────────────────┘          │
│                         │                                   │
│  ~/.cwinbox/            │  (JSONL, 与 SeaShell 协议兼容)     │
│  ├── projects.jsonl     │                                   │
│  ├── inbox.jsonl        │                                   │
│  └── <project>/.cwinbox/│                                   │
│      ├── inbox.jsonl    │                                   │
│      ├── archive.jsonl  │                                   │
│      └── replies.jsonl  │                                   │
└─────────────────────────┴──────────────────────────────────┘
```

### 2.1 为什么是文件系统而不是 socket/DB

- **零依赖**: JSONL 文件，`echo >>` 就能写
- **跨进程天然共享**: 不同 MCP server 实例读同一份文件
- **与 SeaShell 协议兼容**: 未来可以无缝切换
- **人类可调试**: `cat ~/.cwinbox/myapp/inbox.jsonl` 就能看消息

## 3. 组件拆分

### Phase 1: MCP Server (`src/server.py`)

**文件**: `src/server.py` (~250 行)  
**依赖**: Python 3.10+, `mcp` pip 包  
**Transport**: stdio + HTTP (SSE)

#### Tool 定义

| Tool | 参数 | 返回值 | 说明 |
|---|---|---|---|
| `inbox_send` | `text: str`, `role: str`, `priority: int` | `{id, ts, cwd}` | 往当前项目 inbox 写消息 |
| `inbox_poll` | `project: str?`, `limit: int?` | `[{id, ts, text, role, reply_to?}]` | 读未读消息（自动标记已读） |
| `inbox_reply` | `msg_id: str`, `text: str` | `{id, ts}` | 回复某条消息 |
| `inbox_status` | 无 | `{projects, unread_counts, config}` | 全局状态摘要 |

#### 存储格式（对齐 SeaShell）

```jsonl
// inbox.jsonl — 每条消息一行 JSON
{"id":"uuid","ts":"ISO8601","cwd":"/path","hostname":"mac","text":"...","role":"worker","priority":3,"read":false}

// replies.jsonl — 回复
{"message_id":"uuid","ts":"ISO8601","text":"...","role":"supervisor"}

// projects.jsonl — 项目注册表  
{"path":"/Users/.../myapp","name":"myapp","added_at":"ISO8601"}

// archive.jsonl — 已读消息归档
{"id":"uuid","ts":"ISO8601",...,"read":true,"archived_at":"ISO8601"}
```

#### Project config (可选)

```jsonc
// <project>/.ai-crew.json
{
  "supervisor": "codewhale",  // 或 "opencode"
  "worker": "opencode",       // 或 "codewhale"
  "auto_poll_interval": 30    // supervisor 自动轮询间隔（秒）
}
```

### Phase 2: MCP 注册

#### CodeWhale

```bash
codewhale mcp add ai-crew \
  --command python3 \
  --arg ~/ai-crew/src/server.py
```

#### OpenCode

```bash
# 先启动 HTTP server
python3 src/server.py --port 9876 &

# 注册
opencode mcp add ai-crew --url http://localhost:9876/sse
```

### Phase 3: Shell 命令 (`src/ai-*`)

| 命令 | 功能 | 等价 SeaShell |
|---|---|---|
| `ai-msg "text"` | 往 inbox 写消息 | `seashell-msg` |
| `ai-ask "question"` | 写消息 + 轮询等回复（阻塞） | `seashell-ask` |
| `ai-continue [project]` | 恢复项目最近会话 | `hey continue with` |
| `ai-sessions [project]` | 列出会话 | `seashell-sessions` |
| `ai-crew [project]` | 启动 worker + supervisor | 无等价 |

### Phase 4: System Prompts（Worker/Supervisor 行为指令）

```markdown
# Worker system prompt 注入
你是 worker。遇到以下情况时，必须调用 inbox_send() 向 supervisor 报告：
- 架构决策（多方案选型）
- 不确定的 API 使用
- 完成任务后请求下一步指令
调用 inbox_send() 后，调用 inbox_poll() 等待回复再继续。

# Supervisor system prompt 注入
你是 supervisor。每轮对话开始时调用 inbox_status() 检查 inbox。
对每条未读 worker 消息给出明确的方向性指导。
不直接写代码——指导 worker 去做。
```

## 4. 验证方式

### Phase 1 验证

| # | 测试 | 通过标准 |
|---|---|---|
| V1 | `inbox_send("hello")` → 检查 inbox.jsonl | 文件存在，包含完整 JSON 记录 |
| V2 | `inbox_send("msg2")` + `inbox_poll()` | 返回两条未读消息 |
| V3 | 再次 `inbox_poll()` | 返回空（已标记已读） |
| V4 | `inbox_reply(msg_id, "reply")` | replies.jsonl 有回复记录 |
| V5 | `inbox_status()` | 返回项目计数、未读数量 |
| V6 | 模拟另一个进程写入 → poll | 跨进程消息可见 |

### Phase 2 验证

| # | 测试 | 通过标准 |
|---|---|---|
| V7 | `codewhale mcp tools` 列出 ai-crew tools | 4 个 tool 出现在列表中 |
| V8 | 在 CodeWhale 会话中调 `inbox_send` | tool call 成功，文件写入 |
| V9 | 在 CodeWhale 会话中调 `inbox_poll` | 返回刚才写入的消息 |

### Phase 3 验证

| # | 测试 | 通过标准 |
|---|---|---|
| V10 | `ai-msg "test from shell"` | 终端输出确认，文件可读 |
| V11 | `echo "pipe test" \| ai-msg "piped"` | pipe 内容附加到消息 |
| V12 | `ai-continue myproject` | 恢复最近 CodeWhale 会话 |

### Phase 4 验证（终验）

| # | 测试 | 通过标准 |
|---|---|---|
| V13 | Worker (OpenCode) 发消息到 inbox | 消息出现在 inbox.jsonl |
| V14 | Supervisor (CodeWhale) poll 并回复 | 回复出现在 replies.jsonl |
| V15 | Worker poll 看到 supervisor 回复 | Worker 根据回复调整行为 |

## 5. 验收目标

**MVP 验收（Phase 1-2 完成）**:
- [ ] `server.py` 4 个 tool 全部可用
- [ ] CodeWhale 会话能调用 inbox tools
- [ ] 文件存储在 `~/.cwinbox/`，格式与 SeaShell 兼容

**完整验收（Phase 1-4 完成）**:
- [ ] Worker (OpenCode) ↔ Supervisor (CodeWhale) 完成一次往返对话
- [ ] Worker 遇到决策点 → 发 inbox → 等回复 → 继续
- [ ] 跨天恢复：关闭所有会话，重新打开 supervisor，能读到历史消息
- [ ] Shell 命令 `ai-msg` / `ai-ask` 可从终端直接使用

## 6. 文件清单

```
CascadeProjects/ai-crew/
├── PLAN.md              ← 本文档
├── src/
│   ├── server.py         ← MCP server (Phase 1)
│   ├── ai-msg            ← shell: 写 inbox (Phase 3)
│   ├── ai-ask            ← shell: 写 + 轮询 (Phase 3)
│   ├── ai-continue       ← shell: 恢复会话 (Phase 3)
│   ├── ai-sessions       ← shell: 会话列表 (Phase 3)
│   └── ai-crew           ← shell: 一键启动 (Phase 3)
├── tests/
│   ├── test_server.py    ← server 单元测试
│   └── test_integration.sh ← 端到端集成测试
└── docs/
    ├── worker-prompt.md  ← Worker system prompt
    └── supervisor-prompt.md ← Supervisor system prompt
```

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| OpenCode MCP 不支持 stdio | 需要 HTTP transport | server.py 同时支持 stdio + HTTP，Phase 1 就做 |
| CodeWhale sessions 格式变化 | `ai-continue` 失效 | 用 `codewhale sessions` CLI 而非直接读文件 |
| 并发写 inbox.jsonl | 消息丢失/损坏 | Python `fcntl.flock` 文件锁；或接受最终一致性 |
| 两个 MCP server 实例竞争 | archive 冲突 | atomic rename 模式（rename→process→delete），与 SeaShell 一致 |
