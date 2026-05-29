"""
nano-vm traceguard — execution anomaly detector CLI.

Usage:
    traceguard traces/retry_storm.jsonl
    python -m traceguard.cli traces/foo.jsonl
    python -m traceguard.cli traces/foo.jsonl --strict
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

from .guard import TraceGuard
from .recorder import load_trace
from .schema import AnomalySeverity, AnomalyReport

console = Console()

_SEVERITY_STYLE = {
    AnomalySeverity.INFO:     ("cyan",   "ℹ"),
    AnomalySeverity.WARN:     ("yellow", "⚠"),
    AnomalySeverity.CRITICAL: ("red",    "✖"),
}


def _print_report(report: AnomalyReport) -> None:
    color, icon = _SEVERITY_STYLE[report.severity]
    title = Text()
    title.append(f"{icon} ", style=f"bold {color}")
    title.append(report.detector, style="bold white")
    title.append(f"  [{report.severity.value.upper()}]", style=f"dim {color}")

    body = Text()
    body.append(report.message)
    short_ids = [eid[:8] for eid in report.evidence_event_ids[:4]]
    body.append(f"\n\n[dim]evidence:[/dim] {', '.join(short_ids)}")
    if report.occurrences > 1:
        body.append(f"\n[dim]occurrences:[/dim] {report.occurrences}")
    body.append(f"\n[dim]first seen:[/dim] {report.first_seen_at.strftime('%H:%M:%S.%f')[:-3]}")

    console.print(Panel(body, title=title, border_style=color, padding=(0, 1)))


def _print_summary(events: list, reports: list[AnomalyReport]) -> None:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")

    counts = {s: 0 for s in AnomalySeverity}
    for r in reports:
        counts[r.severity] += 1

    table.add_row("Events analyzed", str(len(events)))
    table.add_row("Anomalies found", str(len(reports)))
    table.add_row("  ✖ CRITICAL", Text(str(counts[AnomalySeverity.CRITICAL]), style="bold red"))
    table.add_row("  ⚠ WARN",     Text(str(counts[AnomalySeverity.WARN]),     style="bold yellow"))
    table.add_row("  ℹ INFO",     Text(str(counts[AnomalySeverity.INFO]),      style="bold cyan"))
    console.print(table)


def lint(
    trace_file: Path = typer.Argument(..., help="Path to JSONL trace file"),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 on any WARN"),
) -> None:
    """Analyze a JSONL execution trace for runtime anomalies."""

    if not trace_file.exists():
        console.print(f"[red]Error:[/red] file not found: {trace_file}")
        raise typer.Exit(1)

    console.print()
    console.print(Panel.fit(
        f"[bold]TraceGuard[/bold]  ·  execution anomaly detector\n"
        f"[dim]trace:[/dim] {trace_file}",
        border_style="bright_black",
    ))
    console.print()

    try:
        events = load_trace(trace_file)
    except Exception as e:
        console.print(f"[red]Failed to parse trace:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[dim]Loaded {len(events)} events[/dim]\n")

    guard = TraceGuard()
    reports = guard.analyze(events)

    if not reports:
        console.print("[bold green]✔ No anomalies detected.[/bold green]\n")
        _print_summary(events, reports)
        raise typer.Exit(0)

    criticals = [r for r in reports if r.severity == AnomalySeverity.CRITICAL]
    warns     = [r for r in reports if r.severity == AnomalySeverity.WARN]
    infos     = [r for r in reports if r.severity == AnomalySeverity.INFO]

    for report in criticals + warns + infos:
        _print_report(report)

    console.print()
    _print_summary(events, reports)

    if criticals:
        raise typer.Exit(2)
    if strict and warns:
        raise typer.Exit(1)
    raise typer.Exit(0)


def main() -> None:
    typer.run(lint)


if __name__ == "__main__":
    main()
