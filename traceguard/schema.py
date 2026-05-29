"""
TraceGuard execution event schema.

Proposed execution event contract for autonomous agent runtimes.
δ(S, E) → S' — every observable state transition as an append-only event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    STEP_START = "step_start"
    STEP_END = "step_end"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    AGENT_DELEGATE = "agent_delegate"
    ERROR = "error"


class StepStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class AnomalySeverity(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class TraceEvent(BaseModel):
    """Single immutable execution event. Append-only."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0"

    type: EventType
    step_id: str | None = None
    step_index: int | None = None

    # tool_call / tool_result
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_output: str | None = None

    # llm_request / llm_response
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    # agent_delegate
    target_agent: str | None = None
    caller_agent: str | None = None

    # step_end / error
    status: StepStatus | None = None
    error_message: str | None = None

    # filled by TraceGuard detectors
    anomalies: list[str] = Field(default_factory=list)
    severity: AnomalySeverity = AnomalySeverity.INFO

    model_config = {"frozen": True}


class AnomalyReport(BaseModel):
    """Anomaly found by a detector."""

    detector: str
    severity: AnomalySeverity
    message: str
    evidence_event_ids: list[str]
    first_seen_at: datetime
    occurrences: int = 1
