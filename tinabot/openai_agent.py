"""Streaming agent loop for OpenAI-compatible providers.

Implements the agentic pattern:
1. Send messages with tool schemas
2. Stream response, accumulate text + tool_calls
3. If tool_calls: execute each, append results, loop (max 25 iterations)
4. If text only: done

Works with OpenAI, Gemini, and Grok via their OpenAI-compatible endpoints.

Also supports the ChatGPT Backend Responses API (via OAuth login) through
run_codex() / _stream_responses(), which uses httpx directly against
chatgpt.com/backend-api/codex/responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, TYPE_CHECKING

import httpx
from loguru import logger
from openai import AsyncOpenAI

from tinabot.config import AgentConfig
from tinabot.message_store import MessageStore
from tinabot.tools import get_tool_schemas, get_codex_tool_schemas, execute_tool

if TYPE_CHECKING:
    from tinabot.openai_auth import OpenAIAuth

# Callback types (same as agent.py)
OnText = Callable[[str], Awaitable[None] | None]
OnThinking = Callable[[str], Awaitable[None] | None]
OnTool = Callable[[str, dict[str, Any]], Awaitable[None] | None]

MAX_TOOL_ITERATIONS = 25


@dataclass
class OpenAIResult:
    """Result from a single agent run."""

    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    num_turns: int = 0
    tool_uses: list[str] = field(default_factory=list)
    response_id: str | None = None  # Responses API: for conversation chaining


class OpenAIAgent:
    """Streaming agent with tool-calling loop for OpenAI-compatible APIs."""

    def __init__(
        self,
        config: AgentConfig,
        message_store: MessageStore,
        auth: OpenAIAuth | None = None,
    ):
        self.config = config
        self.message_store = message_store
        self._auth = auth
        # Resolve cwd: expand ~ and ensure directory exists
        from pathlib import Path
        cwd = Path(config.cwd).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)
        self._cwd = str(cwd)
        self._client = AsyncOpenAI(
            api_key=config.api_key or "placeholder",
            base_url=config.resolved_base_url(),
        )

    async def run(
        self,
        task_id: str,
        user_message: str,
        system_prompt: str,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
        on_tool: OnTool | None = None,
        images: list[dict] | None = None,
    ) -> OpenAIResult:
        """Run the agent loop for a user message.

        Args:
            task_id: Task ID for message history.
            user_message: The user's input.
            system_prompt: System prompt to prepend.
            on_text: Callback for text output.
            on_thinking: Callback for thinking (unused, for API compat).
            on_tool: Callback for tool use events.
            images: Optional list of {"data": base64, "media_type": "image/..."}.

        Returns:
            OpenAIResult with text, token counts, and tool uses.
        """
        result = OpenAIResult()
        tools = get_tool_schemas(self.config.allowed_tools)
        cwd = self._cwd

        # Load or initialize message history
        messages = self.message_store.get_messages(task_id)
        if not messages:
            messages.append({"role": "system", "content": system_prompt})
        else:
            # Update system prompt in case it changed
            if messages[0].get("role") == "system":
                messages[0]["content"] = system_prompt

        # Build user message content
        user_content: str | list[dict] = user_message
        if images:
            content_parts: list[dict] = []
            for img in images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img['media_type']};base64,{img['data']}",
                    },
                })
            content_parts.append({"type": "text", "text": user_message})
            user_content = content_parts

        messages.append({"role": "user", "content": user_content})
        self.message_store.set_messages(task_id, messages)

        # Agent loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            text, tool_calls, usage = await self._stream_completion(
                messages, tools, on_text, on_tool
            )

            result.input_tokens += usage.get("input", 0)
            result.output_tokens += usage.get("output", 0)
            result.num_turns += 1

            if not tool_calls:
                # No tool calls — we're done
                result.text = text
                # Save assistant response to history
                if text:
                    messages.append({"role": "assistant", "content": text})
                    self.message_store.set_messages(task_id, messages)
                break

            # Build assistant message with tool calls
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if text:
                assistant_msg["content"] = text
            else:
                assistant_msg["content"] = None
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in tool_calls
            ]
            messages.append(assistant_msg)

            # Execute tools and append results
            for tc in tool_calls:
                result.tool_uses.append(tc["name"])
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}

                tool_result = await execute_tool(tc["name"], args, cwd)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

            self.message_store.set_messages(task_id, messages)
        else:
            # Hit max iterations
            result.text = (text or "") + (
                "\n\n(Reached maximum tool call iterations.)"
            )

        # Trim message history to prevent unbounded growth
        self.message_store.trim_to_budget(task_id, max_messages=100)

        return result

    async def _stream_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        on_text: OnText | None = None,
        on_tool: OnTool | None = None,
    ) -> tuple[str, list[dict], dict]:
        """Make a single streaming API call.

        Returns:
            (text, tool_calls, usage) where tool_calls is a list of
            {"id": str, "name": str, "arguments": str}.
        """
        text_parts: list[str] = []
        tool_calls_by_index: dict[int, dict] = {}
        usage = {"input": 0, "output": 0}

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        stream = await self._client.chat.completions.create(**kwargs)

        async for chunk in stream:
            # Usage info (comes in the final chunk)
            if chunk.usage:
                usage["input"] = chunk.usage.prompt_tokens or 0
                usage["output"] = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Text content
            if delta.content:
                text_parts.append(delta.content)
                if on_text:
                    r = on_text(delta.content)
                    if hasattr(r, "__await__"):
                        await r

            # Tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_by_index:
                        tool_calls_by_index[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    tc = tool_calls_by_index[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["arguments"] += tc_delta.function.arguments

        text = "".join(text_parts)

        # Convert indexed tool calls to ordered list
        tool_calls = [
            tool_calls_by_index[i]
            for i in sorted(tool_calls_by_index)
        ]

        # Fire on_tool callbacks for completed tool calls
        if on_tool and tool_calls:
            for tc in tool_calls:
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                r = on_tool(tc["name"], args)
                if hasattr(r, "__await__"):
                    await r

        return text, tool_calls, usage

    # ------------------------------------------------------------------
    # Responses API path (ChatGPT backend via OAuth)
    # ------------------------------------------------------------------

    async def run_codex(
        self,
        task_id: str,
        user_message: str,
        system_prompt: str,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
        on_tool: OnTool | None = None,
    ) -> OpenAIResult:
        """Run agent loop using the ChatGPT Backend Responses API.

        Maintains conversation history via MessageStore using Responses API
        item format (user/assistant roles + function_call/output items).

        Args:
            task_id: Task ID for message history.
            user_message: The user's input.
            system_prompt: System/developer prompt.
            on_text: Callback for text output chunks.
            on_thinking: Callback for thinking output (unused).
            on_tool: Callback for tool use events.

        Returns:
            OpenAIResult with text, token counts, tool uses, and response_id.
        """
        if not self._auth:
            raise RuntimeError("OAuth auth required for run_codex()")

        result = OpenAIResult()
        tools = get_codex_tool_schemas(self.config.allowed_tools)
        cwd = self._cwd

        # Load conversation history from MessageStore
        # We store Responses API items directly: user/assistant messages,
        # function_call items, and function_call_output items.
        history = self.message_store.get_messages(task_id)

        # Append new user message
        history.append({"role": "user", "content": user_message})

        # Build input_items = full history (for the API call within this turn)
        input_items: list[dict[str, Any]] = list(history)

        # Agent loop (tool-calling iterations within a single turn)
        for iteration in range(MAX_TOOL_ITERATIONS):
            text, func_calls, usage, response_id, output_items = (
                await self._stream_responses(
                    input_items=input_items,
                    tools=tools,
                    instructions=system_prompt,
                    on_text=on_text,
                    on_tool=on_tool,
                )
            )

            result.input_tokens += usage.get("input", 0)
            result.output_tokens += usage.get("output", 0)
            result.num_turns += 1
            if response_id:
                result.response_id = response_id

            if not func_calls:
                result.text = text
                # Save assistant text to history
                if text:
                    history.append({"role": "assistant", "content": text})
                break

            # Append model output items (function_calls) + tool results
            for item in output_items:
                input_items.append(item)
                history.append(item)

            for fc in func_calls:
                result.tool_uses.append(fc["name"])
                try:
                    args = json.loads(fc["arguments"])
                except json.JSONDecodeError:
                    args = {}

                tool_result = await execute_tool(fc["name"], args, cwd)
                output_item = {
                    "type": "function_call_output",
                    "call_id": fc["call_id"],
                    "output": tool_result,
                }
                input_items.append(output_item)
                history.append(output_item)
        else:
            result.text = (text or "") + (
                "\n\n(Reached maximum tool call iterations.)"
            )

        # Persist conversation history
        self.message_store.set_messages(task_id, history)
        self.message_store.trim_to_budget(task_id, max_messages=100)

        return result

    async def _stream_responses(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict],
        instructions: str = "",
        on_text: OnText | None = None,
        on_tool: OnTool | None = None,
    ) -> tuple[str, list[dict], dict, str | None, list[dict]]:
        """Make a single streaming call to the Responses API.

        Returns:
            (text, func_calls, usage, response_id, output_items) where
            func_calls is [{"call_id": str, "name": str, "arguments": str}].
        """
        from tinabot.openai_auth import CODEX_BASE_URL

        token = await self._auth.get_access_token()
        account_id = self._auth.account_id or ""

        url = f"{CODEX_BASE_URL}/responses"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "chatgpt-account-id": account_id,
            "originator": "codex_cli_rs",
            "OpenAI-Beta": "responses=experimental",
        }

        body: dict[str, Any] = {
            "model": self.config.model,
            "input": input_items,
            "instructions": instructions,
            "stream": True,
            "store": False,
        }
        if tools:
            body["tools"] = tools

        text_parts: list[str] = []
        func_calls_list: list[dict] = []
        usage = {"input": 0, "output": 0}
        response_id: str | None = None
        output_items: list[dict] = []

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            async with client.stream(
                "POST", url, headers=headers, json=body
            ) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    raise RuntimeError(
                        f"Responses API error ({resp.status_code}): "
                        f"{error_body.decode()[:500]}"
                    )

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Text streaming
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            text_parts.append(delta)
                            if on_text:
                                r = on_text(delta)
                                if hasattr(r, "__await__"):
                                    await r

                    # Function call argument streaming (accumulated;
                    # final values come via response.output_item.done)
                    elif event_type == "response.function_call_arguments.delta":
                        pass  # streamed args handled in output_item.done

                    # Completed output item (function_call or message)
                    elif event_type == "response.output_item.done":
                        item = event.get("item", {})
                        output_items.append(item)
                        if item.get("type") == "function_call":
                            fc = {
                                "call_id": item.get("call_id", ""),
                                "name": item.get("name", ""),
                                "arguments": item.get("arguments", ""),
                            }
                            func_calls_list.append(fc)
                            if on_tool:
                                try:
                                    args = json.loads(fc["arguments"])
                                except json.JSONDecodeError:
                                    args = {}
                                r = on_tool(fc["name"], args)
                                if hasattr(r, "__await__"):
                                    await r

                    # Response completed
                    elif event_type == "response.completed":
                        resp_obj = event.get("response", {})
                        response_id = resp_obj.get("id")
                        resp_usage = resp_obj.get("usage", {})
                        usage["input"] = resp_usage.get(
                            "input_tokens", 0
                        )
                        usage["output"] = resp_usage.get(
                            "output_tokens", 0
                        )

        text = "".join(text_parts)
        return text, func_calls_list, usage, response_id, output_items
