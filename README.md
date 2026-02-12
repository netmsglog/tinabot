# Tinabot

A local AI agent powered by [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with CLI and Telegram interfaces.

## Features

- **Claude Opus with Extended Thinking** — Uses Claude's thinking capability for deeper reasoning on complex tasks
- **Dual Interface** — Interactive CLI (rich markdown rendering) + Telegram bot
- **Live Progress on Telegram** — Real-time status message showing thinking state and each tool call as it happens, edited in-place (then replaced by the final response)
- **Per-Task Memory** — Each conversation is a "task" with its own session. Context is maintained across messages via SDK session resumption
- **Auto-Compression** — When a task exceeds a configurable turn count, the conversation is summarized and a fresh session starts with the summary injected, bounding token costs
- **Skills System** — Loads skill definitions from `~/.agents/skills/*/SKILL.md`. Small skills are inlined in the system prompt; large ones use progressive loading (agent reads the full file when needed)
- **Scheduled Tasks** — Create recurring tasks from natural language (e.g. "每天9点搜reddit发给我"). Cron-based scheduler runs in background, executes the agent, and delivers results to Telegram
- **Voice Messages** — Send voice messages in Telegram; automatically transcribed to text via Groq Whisper API, then processed by the agent
- **Photo Messages** — Send photos with instructions; images are saved locally and sent to the agent with both visual content and file path, so the agent can see and manipulate the file
- **Full Tool Access** — Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Task — the same tools available in Claude Code

## Quick Start

```bash
# Clone and install
git clone https://github.com/netmsglog/tinabot.git
cd tinabot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Authenticate (pick one)
claude login                        # OAuth login via Claude Code CLI
# or
export ANTHROPIC_API_KEY=sk-ant-... # API key

# Start chatting
tina
```

## CLI Usage

```
tina              # Interactive REPL (default)
tina chat         # Same as above
tina serve        # Start Telegram bot
tina tasks        # List all tasks
tina skills       # List loaded skills
tina user list    # Show Telegram allowlist
tina user add ID  # Add a user to the allowlist
tina user del ID  # Remove a user from the allowlist
tina schedule list              # List all schedules
tina schedule add --name "..." --cron "0 9 * * *" --prompt "..." --chat ID
tina schedule del <id>          # Remove a schedule
```

REPL commands:

| Command | Description |
|---|---|
| `/new [name]` | Create a new task |
| `/tasks` | List all tasks |
| `/resume <id>` | Switch to a task |
| `/compress` | Compress current task context |
| `/skills` | List loaded skills |
| `/help` | Show commands |
| `/exit` | Quit |

## Telegram Bot

1. Create a bot via [@BotFather](https://t.me/BotFather) and get the token
2. Configure and run:

```bash
# Option A: environment variable
TINABOT_TELEGRAM__TOKEN=your_token tina serve

# Option B: config file (~/.tinabot/config.json)
{
  "telegram": {
    "enabled": true,
    "token": "your_token",
    "allowed_users": [123456789]
  }
}
```

Each Telegram chat gets its own isolated task. Bot commands: `/new`, `/tasks`, `/resume`, `/compress`, `/skills`, `/schedules`, `/help`.

### Scheduled Tasks

Tell Tina to create a recurring task in natural language:

> "每天早上9点去reddit搜集关于openclaw的帖子，汇总发给我"

Tina will create a schedule file automatically. The background scheduler checks every 30 seconds for due tasks, runs the agent, and delivers results to the Telegram chat.

You can also manage schedules via CLI:

```bash
tina schedule add --name "reddit-digest" --cron "0 9 * * *" --prompt "Search reddit for openclaw posts and summarize" --chat 123456
tina schedule list
tina schedule del reddit-digest
```

Cron examples: `0 9 * * *` (daily 9am), `*/30 * * * *` (every 30min), `0 9 * * 1-5` (weekdays 9am).

Schedules are stored as JSON files in `~/.tinabot/data/schedules/` and persist across restarts. Use `/schedules` in Telegram to list schedules for the current chat.

### Voice Messages

Send a voice message in Telegram and Tina will automatically transcribe it using the Groq Whisper API, then process the text. Requires a Groq API key (free tier is sufficient).

```bash
# Configure via environment variable
TINABOT_TELEGRAM__GROQ_API_KEY=gsk_xxx tina serve

# Or in config.json
{ "telegram": { "groq_api_key": "gsk_xxx" } }
```

Flow:
1. Send a voice message
2. Bot immediately shows "Transcribing voice..."
3. Transcription replaces the hint: `🎙 your transcribed text`
4. Agent processes the text and replies

The transcribed text is also shown in the live status message while the agent works.

### Photo Messages

Send a photo (with or without a caption) and Tina will process it with the agent. Photos are saved to `~/.tinabot/data/images/` so the agent can reference the file on disk.

Flow:
1. Send a photo with caption "Save this to Apple Notes as hat" → Bot confirms: "📷 Image saved / Request: Save this to... / Reply OK to confirm"
2. Send a photo without caption → Bot asks: "📷 Image saved / What would you like to do with this image?"
3. Reply with OK (or new instructions) → Agent receives both the visual content and the local file path

### User Management

The Telegram bot requires an explicit allowlist — an empty list denies all users. When a denied user messages the bot, they see their user ID with instructions:

```
You are not authorized.
Your user ID: 123456789

Ask the admin to run:
tina user add 123456789
```

Manage the allowlist from CLI:

```bash
tina user add 123456789   # Allow a user
tina user del 123456789   # Revoke access
tina user list            # Show current allowlist
```

Changes are written to `~/.tinabot/config.json`. Restart `tina serve` to apply.

### Live Progress

While the agent works, a status message updates in real-time showing elapsed time, thinking state, and tool calls:

```
⏳ 15s
🧠 Thinking...
💻 `git status`
📖 Read `config.py`
✏️ Edit `main.py`
```

The status message is deleted and replaced by the final response when done. If a task takes too long, simply send a new message to interrupt and start a new request immediately.

## Configuration

Config is loaded from `~/.tinabot/config.json` and can be overridden with `TINABOT_*` environment variables (nested with `__`).

```json
{
  "agent": {
    "model": "claude-opus-4-6",
    "max_thinking_tokens": 10000,
    "permission_mode": "acceptEdits",
    "cwd": "~/.tinabot/workspace",
    "api_key": ""
  },
  "telegram": {
    "enabled": false,
    "token": "",
    "allowed_users": [],
    "groq_api_key": ""
  },
  "memory": {
    "data_dir": "~/.tinabot/data",
    "compress_after_turns": 20
  },
  "skills": {
    "skills_dir": "~/.agents/skills"
  }
}
```

## Skills

Place skill directories in `~/.agents/skills/`:

```
~/.agents/skills/
  my-skill/
    SKILL.md      # Markdown with optional YAML frontmatter
```

Frontmatter example:

```yaml
---
name: my-skill
description: Does something useful
allowed-tools: Bash,WebSearch
always: true
---
Instructions for the agent...
```

## Requirements

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed (`npm install -g @anthropic-ai/claude-code`)

---

# Tinabot

基于 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) 的本地 AI Agent，支持 CLI 和 Telegram 双接口。

## 特性

- **Claude Opus 深度思考** — 使用 Claude 的 extended thinking 能力进行深层推理
- **双接口** — 交互式 CLI（rich markdown 渲染）+ Telegram 机器人
- **Telegram 实时进度** — Agent 工作时，状态消息实时更新，展示思考状态和每一步工具调用，完成后替换为最终回复
- **按任务记忆** — 每个对话是独立的"任务"，通过 SDK session 恢复保持上下文
- **自动压缩** — 任务超过设定轮次后，自动总结对话并开启新 session（摘要注入 system prompt），控制 token 开销
- **技能系统** — 从 `~/.agents/skills/*/SKILL.md` 加载技能定义。小技能内联到 system prompt，大技能按需加载
- **定时任务** — 用自然语言创建定时任务（如"每天9点搜reddit发给我"），后台 cron 调度器自动执行并将结果发送到 Telegram
- **语音消息** — 在 Telegram 发送语音，通过 Groq Whisper API 自动转写为文字后交给 Agent 处理
- **图片消息** — 发送图片附带指令，图片保存到本地并以多模态内容+文件路径发送给 Agent，Agent 既能看到图片也能操作文件
- **完整工具集** — Read、Write、Edit、Bash、Glob、Grep、WebSearch、WebFetch、Task — 与 Claude Code 相同的工具

## 快速开始

```bash
# 克隆并安装
git clone https://github.com/netmsglog/tinabot.git
cd tinabot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 认证（任选一种）
claude login                        # 通过 Claude Code CLI OAuth 登录
# 或
export ANTHROPIC_API_KEY=sk-ant-... # API key

# 开始聊天
tina
```

## CLI 使用

```
tina              # 交互式 REPL（默认）
tina chat         # 同上
tina serve        # 启动 Telegram 机器人
tina tasks        # 列出所有任务
tina skills       # 列出已加载的技能
tina user list    # 查看 Telegram 白名单
tina user add ID  # 添加用户到白名单
tina user del ID  # 从白名单移除用户
tina schedule list              # 列出所有定时任务
tina schedule add --name "..." --cron "0 9 * * *" --prompt "..." --chat ID
tina schedule del <id>          # 删除定时任务
```

REPL 命令：

| 命令 | 说明 |
|---|---|
| `/new [名称]` | 创建新任务 |
| `/tasks` | 列出所有任务 |
| `/resume <id>` | 切换到指定任务 |
| `/compress` | 压缩当前任务上下文 |
| `/skills` | 列出已加载技能 |
| `/help` | 显示帮助 |
| `/exit` | 退出 |

## Telegram 机器人

1. 通过 [@BotFather](https://t.me/BotFather) 创建机器人获取 token
2. 配置并运行：

```bash
# 方式 A：环境变量
TINABOT_TELEGRAM__TOKEN=your_token tina serve

# 方式 B：配置文件 (~/.tinabot/config.json)
{
  "telegram": {
    "enabled": true,
    "token": "your_token",
    "allowed_users": [123456789]
  }
}
```

每个 Telegram 聊天拥有独立的任务。机器人命令：`/new`、`/tasks`、`/resume`、`/compress`、`/skills`、`/schedules`、`/help`。

### 定时任务

用自然语言告诉 Tina 创建定时任务：

> "每天早上9点去reddit搜集关于openclaw的帖子，汇总发给我"

Tina 会自动创建定时任务文件。后台调度器每 30 秒检查一次，到时间后执行 Agent 并将结果发送到 Telegram 聊天。

也可以通过 CLI 管理：

```bash
tina schedule add --name "reddit摘要" --cron "0 9 * * *" --prompt "搜索reddit上关于openclaw的帖子并汇总" --chat 123456
tina schedule list
tina schedule del reddit-digest
```

Cron 示例：`0 9 * * *`（每天9点）、`*/30 * * * *`（每30分钟）、`0 9 * * 1-5`（工作日9点）。

定时任务以 JSON 文件存储在 `~/.tinabot/data/schedules/`，重启后自动恢复。在 Telegram 中使用 `/schedules` 查看当前聊天的定时任务。

### 语音消息

在 Telegram 中发送语音消息，Tina 会通过 Groq Whisper API 自动转写为文字，然后交给 Agent 处理。需要 Groq API key（免费额度足够日常使用）。

```bash
# 环境变量配置
TINABOT_TELEGRAM__GROQ_API_KEY=gsk_xxx tina serve

# 或写入 config.json
{ "telegram": { "groq_api_key": "gsk_xxx" } }
```

流程：
1. 发送语音消息
2. 立即显示 `🎤 Transcribing voice...`
3. 转写完成后更新为 `🎙 转写的文字内容`
4. Agent 处理文字并回复

处理过程中，状态消息也会显示转写内容，方便确认识别是否正确。

### 图片消息

发送图片（可附带说明），Tina 会保存图片到 `~/.tinabot/data/images/` 并交给 Agent 处理。Agent 同时收到图片内容和本地文件路径，既能看到图片也能操作文件。

流程：
1. 发送带说明的图片（如"把这张图存到 Apple Notes"）→ 确认提示：`📷 Image saved / Request: ... / Reply OK to confirm`
2. 发送不带说明的图片 → 询问：`📷 Image saved / What would you like to do with this image?`
3. 回复 OK（或输入新指令）→ Agent 开始处理

### 用户管理

Telegram 机器人需要显式白名单 — 空列表拒绝所有用户。被拒用户发消息时会看到自己的 ID 和添加指引：

```
You are not authorized.
Your user ID: 123456789

Ask the admin to run:
tina user add 123456789
```

通过 CLI 管理白名单：

```bash
tina user add 123456789   # 允许用户
tina user del 123456789   # 移除用户
tina user list            # 查看白名单
```

修改后重启 `tina serve` 生效。

### 实时进度

Agent 工作时，状态消息实时显示经过时间、思考状态和工具调用：

```
⏳ 15s
🧠 Thinking...
💻 `git status`
📖 Read `config.py`
✏️ Edit `main.py`
```

完成后状态消息被删除，替换为最终回复。任务执行时间过长时，直接发送新消息即可中断当前任务，立即处理新请求。

## 配置

配置从 `~/.tinabot/config.json` 加载，可用 `TINABOT_*` 环境变量覆盖（嵌套用 `__` 分隔）。

完整配置示例见上方英文部分。

## 技能

将技能目录放在 `~/.agents/skills/`：

```
~/.agents/skills/
  my-skill/
    SKILL.md      # Markdown 文件，可含 YAML frontmatter
```

## 依赖

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)（`npm install -g @anthropic-ai/claude-code`）
