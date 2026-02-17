# Tinabot

基于 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) 的本地 AI Agent，极简Python代码，平替openclaw功能，接近claude code/codex的使用体验，支持 CLI 和 Telegram 双接口。

## Why Tinabot?

作为 Claude Code 和 Codex 的重度用户，在使用openclaw过程中，经常遇到任务执行耗时很长、token 消耗很多，但又不知道在做什么，又无法干预的情况。Tinabot 的初衷是做一个代码干净、可定制的openclaw平替，给cc/codex的内核**加一个 IM 界面**，通过 Telegram 随时远程操控。

- **全程可视** — 每一步工具调用（读文件、执行命令、搜索）都实时展示在 CLI 和 Telegram 中，清楚知道 Agent 在做什么、做了多久
- **Token 消耗透明** — 每次交互显示输入/输出 token 数和费用估算（`↑5.2k ⚡40k ↓1.1k · $0.0534`），不再为账单焦虑
- **随时可中断** — 在 Telegram 中发送新消息立即中断当前任务，CLI 中 Ctrl+C 随时退出，不会卡住，中断后仍然保持对话上下文
- **复用现有技能库** — 兼容 Claude Code / Codex / OpenClaw 的 `SKILL.md` 技能文件格式，直接复用 `~/.agents/skills/` 目录下的技能，无需迁移
- **多模型自由切换** — 同一套工具和技能，后端可以是 Claude Opus、OpenAI或兼容模型，CLI 命令一键切换（`tina model set claude`）
- **OpenAI 兼容** — 除原生 OpenAI 外，任何兼容 OpenAI 接口的模型（DeepSeek、Mistral、Ollama、vLLM 等）都可通过 `base_url` 接入
- **Claude / ChatGPT 订阅直接用** — 通过 OAuth 登录（Claude Code 内 `/login` 或 `tina login openai`）直接使用 Claude/ChatGPT 订阅额度，无需额外购买 API key


## 特性

- **按任务记忆** — 每个对话是独立的"任务"，跨消息保持上下文，超过设定轮次自动压缩
- **技能系统** — 从 `~/.agents/skills/*/SKILL.md` 加载，小技能内联 system prompt，大技能按需加载
- **定时任务** — 用自然语言创建（如"每天9点搜reddit发给我"），后台 cron 调度器自动执行并发送到 Telegram
- **语音 & 图片** — Telegram 语音消息自动转写（Groq Whisper），图片多模态识别+本地文件操作
- **完整工具集** — Read、Write、Edit、Bash、Glob、Grep、WebSearch、WebFetch、Task

## 快速开始

```bash
# 克隆并安装
git clone https://github.com/netmsglog/tinabot.git
cd tinabot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 拷贝示例配置
cp config.json.example ~/.tinabot/config.json
```

## 配置：模型 Profile

Tinabot 使用 **Profile（配置档）** 管理模型。每个 Profile 是一个完整的模型配置单元，包含模型名称、provider、认证方式、API key、端点和定价。通过 `active_profile` 指定当前使用的 Profile，切换时一键切换全部配置。

直接编辑 `~/.tinabot/config.json`：

```json
{
  "active_profile": "claude",
  "profiles": {
    "claude": {
      "model": "claude-opus-4-6",
      "provider": "claude",
      "auth": "oauth",
      "input_price": 5.0,
      "output_price": 25.0,
      "cache_read_price": 0.5
    },
    "openai": {
      "model": "gpt-4o",
      "provider": "openai",
      "auth": "oauth",
      "input_price": 2.5,
      "output_price": 10.0,
      "cache_read_price": 0.0
    }
  },
  "agent": { ... },
  "telegram": { ... }
}
```

### Profile 字段说明

| 字段 | 说明 |
|---|---|
| `model` | 模型名称 |
| `provider` | `claude` 或 `openai`（OpenAI 兼容模型也用 `openai`） |
| `auth` | 认证方式：`oauth`（使用订阅）或 `api_key`（使用 API 密钥） |
| `api_key` | `auth` 为 `api_key` 时填写，`oauth` 时留空 |
| `base_url` | 自定义 API 端点（OpenAI 兼容模型），留空使用默认 |
| `input_price` | 输入 token 单价（$/MTok），用于费用估算 |
| `output_price` | 输出 token 单价（$/MTok） |
| `cache_read_price` | 缓存读取 token 单价（$/MTok） |

### Profile 示例

**Claude OAuth**（使用 Claude Code 订阅）：
```json
{
  "model": "claude-opus-4-6",
  "provider": "claude",
  "auth": "oauth",
  "input_price": 5.0,
  "output_price": 25.0,
  "cache_read_price": 0.5
}
```

**OpenAI OAuth**（使用 ChatGPT Plus/Pro 订阅）：
```json
{
  "model": "gpt-4o",
  "provider": "openai",
  "auth": "oauth",
  "input_price": 2.5,
  "output_price": 10.0
}
```

