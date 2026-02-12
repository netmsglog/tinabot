"""Application bootstrap - wires agent, memory, skills, and interfaces."""

from __future__ import annotations

import asyncio

from loguru import logger

from tinabot.agent import TinaAgent
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
        """Start the Telegram bot and scheduler."""
        bot = TelegramBot(
            self.config.telegram, self.agent, self.memory, self.schedule_store
        )
        scheduler = Scheduler(self.store, self.agent, bot.send_message)
        scheduler_task: asyncio.Task | None = None
        logger.info("Starting Telegram serve mode...")
        try:
            # Start scheduler as background task
            scheduler_task = asyncio.create_task(scheduler.run())
            await bot.start()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            if scheduler_task and not scheduler_task.done():
                scheduler_task.cancel()
                try:
                    await scheduler_task
                except asyncio.CancelledError:
                    pass
            await bot.stop()

    @property
    def store(self) -> ScheduleStore:
        return self.schedule_store
