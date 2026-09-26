from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.home import service as home_service
from app.modules.home.schemas import HomeSummary
from app.modules.life_areas import service as life_areas_service
from app.modules.life_areas.schemas import MyWorldSummary

# Bounded context policy — concrete numbers, stated as defaults, not
# fixed forever. Reuses home_service.build_home_summary and
# life_areas_service.build_my_world_summary directly rather than
# inventing new queries — both are already built, already tested.
_MAX_ITEMS_PER_SECTION = 15
_MAX_LIFE_AREAS = 50
_MAX_CONTEXT_CHARS = 8000


def _format_task_line(task) -> str:
    """task_id (Checkpoint 3.10) mirrors _format_memory_line's own
    mem_id convention (chat/service.py) — the same "expose the real id
    so a later propose_* tool can reference it" need, this time for
    propose_update_task. Never previously needed since nothing before
    3.10 referenced an existing task by id."""
    due = f", due {task.due_at.isoformat()}" if task.due_at else ""
    return f"- {task.title}{due} (task_id={task.id})"


def _format_agenda_line(item) -> str:
    return f"- [{item.source}] {item.title} at {item.starts_at.isoformat()}"


def _format_section(title: str, lines: list[str], total_count: int) -> str:
    """Truncates to _MAX_ITEMS_PER_SECTION and says so explicitly when it
    does — "Anytime" is the one section with no natural bound in the
    underlying query, so this is what lets the assistant honestly say
    "I can see 15 of your 27 anytime tasks" rather than silently
    implying the list is exhaustive.
    """
    shown = lines[:_MAX_ITEMS_PER_SECTION]
    text = f"{title}:\n" + ("\n".join(shown) if shown else "(none)")
    remaining = total_count - len(shown)
    if remaining > 0:
        text += f"\n... and {remaining} more not shown"
    return text


def gather_context(db: Session, space_id: int, tomorrow_start: datetime, window_end: datetime) -> str:
    """Space isolation is inherited, not re-implemented: both
    aggregations below already go through scoped_query with the
    request's own resolved space_id — there is no code path here that
    could return another space's data, because the underlying functions
    structurally can't.
    """
    home_summary: HomeSummary = home_service.build_home_summary(db, space_id, tomorrow_start, window_end)
    my_world: MyWorldSummary = life_areas_service.build_my_world_summary(db, space_id)

    sections = [
        _format_section(
            "Focus Today",
            [_format_task_line(t) for t in home_summary.focus_today],
            len(home_summary.focus_today),
        ),
        _format_section(
            "Coming Up (next 7 days)",
            [_format_agenda_line(i) for i in home_summary.coming_up],
            len(home_summary.coming_up),
        ),
        _format_section(
            "Needs Attention (unread inbox)",
            [f"- {i.title}" for i in home_summary.needs_attention],
            len(home_summary.needs_attention),
        ),
        _format_section(
            "Anytime (open tasks with no due date)",
            [_format_task_line(t) for t in home_summary.anytime],
            len(home_summary.anytime),
        ),
    ]

    life_area_lines = [
        f"- {area.name}: {area.open_task_count} open"
        + (f", most urgent due {area.most_urgent_due_at.isoformat()}" if area.most_urgent_due_at else "")
        for area in my_world.life_areas[:_MAX_LIFE_AREAS]
    ]
    life_area_lines.append(f"- Unassigned: {my_world.unassigned.open_task_count} open")
    sections.append(_format_section("Life Areas", life_area_lines, len(life_area_lines)))

    context = "\n\n".join(sections)
    if len(context) > _MAX_CONTEXT_CHARS:
        # A defensive backstop against unexpectedly long titles, on top
        # of the per-section item caps above.
        context = context[:_MAX_CONTEXT_CHARS] + "\n... (context truncated)"
    return context