**OpenAI 兼容 API**（DeepSeek、NVIDIA、Ollama 等）：
```json
{
  "model": "deepseek-chat",
  "provider": "openai",
  "auth": "api_key",
  "api_key": "your-key",
  "base_url": "https://api.deepseek.com/v1",
  "input_price": 0.14,
  "output_price": 0.28
}
```

### 认证方式

- **`oauth`** — Claude 使用 Claude Code CLI 的 OAuth session（`claude` → `/login`）；OpenAI 使用 ChatGPT OAuth（`tina login openai`）
- **`api_key`** — 直接在 Profile 中填写 `api_key`

```bash
# Claude OAuth 登录
claude        # 启动 Claude Code CLI
# > /login    # 在 REPL 内登录

# OpenAI OAuth 登录
tina login openai    # 浏览器自动打开授权页面
tina login status    # 查看认证状态
tina login logout    # 登出
```

### 切换 Profile

```bash
tina model list          # 列出所有 Profile
tina model set openai    # 切换到 openai Profile
```

REPL 内：`/models` 列出、`/model openai` 切换（即时生效）。

### 其他配置

`agent` 部分存放与 Profile 无关的执行参数：

| 字段 | 说明 |
|---|---|
| `max_thinking_tokens` | 思考 token 上限（默认 10000） |
| `permission_mode` | Claude 权限模式：`plan`、`acceptEdits`、`bypassPermissions` |
| `cwd` | Agent 工作目录（默认 `~/.tinabot/workspace`） |
| `max_tokens` | 非 Claude 模型的最大输出 token 数 |
| `timeout_seconds` | 单次 Agent 调用超时秒数（默认 3600） |

## CLI 使用

```
tina                # 交互式 REPL（默认）
tina chat           # 同上
tina serve          # 启动 Telegram 机器人
tina tasks          # 列出所有任务
tina skills         # 列出已加载的技能

# Profile 管理
tina model list          # 列出所有 Profile
tina model set <name>    # 切换 Profile

# 认证管理
tina login openai   # OpenAI OAuth 登录
tina login status   # 查看认证状态
tina login logout   # 登出 OAuth

# 用户管理
tina user list      # 查看 Telegram 白名单
tina user add ID    # 添加用户到白名单
tina user del ID    # 从白名单移除用户

# 定时任务
tina schedule list                # 列出所有定时任务
tina schedule add --name "..." --cron "0 9 * * *" --prompt "..." --chat ID
tina schedule del <id>            # 删除定时任务

# 任务管理
tina task list      # 列出所有任务
tina task del ID    # 删除任务
tina task export ID # 导出对话历史
```

REPL 命令：

| 命令 | 说明 |
|---|---|
| `/new [名称]` | 创建新任务 |
| `/tasks` | 列出所有任务 |
| `/resume <id>` | 切换到指定任务 |
| `/compress` | 压缩当前任务上下文 |
| `/delete <id>` | 删除任务 |
| `/export [id]` | 导出对话历史 |
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

### 语音消息

在 Telegram 中发送语音消息，Tina 会通过 Groq Whisper API 自动转写为文字，然后交给 Agent 处理。需要 Groq API key（免费额度足够日常使用）。

```bash
TINABOT_TELEGRAM__GROQ_API_KEY=gsk_xxx tina serve
# 或写入 config.json: { "telegram": { "groq_api_key": "gsk_xxx" } }
```

### 图片消息

发送图片（可附带说明），Tina 会保存图片到 `~/.tinabot/data/images/` 并交给 Agent 处理。Agent 同时收到图片内容和本地文件路径。

### 用户管理

Telegram 机器人需要显式白名单 — 空列表拒绝所有用户。

```bash
tina user add 123456789   # 允许用户
tina user del 123456789   # 移除用户
tina user list            # 查看白名单
```

### 实时进度

Agent 工作时，状态消息实时显示经过时间、思考状态和工具调用：

```
⏳ 15s
🧠 Thinking...
💻 `git status`
📖 Read `config.py`
✏️ Edit `main.py`
```

## 技能

将技能目录放在 `~/.agents/skills/`：

```
~/.agents/skills/
  my-skill/
    SKILL.md      # Markdown 文件，可含 YAML frontmatter
```

Frontmatter 示例：

```yaml
---
name: my-skill
description: 做一些有用的事
allowed-tools: Bash,WebSearch
always: true
---
给 Agent 的指令...
```

## 依赖

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)（`npm install -g @anthropic-ai/claude-code`）— 仅 Claude provider 需要

---

# Tinabot (English)

