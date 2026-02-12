"""Telegram bot interface using python-telegram-bot with long polling."""

from __future__ import annotations

import asyncio
import base64
import re

from loguru import logger
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tinabot.agent import ImageInput, TinaAgent
from tinabot.config import TelegramConfig
from tinabot.memory import TaskMemory
from tinabot.scheduler import ScheduleStore

# Max Telegram message length
MAX_MSG_LEN = 4096


def markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML.

    Protects code blocks, then converts formatting, then restores code.
    """
    if not text:
        return ""

    # 1. Extract and protect code blocks
    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # 3. Headers -> plain text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)

    # 4. Blockquotes -> plain text
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # 5. Escape HTML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 6. Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. Italic _text_ (avoid matching inside words)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. Bullet lists
    text = re.sub(r"^[-*]\s+", "\u2022 ", text, flags=re.MULTILINE)

    # 11. Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def _split_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Split a long message into chunks that fit Telegram's limit."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Try to split at a newline
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            # No good newline break, split at max
            split_at = max_len

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks


class TelegramBot:
    """Telegram bot with long polling.

    Each Telegram chat gets its own active task for context isolation.
    """

    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("new", "Create a new task"),
        BotCommand("tasks", "List tasks"),
        BotCommand("resume", "Resume a task by ID"),
        BotCommand("compress", "Compress current task"),
        BotCommand("skills", "List available skills"),
        BotCommand("schedules", "List scheduled tasks"),
        BotCommand("help", "Show commands"),
    ]

    def __init__(
        self,
        config: TelegramConfig,
        agent: TinaAgent,
        memory: TaskMemory,
        schedule_store: ScheduleStore | None = None,
    ):
        self.config = config
        self.agent = agent
        self.memory = memory
        self.schedule_store = schedule_store
        self._app: Application | None = None
        self._chat_tasks: dict[int, str] = {}  # chat_id -> task_id
        self._typing_tasks: dict[int, asyncio.Task] = {}
        self._processing: dict[int, asyncio.Task] = {}  # chat_id -> agent task
        self._shutdown_event: asyncio.Event | None = None

    def _is_allowed(self, user_id: int) -> bool:
        """Check if user is in allowlist. Empty list = deny all."""
        return user_id in self.config.allowed_users

    async def _check_allowed(self, update: Update) -> bool:
        """Check permission and reply with user ID if denied."""
        if not update.effective_user:
            return False
        user = update.effective_user
        if self._is_allowed(user.id):
            return True
        logger.info(f"Denied user {user.id} (@{user.username})")
        await update.message.reply_text(
            f"You are not authorized.\n"
            f"Your user ID: <code>{user.id}</code>\n\n"
            f"Ask the admin to run:\n"
            f"<code>tina user add {user.id}</code>",
            parse_mode="HTML",
        )
        return False

    def _get_or_create_task(self, chat_id: int, message: str = "") -> str:
        """Get or create the active task for a chat."""
        task_id = self._chat_tasks.get(chat_id)
        if task_id:
            task = self.memory.get_task(task_id)
            if task:
                return task_id

        # Create a new task for this chat
        name = message[:80] if message else f"Telegram chat {chat_id}"
        task = self.memory.create_task(name)
        self._chat_tasks[chat_id] = task.id
        return task.id

    async def start(self):
        """Start the Telegram bot with long polling."""
        if not self.config.token:
            logger.error("Telegram bot token not configured")
            return

        self._app = Application.builder().token(self.config.token).build()

        # Command handlers
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("new", self._on_new))
        self._app.add_handler(CommandHandler("tasks", self._on_tasks))
        self._app.add_handler(CommandHandler("resume", self._on_resume))
        self._app.add_handler(CommandHandler("compress", self._on_compress))
        self._app.add_handler(CommandHandler("skills", self._on_skills))
        self._app.add_handler(CommandHandler("schedules", self._on_schedules))
        self._app.add_handler(CommandHandler("help", self._on_help))

        # Message handlers
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        self._app.add_handler(
            MessageHandler(filters.PHOTO, self._on_photo)
        )

        logger.info("Starting Telegram bot (polling)...")
        await self._app.initialize()
        await self._app.start()

        bot_info = await self._app.bot.get_me()
        logger.info(f"Telegram bot @{bot_info.username} connected")

        try:
            await self._app.bot.set_my_commands(self.BOT_COMMANDS)
        except Exception as e:
            logger.warning(f"Failed to register commands: {e}")

        await self._app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
        )

        # Block until shutdown is signaled or task is cancelled
        self._shutdown_event = asyncio.Event()
        await self._shutdown_event.wait()

    async def stop(self):
        """Stop the bot."""
        if self._shutdown_event:
            self._shutdown_event.set()

        # Cancel all in-flight agent processing
        for proc in self._processing.values():
            if not proc.done():
                proc.cancel()
        self._processing.clear()

        for task in self._typing_tasks.values():
            if not task.done():
                task.cancel()
        self._typing_tasks.clear()

        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    async def _send(self, chat_id: int, text: str, parse_html: bool = True):
        """Send a message, splitting if needed."""
        if not self._app:
            return

        self._stop_typing(chat_id)

        chunks = _split_message(text)
        for chunk in chunks:
            if parse_html:
                html = markdown_to_telegram_html(chunk)
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id, text=html, parse_mode="HTML"
                    )
                    continue
                except Exception as e:
                    logger.warning(f"HTML send failed, falling back: {e}")

            # Fallback to plain text
            await self._app.bot.send_message(chat_id=chat_id, text=chunk)

    async def send_message(self, chat_id: int, text: str):
        """Public interface for sending messages (used by Scheduler)."""
        await self._send(chat_id, text)

    def _start_typing(self, chat_id: int):
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(
            self._typing_loop(chat_id)
        )

    def _stop_typing(self, chat_id: int):
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: int):
        try:
            while self._app:
                await self._app.bot.send_chat_action(
                    chat_id=chat_id, action="typing"
                )
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    # --- Command handlers ---

    async def _on_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        await update.message.reply_text(
            f"Hi {update.effective_user.first_name}! I'm Tina.\n\n"
            "Send me a message and I'll help you out.\n"
            "Type /help for commands."
        )

    async def _on_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        chat_id = update.message.chat_id
        name = update.message.text.replace("/new", "").strip() or "New task"
        task = self.memory.create_task(name)
        self._chat_tasks[chat_id] = task.id
        await update.message.reply_text(f"Created task [{task.id}] {task.name}")

    async def _on_tasks(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        tasks = self.memory.list_tasks()
        if not tasks:
            await update.message.reply_text("No tasks.")
            return

        chat_id = update.message.chat_id
        active_id = self._chat_tasks.get(chat_id)
        lines = []
        for t in tasks[:20]:  # Limit display
            marker = " *" if t.id == active_id else ""
            compressed = " (compressed)" if t.summary else ""
            lines.append(
                f"[{t.id}] {t.name}  turns:{t.turn_count}{compressed}{marker}"
            )
        await update.message.reply_text("\n".join(lines))

    async def _on_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        chat_id = update.message.chat_id
        task_id = update.message.text.replace("/resume", "").strip()
        if not task_id:
            await update.message.reply_text("Usage: /resume <task_id>")
            return

        task = self.memory.get_task(task_id)
        if not task:
            await update.message.reply_text(f"Task '{task_id}' not found")
            return

        self._chat_tasks[chat_id] = task.id
        await update.message.reply_text(f"Resumed [{task.id}] {task.name}")

    async def _on_compress(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        chat_id = update.message.chat_id
        task_id = self._chat_tasks.get(chat_id)
        if not task_id:
            await update.message.reply_text("No active task")
            return

        task = self.memory.get_task(task_id)
        if not task or not task.session_id:
            await update.message.reply_text("No session to compress")
            return

        await update.message.reply_text("Compressing...")
        summary = await self.agent.force_compress(task)
        if summary:
            await self._send(chat_id, f"Summary:\n\n{summary}")
        else:
            await update.message.reply_text("Compression failed")

    async def _on_skills(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        skills = self.agent.skills.list_skills()
        if not skills:
            await update.message.reply_text("No skills loaded.")
            return

        lines = [f"{s['name']}: {s['description']}" for s in skills]
        await update.message.reply_text("\n".join(lines))

    async def _on_schedules(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message or not await self._check_allowed(update):
            return

        if not self.schedule_store:
            await update.message.reply_text("Scheduling not available.")
            return

        chat_id = update.message.chat_id
        schedules = self.schedule_store.list()
        # Filter to this chat's schedules
        mine = [s for s in schedules if s.chat_id == chat_id]
        if not mine:
            await update.message.reply_text("No schedules for this chat.")
            return

        lines = []
        for s in mine:
            status = "on" if s.enabled else "off"
            last = s.last_run[:16] if s.last_run else "never"
            lines.append(f"[{s.id}] {s.name}\n  cron: {s.cron}  status: {status}  last: {last}")
        await update.message.reply_text("\n\n".join(lines))

    async def _on_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        await update.message.reply_text(
            "Tina commands:\n\n"
            "/new [name] - Create a new task\n"
            "/tasks - List tasks\n"
            "/resume <id> - Switch to a task\n"
            "/compress - Compress current task\n"
            "/skills - List skills\n"
            "/schedules - List scheduled tasks\n"
            "/help - This message\n\n"
            "Send any text message to chat!"
        )

    async def _on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages with live status updates."""
        if not update.message or not await self._check_allowed(update):
            return

        chat_id = update.message.chat_id
        text = update.message.text or ""
        if not text.strip():
            return

        # Interrupt any in-flight agent call for this chat
        await self._cancel_processing(chat_id)

        # Wrap the actual work in a trackable task
        proc_task = asyncio.create_task(self._process_message(chat_id, text, update))
        self._processing[chat_id] = proc_task

        # Clean up reference when done (don't await - let it run)
        proc_task.add_done_callback(lambda _: self._processing.pop(chat_id, None))

    async def _on_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle incoming photo messages (photo + optional caption)."""
        if not update.message or not await self._check_allowed(update):
            return

        chat_id = update.message.chat_id
        text = update.message.caption or "What's in this image?"

        # Download the largest photo (last in the list)
        photo = update.message.photo[-1]
        try:
            file = await ctx.bot.get_file(photo.file_id)
            data = await file.download_as_bytearray()
        except Exception as e:
            logger.error(f"Failed to download photo: {e}")
            await update.message.reply_text(f"Failed to download photo: {e}")
            return

        b64 = base64.b64encode(data).decode("utf-8")
        # Telegram sends photos as JPEG
        image = ImageInput(data=b64, media_type="image/jpeg")

        await self._cancel_processing(chat_id)

        proc_task = asyncio.create_task(
            self._process_message(chat_id, text, update, images=[image])
        )
        self._processing[chat_id] = proc_task
        proc_task.add_done_callback(lambda _: self._processing.pop(chat_id, None))

    async def _cancel_processing(self, chat_id: int):
        """Cancel in-flight agent processing for a chat."""
        proc = self._processing.pop(chat_id, None)
        if proc and not proc.done():
            proc.cancel()
            # Wait briefly for cleanup to finish
            try:
                await asyncio.wait_for(asyncio.shield(proc), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    async def _process_message(
        self,
        chat_id: int,
        text: str,
        update: Update,
        images: list[ImageInput] | None = None,
    ):
        """Run agent and send response. Cancellable by new messages."""
        # Start typing
        self._start_typing(chat_id)

        # Get/create task for this chat
        task_id = self._get_or_create_task(chat_id, text)
        task = self.memory.get_task(task_id)

        # Send initial status message that we'll edit in-place
        status_msg = await self._app.bot.send_message(chat_id, "\u23f3 Thinking...")
        status = _StatusTracker(self._app, chat_id, status_msg.message_id)

        try:
            response = await self.agent.process(
                message=text,
                task=task,
                on_thinking=status.on_thinking,
                on_tool=status.on_tool,
                chat_id=chat_id,
                images=images,
            )

            # Delete the status message, then send the real response
            await status.delete()
            self._stop_typing(chat_id)

            reply = response.text or "(no response)"
            if response.cost_usd is not None:
                reply += f"\n\n_${response.cost_usd:.4f}_"

            await self._send(chat_id, reply)

        except asyncio.CancelledError:
            # Interrupted by a new message from the user
            await status.delete()
            self._stop_typing(chat_id)
            await self._app.bot.send_message(chat_id, "\u26a0\ufe0f Interrupted by new message")
            raise  # Re-raise so the task is marked as cancelled

        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await status.delete()
            self._stop_typing(chat_id)
            await self._app.bot.send_message(chat_id, f"Error: {e}")


# --- Live status message tracker ---

# Tool display names / icons
_TOOL_ICONS: dict[str, str] = {
    "Read": "\U0001f4d6",       # open book
    "Write": "\u270f\ufe0f",    # pencil
    "Edit": "\u270f\ufe0f",     # pencil
    "Bash": "\U0001f4bb",       # laptop
    "Glob": "\U0001f50d",       # magnifying glass
    "Grep": "\U0001f50d",       # magnifying glass
    "WebSearch": "\U0001f310",  # globe
    "WebFetch": "\U0001f310",   # globe
    "Task": "\U0001f916",       # robot
}


def _tool_detail(name: str, input_data: dict) -> str:
    """Build a short human-readable description of a tool call."""
    icon = _TOOL_ICONS.get(name, "\u2699\ufe0f")
    if name == "Bash":
        cmd = input_data.get("command", "")
        # Show first meaningful token of the command
        short = cmd.split("\n")[0][:60]
        return f"{icon} `{short}`"
    elif name in ("Read", "Write", "Edit"):
        path = input_data.get("file_path", "")
        # Show just the filename
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"{icon} {name} `{short}`"
    elif name in ("Glob", "Grep"):
        pattern = input_data.get("pattern", "")
        return f"{icon} {name} `{pattern[:40]}`"
    elif name == "WebSearch":
        query = input_data.get("query", "")
        return f"{icon} Search `{query[:40]}`"
    elif name == "WebFetch":
        url = input_data.get("url", "")
        return f"{icon} Fetch `{url[:40]}`"
    elif name == "Task":
        desc = input_data.get("description", "")
        return f"{icon} {desc[:40]}"
    return f"{icon} {name}"


class _StatusTracker:
    """Manages a single Telegram message that shows live agent progress.

    Shows elapsed time (always ticking) + tool call history. Edits the
    message once per second to keep the user informed even during long
    stretches without tool events.
    """

    def __init__(self, app: Application, chat_id: int, message_id: int):
        self._app = app
        self._chat_id = chat_id
        self._message_id = message_id
        self._thinking = True
        self._steps: list[str] = ["\U0001f9e0 Thinking..."]
        self._deleted = False
        self._flush_task: asyncio.Task | None = None
        self._start_time = asyncio.get_event_loop().time()
        self._last_text = ""
        # Start background flush loop - ticks every second
        self._flush_task = asyncio.create_task(self._flush_loop())

    def _elapsed(self) -> str:
        secs = int(asyncio.get_event_loop().time() - self._start_time)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60:02d}s"

    async def on_thinking(self, text: str):
        if not self._thinking:
            self._thinking = True
            self._steps.append("\U0001f9e0 Thinking...")

    async def on_tool(self, name: str, input_data: dict):
        detail = _tool_detail(name, input_data)
        self._steps.append(detail)

    async def _flush_loop(self):
        """Edit the status message every second with elapsed time."""
        try:
            while not self._deleted:
                await self._edit()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _edit(self):
        """Push current status to the Telegram message."""
        if self._deleted or not self._app:
            return

        elapsed = self._elapsed()
        lines = self._steps[-8:]  # Show last 8 steps
        header = f"\u23f3 {elapsed}"
        body = header + "\n" + "\n".join(lines) if lines else header

        # Skip edit if text hasn't changed (avoids Telegram error)
        if body == self._last_text:
            return
        self._last_text = body

        try:
            await self._app.bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=body,
            )
        except Exception:
            pass

    async def delete(self):
        """Delete the status message and stop the flush loop."""
        self._deleted = True
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        try:
            if self._app:
                await self._app.bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                )
        except Exception:
            pass
