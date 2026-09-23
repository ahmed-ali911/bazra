from decimal import Decimal

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BaseModel


class AiTrace(BaseModel):
    """Observability record for every outbound model-provider call —
    written unconditionally with respect to the model call's own
    success/failure, before this system's first real, cost-incurring
    call (Checkpoint 3.1). See model_router/service.py's complete() for
    the exact boundaries this guarantee does and doesn't cover (a
    tracing-infrastructure failure, e.g. the database itself being
    unavailable, is logged, not silently claimed as unconditional).

    Global (no SpaceScopedMixin), and deliberately no user_id: this is
    system-level operational telemetry, not space-owned or per-user
    content — the same category as User/UserSession, not
    Task/CalendarEvent. Contrast with Memory (Phase 3, later checkpoint),
    which DOES need explicit per-user ownership since it stores personal
    facts that must never leak across users; AiTrace stores none of
    that, so this asymmetry is deliberate, not an inconsistency.

    Never stores prompt/response text or provider secrets — see
    service.py's _record_trace and this module's explicit absence tests.
    """

    __tablename__ = "ai_traces"

    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # "success" | "error"
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String, nullable=True)
