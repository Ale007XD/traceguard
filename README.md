# TraceGuard

**Execution anomaly detector for autonomous agent runtimes.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-12%2F12%20PASS-brightgreen)]()

> *Autonomous agents are becoming distributed systems. Distributed systems need observability.*

TraceGuard is a lightweight, embeddable flight recorder for agent execution traces. It reads append-only JSONL event streams and detects three critical runtime anomalies that silent agent failures produce.

Built on the same deterministic execution substrate as [llm-nano-vm](https://github.com/Ale007XD/nano_vm):

```
δ(S, E) → S'   —   every observable state transition as an append-only event
```

---

## Install

```bash
pip install traceguard
```

Or from source:

```bash
git clone https://github.com/Ale007XD/traceguard
cd traceguard
pip install -e .
```

---

## Demo

```bash
# Analyze a trace — exit 0 if clean
traceguard traces/clean.jsonl

# Retry storm detected — exit 2 (CRITICAL) or 1 (WARN)
traceguard traces/retry_storm.jsonl

# Recursive delegation detected — exit 2 (CRITICAL)
traceguard traces/recursive_delegation.jsonl

# Silent failure detected — exit 1 (WARN) or 2 with --strict
traceguard traces/silent_failure.jsonl --strict
```

Exit codes are CI-composable: `0` = clean, `1` = WARN (with `--strict`), `2` = CRITICAL.

---

## What It Detects

| Detector | Severity | Description |
| :--- | :---: | :--- |
| `RetryStormDetector` | WARN | Same tool called N times without state change or success |
| `SilentFailureDetector` | WARN | Error/empty result followed by next step as if nothing happened |
| `RecursiveDelegationDetector` | CRITICAL | Agent A delegates to B which delegates back to A (or self) |

---

## Architecture

```
append-only JSONL trace
        ↓
  TraceRecorder (load_trace)
        ↓
  TraceGuard.analyze(events)
        ↓
  [RetryStormDetector, SilentFailureDetector, RecursiveDelegationDetector]
        ↓
  list[AnomalyReport]
        ↓
  CLI (rich output, exit codes)
```

Four layers, single responsibility each:

| Layer | File | Responsibility |
| :--- | :--- | :--- |
| Schema | `schema.py` | `TraceEvent` (frozen Pydantic v2), `AnomalyReport`, enums |
| Storage | `recorder.py` | Append-only JSONL read/write |
| Detection | `detectors.py` | `BaseDetector` ABC + 3 stateful detectors |
| Orchestration | `guard.py` | `TraceGuard` — batch (`analyze`) + streaming (`feed`) |

---

## CI / GitHub Actions

TraceGuard exit codes are designed for pipeline integration:

```yaml
# .github/workflows/agent-qa.yml
- name: Run agent
  run: python my_agent.py --trace-output traces/latest.jsonl

- name: Lint execution trace
  run: traceguard traces/latest.jsonl --strict
  # exits 0 = clean, 1 = WARN, 2 = CRITICAL → fails the build
```

This enables **behavioral regression testing for agent runs**: fail pull requests when the agent runtime exhibits retry storms, silent failures, or delegation cycles — the same way unit tests catch code regressions.

```
agent PR → run → emit trace → traceguard --strict → fail CI if anomaly
```

---

## Event Schema

```python
from traceguard import TraceEvent, EventType, StepStatus

event = TraceEvent(
    session_id="order-abc-123",
    type=EventType.TOOL_CALL,
    tool_name="bash",
    tool_args={"cmd": "ls /tmp"},
)
```

`TraceEvent` is **frozen** (`model_config = {"frozen": True}`) — immutable after creation. Every field is optional except `session_id` and `type`.

Supported event types: `step_start`, `step_end`, `tool_call`, `tool_result`, `llm_request`, `llm_response`, `agent_delegate`, `error`.

---

## Python API

### Batch analysis

```python
from pathlib import Path
from traceguard import TraceGuard
from traceguard.recorder import load_trace

events = load_trace(Path("traces/my_agent_run.jsonl"))
guard = TraceGuard()
reports = guard.analyze(events)

for r in reports:
    print(r.detector, r.severity, r.message)
```

### Streaming (real-time)

```python
from traceguard import TraceGuard, TraceEvent, EventType

guard = TraceGuard()

for raw_event in agent_event_stream():
    event = TraceEvent(**raw_event)
    report = guard.feed(event)
    if report:
        alert(f"[{report.severity}] {report.message}")
```

### Recording traces

```python
from traceguard.recorder import TraceRecorder

recorder = TraceRecorder(Path("run-001.jsonl"))
recorder.write(event)           # append one event
events = recorder.load()        # read all events back
```

---

## Relation to Hermes Agent

TraceGuard proposes an **execution event contract** for autonomous agent runtimes like Hermes. Rather than parsing terminal output, it defines a structured JSONL stream that any agent runtime can emit:

```
Hermes Agent runtime
      ↓  emits TraceEvent stream
TraceGuard (external observer)
      ↓  detects anomaly patterns
AnomalyReport → alert / CI fail / audit log
```

**TraceGuard intentionally operates as an external observer, not embedded middleware.**
This is a deliberate architectural choice:

- no monkey-patching of the runtime
- no invasive hooks inside the agent loop
- no framework lock-in — works with Hermes, AutoGen, CrewAI, or any runtime that emits structured events
- the runtime stays clean; TraceGuard stays composable

Unlike terminal log scrapers, structured execution traces are **replayable, diffable, and analyzable** — suitable for CI regression testing, post-mortem forensics, and behavioral drift detection across model versions.

The synthetic traces in `traces/` demonstrate what that contract looks like for each anomaly class. See [issue #169](https://github.com/NousResearch/hermes-agent/issues/169) for the upstream proposal.

---

## Known Limitations (MVP)

These are documented intentionally — they define the production roadmap:

- **`recorder.py` — no `fsync`, no file locking, no log rotation.** The current append-only JSONL writer is correct for single-writer scenarios and demo workloads. Production use requires `os.fsync()` after each write, advisory file locking for concurrent writers, and size-based rotation. These are intentionally deferred for the v0.1.0 MVP.

- **`RecursiveDelegationDetector` — stack set rebuilt on each event.** `callers_in_stack = {c for c, _, _, _ in self._stack}` creates a new `set` on every `AGENT_DELEGATE` event. For delegation stacks deeper than ~100 entries this produces measurable CPU overhead. Fix: maintain a persistent `_callers_set` updated incrementally. Deferred — demo traces are 3–15 events.

- **No integrations.** There are no adapters for OpenAI SDK, LangChain, or CrewAI. Adding one requires wrapping the client and emitting `TraceEvent` on each call — roughly 20 lines per framework.

- **CLI: batch mode only.** `traceguard <file>` reads a completed trace. A `--watch` streaming mode (tail JSONL, emit alerts on new events) is on the roadmap.

- **No tests for `cli.py` and `recorder.py`.** The detection logic (12/12 tests) is fully covered. CLI output formatting and file I/O are not yet under test.

---

## Future Direction

TraceGuard v0.1.0 is an anomaly detector. The deeper direction is an **execution governance layer** for autonomous runtimes.

The `RecursiveDelegationDetector` is the first hint at this: it operates on delegation graph topology, not event sequences. That's a qualitatively different class of analysis — and it scales toward:

- **depth limit enforcement** — hard `max_delegation_depth` guard, budget attribution per delegation branch
- **ownership context propagation** — trace which root task initiated each sub-chain
- **behavioral regression testing** — same task, different model version, compare traces, detect drift
- **runtime policy enforcement** — allow/deny delegation patterns at the governance layer, not the prompt layer
- **execution auditability** — immutable append-only record suitable for EU AI Act compliance

The execution event contract (`δ(S, E) → S'`) is the foundation. Everything above is a policy layer on top of a replayable trace.

> TraceGuard is to agent runtimes what OpenTelemetry is to distributed services — a structured, vendor-neutral execution signal.

---

## Roadmap

- [ ] `os.fsync` + file locking in `recorder.py`
- [ ] `--watch` streaming CLI mode
- [ ] OpenAI SDK adapter (20 lines)
- [ ] LangChain callback handler
- [ ] `RecursiveDelegationDetector` persistent caller set
- [ ] Token budget detector (`TokenBudgetDetector`)
- [ ] PII leak detector (regex-based field scan)
- [ ] CLI tests + recorder tests

---

## License

[MIT](LICENSE)

---

## Author

[@ale007xd](https://t.me/ale007xd) · built on [llm-nano-vm](https://github.com/Ale007XD/nano_vm)
