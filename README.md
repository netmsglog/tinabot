# Tinabot

基于 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) 的本地 AI Agent，支持 CLI 和 Telegram 双接口。

## Why Tinabot?

[OpenClaw](https://github.com/anthropics/claude-code) (Claude Code) 是目前体验最好的本地 AI Agent，但它是闭源商业产品。Tinabot 是一个开源平替，目标是用纯 Python 实现同等体验：

- **全程可视** — 每一步工具调用（读文件、执行命令、搜索）都实时展示在 CLI 和 Telegram 中，清楚知道 Agent 在做什么、做了多久
- **Token 消耗透明** — 每次交互显示输入/输出 token 数和费用估算（`↑5.2k ⚡40k ↓1.1k · $0.0534`），不再为账单焦虑
- **随时可中断** — 在 Telegram 中发送新消息立即中断当前任务，CLI 中 Ctrl+C 随时退出，不会卡住
- **复用现有技能库** — 兼容 Claude Code / Codex / OpenClaw 的 `SKILL.md` 技能文件格式，直接复用 `~/.agents/skills/` 目录下的技能，无需迁移
- **多模型自由切换** — 同一套工具和技能，后端可以是 Claude Opus、GPT-4o、o3、Gemini、Grok，随时在 config 里切换
- **ChatGPT 订阅直接用** — 通过 OAuth 登录（`tina login openai`）直接使用 ChatGPT Plus/Pro 订阅额度，无需额外购买 API key
- **Telegram 随身用** — 不在电脑前也能通过 Telegram 让 Agent 执行任务、定时任务、语音指令

简单来说：**Claude Code 的丝滑体验 + 开源 + 多模型 + Telegram 移动端**。

支持多种模型后端：**Claude**（通过 Agent SDK）、**OpenAI**（API key 或 ChatGPT OAuth 登录）、**Gemini**、**Grok**。

## 特性

- **多模型支持** — Claude Opus/Sonnet、GPT-4o/o3/o4-mini（API key 或 ChatGPT Plus/Pro 订阅 OAuth）、Gemini、Grok
- **Claude 深度思考** — 使用 Claude 的 extended thinking 能力进行深层推理
- **双接口** — 交互式 CLI（rich markdown 渲染）+ Telegram 机器人
- **Telegram 实时进度** — Agent 工作时，状态消息实时更新，展示思考状态和每一步工具调用，完成后替换为最终回复
- **按任务记忆** — 每个对话是独立的"任务"，跨消息保持上下文
- **自动压缩** — 任务超过设定轮次后，自动总结对话并开启新 session，控制 token 开销
- **技能系统** — 从 `~/.agents/skills/*/SKILL.md` 加载技能定义。小技能内联到 system prompt，大技能按需加载
- **定时任务** — 用自然语言创建定时任务（如"每天9点搜reddit发给我"），后台 cron 调度器自动执行并将结果发送到 Telegram
- **语音消息** — 在 Telegram 发送语音，通过 Groq Whisper API 自动转写为文字后交给 Agent 处理
- **图片消息** — 发送图片附带指令，图片保存到本地并以多模态内容+文件路径发送给 Agent
- **完整工具集** — Read、Write、Edit、Bash、Glob、Grep、WebSearch、WebFetch、Task

## 快速开始

```bash
# 克隆并安装
git clone https://github.com/netmsglog/tinabot.git
cd tinabot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 认证配置

Tinabot 支持多种认证方式，按 provider 选择：

### Claude（默认）

```bash
# 方式 A：通过 Claude Code CLI OAuth 登录
claude login

# 方式 B：API key
export ANTHROPIC_API_KEY=sk-ant-...
# 或写入 config.json: "agent": { "api_key": "sk-ant-..." }
```

### OpenAI — ChatGPT OAuth 登录（推荐）

使用你的 ChatGPT Plus/Pro 订阅，无需单独创建 API key：

```bash
# 1. 登录（浏览器会自动打开 OpenAI 授权页面）
tina login openai

# 2. 切换 provider 和模型
# 编辑 ~/.tinabot/config.json:
{
  "agent": {
    "provider": "openai",
    "model": "o3"
  }
}

# 3. 开始使用
tina
```

支持的模型：`gpt-4o`、`gpt-4o-mini`、`gpt-4.1`、`o3`、`o4-mini` 等 ChatGPT 订阅可用的模型。

OAuth token 自动刷新，无需手动管理。

```bash
# 查看登录状态
tina login status

# 登出
tina login logout
```

### OpenAI — API Key

```bash
# 编辑 ~/.tinabot/config.json:
{
  "agent": {
    "provider": "openai",
    "api_key": "sk-...",
    "model": "gpt-4o"
  }
}
```

### Gemini / Grok

```bash
# Gemini
{
  "agent": {
    "provider": "gemini",
    "api_key": "your-gemini-key",
    "model": "gemini-2.5-pro"
  }
}

# Grok
{
  "agent": {
    "provider": "grok",
    "api_key": "your-grok-key",
    "model": "grok-3"
  }
}
```

## 配置

配置从 `~/.tinabot/config.json` 加载，可用 `TINABOT_*` 环境变量覆盖（嵌套用 `__` 分隔）。

```json
{
  "agent": {
    "provider": "claude",
    "model": "claude-opus-4-6",
    "max_thinking_tokens": 10000,
    "permission_mode": "acceptEdits",
    "cwd": "~/.tinabot/workspace",
    "api_key": "",
    "base_url": "",
    "max_tokens": 16384,
    "timeout_seconds": 300
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

| 字段 | 说明 |
|---|---|
| `provider` | `claude`、`openai`、`gemini`、`grok` |
| `model` | 模型名称 |
| `api_key` | API key（Claude/OpenAI/Gemini/Grok）。OpenAI 留空则使用 OAuth |
| `base_url` | 自定义 API 端点（留空自动根据 provider 解析） |
| `max_tokens` | 非 Claude 模型的最大输出 token 数 |
| `timeout_seconds` | 单次 Agent 调用超时（0=无限制） |
| `permission_mode` | Claude 权限模式：`plan`、`acceptEdits`、`bypassPermissions` |

## CLI 使用

```
tina                # 交互式 REPL（默认）
tina chat           # 同上
tina serve          # 启动 Telegram 机器人
tina tasks          # 列出所有任务
tina skills         # 列出已加载的技能

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

A local AI agent powered by [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with CLI and Telegram interfaces.

## Why Tinabot?

[OpenClaw](https://github.com/anthropics/claude-code) (Claude Code) is the best local AI agent experience today, but it's a closed-source commercial product. Tinabot is an open-source alternative aiming for the same experience in pure Python:

- **Full visibility** — Every tool call (file reads, commands, searches) shown in real-time in CLI and Telegram
- **Transparent token costs** — Each interaction shows input/output tokens and cost estimate (`↑5.2k ⚡40k ↓1.1k · $0.0534`)
- **Interruptible anytime** — Send a new message in Telegram to interrupt instantly, Ctrl+C in CLI
- **Reuse existing skills** — Compatible with Claude Code / Codex / OpenClaw `SKILL.md` skill format, reuses `~/.agents/skills/` directly
- **Multi-model freedom** — Same tools and skills across Claude Opus, GPT-4o, o3, Gemini, Grok — switch in config
- **ChatGPT subscription support** — OAuth login (`tina login openai`) uses your ChatGPT Plus/Pro subscription directly, no separate API key needed
- **Telegram on the go** — Run agent tasks, schedules, and voice commands from your phone

In short: **Claude Code experience + open source + multi-model + Telegram mobile**.

Supports multiple model backends: **Claude** (via Agent SDK), **OpenAI** (API key or ChatGPT OAuth login), **Gemini**, **Grok**.

## Features

- **Multi-model support** — Claude Opus/Sonnet, GPT-4o/o3/o4-mini (API key or ChatGPT Plus/Pro subscription OAuth), Gemini, Grok
- **Claude Extended Thinking** — Uses Claude's thinking capability for deeper reasoning
- **Dual Interface** — Interactive CLI (rich markdown rendering) + Telegram bot
- **Live Progress on Telegram** — Real-time status updates showing thinking state and tool calls
- **Per-Task Memory** — Each conversation is a "task" with context maintained across messages
- **Auto-Compression** — Summarizes conversations when they exceed a turn limit
- **Skills System** — Loads skill definitions from `~/.agents/skills/*/SKILL.md`
- **Scheduled Tasks** — Create recurring tasks from natural language
- **Voice Messages** — Automatic transcription via Groq Whisper API
- **Photo Messages** — Multimodal image processing with local file access
- **Full Tool Access** — Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Task

## Quick Start

```bash
git clone https://github.com/netmsglog/tinabot.git
cd tinabot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Authentication

### Claude (default)

```bash
claude login                        # OAuth login via Claude Code CLI
# or
export ANTHROPIC_API_KEY=sk-ant-... # API key
```

### OpenAI — ChatGPT OAuth (recommended)

Use your ChatGPT Plus/Pro subscription without creating a separate API key:

```bash
# 1. Login (browser opens automatically)
tina login openai

# 2. Set provider and model in ~/.tinabot/config.json:
{
  "agent": {
    "provider": "openai",
    "model": "o3"
  }
}

# 3. Start chatting
tina
```

Models: `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o3`, `o4-mini`, etc.

```bash
tina login status   # Check auth state
tina login logout   # Clear OAuth tokens
```

### OpenAI — API Key

```json
{
  "agent": {
    "provider": "openai",
    "api_key": "sk-...",
    "model": "gpt-4o"
  }
}
```

### Gemini / Grok

```json
{
  "agent": {
    "provider": "gemini",
    "api_key": "your-key",
    "model": "gemini-2.5-pro"
  }
}
```

## CLI Usage

```
tina                # Interactive REPL (default)
tina serve          # Start Telegram bot
tina login openai   # OpenAI OAuth login
tina login status   # Show auth state
tina login logout   # Clear OAuth tokens
tina tasks          # List all tasks
tina skills         # List loaded skills
tina user list/add/del   # Manage Telegram allowlist
tina schedule list/add/del   # Manage scheduled tasks
tina task list/del/export    # Manage tasks
```

## Requirements

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`npm install -g @anthropic-ai/claude-code`) — only needed for Claude provider
