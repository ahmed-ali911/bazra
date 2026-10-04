"""Checkpoint 5.5 — real-model comparison benchmark infrastructure.

Deliberately a SEPARATE subpackage from the offline 5.4 harness
(`evals.schemas`/`evals.runner`/`evals.proactive_narration`/
`evals.report`) — importing anything under `evals.benchmarks` must
never be required to run `python -m evals.report` or any 5.4 offline
test. Only this subpackage's own `generation.py` makes real provider
calls, and only when its functions are explicitly invoked — never on
import, never from a test, never from the offline report.

MEASURE FIRST. ROUTE LATER. Nothing here changes, or is capable of
changing, production model selection, tier routing, or any runtime
behavior — see generation.py's own docstring for exactly how real
provider calls are made without going through
model_router_service.complete() (which resolves its model from
`purpose` alone, with no override point, and unconditionally writes an
AiTrace row this benchmark must never produce).
"""
