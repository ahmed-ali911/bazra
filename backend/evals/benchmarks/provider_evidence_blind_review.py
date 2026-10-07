"""Checkpoint 5.8A, section 22 — blind human-review design. Ahmed must
not see which provider generated which answer before scoring a
subjective case (section 9's rubric) — this module provides a
DETERMINISTIC, reproducible shuffle (section 22's own explicit
preference: "Do not require random runtime behavior if deterministic
reproducibility is preferable; a frozen shuffle mapping is fine"), never
true runtime randomness, so a re-rendered review page always shows the
same slot assignment for the same case.

No execution happens here — this is the ordering FUNCTION a future
review-report renderer would call once real candidate generations exist
(zero exist today); it is tested here purely as a deterministic,
offline function of (case_id, provider_model_list).
"""

import hashlib


def blind_slot_order(case_id: str, provider_models: tuple[str, ...]) -> tuple[str, ...]:
    """A frozen, deterministic permutation of `provider_models` for this
    one `case_id` — stable across repeated calls/renders (same inputs
    always produce the same order), and DIFFERENT across different
    case_ids (so a reviewer cannot learn "slot A is always Gemini" by
    noticing a pattern across many cases in one sitting).

    Implementation: sort `provider_models` by the hex digest of
    sha256(case_id + provider_model) — a simple, dependency-free,
    deterministic shuffle; not cryptographically meaningful here, only
    used for its uniform, case-dependent ordering property.
    """
    def _sort_key(provider_model: str) -> str:
        return hashlib.sha256(f"{case_id}:{provider_model}".encode("utf-8")).hexdigest()

    return tuple(sorted(provider_models, key=_sort_key))


def slot_labels(n: int) -> tuple[str, ...]:
    """Plain "Response A"/"Response B"/... labels shown to the reviewer
    — never the real provider/model name, which is revealed only after
    scoring is recorded (section 22's own explicit requirement)."""
    return tuple(f"Response {chr(ord('A') + i)}" for i in range(n))


BLIND_REVIEW_PROTOCOL = (
    "1. For each case, compute blind_slot_order(case_id, provider_models) once "
    "and hold it fixed for that case's review.",
    "2. Render each candidate under its slot_labels() label only — no provider "
    "or model name, no tells (e.g. never show raw token counts or latency "
    "alongside a response during scoring, since Gemini's own cost profile is "
    "distinctly cheaper and a latency/length tell could let a reviewer infer "
    "identity).",
    "3. Ahmed scores every rubric dimension (provider_evidence_rubric.py) for "
    "every slot, for this case, before any identity is revealed.",
    "4. Only after scores are recorded for every slot of this case does the "
    "review tool reveal which slot was which provider/model.",
    "5. A revealed identity for case N must never be shown before scoring case "
    "N+1 in a way that could bias it — review proceeds case-by-case, identity "
    "reveal is per-case, not deferred to the end of the whole session.",
)
