"""Core agent wrapping claude-agent-sdk (Claude) and openai SDK (others)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from loguru import logger

from tinabot.config import AgentConfig
from tinabot.memory import Task, TaskMemory
from tinabot.skills import SkillsLoader

# Callback types for streaming output
OnText = Callable[[str], Awaitable[None] | None]
OnThinking = Callable[[str], Awaitable[None] | None]
OnTool = Callable[[str, dict[str, Any]], Awaitable[None] | None]


IDENTITY_PROMPT_BASE = """\
You are Tina, a capable AI agent running on the user's local machine. \
You have direct access to tools and MUST use them proactively to accomplish tasks. \
Be direct, concise, and action-oriented.

## Key Behaviors
- **Be proactive**: Execute commands directly. Don't ask "would you like me to..." — just do it.
- **Don't ask for confirmation** unless the action is destructive or irreversible.
- **Error recovery**: If a command fails, try an alternative approach. Don't give up and ask the user.
- **Respond in the user's language**: If the user writes in Chinese, respond in Chinese.
- **Image analysis**: When you receive an image, analyze it directly from the visual content.
- **Apple Notes**: Use `osascript -e 'tell application "Notes" to ...'` via Bash.

When you have skills available, use them to guide your approach. \
You can read skill files for detailed instructions when needed.
"""

# Claude SDK handles tool descriptions and orchestration, so minimal prompt suffices.
IDENTITY_PROMPT = IDENTITY_PROMPT_BASE

# OpenAI-compatible models need explicit tool docs since we run our own tool loop.
IDENTITY_PROMPT_OPENAI = IDENTITY_PROMPT_BASE + """
## Platform
- Running on macOS (Darwin)
- You can execute any shell command via the Bash tool
- You have full access to the local filesystem

## Tools
You have the following tools — USE THEM, don't ask the user to run commands manually:
- **Bash**: Execute any shell command (Unix commands, osascript, open, git, package managers)
- **Read**: Read file contents with line numbers
- **Write**: Write/overwrite files
- **Edit**: Edit files with precise string replacements
- **Glob**: Find files by pattern (e.g. "**/*.py")
- **Grep**: Search file contents with regex
- **WebFetch**: Fetch and process URL content
- **WebSearch**: Search the web for information
"""

SCHEDULING_PROMPT_TEMPLATE = """\
## Scheduling & Reminders

IMPORTANT: For ANY request involving time — reminders, delayed tasks, scheduled tasks, recurring tasks — \
you MUST use the schedule system below. NEVER use sleep, cron CLI, at, or osascript for timing.

Create a schedule file at: ~/.tinabot/data/schedules/<short-name>.json

Format:
{{
  "name": "Human-readable description",
  "cron": "minute hour day month weekday",
  "prompt": "The message or task to execute when triggered",
  "chat_id": {chat_id},
  "enabled": true,
  "once": false,
  "created_at": "<current ISO timestamp>"
}}

Fields:
- cron: Standard cron expression (server local time). Examples: "41 12 * * *" (12:41 daily), "0 9 * * *" (9am daily), "*/30 * * * *" (every 30min)
- once: Set to true for one-time reminders (auto-deleted after execution). Set to false for recurring tasks.
- prompt: What to send to the user when triggered. For reminders, just put the reminder text directly.

One-time reminder example ("12点41分提醒我喝水"):
{{
  "name": "喝水提醒",
  "cron": "41 12 * * *",
  "prompt": "提醒：该喝水了！💧",
  "chat_id": {chat_id},
  "enabled": true,
  "once": true,
  "created_at": "<current ISO timestamp>"
}}

Recurring example ("每天9点搜reddit"):
{{
  "name": "Reddit摘要",
  "cron": "0 9 * * *",
  "prompt": "Search reddit for openclaw posts and summarize the findings",
  "chat_id": {chat_id},
  "enabled": true,
  "once": false,
  "created_at": "<current ISO timestamp>"
}}

