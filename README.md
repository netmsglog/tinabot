# Tinabot

A local AI agent powered by [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with CLI and Telegram interfaces.

## Features

- **Claude Opus with Extended Thinking** — Uses Claude's thinking capability for deeper reasoning on complex tasks
- **Dual Interface** — Interactive CLI (rich markdown rendering) + Telegram bot
- **Live Progress on Telegram** — Real-time status message showing thinking state and each tool call as it happens, edited in-place (then replaced by the final response)
- **Per-Task Memory** — Each conversation is a "task" with its own session. Context is maintained across messages via SDK session resumption
- **Auto-Compression** — When a task exceeds a configurable turn count, the conversation is summarized and a fresh session starts with the summary injected, bounding token costs
- **Skills System** — Loads skill definitions from `~/.agents/skills/*/SKILL.md`. Small skills are inlined in the system prompt; large ones use progressive loading (agent reads the full file when needed)
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

Each Telegram chat gets its own isolated task. Bot commands: `/new`, `/tasks`, `/resume`, `/compress`, `/skills`, `/help`.

**Live progress:** While the agent works, a status message updates in real-time showing thinking and tool calls:

```
🧠 Thinking...
💻 `git status`
📖 Read `config.py`
✏️ Edit `main.py`
```

The status message is deleted and replaced by the final response when done.

## Configuration

Config is loaded from `~/.tinabot/config.json` and can be overridden with `TINABOT_*` environment variables (nested with `__`).

```json
{
  "agent": {
    "model": "claude-opus-4-6",
    "max_thinking_tokens": 10000,
    "permission_mode": "acceptEdits",
    "cwd": "~",
    "api_key": ""
  },
  "telegram": {
    "enabled": false,
    "token": "",
    "allowed_users": []
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

每个 Telegram 聊天拥有独立的任务。机器人命令：`/new`、`/tasks`、`/resume`、`/compress`、`/skills`、`/help`。

**实时进度：** Agent 工作时，状态消息实时显示思考和工具调用过程：

```
🧠 Thinking...
💻 `git status`
📖 Read `config.py`
✏️ Edit `main.py`
```

完成后状态消息被删除，替换为最终回复。

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
