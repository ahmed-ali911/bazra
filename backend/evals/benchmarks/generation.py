"""Checkpoint 5.5 — the ONLY module in this entire repository (both
`app/` and `evals/`) where the real-model-comparison benchmark talks to
a provider. Nothing here is imported by `evals.report`, any 5.4 offline
test, or any `app/` production code path.

Deliberately bypasses `model_router_service.complete()` entirely rather
than calling it with a forced model, for two concrete reasons, both
load-bearing:

1. `complete()` resolves its model EXCLUSIVELY from `purpose` via
   `_resolve_model` — there is no parameter anywhere in its signature
   to force an explicit candidate model string. Giving it one would
   require either mutating `_MODEL_BY_PURPOSE` at runtime (a real,
   if temporary, change to production routing state — forbidden by
   section 31) or adding a new parameter to the production function
   purely to serve this benchmark (the "small shared module" section 7
   permits only "if genuinely necessary" — it is NOT necessary here,
   since the lower-level primitives below already do the job without
   touching `complete()` at all).

2. `complete()` unconditionally writes an AiTrace row for every call
   (`_record_trace`) — production observability this benchmark must
   never pollute (section 25). Calling the lower-level primitives
   directly makes this a structural guarantee, not a flag to remember:
   there is no AiTrace-writing code reachable from this module at all.

Instead this reuses three existing, already-tested, side-effect-free
(at the Python level) building blocks `complete()` itself is built
from: `_call_anthropic` (the real wire-level SDK call),
`_extract_response_parts` (the real response parser), and
`_classify_failure` (the real Checkpoint 5.1 provider-neutral failure
taxonomy) — so a benchmark generation failure is classified with the
EXACT SAME categories (authentication, billing_or_credits,
rate_limited, timeout, connection, provider_unavailable,
invalid_request, model_unavailable, unparseable_response,
unknown_provider_error) a real production failure would get, with zero
duplicated classification logic to drift out of sync.
"""

import time

from app.modules.model_router import service as model_router_service

from evals.benchmarks.schemas import GenerationOutcome


def generate_with_explicit_model(model: str, system: str, user_message: str) -> GenerationOutcome:
    """The one real-provider call site. No `purpose`, no `tier`, no
    AiTrace row — this function is intentionally outside that whole
    vocabulary; it exists only to answer "what does THIS exact model
    produce for THIS exact prompt," nothing else.
    """
    start = time.monotonic()
    try:
        raw = model_router_service._call_anthropic(
            model, [{"role": "user", "content": user_message}], system=system,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        category = model_router_service._classify_failure(exc)
        return GenerationOutcome(
            model=model, text=None, failure_category=category, latency_ms=latency_ms,
            prompt_tokens=None, completion_tokens=None,
        )

    latency_ms = int((time.monotonic() - start) * 1000)
    try:
        prompt_tokens = raw.usage.input_tokens
        completion_tokens = raw.usage.output_tokens
        text, _tool_uses = model_router_service._extract_response_parts(raw)
    except Exception as exc:
        category = model_router_service._classify_failure(exc)
        return GenerationOutcome(
            model=model, text=None, failure_category=category, latency_ms=latency_ms,
            prompt_tokens=None, completion_tokens=None,
        )

    return GenerationOutcome(
        model=model, text=text, failure_category=None, latency_ms=latency_ms,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
    )