ALWAYS use `date` to check the current time first, then construct the correct cron expression.
To delete a schedule, delete the file. To list schedules, read ~/.tinabot/data/schedules/.
Always confirm to the user what was created and when it will trigger.
"""

COMPRESSION_PROMPT = """\
Summarize our conversation so far, capturing:
1. What was originally requested
2. Key decisions made
3. Current state of the work
4. Any pending items or next steps

Be concise but preserve all important context needed to continue this work."""


@dataclass
class ImageInput:
    """An image to include in a message to the agent."""

    data: str  # base64-encoded image data
    media_type: str  # e.g. "image/jpeg", "image/png"


MODEL_PRICING: dict[str, tuple[float, float]] = {
    # model_prefix -> (input_$/MTok, output_$/MTok)
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
}

MODEL_PRICING_OPENAI: dict[str, tuple[float, float]] = {
    # model -> (input_$/MTok, output_$/MTok)
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-5.2": (0.0, 0.0),
    "o3": (2.0, 8.0),
    "o4-mini": (1.1, 4.4),
}

# Unified model registry: model -> (provider, input_$/MTok, output_$/MTok)
KNOWN_MODELS: dict[str, tuple[str, float, float]] = {
    # Claude
    "claude-opus-4-6": ("claude", 5.0, 25.0),
    "claude-sonnet-4-5-20250929": ("claude", 3.0, 15.0),
    "claude-haiku-4-5-20251001": ("claude", 1.0, 5.0),
    # OpenAI
    "gpt-4o": ("openai", 2.5, 10.0),
    "gpt-4o-mini": ("openai", 0.15, 0.6),
    "gpt-4.1": ("openai", 2.0, 8.0),
    "gpt-5.2": ("openai", 0.0, 0.0),
    "o3": ("openai", 2.0, 8.0),
    "o4-mini": ("openai", 1.1, 4.4),
}


def get_known_models() -> dict[str, tuple[str, float, float]]:
    """Return the known models registry."""
    return KNOWN_MODELS


def infer_provider(model: str) -> str | None:
    """Infer provider from model name. Returns None if unknown.

    Only Claude and OpenAI are natively supported. Other OpenAI-compatible
    providers (DeepSeek, Mistral, local LLMs, etc.) should use provider="openai"
    with a custom base_url in config.
    """
    if model in KNOWN_MODELS:
        return KNOWN_MODELS[model][0]
    # Fallback heuristics
    if model.startswith("claude-"):
        return "claude"
    if model.startswith(("gpt-", "o3", "o4", "o1")):
        return "openai"
    # Unknown models default to OpenAI-compatible
    return "openai"


@dataclass
class AgentResponse:
    """Response from an agent interaction."""

    text: str = ""
    session_id: str | None = None
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    thinking: str = ""
    num_turns: int = 0
    tool_uses: list[str] = field(default_factory=list)


class TinaAgent:
    """Multi-provider agent with skills and memory integration.

    Claude: uses claude-agent-sdk with full agent capabilities.
    OpenAI/Gemini/Grok: uses openai SDK with our own tool-calling loop.

    Session management (Claude):
    - Task has session_id and no summary -> resume session (SDK restores context)
    - Task has summary (was compressed) -> fresh session with summary in system_prompt
    - New task -> fresh session, capture session_id from init message

    Session management (non-Claude):
    - Message history stored per-task via MessageStore
    """

    def __init__(
        self,
        config: AgentConfig,
        skills_loader: SkillsLoader,
        task_memory: TaskMemory,
    ):
        self.config = config
        self.skills = skills_loader
        self.memory = task_memory

        # Lazy-init for non-Claude providers
        self._openai_agent = None
        self._message_store = None
        self._openai_auth = None  # OAuth for ChatGPT backend
        self._use_codex = False   # True when using OAuth Responses API

        if not config.is_claude:
            from tinabot.message_store import MessageStore
            from tinabot.openai_agent import OpenAIAgent

            self._message_store = MessageStore(
                Path(self.memory.data_dir)
            )

            if config.provider == "openai" and not config.api_key:
                # No API key — try OAuth tokens
                from tinabot.openai_auth import OpenAIAuth

                self._openai_auth = OpenAIAuth()
                if self._openai_auth.is_logged_in:
                    self._use_codex = True
                    self._openai_agent = OpenAIAgent(
                        config, self._message_store, auth=self._openai_auth
                    )
                else:
                    logger.warning(
                        "OpenAI provider with no api_key and no OAuth tokens. "
                        "Run: tina login openai"
                    )
                    self._openai_agent = OpenAIAgent(config, self._message_store)
            else:
                self._openai_agent = OpenAIAgent(config, self._message_store)

    def reinit(self, config: AgentConfig):
        """Reinitialize agent with new config (e.g. after model switch)."""
        self.config = config
        self._openai_agent = None
        self._message_store = None
        self._openai_auth = None
        self._use_codex = False

        if not config.is_claude:
            from tinabot.message_store import MessageStore
            from tinabot.openai_agent import OpenAIAgent

            self._message_store = MessageStore(
                Path(self.memory.data_dir)
            )

            if config.provider == "openai" and not config.api_key:
                from tinabot.openai_auth import OpenAIAuth

                self._openai_auth = OpenAIAuth()
                if self._openai_auth.is_logged_in:
                    self._use_codex = True
                    self._openai_agent = OpenAIAgent(
                        config, self._message_store, auth=self._openai_auth
                    )
                else:
                    logger.warning(
                        "OpenAI provider with no api_key and no OAuth tokens. "
                        "Run: tina login openai"
                    )
                    self._openai_agent = OpenAIAgent(config, self._message_store)
            else:
                self._openai_agent = OpenAIAgent(config, self._message_store)

    def _build_system_prompt(
        self, task: Task, chat_id: int | None = None
    ) -> str:
        """Build the full system prompt with identity, skills, and task context."""
        identity = IDENTITY_PROMPT if self.config.is_claude else IDENTITY_PROMPT_OPENAI
        parts = [identity]

        # Add skills section
        skills_section = self.skills.build_system_prompt_section()
        if skills_section:
            parts.append(skills_section)

        # Add scheduling instructions when chat_id is available
        if chat_id is not None:
            parts.append(SCHEDULING_PROMPT_TEMPLATE.format(chat_id=chat_id))

        # When session can't be resumed (compressed or lost), inject context
        if not (task.session_id and task.summary is None):
            context_parts: list[str] = []

            summary = self.memory.get_summary(task.id)
            if summary:
                context_parts.append(
                    "## Conversation Summary\n" + summary
                )

            last_resp = self.memory.get_last_response(task.id)
            if last_resp:
                context_parts.append(
                    "## Your Last Response (for reference)\n" + last_resp
                )

            if context_parts:
                parts.append(
                    "<previous-context>\n"
                    "This is a continuation of a previous conversation. "
                    "The session history is not available, but here is "
                    "what we know:\n\n"
                    + "\n\n".join(context_parts)
                    + "\n</previous-context>"
                )

        return "\n\n".join(parts)

    def _build_options(
        self,
        task: Task,
        chat_id: int | None = None,
        no_thinking: bool = False,
    ):
        """Build SDK options for a Claude query."""
        from claude_agent_sdk import ClaudeAgentOptions
        # Merge skill-provided tools with config tools
        all_tools = list(self.config.allowed_tools)
        skill_tools = self.skills.get_all_allowed_tools()
        for tool in skill_tools:
            if tool not in all_tools:
                all_tools.append(tool)

        system_prompt = self._build_system_prompt(task, chat_id=chat_id)

        # Determine resume behavior
        resume = None
        if task.session_id and task.summary is None:
            resume = task.session_id

        # Pass API key to CLI subprocess if configured
        env = {}
        if self.config.api_key:
            env["ANTHROPIC_API_KEY"] = self.config.api_key

        cwd = Path(self.config.cwd).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)

        thinking_tokens = 0 if no_thinking else self.config.max_thinking_tokens

        return ClaudeAgentOptions(
            model=self.config.model,
            max_thinking_tokens=thinking_tokens,
            system_prompt=system_prompt,
            allowed_tools=all_tools,
            permission_mode=self.config.permission_mode,
            cwd=str(cwd),
            resume=resume,
            env=env,
        )

    def _estimate_cost(self, response: AgentResponse) -> float:
        """Estimate cost from token counts using known model pricing."""
        in_price, out_price = 5.0, 25.0  # default to Opus pricing
        for prefix, (ip, op) in MODEL_PRICING.items():
            if self.config.model.startswith(prefix):
                in_price, out_price = ip, op
                break
        # cache reads are 10% of input price, cache writes are 125%
        cost = (
            response.input_tokens * in_price
            + response.cache_read_tokens * in_price * 0.1
            + response.cache_creation_tokens * in_price * 1.25
            + response.output_tokens * out_price
        ) / 1_000_000
        return cost

    @staticmethod
    def _make_multimodal_prompt(
        message: str, images: list[ImageInput]
    ) -> AsyncIterator[dict[str, Any]]:
        """Build an AsyncIterable prompt with text + image content blocks."""

        async def _gen() -> AsyncIterator[dict[str, Any]]:
            content: list[dict[str, Any]] = []
            for img in images:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.media_type,
                            "data": img.data,
                        },
                    }
                )
            content.append({"type": "text", "text": message})
            yield {
                "type": "user",
                "session_id": "",
                "message": {"role": "user", "content": content},
                "parent_tool_use_id": None,
            }

        return _gen()

    async def process(
        self,
        message: str,
        task: Task | None = None,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
        on_tool: OnTool | None = None,
        chat_id: int | None = None,
        images: list[ImageInput] | None = None,
        no_thinking: bool = False,
    ) -> AgentResponse:
        """Process a user message through the agent.

        Args:
            message: The user's input message.
            task: Task to use. If None, uses active or creates new.
            on_text: Callback for text output chunks.
            on_thinking: Callback for thinking output.
            on_tool: Callback for tool use events (name, input).
            chat_id: Telegram chat ID (enables scheduling instructions).
            images: Optional list of images to include in the message.

        Returns:
            AgentResponse with text, session info, and cost.
        """
        # Resolve task
        if task is None:
            task = self.memory.get_active_task()
        if task is None:
            task = self.memory.create_task(message[:80])

        # Route non-Claude providers to OpenAI agent
        if not self.config.is_claude:
            return await self._process_openai(
                message=message,
                task=task,
                on_text=on_text,
                on_thinking=on_thinking,
                on_tool=on_tool,
                chat_id=chat_id,
                images=images,
            )

        options = self._build_options(task, chat_id=chat_id, no_thinking=no_thinking)
        logger.info(
            f"process task={task.id} session={task.session_id} "
            f"resume={'yes' if options.resume else 'no'} "
            f"summary={'yes' if task.summary else 'no'} "
            f"turns={task.turn_count}"
        )
        response = AgentResponse()
        text_parts: list[str] = []

        # Build prompt: multimodal if images present, plain string otherwise
        prompt: str | AsyncIterator[dict[str, Any]] = message
        if images:
            prompt = self._make_multimodal_prompt(message, images)

        from claude_agent_sdk import (
            query,
            AssistantMessage,
            SystemMessage,
            ResultMessage,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
        )

        timeout = self.config.timeout_seconds or None
        try:
            async with asyncio.timeout(timeout):
                async for msg in query(prompt=prompt, options=options):
                    if isinstance(msg, SystemMessage):
                        if msg.subtype == "init":
                            session_id = msg.data.get("session_id")
                            if session_id:
                                response.session_id = session_id
                                self.memory.update_session_id(
                                    task.id, session_id
                                )
                                logger.debug(
                                    f"Session init: task={task.id} "
                                    f"session={session_id}"
                                )

                    elif isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                text_parts.append(block.text)
                                if on_text:
                                    result = on_text(block.text)
                                    if hasattr(result, "__await__"):
                                        await result

                            elif isinstance(block, ThinkingBlock):
                                response.thinking += block.thinking
                                if on_thinking:
                                    result = on_thinking(block.thinking)
                                    if hasattr(result, "__await__"):
                                        await result

                            elif isinstance(block, ToolUseBlock):
                                response.tool_uses.append(block.name)
                                if on_tool:
                                    result = on_tool(block.name, block.input)
                                    if hasattr(result, "__await__"):
                                        await result

                    elif isinstance(msg, ResultMessage):
                        response.session_id = msg.session_id
                        response.cost_usd = msg.total_cost_usd
                        response.num_turns = msg.num_turns
                        if msg.usage:
                            logger.debug(f"Usage: {msg.usage}")
                            response.input_tokens = msg.usage.get(
                                "input_tokens", 0
                            )
                            response.output_tokens = msg.usage.get(
                                "output_tokens", 0
                            )
                            response.cache_read_tokens = msg.usage.get(
                                "cache_read_input_tokens", 0
                            )
                            response.cache_creation_tokens = msg.usage.get(
                                "cache_creation_input_tokens", 0
                            )
                        if response.cost_usd is None and (
                            response.input_tokens or response.output_tokens
                        ):
                            response.cost_usd = self._estimate_cost(response)
                        self.memory.update_session_id(
                            task.id, msg.session_id
                        )

        except TimeoutError:
            logger.warning(f"Agent timed out after {timeout}s for task {task.id}")
            text_parts.append(
                f"Request timed out after {timeout}s. "
                "You can retry or send a simpler request."
            )
        except Exception as e:
            logger.error(f"Agent error: {e}")
            text_parts.append(f"Error: {e}")

        response.text = "\n".join(text_parts) if text_parts else ""

        # Save last response as safety net (survives session loss / compression)
        if response.text:
            self.memory.save_last_response(task.id, response.text)

        # Update turn count (auto-compression disabled — use /compress manually)
        self.memory.increment_turns(task.id)

        return response

    def _estimate_cost_openai(self, response: AgentResponse) -> float:
        """Estimate cost for non-Claude models."""
        in_price, out_price = 2.5, 10.0  # default to gpt-4o pricing
        model = self.config.model
        for name, (ip, op) in MODEL_PRICING_OPENAI.items():
            if model == name or model.startswith(name):
                in_price, out_price = ip, op
                break
        return (
            response.input_tokens * in_price
            + response.output_tokens * out_price
        ) / 1_000_000

    async def _process_openai(
        self,
        message: str,
        task: Task,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
        on_tool: OnTool | None = None,
        chat_id: int | None = None,
        images: list[ImageInput] | None = None,
    ) -> AgentResponse:
        """Process a message via OpenAI-compatible provider."""
        system_prompt = self._build_system_prompt(task, chat_id=chat_id)

        mode = "codex" if self._use_codex else "api"
        logger.info(
            f"process_openai task={task.id} mode={mode} "
            f"provider={self.config.provider} model={self.config.model} "
            f"turns={task.turn_count}"
        )

        response = AgentResponse()

        # Convert ImageInput objects to dicts for the OpenAI agent
        img_dicts = None
        if images:
            img_dicts = [
                {"data": img.data, "media_type": img.media_type}
                for img in images
            ]

        timeout = self.config.timeout_seconds or None
        try:
            async with asyncio.timeout(timeout):
                if self._use_codex:
                    # OAuth path: use Responses API via ChatGPT backend
                    result = await self._openai_agent.run_codex(
                        task_id=task.id,
                        user_message=message,
                        system_prompt=system_prompt,
                        on_text=on_text,
                        on_thinking=on_thinking,
                        on_tool=on_tool,
                        images=img_dicts,
                    )
                else:
                    # Standard API key path
                    result = await self._openai_agent.run(
                        task_id=task.id,
                        user_message=message,
                        system_prompt=system_prompt,
                        on_text=on_text,
                        on_thinking=on_thinking,
                        on_tool=on_tool,
                        images=img_dicts,
                    )

            response.text = result.text
            response.input_tokens = result.input_tokens
            response.output_tokens = result.output_tokens
            response.num_turns = result.num_turns
            response.tool_uses = result.tool_uses

            if self._use_codex:
                # ChatGPT subscription — no per-token cost
                response.cost_usd = 0.0
            else:
                response.cost_usd = self._estimate_cost_openai(response)

        except TimeoutError:
            logger.warning(f"Agent timed out after {timeout}s for task {task.id}")
            response.text = (
                f"Request timed out after {timeout}s. "
                "You can retry or send a simpler request."
            )
        except Exception as e:
            logger.error(f"OpenAI agent error: {e}")
            response.text = f"Error: {e}"

        # Save last response
        if response.text:
            self.memory.save_last_response(task.id, response.text)

        self.memory.increment_turns(task.id)

        return response

    async def _compress_task_claude(self, task: Task):
        """Compress a Claude task by asking the agent (via resumed session) to summarize."""
        from claude_agent_sdk import (
            query,
            ClaudeAgentOptions,
            AssistantMessage,
            TextBlock,
        )

        if not task.session_id:
            logger.warning(f"Cannot compress task {task.id}: no session_id")
            return

        logger.info(f"Compressing task {task.id} ({task.turn_count} turns)")

        env = {}
        if self.config.api_key:
            env["ANTHROPIC_API_KEY"] = self.config.api_key

        cwd = Path(self.config.cwd).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)

        try:
            options = ClaudeAgentOptions(
                model=self.config.model,
                max_thinking_tokens=0,
                resume=task.session_id,
                max_turns=1,
                permission_mode="plan",
                cwd=str(cwd),
                env=env,
            )

            summary_parts: list[str] = []
            async for msg in query(prompt=COMPRESSION_PROMPT, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            summary_parts.append(block.text)

            if summary_parts:
                summary = "\n".join(summary_parts)
                self.memory.save_summary(task.id, summary)
                logger.info(f"Task {task.id} compressed ({len(summary)} chars)")
            else:
                logger.warning(f"Compression returned empty for task {task.id}")

        except Exception as e:
            logger.error(f"Compression failed for task {task.id}: {e}")

    async def _compress_task_openai(self, task: Task):
        """Compress a non-Claude task by asking the model to summarize message history."""
        logger.info(f"Compressing task {task.id} ({task.turn_count} turns)")

        try:
            result = await self._openai_agent.run(
                task_id=task.id,
                user_message=COMPRESSION_PROMPT,
                system_prompt="You are a helpful assistant. Summarize the conversation.",
            )

            if result.text:
                self.memory.save_summary(task.id, result.text)
                # Clear message history since we've summarized
                self._message_store.clear(task.id)
                logger.info(f"Task {task.id} compressed ({len(result.text)} chars)")
            else:
                logger.warning(f"Compression returned empty for task {task.id}")

        except Exception as e:
            logger.error(f"Compression failed for task {task.id}: {e}")

    async def force_compress(self, task: Task) -> str | None:
        """Force compress a task regardless of turn count."""
        if self.config.is_claude:
            if not task.session_id:
                return None
            await self._compress_task_claude(task)
        else:
            # Non-Claude: compress if we have message history
            msgs = self._message_store.get_messages(task.id)
            if len(msgs) <= 1:  # Only system message or empty
                return None
            await self._compress_task_openai(task)
        return self.memory.get_summary(task.id)
