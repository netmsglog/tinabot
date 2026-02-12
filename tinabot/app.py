"""Application bootstrap - wires agent, memory, skills, and interfaces."""

from __future__ import annotations

import asyncio

from loguru import logger

from tinabot.agent import TinaAgent
from tinabot.config import Config
from tinabot.memory import TaskMemory
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

    async def run_serve(self):
        """Start the Telegram bot."""
        bot = TelegramBot(self.config.telegram, self.agent, self.memory)
        logger.info("Starting Telegram serve mode...")
        try:
            await bot.start()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await bot.stop()
