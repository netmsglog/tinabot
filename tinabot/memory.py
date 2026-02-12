"""Per-task memory system with session tracking and compression."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


@dataclass
class Task:
    """A conversation task with its associated session state."""

    id: str
    name: str
    session_id: str | None = None
    created_at: str = ""
    turn_count: int = 0
    summary: str | None = None
    active: bool = False

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class TaskMemory:
    """Manages task persistence and session tracking.

    Storage layout:
        data_dir/
            tasks.json              # Task list
            summaries/{task_id}.md  # Compressed summaries
    """

    def __init__(self, data_dir: str | Path, compress_after_turns: int = 20):
        self.data_dir = Path(data_dir).expanduser()
        self.compress_after_turns = compress_after_turns
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self):
        """Load tasks from disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "summaries").mkdir(exist_ok=True)

        tasks_file = self.data_dir / "tasks.json"
        if tasks_file.exists():
            try:
                data = json.loads(tasks_file.read_text())
                for item in data:
                    task = Task(**item)
                    self._tasks[task.id] = task
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to load tasks: {e}")

    def _save(self):
        """Persist tasks to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tasks_file = self.data_dir / "tasks.json"
        data = [asdict(t) for t in self._tasks.values()]
        tasks_file.write_text(json.dumps(data, indent=2))

    def create_task(self, name: str) -> Task:
        """Create a new task and set it as active."""
        task_id = secrets.token_hex(4)  # 8-char hex ID
        task = Task(id=task_id, name=name[:80], active=True)

        # Deactivate all other tasks
        for t in self._tasks.values():
            t.active = False

        self._tasks[task_id] = task
        self._save()
        logger.info(f"Created task {task_id}: {name[:50]}")
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_active_task(self) -> Task | None:
        """Get the currently active task."""
        for task in self._tasks.values():
            if task.active:
                return task
        return None

    def set_active(self, task_id: str) -> Task | None:
        """Set a task as the active one."""
        task = self._tasks.get(task_id)
        if not task:
            return None

        for t in self._tasks.values():
            t.active = False
        task.active = True
        self._save()
        return task

    def list_tasks(self) -> list[Task]:
        """List all tasks, most recent first."""
        return sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )

    def update_session_id(self, task_id: str, session_id: str):
        """Store the SDK session ID for a task."""
        task = self._tasks.get(task_id)
        if task:
            task.session_id = session_id
            self._save()

    def increment_turns(self, task_id: str) -> int:
        """Increment turn count and return new value."""
        task = self._tasks.get(task_id)
        if task:
            task.turn_count += 1
            self._save()
            return task.turn_count
        return 0

    def needs_compression(self, task: Task) -> bool:
        """Check if a task needs its context compressed."""
        return (
            task.turn_count >= self.compress_after_turns
            and task.session_id is not None
            and task.summary is None
        )

    def save_summary(self, task_id: str, summary: str):
        """Save a compressed summary and clear the session for fresh start."""
        task = self._tasks.get(task_id)
        if not task:
            return

        # Write summary file
        summary_path = self.data_dir / "summaries" / f"{task_id}.md"
        summary_path.write_text(summary, encoding="utf-8")

        # Update task: clear session_id so next interaction starts fresh with summary
        task.summary = summary
        task.session_id = None
        task.turn_count = 0
        self._save()
        logger.info(f"Compressed task {task_id}, summary saved")

    def get_summary(self, task_id: str) -> str | None:
        """Get the compressed summary for a task."""
        task = self._tasks.get(task_id)
        if task and task.summary:
            return task.summary

        # Try loading from file
        summary_path = self.data_dir / "summaries" / f"{task_id}.md"
        if summary_path.exists():
            return summary_path.read_text(encoding="utf-8")
        return None

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and its summary."""
        if task_id not in self._tasks:
            return False

        del self._tasks[task_id]
        summary_path = self.data_dir / "summaries" / f"{task_id}.md"
        if summary_path.exists():
            summary_path.unlink()

        self._save()
        return True
