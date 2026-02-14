"""Interactive CLI REPL with prompt_toolkit and rich rendering."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from tinabot.agent import AgentResponse, get_known_models, infer_provider
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


def _fmt_tokens(n: int) -> str:
    tk = n / 1000
    if tk < 0.1:
        return f"{tk:.2f}k"
    if tk < 10:
        return f"{tk:.1f}k"
    return f"{tk:.0f}k"


def _print_response(r: AgentResponse):
    """Print the agent's response with rich markdown rendering."""
    if r.text:
        console.print()
        console.print(Markdown(r.text))

    # Cost footer: ↑5.2k ⚡40k ↓1.1k | $0.0534 | 3 turns
    footer_parts = []
    if r.input_tokens or r.output_tokens:
        token_parts = [f"↑{_fmt_tokens(r.input_tokens)}"]
        if r.cache_read_tokens:
            token_parts.append(f"⚡{_fmt_tokens(r.cache_read_tokens)}")
        token_parts.append(f"↓{_fmt_tokens(r.output_tokens)}")
        footer_parts.append(" ".join(token_parts))
    if r.cost_usd is not None:
        footer_parts.append(f"${r.cost_usd:.4f}")
    if r.num_turns > 0:
        footer_parts.append(f"{r.num_turns} turn{'s' if r.num_turns != 1 else ''}")
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
    history_path = str(Path(tina.config.memory.data_dir).expanduser().parent / "history")
    session: PromptSession = PromptSession(
        history=FileHistory(history_path),
    )

    # Ensure an active task exists
    if not tina.memory.get_active_task():
        tina.memory.create_task("Default task")

    console.print(
        Panel(
            "[bold]Tina[/bold] - AI Agent\n"
            "Type a message to chat. Commands: /new /tasks /model /models /help /exit",
            border_style="blue",
        )
    )

    task = tina.memory.get_active_task()
    model_info = f"{tina.config.agent.model} ({tina.config.agent.provider})"
    if task:
        console.print(f"Active task: [{task.id}] {task.name}  Model: {model_info}", style="dim")
    else:
        console.print(f"Model: {model_info}", style="dim")

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

        _print_response(response)


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
        elif tina.agent.config.is_claude and not task.session_id:
            console.print("No session to compress", style="yellow")
        else:
            console.print("Compressing...", style="dim")
            summary = await tina.agent.force_compress(task)
            if summary:
                console.print(Panel(summary, title="Summary", border_style="blue"))
            else:
                console.print("Compression failed", style="red")

    elif command == "/delete":
        if not arg:
            console.print("Usage: /delete <task_id>", style="yellow")
        else:
            task = tina.memory.get_task(arg)
            if not task:
                console.print(f"Task '{arg}' not found", style="yellow")
            else:
                tina.memory.delete_task(arg)
                console.print(f"Deleted [{arg}] {task.name}", style="green")

    elif command == "/export":
        task_id = arg
        if not task_id:
            task = tina.memory.get_active_task()
            if task:
                task_id = task.id
        if not task_id:
            console.print("Usage: /export [task_id]", style="yellow")
        else:
            history = tina.memory.export_task_history(task_id)
            if history:
                from pathlib import Path

                out = Path(f"task_{task_id}.md")
                out.write_text(history, encoding="utf-8")
                console.print(f"Exported to {out}", style="green")
            else:
                console.print("No conversation history found", style="yellow")

    elif command == "/skills":
        skills = tina.skills.list_skills()
        if not skills:
            console.print("No skills found.", style="dim")
        else:
            console.print("Skills:", style="bold")
            for s in skills:
                console.print(f"  {s['name']}: {s['description']}", style="cyan")

    elif command == "/models":
        _print_model_list(tina.config.agent.model, tina.config.agent.provider)

    elif command == "/model":
        if not arg:
            console.print(
                f"Current model: {tina.config.agent.model} ({tina.config.agent.provider})",
            )
        else:
            model, provider = _switch_model(arg)
            # Update in-memory config and reinitialize agent
            tina.config.agent.model = model
            tina.config.agent.provider = provider
            tina.agent.reinit(tina.config.agent)
            console.print(f"Switched to {model} ({provider})", style="green")

    elif command == "/help":
        console.print(
            Panel(
                "/new [name]     Create a new task\n"
                "/tasks          List all tasks\n"
                "/resume <id>    Switch to a task\n"
                "/compress       Compress current task context\n"
                "/delete <id>    Delete a task\n"
                "/export [id]    Export conversation history\n"
                "/skills         List loaded skills\n"
                "/models         List available models\n"
                "/model [name]   Show or switch model\n"
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


schedule_cli = typer.Typer(help="Manage scheduled tasks")
app_cli.add_typer(schedule_cli, name="schedule")


@schedule_cli.command("list")
def schedule_list():
    """List all schedules."""
    from tinabot.scheduler import ScheduleStore

    config = Config.load()
    store = ScheduleStore(config.memory.data_dir)
    schedules = store.list()
    if not schedules:
        console.print("No schedules.", style="dim")
    else:
        for s in schedules:
            status = "on" if s.enabled else "off"
            last = s.last_run[:16] if s.last_run else "never"
            console.print(
                f"  [{s.id}] {s.name}\n"
                f"    cron: {s.cron}  chat: {s.chat_id}  status: {status}  last: {last}"
            )


@schedule_cli.command("add")
def schedule_add(
    name: str = typer.Option(..., "--name", "-n", help="Schedule name"),
    cron: str = typer.Option(..., "--cron", "-c", help='Cron expression, e.g. "0 9 * * *"'),
    prompt: str = typer.Option(..., "--prompt", "-p", help="Prompt to run"),
    chat: int = typer.Option(..., "--chat", help="Telegram chat ID for delivery"),
):
    """Add a new schedule."""
    from croniter import croniter
    from tinabot.scheduler import ScheduleStore

    if not croniter.is_valid(cron):
        console.print(f"Invalid cron expression: {cron}", style="red")
        raise typer.Exit(1)

    config = Config.load()
    store = ScheduleStore(config.memory.data_dir)
    s = store.add(name=name, cron=cron, prompt=prompt, chat_id=chat)
    console.print(f"Created schedule [{s.id}] {s.name}  cron: {s.cron}", style="green")


@schedule_cli.command("del")
def schedule_del(
    schedule_id: str = typer.Argument(..., help="Schedule ID to remove"),
):
    """Remove a schedule."""
    from tinabot.scheduler import ScheduleStore

    config = Config.load()
    store = ScheduleStore(config.memory.data_dir)
    if store.remove(schedule_id):
        console.print(f"Removed schedule '{schedule_id}'", style="green")
    else:
        console.print(f"Schedule '{schedule_id}' not found", style="yellow")


task_cli = typer.Typer(help="Manage tasks")
app_cli.add_typer(task_cli, name="task")


@task_cli.command("list")
def task_list():
    """List all tasks."""
    config = Config.load()
    tina = TinaApp(config)
    task_list_ = tina.memory.list_tasks()
    if not task_list_:
        console.print("No tasks.", style="dim")
    else:
        for t in task_list_:
            _print_task_info(t)


@task_cli.command("del")
def task_del(
    task_id: str = typer.Argument(..., help="Task ID to delete"),
):
    """Delete a task and its associated data."""
    config = Config.load()
    tina = TinaApp(config)
    task = tina.memory.get_task(task_id)
    if not task:
        console.print(f"Task '{task_id}' not found", style="yellow")
        raise typer.Exit(1)
    tina.memory.delete_task(task_id)
    console.print(f"Deleted [{task_id}] {task.name}", style="green")


@task_cli.command("export")
def task_export(
    task_id: str = typer.Argument(..., help="Task ID to export"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Export task conversation history as markdown."""
    config = Config.load()
    tina = TinaApp(config)
    history = tina.memory.export_task_history(task_id)
    if not history:
        console.print(
            f"No conversation history found for task '{task_id}'.\n"
            "The task may have no session or the session file is missing.",
            style="yellow",
        )
        raise typer.Exit(1)

    if output:
        from pathlib import Path

        Path(output).write_text(history, encoding="utf-8")
        console.print(f"Exported to {output}", style="green")
    else:
        console.print(history)


login_cli = typer.Typer(help="Manage provider authentication")
app_cli.add_typer(login_cli, name="login")


@login_cli.command("openai")
def login_openai():
    """Login to OpenAI via OAuth (ChatGPT account)."""
    from tinabot.openai_auth import OpenAIAuth

    auth = OpenAIAuth()
    if auth.is_logged_in:
        console.print(
            f"Already logged in (account: {auth.account_id or 'unknown'}). "
            "Use 'tina login logout' first to re-authenticate.",
            style="yellow",
        )
        raise typer.Exit(0)

    success = auth.login()
    if not success:
        raise typer.Exit(1)


@login_cli.command("status")
def login_status():
    """Show current authentication state."""
    config = Config.load()
    provider = config.agent.provider

    console.print(f"Provider: {provider}", style="bold")

    if config.agent.api_key:
        key = config.agent.api_key
        masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        console.print(f"Auth: API key ({masked})", style="green")
        return

    if provider == "openai":
        from tinabot.openai_auth import OpenAIAuth

        auth = OpenAIAuth()
        if auth.is_logged_in:
            console.print(
                f"Auth: OAuth (ChatGPT login, account: {auth.account_id or 'unknown'})",
                style="green",
            )
        else:
            console.print(
                "Auth: Not configured. Run 'tina login openai' or set api_key in config.",
                style="yellow",
            )
    elif provider == "claude":
        console.print("Auth: Claude SDK (uses 'claude login' session)", style="green")
    else:
        console.print(
            "Auth: Not configured. Set api_key in ~/.tinabot/config.json",
            style="yellow",
        )


@login_cli.command("logout")
def login_logout():
    """Clear stored OAuth tokens."""
    from tinabot.openai_auth import OpenAIAuth

    auth = OpenAIAuth()
    if not auth.is_logged_in:
        console.print("Not logged in.", style="dim")
        return
    auth.logout()
    console.print("Logged out. OAuth tokens cleared.", style="green")


model_cli = typer.Typer(help="Manage model selection")
app_cli.add_typer(model_cli, name="model")


def _print_model_list(current_model: str, current_provider: str):
    """Print all known models grouped by provider, marking current with *."""
    models = get_known_models()
    console.print(f"Current: {current_model} ({current_provider})\n")

    # Group by provider
    by_provider: dict[str, list[tuple[str, float, float]]] = {}
    for name, (provider, inp, out) in models.items():
        by_provider.setdefault(provider, []).append((name, inp, out))

    for provider_key, label in (("claude", "Claude"), ("openai", "OpenAI")):
        entries = by_provider.get(provider_key, [])
        if not entries:
            continue
        console.print(f"{label}:", style="bold")
        for name, inp, out in entries:
            marker = "*" if name == current_model else " "
            style = "green" if name == current_model else ""
            console.print(
                f"  {marker} {name:<30s}  ${inp:.2f} / ${out:.2f}",
                style=style,
            )
        console.print()

    console.print(
        "Tip: Any OpenAI-compatible model works — set base_url in config for custom endpoints.",
        style="dim",
    )


def _switch_model(model_name: str) -> tuple[str, str]:
    """Validate and persist a model switch. Returns (model, provider)."""
    provider = infer_provider(model_name)
    models = get_known_models()

    if model_name not in models:
        if provider:
            console.print(
                f"Warning: Unknown model '{model_name}' — provider auto-detected as {provider}",
                style="yellow",
            )
        else:
            console.print(
                f"Warning: Unknown model '{model_name}' — cannot infer provider, keeping current",
                style="yellow",
            )
            # Load current provider as fallback
            data = Config.load_raw()
            provider = data.get("agent", {}).get("provider", "claude")

    data = Config.load_raw()
    data.setdefault("agent", {})["model"] = model_name
    data["agent"]["provider"] = provider
    Config.save_raw(data)
    return model_name, provider


@model_cli.command("list")
def model_list():
    """List all known models with pricing."""
    config = Config.load()
    _print_model_list(config.agent.model, config.agent.provider)


@model_cli.command("set")
def model_set(
    model_name: str = typer.Argument(..., help="Model name to switch to"),
):
    """Switch the active model."""
    model, provider = _switch_model(model_name)
    console.print(f"Switched to {model} ({provider})", style="green")


user_cli = typer.Typer(help="Manage Telegram allowed users")
app_cli.add_typer(user_cli, name="user")


@user_cli.command("add")
def user_add(user_id: int = typer.Argument(..., help="Telegram user ID to allow")):
    """Add a user to the Telegram allowlist."""
    from tinabot.config import Config

    data = Config.load_raw()
    tg = data.setdefault("telegram", {})
    users = tg.setdefault("allowed_users", [])
    if user_id in users:
        console.print(f"User {user_id} already in allowlist", style="yellow")
    else:
        users.append(user_id)
        Config.save_raw(data)
        console.print(f"Added user {user_id}", style="green")
    console.print(f"Allowed users: {users}", style="dim")


@user_cli.command("del")
def user_del(user_id: int = typer.Argument(..., help="Telegram user ID to remove")):
    """Remove a user from the Telegram allowlist."""
    from tinabot.config import Config

    data = Config.load_raw()
    users = data.get("telegram", {}).get("allowed_users", [])
    if user_id not in users:
        console.print(f"User {user_id} not in allowlist", style="yellow")
    else:
        users.remove(user_id)
        data["telegram"]["allowed_users"] = users
        Config.save_raw(data)
        console.print(f"Removed user {user_id}", style="green")
    console.print(f"Allowed users: {users}", style="dim")


@user_cli.command("list")
def user_list():
    """Show the Telegram allowlist."""
    from tinabot.config import Config

    data = Config.load_raw()
    users = data.get("telegram", {}).get("allowed_users", [])
    if not users:
        console.print("Allowlist is empty (all users permitted)", style="dim")
    else:
        console.print(f"Allowed users ({len(users)}):", style="bold")
        for uid in users:
            console.print(f"  {uid}")


def main():
    """Entry point."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    app_cli()
