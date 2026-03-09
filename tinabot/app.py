"""Application bootstrap - wires agent, memory, skills, and interfaces."""

from __future__ import annotations

import asyncio

from loguru import logger

from tinabot.agent import TinaAgent, kill_descendants
from tinabot.config import Config
from tinabot.memory import TaskMemory
from tinabot.scheduler import Scheduler, ScheduleStore
from tinabot.skills import SkillsLoader
from tinabot.telegram import TelegramBot


class TinaApp:
    """Bootstrap container that wires all components together."""

    def __init__(self, config: Config):
        self.config = config
        self.memory = TaskMemory(
            data_dir=config.memory.data_dir,
            compress_after_turns=config.memory.compress_after_turns,
        )
        self.skills = SkillsLoader(config.skills.skills_dir)
        self.agent = TinaAgent(config.agent, self.skills, self.memory)
        self.schedule_store = ScheduleStore(config.memory.data_dir)

    async def run_serve(self):
        """Start the Telegram bot, web server, and scheduler."""
        bg_tasks: list[asyncio.Task] = []
        bot: TelegramBot | None = None
        web_server = None

        try:
            # Start web server if configured
            if self.config.web.enabled and self.config.web.auth_token:
                from tinabot.web import WebServer
                web_server = WebServer(self.config.web, self.agent, self.memory)
                bg_tasks.append(asyncio.create_task(web_server.start()))

            # Start Telegram bot
            bot = TelegramBot(
                self.config.telegram, self.agent, self.memory, self.schedule_store
            )
            scheduler = Scheduler(self.store, self.agent, bot.send_message)
            bg_tasks.append(asyncio.create_task(scheduler.run()))

            logger.info("Starting serve mode...")
            await bot.start()

        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            for t in bg_tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            if bot:
                await bot.stop()
            if web_server:
                await web_server.stop()
            # Kill any leftover child processes (agent subprocesses, background tasks)
            kill_descendants()

    async def run_web(self):
        """Start only the web server (no Telegram)."""
        from tinabot.web import WebServer
        web_server = WebServer(self.config.web, self.agent, self.memory)
        logger.info("Starting web-only mode...")
        try:
            await web_server.start()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await web_server.stop()
            kill_descendants()

    @property
    def store(self) -> ScheduleStore:
        return self.schedule_store
