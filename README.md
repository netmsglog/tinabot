# Tinabot

基于 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) 的本地 AI Agent，精简代码量，平替openclaw功能，接近claude code/codex的使用体验，支持 CLI 和 Telegram 双接口。

## Why Tinabot?

作为 Claude Code 和 Codex 的重度用户，在使用openclaw过程中，经常遇到任务执行耗时很长、token 消耗很多，但又不知道在做什么的情况。Tinabot 的初衷是做一个代码干净、可定制的openclaw平替，给cc/codex**加一个 IM 界面**，通过 Telegram 随时远程操控。

- **全程可视** — 每一步工具调用（读文件、执行命令、搜索）都实时展示在 CLI 和 Telegram 中，清楚知道 Agent 在做什么、做了多久
- **Token 消耗透明** — 每次交互显示输入/输出 token 数和费用估算（`↑5.2k ⚡40k ↓1.1k · $0.0534`），不再为账单焦虑
- **随时可中断** — 在 Telegram 中发送新消息立即中断当前任务，CLI 中 Ctrl+C 随时退出，不会卡住
- **复用现有技能库** — 兼容 Claude Code / Codex / OpenClaw 的 `SKILL.md` 技能文件格式，直接复用 `~/.agents/skills/` 目录下的技能，无需迁移
- **多模型自由切换** — 同一套工具和技能，后端可以是 Claude Opus、GPT-4o、o3 等，CLI 命令或 REPL 内一键切换（`tina model set o3` 或 `/model o3`）
- **OpenAI 兼容** — 除原生 OpenAI 外，任何兼容 OpenAI 接口的模型（DeepSeek、Mistral、Ollama、vLLM 等）都可通过 `base_url` 接入
- **Claude / ChatGPT 订阅直接用** — 通过 OAuth 登录（Claude Code 内 `/login` 或 `tina login openai`）直接使用 Claude/ChatGPT 订阅额度，无需额外购买 API key

简单来说：**给 Claude Code / Codex 加上 Telegram 遥控 + 多模型支持**。

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
```

## 认证配置

Tinabot 支持多种认证方式，按 provider 选择：

### Claude（默认）

```bash
# 方式 A：通过 Claude Code CLI OAuth 登录
# 启动 claude，然后在 REPL 内输入 /login
claude
# > /login

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

### OpenAI 兼容模型（DeepSeek、Mistral、Ollama 等）

任何提供 OpenAI 兼容接口的模型都可以使用，只需设置 `base_url` 指向对应端点：

```json
{
  "agent": {
    "provider": "openai",
    "api_key": "your-key",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1"
  }
}
```

更多示例：
- Ollama 本地：`"base_url": "http://localhost:11434/v1"`
- vLLM：`"base_url": "http://localhost:8000/v1"`
- Azure OpenAI：`"base_url": "https://your-resource.openai.azure.com/openai/deployments/your-deployment/"`

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
| `provider` | `claude` 或 `openai`（OpenAI 兼容模型也用 `openai`） |
| `model` | 模型名称 |
| `api_key` | API key（Claude 或 OpenAI）。OpenAI 留空则使用 OAuth |
| `base_url` | 自定义 API 端点，用于 OpenAI 兼容模型（留空默认 OpenAI 官方） |
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

# 模型管理
tina model list     # 列出所有已知模型和定价
tina model set o3   # 切换模型（自动检测 provider，写入 config）

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
| `/models` | 列出可用模型和定价 |
| `/model [名称]` | 查看或切换当前模型（会话内即时生效） |
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

As a heavy user of Claude Code and Codex, I often ran into long-running tasks with [OpenClaw](https://github.com/nicepkg/openclaw) where token costs piled up with no visibility into what the agent was actually doing. Tinabot was built to **add an IM interface** so you can remotely control your Agent via Telegram from anywhere.

On top of that, Tinabot implements a complete agent experience in pure Python:

- **Telegram remote control** — Run agent tasks, schedules, and voice commands from your phone, anytime, anywhere
- **Full visibility** — Every tool call (file reads, commands, searches) shown in real-time in CLI and Telegram
- **Transparent token costs** — Each interaction shows input/output tokens and cost estimate (`↑5.2k ⚡40k ↓1.1k · $0.0534`)
- **Interruptible anytime** — Send a new message in Telegram to interrupt instantly, Ctrl+C in CLI
- **Reuse existing skills** — Compatible with Claude Code / Codex / OpenClaw `SKILL.md` skill format, reuses `~/.agents/skills/` directly
- **Multi-model freedom** — Same tools and skills across Claude Opus, GPT-4o, o3, etc. — switch with `tina model set o3` or `/model o3` in REPL
- **OpenAI-compatible** — Beyond native OpenAI, any OpenAI-compatible API (DeepSeek, Mistral, Ollama, vLLM, etc.) works via `base_url`
- **ChatGPT subscription support** — OAuth login (`tina login openai`) uses your ChatGPT Plus/Pro subscription directly, no separate API key needed

In short: **Telegram remote control for Claude Code / Codex + multi-model support**.

## Features

- **Multi-model** — Claude Opus/Sonnet, GPT-4o/o3/o4-mini, via API key or ChatGPT OAuth; any OpenAI-compatible API supported
- **Dual Interface** — Interactive CLI (rich markdown rendering) + Telegram bot
- **Per-Task Memory** — Each conversation is a "task" with cross-message context, auto-compressed when turns exceed limit
- **Skills System** — Loads from `~/.agents/skills/*/SKILL.md`, small skills inlined, large skills loaded on demand
- **Scheduled Tasks** — Create from natural language (e.g. "search reddit daily at 9am"), cron-based background execution
- **Voice & Photos** — Telegram voice auto-transcription (Groq Whisper), multimodal image recognition + local file access
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
# Option A: OAuth via Claude Code CLI (run claude, then /login inside REPL)
claude
# > /login

# Option B: API key
export ANTHROPIC_API_KEY=sk-ant-...
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

### OpenAI-Compatible Models (DeepSeek, Mistral, Ollama, etc.)

Any model with an OpenAI-compatible API works — just set `base_url`:

```json
{
  "agent": {
    "provider": "openai",
    "api_key": "your-key",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1"
  }
}
```

More examples: Ollama local `"http://localhost:11434/v1"`, vLLM `"http://localhost:8000/v1"`.

## CLI Usage

```
tina                     # Interactive REPL (default)
tina serve               # Start Telegram bot
tina model list          # List known models with pricing
tina model set o3        # Switch model (auto-detects provider, persists to config)
tina login openai        # OpenAI OAuth login
tina login status        # Show auth state
tina login logout        # Clear OAuth tokens
tina tasks               # List all tasks
tina skills              # List loaded skills
tina user list/add/del   # Manage Telegram allowlist
tina schedule list/add/del   # Manage scheduled tasks
tina task list/del/export    # Manage tasks
```

## Requirements

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`npm install -g @anthropic-ai/claude-code`) — only needed for Claude provider
