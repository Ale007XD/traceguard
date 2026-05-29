"""
TraceRecorder — append-only JSONL execution trace writer.

Usage:
    recorder = TraceRecorder("session.jsonl", session_id="abc-123")
    recorder.write(TraceEvent(session_id=..., type=EventType.TOOL_CALL, ...))
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import TraceEvent


class TraceRecorder:
    """Writes TraceEvents to an append-only JSONL file.

    Thread-safety: single-writer assumed (asyncio single-task context).
    For concurrent writes use asyncio.Lock externally.
    """

    def __init__(self, path: str | Path, session_id: str) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self._count = 0

    def write(self, event: TraceEvent) -> None:
        """Append one event. Flushes immediately (crash-safe)."""
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
            f.flush()
        self._count += 1

    @property
    def events_written(self) -> int:
        return self._count


def load_trace(path: str | Path) -> list[TraceEvent]:
    """Load all events from a JSONL trace file."""
    events: list[TraceEvent] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(TraceEvent.model_validate(json.loads(line)))
    return events
