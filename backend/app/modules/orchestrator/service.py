from app.modules.model_router import service as model_router_service
from app.modules.orchestrator.schemas import HistoryTurn

# Prompt-level instructions only — NOT code-enforced. The code-level
# guarantee that no write can actually happen regardless of what the
# model says lives entirely in the fact that this module (and chat/)
# never import a create_*/update_*/delete_* function from any other
# module — see this module's own test proving that, plus chat's
# adversarial test proving the database stays unchanged even if the
# model doesn't follow these instructions.
_SYSTEM_INSTRUCTIONS = (
    "You are BAZRA's assistant, answering questions about the user's own tasks, "
    "calendar, inbox, and life areas.\n\n"
    "Rules you must follow:\n"
    "- You can only READ the data provided below in \"Current Data\" — you have NO "
    "ability to create, edit, or delete any task, calendar event, inbox item, or "
    "life area in this conversation.\n"
    "- If the user asks you to create, add, edit, update, delete, or otherwise "
    "change anything, say plainly that this isn't available yet in this version — "
    "do not claim to have done it, and do not pretend the change happened.\n"
    "- Only state facts that are explicitly present in \"Current Data\" below. If "
    "something isn't there, say you don't have that information rather than "
    "guessing.\n"
    "- Clearly distinguish what the data explicitly shows (\"your task list "
    "shows...\") from any inference you're making (\"it looks like you might be "
    "busy this week, based on...\").\n"
    "- The data below reflects a limited, bounded snapshot — some items may be "
    "omitted if there were too many to show; the data will say so explicitly when "
    "that happens.\n"
)


class OrchestratorError(Exception):
    """Wraps model_router_service.ModelRouterError so callers of this
    module don't need to import model_router directly."""


def _build_system_prompt(context: str) -> str:
    return f"{_SYSTEM_INSTRUCTIONS}\n## Current Data\n{context}"


def generate_reply(history: list[HistoryTurn], context: str, user_message: str) -> str:
    """Coordinates inputs and the provider call. Never touches the
    database itself — history and context are both handed in as plain
    data by the caller (Chat), which is what avoids both a circular
    import (Chat would otherwise need to call back into this module,
    which would need to call back into Chat's own storage to read
    history) and any direct DB access from this module.

    NOT a pure function: the underlying model call is a real side
    effect (network I/O, real cost, non-deterministic output). The
    history-as-argument design solves the circular-import and
    direct-DB-access concerns; it does not make this function pure.
    """
    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    messages.append({"role": "user", "content": user_message})

    try:
        response = model_router_service.complete(
            purpose="chat_completion",
            messages=messages,
            system=_build_system_prompt(context),
        )
    except model_router_service.ModelRouterError as exc:
        raise OrchestratorError(str(exc)) from exc

    return response.text
