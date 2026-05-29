"""Unit tests for TraceGuard detectors."""

from __future__ import annotations

from traceguard import TraceGuard, TraceEvent, EventType, StepStatus, AnomalySeverity
from traceguard.detectors import RetryStormDetector, SilentFailureDetector, RecursiveDelegationDetector
from traceguard.recorder import load_trace
from pathlib import Path

SESSION = "test-session"


def _ev(**kwargs) -> TraceEvent:
    return TraceEvent(session_id=SESSION, **kwargs)


# ---------------------------------------------------------------------------
# RetryStormDetector
# ---------------------------------------------------------------------------

def test_retry_storm_fires_at_threshold():
    d = RetryStormDetector(threshold=3)
    for i in range(2):
        r = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={"cmd": "ls"}))
        assert r is None
    r = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={"cmd": "ls"}))
    assert r is not None
    assert r.severity == AnomalySeverity.WARN
    assert "bash" in r.message
    assert r.occurrences == 3


def test_retry_storm_resets_on_success():
    d = RetryStormDetector(threshold=2)
    d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={}))
    d.feed(_ev(type=EventType.TOOL_RESULT, tool_name="bash", status=StepStatus.SUCCESS))
    # After success, counter resets
    r = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={}))
    assert r is None


def test_retry_storm_different_tools_independent():
    d = RetryStormDetector(threshold=2)
    d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={"cmd": "a"}))
    d.feed(_ev(type=EventType.TOOL_CALL, tool_name="python", tool_args={"code": "x"}))
    # Neither hits threshold yet
    r1 = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={"cmd": "a"}))
    r2 = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="python", tool_args={"code": "x"}))
    assert r1 is not None  # bash hits 2
    assert r2 is not None  # python hits 2


# ---------------------------------------------------------------------------
# SilentFailureDetector
# ---------------------------------------------------------------------------

def test_silent_failure_detects_continuation():
    d = SilentFailureDetector()
    d.feed(_ev(type=EventType.TOOL_RESULT, tool_name="read_file",
               tool_output="", status=StepStatus.FAILED))
    r = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="write_file", tool_args={}))
    assert r is not None
    assert "read_file" in r.message


def test_silent_failure_clears_on_successful_result():
    d = SilentFailureDetector()
    d.feed(_ev(type=EventType.TOOL_RESULT, tool_name="t",
               tool_output="ok", status=StepStatus.SUCCESS))
    r = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="next", tool_args={}))
    assert r is None


def test_silent_failure_not_triggered_without_prior_error():
    d = SilentFailureDetector()
    r = d.feed(_ev(type=EventType.TOOL_CALL, tool_name="bash", tool_args={}))
    assert r is None


# ---------------------------------------------------------------------------
# RecursiveDelegationDetector
# ---------------------------------------------------------------------------

def test_recursive_delegation_cycle():
    d = RecursiveDelegationDetector()
    d.feed(_ev(type=EventType.AGENT_DELEGATE,
               caller_agent="planner", target_agent="executor"))
    r = d.feed(_ev(type=EventType.AGENT_DELEGATE,
                   caller_agent="executor", target_agent="planner"))
    assert r is not None
    assert r.severity == AnomalySeverity.CRITICAL
    assert "planner" in r.message


def test_self_delegation():
    d = RecursiveDelegationDetector()
    r = d.feed(_ev(type=EventType.AGENT_DELEGATE,
                   caller_agent="agent-a", target_agent="agent-a"))
    assert r is not None
    assert r.severity == AnomalySeverity.CRITICAL
    assert "Self-delegation" in r.message


def test_no_cycle_linear_delegation():
    d = RecursiveDelegationDetector()
    r1 = d.feed(_ev(type=EventType.AGENT_DELEGATE,
                    caller_agent="a", target_agent="b"))
    r2 = d.feed(_ev(type=EventType.AGENT_DELEGATE,
                    caller_agent="b", target_agent="c"))
    assert r1 is None
    assert r2 is None


# ---------------------------------------------------------------------------
# Integration: TraceGuard over synthetic trace files
# ---------------------------------------------------------------------------

def test_clean_trace_no_anomalies():
    events = load_trace(Path("traces/clean.jsonl"))
    guard = TraceGuard()
    reports = guard.analyze(events)
    assert reports == []


def test_retry_storm_trace_has_warn():
    events = load_trace(Path("traces/retry_storm.jsonl"))
    guard = TraceGuard()
    reports = guard.analyze(events)
    detectors = {r.detector for r in reports}
    assert "RetryStormDetector" in detectors


def test_recursive_trace_has_critical():
    events = load_trace(Path("traces/recursive_delegation.jsonl"))
    guard = TraceGuard()
    reports = guard.analyze(events)
    assert any(r.severity == AnomalySeverity.CRITICAL for r in reports)


def test_silent_failure_trace_has_warn():
    events = load_trace(Path("traces/silent_failure.jsonl"))
    guard = TraceGuard()
    reports = guard.analyze(events)
    detectors = {r.detector for r in reports}
    assert "SilentFailureDetector" in detectors
    assert any(r.severity == AnomalySeverity.WARN for r in reports)
