"""Scheduler: recurring task execution via cron-style schedules."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from croniter import croniter
from loguru import logger


@dataclass
class Schedule:
    """A single recurring schedule."""

    id: str
    name: str
    cron: str
    prompt: str
    chat_id: int
    enabled: bool = True
    created_at: str = ""
    last_run: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("id")  # id comes from filename
        return d


class ScheduleStore:
    """Read/write schedule files from a directory (one JSON file per schedule)."""

    def __init__(self, data_dir: str | Path):
        self._dir = Path(data_dir).expanduser() / "schedules"
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def schedules_dir(self) -> Path:
        return self._dir

    def list(self) -> list[Schedule]:
        """List all schedules from disk."""
        schedules = []
        for p in sorted(self._dir.glob("*.json")):
            s = self._load(p)
            if s:
                schedules.append(s)
        return schedules

    def get(self, schedule_id: str) -> Schedule | None:
        """Get a schedule by ID (filename stem)."""
        p = self._dir / f"{schedule_id}.json"
        if p.exists():
            return self._load(p)
        return None

    def add(
        self,
        name: str,
        cron: str,
        prompt: str,
        chat_id: int,
        schedule_id: str | None = None,
    ) -> Schedule:
        """Create a new schedule and write to disk."""
        if schedule_id is None:
            schedule_id = uuid.uuid4().hex[:8]

        now = datetime.now(timezone.utc).isoformat()
        schedule = Schedule(
            id=schedule_id,
            name=name,
            cron=cron,
            prompt=prompt,
            chat_id=chat_id,
            created_at=now,
        )
        self._save(schedule)
        return schedule

    def remove(self, schedule_id: str) -> bool:
        """Remove a schedule file. Returns True if it existed."""
        p = self._dir / f"{schedule_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def update_last_run(self, schedule_id: str, timestamp: str):
        """Update last_run for a schedule."""
        schedule = self.get(schedule_id)
        if schedule:
            schedule.last_run = timestamp
            self._save(schedule)

    def _load(self, path: Path) -> Schedule | None:
        try:
            data = json.loads(path.read_text())
            return Schedule(id=path.stem, **data)
        except Exception as e:
            logger.warning(f"Failed to load schedule {path.name}: {e}")
            return None

    def _save(self, schedule: Schedule):
        path = self._dir / f"{schedule.id}.json"
        path.write_text(json.dumps(schedule.to_dict(), indent=2) + "\n")


SendFn = Callable[[int, str], Awaitable[None]]


class Scheduler:
    """Background loop that checks for due schedules and executes them."""

    def __init__(
        self,
        store: ScheduleStore,
        agent: Any,  # TinaAgent (avoid circular import)
        send_fn: SendFn,
    ):
        self.store = store
        self.agent = agent
        self.send_fn = send_fn

    async def run(self):
        """Main loop — check every 30 seconds for due schedules."""
        logger.info("Scheduler started")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                logger.info("Scheduler stopped")
                return
            except Exception as e:
                logger.error(f"Scheduler tick error: {e}")
            await asyncio.sleep(30)

    async def _tick(self):
        """Check all schedules and execute any that are due."""
        for schedule in self.store.list():
            if not schedule.enabled:
                continue
            if self._is_due(schedule):
                await self._execute(schedule)

    def _is_due(self, schedule: Schedule) -> bool:
        """Check if a schedule is due to run.

        Uses croniter to find the next fire time after last_run (or created_at).
        If that time is <= now, it's due.
        """
        try:
            base_str = schedule.last_run or schedule.created_at
            if not base_str:
                return False
            base = datetime.fromisoformat(base_str)
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)

            cron = croniter(schedule.cron, base)
            next_time = cron.get_next(datetime)
            now = datetime.now(timezone.utc)
            return next_time <= now
        except Exception as e:
            logger.warning(f"Schedule '{schedule.id}' cron error: {e}")
            return False

    async def _execute(self, schedule: Schedule):
        """Run the agent for a schedule and deliver the result."""
        logger.info(f"Executing schedule '{schedule.id}': {schedule.name}")
        now = datetime.now(timezone.utc).isoformat()

        try:
            response = await self.agent.process(message=schedule.prompt)
            text = response.text or "(no output)"
            await self.send_fn(schedule.chat_id, f"[{schedule.name}]\n\n{text}")
        except Exception as e:
            logger.error(f"Schedule '{schedule.id}' execution failed: {e}")
            try:
                await self.send_fn(
                    schedule.chat_id,
                    f"\u26a0\ufe0f Schedule '{schedule.name}' failed: {e}",
                )
            except Exception:
                pass

        # Update last_run regardless (avoid retrying every 30s on failure)
        self.store.update_last_run(schedule.id, now)
