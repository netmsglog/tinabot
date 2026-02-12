"""Interactive CLI REPL with prompt_toolkit and rich rendering."""

from __future__ import annotations

import asyncio
import sys

import typer
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from tinabot.app import TinaApp
from tinabot.config import Config
from tinabot.memory import Task

app_cli = typer.Typer(
    name="tina",
    help="Tinabot - Local AI agent with CLI and Telegram interfaces",
    no_args_is_help=False,
    invoke_without_command=True,
)

console = Console()


def _print_thinking(text: str):
    """Print thinking output in dim style."""
    console.print(Text(text, style="dim italic"), end="")


def _print_tool(name: str, input_data: dict):
    """Print tool usage indicator."""
    detail = ""
    if name == "Bash":
        detail = f": {input_data.get('command', '')[:80]}"
    elif name == "Read":
        detail = f": {input_data.get('file_path', '')}"
    elif name == "Write":
        detail = f": {input_data.get('file_path', '')}"
    elif name == "Edit":
        detail = f": {input_data.get('file_path', '')}"
    elif name in ("Glob", "Grep"):
        detail = f": {input_data.get('pattern', '')}"
    console.print(Text(f"  [{name}{detail}]", style="cyan dim"))


def _print_response(text: str, cost: float | None, turns: int):
    """Print the agent's response with rich markdown rendering."""
    if text:
        console.print()
        console.print(Markdown(text))

    # Cost footer
    footer_parts = []
    if cost is not None:
        footer_parts.append(f"${cost:.4f}")
    if turns > 0:
        footer_parts.append(f"{turns} turn{'s' if turns != 1 else ''}")
    if footer_parts:
        console.print(Text(" | ".join(footer_parts), style="dim"), justify="right")


def _print_task_info(task: Task):
    """Print task summary."""
    status = "active" if task.active else ""
    compressed = " (compressed)" if task.summary else ""
    session = f" session:{task.session_id[:8]}" if task.session_id else ""
    console.print(
        f"  [{task.id}] {task.name}"
        f"  turns:{task.turn_count}{compressed}{session}"
        f"  {'*' if task.active else ''}",
        style="green" if task.active else "",
    )


async def _run_repl(tina: TinaApp):
    """Run the interactive REPL loop."""
    history_path = tina.config.memory.data_dir.replace("data", "history")
    session: PromptSession = PromptSession(
        history=FileHistory(history_path),
    )

    # Ensure an active task exists
    if not tina.memory.get_active_task():
        tina.memory.create_task("Default task")

    console.print(
        Panel(
            "[bold]Tina[/bold] - AI Agent\n"
            "Type a message to chat. Commands: /new /tasks /resume /compress /skills /help /exit",
            border_style="blue",
        )
    )

    task = tina.memory.get_active_task()
    if task:
        console.print(f"Active task: [{task.id}] {task.name}", style="dim")

    while True:
        try:
            user_input = await session.prompt_async("you> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!", style="dim")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle commands
        if user_input.startswith("/"):
            handled = await _handle_command(user_input, tina)
            if handled == "exit":
                break
            continue

        # Send to agent
        task = tina.memory.get_active_task()
        console.print()

        thinking_started = False

        def on_thinking(text: str):
            nonlocal thinking_started
            if not thinking_started:
                console.print(Text("thinking...", style="dim italic"))
                thinking_started = True

        response = await tina.agent.process(
            message=user_input,
            task=task,
            on_thinking=on_thinking,
            on_tool=_print_tool,
        )

        _print_response(response.text, response.cost_usd, response.num_turns)


async def _handle_command(cmd: str, tina: TinaApp) -> str | None:
    """Handle a /command. Returns 'exit' to quit."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command == "/exit" or command == "/quit":
        console.print("Bye!", style="dim")
        return "exit"

    elif command == "/new":
        name = arg or "New task"
        task = tina.memory.create_task(name)
        console.print(f"Created task [{task.id}] {task.name}", style="green")

    elif command == "/tasks":
        tasks = tina.memory.list_tasks()
        if not tasks:
            console.print("No tasks.", style="dim")
        else:
            console.print("Tasks:", style="bold")
            for t in tasks:
                _print_task_info(t)

    elif command == "/resume":
        if not arg:
            console.print("Usage: /resume <task_id>", style="yellow")
        else:
            task = tina.memory.set_active(arg)
            if task:
                console.print(f"Resumed task [{task.id}] {task.name}", style="green")
            else:
                console.print(f"Task '{arg}' not found", style="red")

    elif command == "/compress":
        task = tina.memory.get_active_task()
        if not task:
            console.print("No active task", style="yellow")
        elif not task.session_id:
            console.print("No session to compress", style="yellow")
        else:
            console.print("Compressing...", style="dim")
            summary = await tina.agent.force_compress(task)
            if summary:
                console.print(Panel(summary, title="Summary", border_style="blue"))
            else:
                console.print("Compression failed", style="red")

    elif command == "/skills":
        skills = tina.skills.list_skills()
        if not skills:
            console.print("No skills found.", style="dim")
        else:
            console.print("Skills:", style="bold")
            for s in skills:
                console.print(f"  {s['name']}: {s['description']}", style="cyan")

    elif command == "/help":
        console.print(
            Panel(
                "/new [name]     Create a new task\n"
                "/tasks          List all tasks\n"
                "/resume <id>    Switch to a task\n"
                "/compress       Compress current task context\n"
                "/skills         List loaded skills\n"
                "/help           Show this help\n"
                "/exit           Quit",
                title="Commands",
                border_style="blue",
            )
        )
    else:
        console.print(f"Unknown command: {command}. Type /help", style="yellow")

    return None


@app_cli.callback(invoke_without_command=True)
def default_command(ctx: typer.Context):
    """Start interactive chat (default)."""
    if ctx.invoked_subcommand is None:
        chat()


@app_cli.command()
def chat():
    """Start interactive REPL."""
    config = Config.load()
    tina = TinaApp(config)
    asyncio.run(_run_repl(tina))


@app_cli.command()
def serve():
    """Start Telegram bot."""
    config = Config.load()
    if not config.telegram.token:
        console.print(
            "Set TINABOT_TELEGRAM__TOKEN or configure telegram.token in ~/.tinabot/config.json",
            style="red",
        )
        raise typer.Exit(1)
    config.telegram.enabled = True
    tina = TinaApp(config)
    asyncio.run(tina.run_serve())


@app_cli.command()
def tasks():
    """List all tasks."""
    config = Config.load()
    tina = TinaApp(config)
    task_list = tina.memory.list_tasks()
    if not task_list:
        console.print("No tasks.", style="dim")
    else:
        for t in task_list:
            _print_task_info(t)


@app_cli.command()
def skills():
    """List loaded skills."""
    config = Config.load()
    tina = TinaApp(config)
    skill_list = tina.skills.list_skills()
    if not skill_list:
        console.print("No skills found.", style="dim")
    else:
        for s in skill_list:
            console.print(f"  {s['name']}: {s['description']}", style="cyan")


def main():
    """Entry point."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    app_cli()