A local AI agent built on [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with minimal Python code, replacing OpenClaw with a Claude Code/Codex-like experience, supporting both CLI and Telegram interfaces.

## Why Tinabot?

As a heavy user of Claude Code and Codex, I often ran into long-running tasks with [OpenClaw](https://github.com/nicepkg/openclaw) where token costs piled up with no visibility into what the agent was doing and no way to intervene. Tinabot was built as a clean, customizable OpenClaw replacement — **adding an IM interface** to the core of Claude Code/Codex so you can remotely control it via Telegram anytime.

- **Full visibility** — Every tool call (file reads, commands, searches) shown in real-time in CLI and Telegram, so you always know what the agent is doing and for how long
- **Transparent token costs** — Each interaction shows input/output tokens and cost estimate (`↑5.2k ⚡40k ↓1.1k · $0.0534`)
- **Interruptible anytime** — Send a new message in Telegram to interrupt instantly, Ctrl+C in CLI — conversation context is preserved after interruption
- **Reuse existing skills** — Compatible with Claude Code / Codex / OpenClaw `SKILL.md` skill format, reuses `~/.agents/skills/` directly
- **Multi-model switching** — Same tools and skills across Claude Opus, OpenAI, or compatible models — switch with `tina model set claude`
- **OpenAI-compatible** — Beyond native OpenAI, any OpenAI-compatible API (DeepSeek, Mistral, Ollama, vLLM, etc.) works via `base_url`
- **Use Claude / ChatGPT subscriptions directly** — OAuth login (`claude` → `/login` or `tina login openai`) uses your subscription quota, no separate API key needed

## Features

- **Per-Task Memory** — Each conversation is a "task" with cross-message context, auto-compressed when turns exceed limit
- **Skills System** — Loads from `~/.agents/skills/*/SKILL.md`, small skills inlined, large skills loaded on demand
- **Scheduled Tasks** — Create from natural language (e.g. "search reddit daily at 9am"), cron-based background execution with Telegram delivery
- **Voice & Photos** — Telegram voice auto-transcription (Groq Whisper), multimodal image recognition + local file access
- **Full Tool Access** — Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Task

## Quick Start

```bash
git clone https://github.com/netmsglog/tinabot.git
cd tinabot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Copy example config
cp config.json.example ~/.tinabot/config.json
```

## Configuration: Model Profiles

Tinabot uses **Profiles** to manage models. Each profile is a complete model configuration unit containing model name, provider, auth method, API key, endpoint, and pricing. Set `active_profile` to switch all settings atomically.

Edit `~/.tinabot/config.json`:

```json
{
  "active_profile": "claude",
  "profiles": {
    "claude": {
      "model": "claude-opus-4-6",
      "provider": "claude",
      "auth": "oauth",
      "input_price": 5.0,
      "output_price": 25.0,
      "cache_read_price": 0.5
    },
    "openai": {
      "model": "gpt-4o",
      "provider": "openai",
      "auth": "oauth",
      "input_price": 2.5,
      "output_price": 10.0,
      "cache_read_price": 0.0
    }
  },
  "agent": { ... },
  "telegram": { ... }
}
```

### Profile Fields

| Field | Description |
|---|---|
| `model` | Model name |
| `provider` | `claude` or `openai` (OpenAI-compatible models also use `openai`) |
| `auth` | `oauth` (use subscription) or `api_key` (use API key) |
| `api_key` | Required when `auth` is `api_key`, empty for `oauth` |
| `base_url` | Custom API endpoint for OpenAI-compatible models (empty = default) |
| `input_price` | Input token price ($/MTok) for cost estimation |
| `output_price` | Output token price ($/MTok) |
| `cache_read_price` | Cache read token price ($/MTok) |

### Profile Examples

**Claude OAuth** (uses Claude Code subscription):
```json
{
  "model": "claude-opus-4-6",
  "provider": "claude",
  "auth": "oauth",
  "input_price": 5.0,
  "output_price": 25.0,
  "cache_read_price": 0.5
}
```

**OpenAI OAuth** (uses ChatGPT Plus/Pro subscription):
```json
{
  "model": "gpt-4o",
  "provider": "openai",
  "auth": "oauth",
  "input_price": 2.5,
  "output_price": 10.0
}
```

**OpenAI-compatible API** (DeepSeek, NVIDIA, Ollama, etc.):
```json
{
  "model": "deepseek-chat",
  "provider": "openai",
  "auth": "api_key",
  "api_key": "your-key",
  "base_url": "https://api.deepseek.com/v1",
  "input_price": 0.14,
  "output_price": 0.28
}
```

### Auth Setup

- **`oauth`** — Claude uses Claude Code CLI session (`claude` → `/login`); OpenAI uses ChatGPT OAuth (`tina login openai`)
- **`api_key`** — Set `api_key` directly in the profile

```bash
# Claude OAuth
claude        # Start Claude Code CLI
# > /login    # Login inside the REPL

# OpenAI OAuth
tina login openai    # Browser opens automatically
tina login status    # Check auth state
tina login logout    # Clear tokens
```

### Switching Profiles

```bash
tina model list          # List all profiles
tina model set openai    # Switch to openai profile
```

In REPL: `/models` to list, `/model openai` to switch (takes effect immediately).

### Other Settings

The `agent` section holds execution parameters independent of profiles:

| Field | Description |
|---|---|
| `max_thinking_tokens` | Thinking token limit (default 10000) |
| `permission_mode` | Claude permission mode: `plan`, `acceptEdits`, `bypassPermissions` |
| `cwd` | Agent working directory (default `~/.tinabot/workspace`) |
| `max_tokens` | Max output tokens for non-Claude models |
| `timeout_seconds` | Per-call timeout in seconds (default 3600) |

## CLI Usage

```
tina                # Interactive REPL (default)
tina chat           # Same as above
tina serve          # Start Telegram bot
tina tasks          # List all tasks
tina skills         # List loaded skills

# Profile management
tina model list          # List all profiles
tina model set <name>    # Switch profile

# Auth management
tina login openai   # OpenAI OAuth login
tina login status   # Check auth state
tina login logout   # Clear OAuth tokens

# User management
tina user list      # Show Telegram allowlist
tina user add ID    # Add user to allowlist
tina user del ID    # Remove user from allowlist

# Scheduled tasks
tina schedule list                # List all schedules
tina schedule add --name "..." --cron "0 9 * * *" --prompt "..." --chat ID
tina schedule del <id>            # Delete a schedule

# Task management
tina task list      # List all tasks
tina task del ID    # Delete a task
tina task export ID # Export conversation history
```

REPL commands:

| Command | Description |
|---|---|
| `/new [name]` | Create a new task |
| `/tasks` | List all tasks |
| `/resume <id>` | Switch to a task |
| `/compress` | Compress current task context |
| `/delete <id>` | Delete a task |
| `/export [id]` | Export conversation history |
| `/skills` | List loaded skills |
| `/help` | Show help |
| `/exit` | Quit |

## Telegram Bot

1. Create a bot via [@BotFather](https://t.me/BotFather) to get a token
2. Configure and run:

```bash
# Option A: Environment variable
TINABOT_TELEGRAM__TOKEN=your_token tina serve

# Option B: Config file (~/.tinabot/config.json)
{
  "telegram": {
    "enabled": true,
    "token": "your_token",
    "allowed_users": [123456789]
  }
}
```

Each Telegram chat has its own independent task. Bot commands: `/new`, `/tasks`, `/resume`, `/compress`, `/skills`, `/schedules`, `/help`.

### Scheduled Tasks

Tell Tina to create scheduled tasks in natural language:

> "Search reddit for OpenClaw posts every day at 9am and send me a summary"

Tina will automatically create a schedule file. The background scheduler checks every 30 seconds and executes the agent when due, sending results to the Telegram chat.

You can also manage schedules via CLI:

```bash
tina schedule add --name "reddit digest" --cron "0 9 * * *" --prompt "Search reddit for OpenClaw posts and summarize" --chat 123456
tina schedule list
tina schedule del reddit-digest
```

Cron examples: `0 9 * * *` (daily 9am), `*/30 * * * *` (every 30min), `0 9 * * 1-5` (weekdays 9am).

### Voice Messages

Send a voice message in Telegram and Tina will auto-transcribe it via Groq Whisper API, then pass the text to the agent. Requires a Groq API key (free tier is sufficient for daily use).

```bash
TINABOT_TELEGRAM__GROQ_API_KEY=gsk_xxx tina serve
# Or in config.json: { "telegram": { "groq_api_key": "gsk_xxx" } }
```

### Photo Messages

Send a photo (with optional caption) and Tina will save it to `~/.tinabot/data/images/` and pass it to the agent. The agent receives both the image content and the local file path.

### User Management

The Telegram bot requires an explicit allowlist — an empty list rejects all users.

```bash
tina user add 123456789   # Allow user
tina user del 123456789   # Remove user
tina user list            # Show allowlist
```

### Real-time Progress

While the agent works, status messages show elapsed time, thinking state, and tool calls in real-time:

```
⏳ 15s
🧠 Thinking...
💻 `git status`
📖 Read `config.py`
✏️ Edit `main.py`
```

## Skills

Place skill directories in `~/.agents/skills/`:

```
~/.agents/skills/
  my-skill/
    SKILL.md      # Markdown file, may include YAML frontmatter
```

Frontmatter example:

```yaml
---
name: my-skill
description: Do something useful
allowed-tools: Bash,WebSearch
always: true
---
Instructions for the agent...
```

## Requirements

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`npm install -g @anthropic-ai/claude-code`) — only needed for Claude provider
