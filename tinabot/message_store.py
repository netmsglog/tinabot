"""Per-task message history persistence for non-Claude providers.

Stores OpenAI-format message arrays as JSON files, one per task.
Storage: ~/.tinabot/data/messages/{task_id}.json
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


class MessageStore:
    """Manages per-task message history on disk with in-memory cache."""

    def __init__(self, data_dir: str | Path):
        self._dir = Path(data_dir).expanduser() / "messages"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, list[dict]] = {}

    def _path(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def get_messages(self, task_id: str) -> list[dict]:
        """Load messages from cache or disk."""
        if task_id in self._cache:
            return self._cache[task_id]

        path = self._path(task_id)
        if path.exists():
            try:
                msgs = json.loads(path.read_text(encoding="utf-8"))
                self._cache[task_id] = msgs
                return msgs
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load messages for {task_id}: {e}")

        self._cache[task_id] = []
        return self._cache[task_id]

    def append_message(self, task_id: str, message: dict):
        """Append a message and persist to disk."""
        msgs = self.get_messages(task_id)
        msgs.append(message)
        self._persist(task_id)

    def append_messages(self, task_id: str, messages: list[dict]):
        """Append multiple messages and persist once."""
        msgs = self.get_messages(task_id)
        msgs.extend(messages)
        self._persist(task_id)

    def set_messages(self, task_id: str, messages: list[dict]):
        """Replace all messages for a task."""
        self._cache[task_id] = messages
        self._persist(task_id)

    def trim_to_budget(self, task_id: str, max_messages: int = 100):
        """Keep system message (if first) + last N messages."""
        msgs = self.get_messages(task_id)
        if len(msgs) <= max_messages:
            return

        # Preserve system message if it's the first one
        if msgs and msgs[0].get("role") == "system":
            system = [msgs[0]]
            rest = msgs[1:]
        else:
            system = []
            rest = msgs

        trimmed = system + rest[-max_messages:]
        self._cache[task_id] = trimmed
        self._persist(task_id)

    def clear(self, task_id: str):
        """Clear all messages for a task."""
        self._cache.pop(task_id, None)
        path = self._path(task_id)
        if path.exists():
            path.unlink()

    def _persist(self, task_id: str):
        """Write cached messages to disk."""
        msgs = self._cache.get(task_id, [])
        path = self._path(task_id)
        try:
            path.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to persist messages for {task_id}: {e}")
