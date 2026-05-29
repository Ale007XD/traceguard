"""
TraceGuard — runs all detectors over an event stream.

Usage:
    guard = TraceGuard()
    reports = guard.analyze(events)
"""

from __future__ import annotations

from .detectors import (
    ALL_DETECTORS,
    BaseDetector,
    RecursiveDelegationDetector,
    RetryStormDetector,
    SilentFailureDetector,
)
from .schema import AnomalyReport, TraceEvent


class TraceGuard:
    """Runs a pipeline of detectors over a sequence of TraceEvents.

    Detectors are stateful — feed events one by one to support
    both batch (offline) and streaming (live) analysis.
    """

    def __init__(self, detectors: list[BaseDetector] | None = None) -> None:
        if detectors is None:
            detectors = [cls() for cls in ALL_DETECTORS]
        self._detectors = detectors

    def analyze(self, events: list[TraceEvent]) -> list[AnomalyReport]:
        """Batch analysis: process all events and return all anomalies."""
        reports: list[AnomalyReport] = []
        for event in events:
            for detector in self._detectors:
                report = detector.feed(event)
                if report is not None:
                    reports.append(report)
        return reports

    def feed(self, event: TraceEvent) -> list[AnomalyReport]:
        """Streaming: process one event, return any anomalies it triggers."""
        reports = []
        for detector in self._detectors:
            report = detector.feed(event)
            if report is not None:
                reports.append(report)
        return reports
