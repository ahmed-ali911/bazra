from dataclasses import dataclass
from typing import Literal

#: Which of the two paths actually produced the returned text — the
#: one piece of information a later (4.5e) surfacing consumer needs to
#: tell apart a model-authored, verified opening from an application-
#: authored deterministic one. Never persisted by this checkpoint.
NarrationSource = Literal["model", "fallback"]

#: Kept deliberately minimal (Checkpoint 4.5d's own brief: "do not
#: build a large error taxonomy") — just enough for tests/observability
#: to distinguish WHY a fallback was used, never a general error code
#: system.
FallbackReason = Literal[
    "narration_failed", "narration_empty", "verification_blocked", "verification_failed"
]


@dataclass(frozen=True)
class NarrationResult:
    """The one typed result generate_app_opened_narration ever returns.
    `fallback_reason` is always None when source="model", and always
    set to one of the four FallbackReason values when source="fallback"
    — never the reverse combination.
    """

    text: str
    source: NarrationSource
    fallback_reason: FallbackReason | None = None
