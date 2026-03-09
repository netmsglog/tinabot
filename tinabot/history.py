"""Task history logger — appends JSONL events per task for full interaction replay."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _format_ts(iso: str) -> str:
    """Convert ISO timestamp to compact local display: ``YYYY-MM-DD HH:MM:SS``."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso


def _brief_tool_input(input_dict: dict) -> str:
    """Return a short one-line summary of tool input."""
    # Common patterns: show the most useful field
    for key in ("command", "query", "file_path", "pattern", "url"):
        if key in input_dict:
            val = str(input_dict[key])
            if len(val) > 120:
                val = val[:117] + "..."
            return f"{key}={val}"
    # Fallback: compact JSON
    raw = json.dumps(input_dict, ensure_ascii=False)
    if len(raw) > 120:
        raw = raw[:117] + "..."
    return raw


class HistoryLogger:
    """Append-only JSONL logger for task interaction history.

    Each task gets one file: ``{data_dir}/history/{task_id}.jsonl``
    with one JSON object per line, ordered by time.
    """

    def __init__(self, data_dir: str | Path):
        self._dir = Path(data_dir).expanduser() / "history"
        self._dir.mkdir(parents=True, exist_ok=True)

    def log(self, task_id: str, event: str, **data) -> None:
        """Append one event to the task's JSONL file.

        None values are automatically filtered out to reduce file size.
        """
        entry = {
            "ts": _now_iso(),
            "event": event,
            **{k: v for k, v in data.items() if v is not None},
        }
        path = self._dir / f"{task_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read(self, task_id: str) -> list[dict]:
        """Read all events for a task. Returns empty list if no history."""
        path = self._dir / f"{task_id}.jsonl"
        if not path.exists():
            return []
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    def read_paginated(
        self, task_id: str, max_pairs: int = 10, before_index: int | None = None,
    ) -> tuple[list[dict], bool, int | None]:
        """Read recent conversation pairs with pagination.

        A "pair" is a user_message followed by its tool_calls and response.

        Args:
            task_id: Task to read.
            max_pairs: Max conversation pairs to return.
            before_index: If set, return pairs whose first event index < this value.

        Returns:
            (events, has_more, next_offset) where next_offset can be passed
            as before_index in subsequent calls to load older pairs.
        """
        all_events = self.read(task_id)
        if not all_events:
            return [], False, None

        # Group events into conversation pairs, tracking their start indices
        pairs: list[tuple[int, list[dict]]] = []  # (start_index, events)
        current_pair: list[dict] = []
        current_start = 0

        for i, ev in enumerate(all_events):
            kind = ev.get("event", "")
            if kind == "user_message":
                if current_pair:
                    pairs.append((current_start, current_pair))
                current_pair = [ev]
                current_start = i
            elif kind in ("tool_call", "response"):
                current_pair.append(ev)
            # skip system_prompt etc.

        if current_pair:
            pairs.append((current_start, current_pair))

        if not pairs:
            return [], False, None

        # Filter by before_index
        if before_index is not None:
            pairs = [(si, evs) for si, evs in pairs if si < before_index]

        if not pairs:
            return [], False, None

        # Take last max_pairs
        has_more = len(pairs) > max_pairs
        selected = pairs[-max_pairs:]

        # Flatten selected pairs back to event list
        result: list[dict] = []
        for _, evs in selected:
            result.extend(evs)

        next_offset = selected[0][0] if has_more else None
        return result, has_more, next_offset

    def format_export(self, task_id: str, task_name: str = "") -> str | None:
        """Format task history as human-readable text.

        Shows time, user messages, tool call sequences, and responses.
        Returns None if no history exists.
        """
        events = self.read(task_id)
        if not events:
            return None

        lines: list[str] = []
        header = f"# Task: {task_name}" if task_name else f"# Task: {task_id}"
        lines.append(header)
        lines.append("")

        for ev in events:
            ts = _format_ts(ev.get("ts", ""))
            kind = ev.get("event", "")

            if kind == "user_message":
                lines.append(f"[{ts}] user> {ev.get('text', '')}")
                lines.append("")

            elif kind == "tool_call":
                name = ev.get("name", "?")
                inp = ev.get("input", {})
                lines.append(f"[{ts}]   [{name}] {_brief_tool_input(inp)}")

            elif kind == "response":
                text = ev.get("text", "")
                meta_parts: list[str] = []
                if ev.get("input_tokens"):
                    meta_parts.append(f"in:{ev['input_tokens']}")
                if ev.get("output_tokens"):
                    meta_parts.append(f"out:{ev['output_tokens']}")
                if ev.get("cost_usd") is not None:
                    meta_parts.append(f"${ev['cost_usd']:.4f}")
                if ev.get("model"):
                    meta_parts.append(ev["model"])
                meta = f"  ({', '.join(meta_parts)})" if meta_parts else ""
                lines.append(f"[{ts}] tina>{meta}")
                lines.append(text)
                lines.append("")

            # system_prompt is intentionally omitted for readability

        return "\n".join(lines)
