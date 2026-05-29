"""
Synthetic trace generator for TraceGuard demo.

Generates three trace files, each containing a different anomaly pattern:
  - retry_storm.jsonl       — same tool called 5x with no success
  - silent_failure.jsonl    — failed tool result silently ignored
  - recursive_delegation.jsonl — A → B → A delegation cycle

These traces simulate the execution event stream an agent runtime
(such as Hermes Agent) could emit if it exposed structured execution events.

See: https://github.com/NousResearch/hermes-agent/issues/169
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from traceguard.schema import (
    TraceEvent,
    EventType,
    StepStatus,
)

SESSION = "demo-session-001"


def _ts(offset_ms: int = 0) -> datetime:
    base = datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(milliseconds=offset_ms)


def _write(path: Path, events: list[TraceEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(event.model_dump_json() + "\n")
    print(f"  wrote {len(events)} events → {path}")


# ---------------------------------------------------------------------------
# Scenario 1: Retry Storm
# ---------------------------------------------------------------------------

def make_retry_storm(path: Path) -> None:
    """bash tool called 5 times with identical args, no success."""
    events: list[TraceEvent] = []
    t = 0

    events.append(TraceEvent(
        session_id=SESSION, type=EventType.STEP_START,
        step_id="step-1", step_index=0, timestamp=_ts(t),
    ))
    t += 50

    for i in range(5):
        events.append(TraceEvent(
            session_id=SESSION, type=EventType.TOOL_CALL,
            step_id=f"step-1-retry-{i}", step_index=i,
            tool_name="bash",
            tool_args={"command": "git status --porcelain"},
            timestamp=_ts(t),
        ))
        t += 30
        events.append(TraceEvent(
            session_id=SESSION, type=EventType.TOOL_RESULT,
            step_id=f"step-1-retry-{i}", step_index=i,
            tool_name="bash",
            tool_output="",
            status=StepStatus.FAILED,
            error_message="command timed out",
            timestamp=_ts(t),
        ))
        t += 200

    _write(path, events)


# ---------------------------------------------------------------------------
# Scenario 2: Silent Failure Continuation
# ---------------------------------------------------------------------------

def make_silent_failure(path: Path) -> None:
    """read_file returns empty output; agent immediately calls write_file."""
    events: list[TraceEvent] = []
    t = 0

    events.append(TraceEvent(
        session_id=SESSION, type=EventType.STEP_START,
        step_id="step-read", step_index=0, timestamp=_ts(t),
    ))
    t += 50
    events.append(TraceEvent(
        session_id=SESSION, type=EventType.TOOL_CALL,
        step_id="step-read", step_index=0,
        tool_name="read_file",
        tool_args={"path": "/workspace/config.json"},
        timestamp=_ts(t),
    ))
    t += 80
    # Empty result — file missing or unreadable
    events.append(TraceEvent(
        session_id=SESSION, type=EventType.TOOL_RESULT,
        step_id="step-read", step_index=0,
        tool_name="read_file",
        tool_output="",         # silent empty
        status=StepStatus.FAILED,
        error_message=None,
        timestamp=_ts(t),
    ))
    t += 20

    # Agent continues without error handling
    events.append(TraceEvent(
        session_id=SESSION, type=EventType.TOOL_CALL,
        step_id="step-write", step_index=1,
        tool_name="write_file",
        tool_args={"path": "/workspace/config.json", "content": "{}"},
        timestamp=_ts(t),
    ))

    _write(path, events)


# ---------------------------------------------------------------------------
# Scenario 3: Recursive Delegation
# ---------------------------------------------------------------------------

def make_recursive_delegation(path: Path) -> None:
    """Agent A delegates to B, B delegates back to A."""
    events: list[TraceEvent] = []
    t = 0

    events.append(TraceEvent(
        session_id=SESSION, type=EventType.STEP_START,
        step_id="step-plan", step_index=0, timestamp=_ts(t),
    ))
    t += 100
    events.append(TraceEvent(
        session_id=SESSION, type=EventType.AGENT_DELEGATE,
        step_id="step-plan", step_index=0,
        caller_agent="planner-agent",
        target_agent="executor-agent",
        timestamp=_ts(t),
    ))
    t += 200
    # executor-agent delegates back to planner-agent — cycle
    events.append(TraceEvent(
        session_id=SESSION, type=EventType.AGENT_DELEGATE,
        step_id="step-exec", step_index=1,
        caller_agent="executor-agent",
        target_agent="planner-agent",
        timestamp=_ts(t),
    ))

    _write(path, events)


# ---------------------------------------------------------------------------
# Scenario 4: Clean trace (no anomalies — baseline)
# ---------------------------------------------------------------------------

def make_clean_trace(path: Path) -> None:
    """Successful 3-step execution: read → analyze → write."""
    events: list[TraceEvent] = []
    t = 0

    for i, (tool, args) in enumerate([
        ("read_file",  {"path": "/workspace/data.csv"}),
        ("python_eval", {"code": "df.describe()"}),
        ("write_file", {"path": "/workspace/report.md", "content": "# Report\n..."}),
    ]):
        events.append(TraceEvent(
            session_id=SESSION, type=EventType.STEP_START,
            step_id=f"step-{i}", step_index=i, timestamp=_ts(t),
        ))
        t += 30
        events.append(TraceEvent(
            session_id=SESSION, type=EventType.TOOL_CALL,
            step_id=f"step-{i}", step_index=i,
            tool_name=tool, tool_args=args, timestamp=_ts(t),
        ))
        t += 100
        events.append(TraceEvent(
            session_id=SESSION, type=EventType.TOOL_RESULT,
            step_id=f"step-{i}", step_index=i,
            tool_name=tool, tool_output="ok",
            status=StepStatus.SUCCESS, timestamp=_ts(t),
        ))
        t += 50

    events.append(TraceEvent(
        session_id=SESSION, type=EventType.STEP_END,
        step_id="step-2", step_index=2,
        status=StepStatus.SUCCESS, timestamp=_ts(t),
    ))

    _write(path, events)


if __name__ == "__main__":
    base = Path("traces")
    print("Generating synthetic traces...")
    make_retry_storm(base / "retry_storm.jsonl")
    make_silent_failure(base / "silent_failure.jsonl")
    make_recursive_delegation(base / "recursive_delegation.jsonl")
    make_clean_trace(base / "clean.jsonl")
    print("Done.")
