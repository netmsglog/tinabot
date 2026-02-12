"""Core agent wrapping claude-agent-sdk for execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from loguru import logger

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from tinabot.config import AgentConfig
from tinabot.memory import Task, TaskMemory
from tinabot.skills import SkillsLoader

# Callback types for streaming output
OnText = Callable[[str], Awaitable[None] | None]
OnThinking = Callable[[str], Awaitable[None] | None]
OnTool = Callable[[str, dict[str, Any]], Awaitable[None] | None]


IDENTITY_PROMPT = """\
You are Tina, a capable AI assistant. You help users accomplish tasks \
using the tools available to you. Be direct, concise, and helpful.

When you have skills available, use them to guide your approach. \
You can read skill files for detailed instructions when needed.
"""

COMPRESSION_PROMPT = """\
Summarize our conversation so far, capturing:
1. What was originally requested
2. Key decisions made
3. Current state of the work
4. Any pending items or next steps

Be concise but preserve all important context needed to continue this work."""


@dataclass
class AgentResponse:
    """Response from an agent interaction."""

    text: str = ""
    session_id: str | None = None
    cost_usd: float | None = None
    thinking: str = ""
    num_turns: int = 0
    tool_uses: list[str] = field(default_factory=list)


class TinaAgent:
    """Wraps claude-agent-sdk query() with skills and memory integration.

    Session management:
    - Task has session_id and no summary -> resume session (SDK restores context)
    - Task has summary (was compressed) -> fresh session with summary in system_prompt
    - New task -> fresh session, capture session_id from init message
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

    def _build_system_prompt(self, task: Task) -> str:
        """Build the full system prompt with identity, skills, and task context."""
        parts = [IDENTITY_PROMPT]

        # Add skills section
        skills_section = self.skills.build_system_prompt_section()
        if skills_section:
            parts.append(skills_section)

        # Add task summary if this is a compressed task resuming
        summary = self.memory.get_summary(task.id)
        if summary:
            parts.append(
                "<previous-context>\n"
                "This is a continuation of a previous conversation. "
                "Here is a summary of what happened:\n\n"
                f"{summary}\n"
                "</previous-context>"
            )

        return "\n\n".join(parts)

    def _build_options(self, task: Task) -> ClaudeAgentOptions:
        """Build SDK options for a query."""
        # Merge skill-provided tools with config tools
        all_tools = list(self.config.allowed_tools)
        skill_tools = self.skills.get_all_allowed_tools()
        for tool in skill_tools:
            if tool not in all_tools:
                all_tools.append(tool)

        system_prompt = self._build_system_prompt(task)

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

        return ClaudeAgentOptions(
            model=self.config.model,
            max_thinking_tokens=self.config.max_thinking_tokens,
            system_prompt=system_prompt,
            allowed_tools=all_tools,
            permission_mode=self.config.permission_mode,
            cwd=str(cwd),
            resume=resume,
            env=env,
        )

    async def process(
        self,
        message: str,
        task: Task | None = None,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
        on_tool: OnTool | None = None,
    ) -> AgentResponse:
        """Process a user message through the agent.

        Args:
            message: The user's input message.
            task: Task to use. If None, uses active or creates new.
            on_text: Callback for text output chunks.
            on_thinking: Callback for thinking output.
            on_tool: Callback for tool use events (name, input).

        Returns:
            AgentResponse with text, session info, and cost.
        """
        # Resolve task
        if task is None:
            task = self.memory.get_active_task()
        if task is None:
            task = self.memory.create_task(message[:80])

        options = self._build_options(task)
        response = AgentResponse()
        text_parts: list[str] = []

        try:
            async for msg in query(prompt=message, options=options):
                if isinstance(msg, SystemMessage):
                    if msg.subtype == "init":
                        session_id = msg.data.get("session_id")
                        if session_id:
                            response.session_id = session_id
                            self.memory.update_session_id(task.id, session_id)

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
                    self.memory.update_session_id(task.id, msg.session_id)

        except Exception as e:
            logger.error(f"Agent error: {e}")
            text_parts.append(f"Error: {e}")

        response.text = "\n".join(text_parts) if text_parts else ""

        # Update turn count and check compression
        turns = self.memory.increment_turns(task.id)
        if self.memory.needs_compression(task):
            await self._compress_task(task)

        return response

    async def _compress_task(self, task: Task):
        """Compress a task's conversation by summarizing it."""
        logger.info(f"Compressing task {task.id} ({task.turn_count} turns)")

        try:
            options = ClaudeAgentOptions(
                model=self.config.model,
                resume=task.session_id,
                max_turns=1,
                permission_mode="plan",  # Read-only for summarization
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

        except Exception as e:
            logger.error(f"Compression failed for task {task.id}: {e}")

    async def force_compress(self, task: Task) -> str | None:
        """Force compress a task regardless of turn count."""
        if not task.session_id:
            return None
        await self._compress_task(task)
        return self.memory.get_summary(task.id)
