"""
TraceGuard anomaly detectors.

Three detectors, each scanning an append-only event stream:

  RetryStormDetector      — same tool called N times without state change
  SilentFailureDetector   — error/empty result followed by next step as if nothing happened
  RecursiveDelegationDetector — agent A delegates to B which delegates back to A (or self)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone

from .schema import AnomalyReport, AnomalySeverity, EventType, StepStatus, TraceEvent


class BaseDetector(ABC):
    name: str

    @abstractmethod
    def feed(self, event: TraceEvent) -> AnomalyReport | None:
        """Process one event; return AnomalyReport if anomaly detected, else None."""
        ...

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# RetryStormDetector
# ---------------------------------------------------------------------------

class RetryStormDetector(BaseDetector):
    """Detects repeated calls to the same tool with identical args and no success.

    Fires when the same (tool_name, tool_args) pair appears >= threshold times
    in a sliding window of recent tool_call events without an intervening success.
    """

    name = "RetryStormDetector"

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        # (tool_name, args_key) → (count, [event_ids], first_seen)
        self._counters: dict[str, tuple[int, list[str], datetime]] = {}
        self._last_success_tool: set[str] = set()

    def feed(self, event: TraceEvent) -> AnomalyReport | None:
        if event.type == EventType.TOOL_RESULT and event.status == StepStatus.SUCCESS:
            if event.tool_name:
                self._last_success_tool.add(event.tool_name)
            return None

        if event.type != EventType.TOOL_CALL:
            return None

        tool = event.tool_name or "<unknown>"
        # Reset on success
        if tool in self._last_success_tool:
            self._last_success_tool.discard(tool)
            key = self._make_key(event)
            self._counters.pop(key, None)
            return None

        key = self._make_key(event)
        if key not in self._counters:
            self._counters[key] = (1, [event.event_id], event.timestamp)
        else:
            count, ids, first = self._counters[key]
            count += 1
            ids.append(event.event_id)
            self._counters[key] = (count, ids, first)

            if count == self.threshold:
                return AnomalyReport(
                    detector=self.name,
                    severity=AnomalySeverity.WARN,
                    message=(
                        f"Retry storm: tool '{tool}' called {count} times "
                        f"with identical args and no successful result."
                    ),
                    evidence_event_ids=list(ids),
                    first_seen_at=first,
                    occurrences=count,
                )
            if count > self.threshold:
                # Update but don't re-fire for each additional call
                return None

        return None

    @staticmethod
    def _make_key(event: TraceEvent) -> str:
        args_repr = str(sorted((event.tool_args or {}).items()))
        return f"{event.tool_name}::{args_repr}"


# ---------------------------------------------------------------------------
# SilentFailureDetector
# ---------------------------------------------------------------------------

class SilentFailureDetector(BaseDetector):
    """Detects when an error or empty tool result is silently ignored.

    Pattern: TOOL_RESULT(status=failed OR output='') → STEP_START or TOOL_CALL
    with no intervening error-handling step.
    """

    name = "SilentFailureDetector"

    def __init__(self) -> None:
        self._pending_failure: TraceEvent | None = None

    def feed(self, event: TraceEvent) -> AnomalyReport | None:
        if event.type == EventType.TOOL_RESULT:
            is_failure = (
                event.status == StepStatus.FAILED
                or event.error_message is not None
                or event.tool_output in (None, "", "null", "None")
            )
            if is_failure:
                self._pending_failure = event
            else:
                self._pending_failure = None
            return None

        if event.type == EventType.ERROR:
            self._pending_failure = event
            return None

        # If we see a new tool call or step start after a pending failure — silent continuation
        if self._pending_failure is not None and event.type in (
            EventType.TOOL_CALL,
            EventType.STEP_START,
            EventType.LLM_REQUEST,
        ):
            failure = self._pending_failure
            self._pending_failure = None
            return AnomalyReport(
                detector=self.name,
                severity=AnomalySeverity.WARN,
                message=(
                    f"Silent failure continuation: execution continued after "
                    f"failed/empty result from '{failure.tool_name or 'unknown'}' "
                    f"without error handling."
                ),
                evidence_event_ids=[failure.event_id, event.event_id],
                first_seen_at=failure.timestamp,
                occurrences=1,
            )

        return None


# ---------------------------------------------------------------------------
# RecursiveDelegationDetector
# ---------------------------------------------------------------------------

class RecursiveDelegationDetector(BaseDetector):
    """Detects when agent delegation forms a cycle.

    Tracks the delegation chain: A→B→C→... and fires if any agent
    appears twice in the active call stack (cycle), or if self-delegation occurs.
    """

    name = "RecursiveDelegationDetector"

    def __init__(self, max_depth: int = 5) -> None:
        self.max_depth = max_depth
        # Stack of (caller, target, event_id)
        self._stack: deque[tuple[str, str, str]] = deque()

    def feed(self, event: TraceEvent) -> AnomalyReport | None:
        if event.type != EventType.AGENT_DELEGATE:
            return None

        caller = event.caller_agent or event.session_id
        target = event.target_agent or "<unknown>"

        # Self-delegation
        if caller == target:
            return AnomalyReport(
                detector=self.name,
                severity=AnomalySeverity.CRITICAL,
                message=f"Self-delegation detected: agent '{caller}' delegated to itself.",
                evidence_event_ids=[event.event_id],
                first_seen_at=event.timestamp,
                occurrences=1,
            )

        self._stack.append((caller, target, event.event_id, event.timestamp))

        # Check for cycle: target already appears as a caller in the stack
        callers_in_stack = {c for c, _, _, _ in self._stack}
        if target in callers_in_stack:
            ids = [eid for _, _, eid, _ in self._stack]
            return AnomalyReport(
                detector=self.name,
                severity=AnomalySeverity.CRITICAL,
                message=(
                    f"Recursive delegation cycle: '{target}' appears as both "
                    f"caller and delegate. Chain depth: {len(self._stack)}."
                ),
                evidence_event_ids=ids,
                first_seen_at=self._stack[0][3]
                    if self._stack else event.timestamp,
                occurrences=len(self._stack),
            )

        # Depth exceeded
        if len(self._stack) > self.max_depth:
            ids = [eid for _, _, eid, _ in self._stack]
            return AnomalyReport(
                detector=self.name,
                severity=AnomalySeverity.WARN,
                message=(
                    f"Delegation depth exceeded {self.max_depth}: "
                    f"chain is {len(self._stack)} levels deep."
                ),
                evidence_event_ids=ids,
                first_seen_at=self._stack[0][3]
                    if self._stack else event.timestamp,
                occurrences=len(self._stack),
            )

        return None


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

ALL_DETECTORS: list[type[BaseDetector]] = [
    RetryStormDetector,
    SilentFailureDetector,
    RecursiveDelegationDetector,
]
