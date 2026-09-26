import re

# Deterministic, NOT a general intent classifier. Catches only phrasings
# explicit enough to match a fixed regex with confidence — "delete my
# meeting with Bob", "mark X as done", "create an event tomorrow". It is
# EXPECTED to miss less direct phrasings ("I won't be free Tuesday
# anymore, can you sort that out"). Those still reach the model — this
# function protects the user experience for the clear cases; it does
# not and cannot protect the database from a false claim on a phrasing
# it misses. That's what the adversarial test in chat/tests/test_chat.py
# is for, and it's a genuinely separate guarantee (structural, not
# dependent on this detection).
#
# Checkpoint 3.3: task CREATION deliberately no longer matches here (the
# CREATE patterns' noun list dropped "task"/"مهمة") — that phrasing now
# reaches the Orchestrator, which may offer propose_create_task.
#
# Checkpoint 3.10: task UPDATE (mark done/reopen, reschedule, rename,
# edit description, reassign life area) similarly no longer matches —
# "task" and "due date" were dropped from the edit/update noun list, and
# the standalone mark-done pattern was removed entirely (nothing else in
# this app has a "done" concept to mark), so that phrasing now reaches
# the Orchestrator, which may offer propose_update_task. Calendar/Inbox/
# Life-Area edits and deletes are unaffected and still decline here,
# since only Task writes are implemented so far.
#
# Checkpoint 3.13: task DELETION similarly narrowed — "task" (and its
# Arabic equivalent "مهمة") dropped from the delete/remove/cancel noun
# list, the same precedent as the two narrowings above, so that phrasing
# now reaches the Orchestrator, which may offer propose_delete_task.
# Event/meeting/reminder/life-area deletion are deliberately left in
# this list and still decline exactly as before — this checkpoint only
# narrows what already-implemented capability (Task) is exempted, it
# does not touch the unimplemented ones.
_WRITE_INTENT_PATTERNS_EN = [
    re.compile(r"\b(create|add|schedule|make)\b.{0,40}\b(event|meeting|reminder)\b", re.IGNORECASE),
    re.compile(r"\b(delete|remove|cancel)\b.{0,40}\b(event|meeting|reminder|life area)\b", re.IGNORECASE),
    re.compile(r"\b(update|edit|change|reschedule|rename|move)\b.{0,40}\b(life area)\b", re.IGNORECASE),
]

# A good-faith attempt at common, unambiguous Arabic write phrasings —
# NOT verified with native-speaker confidence the way the English list
# above is. Arabic's rich verb conjugation and dialectal variation make
# a simple keyword-regex approach considerably weaker here than for
# English; this is a real, stated limitation, not a solved problem.
# Flagged for native-speaker review before this ships to real usage.
_WRITE_INTENT_PATTERNS_AR = [
    re.compile(r"(ضيف|أضف|اضافة|سجل)\s+.{0,20}(حدث|موعد|تذكير)"),
    re.compile(r"(احذف|امسح|الغاء|إلغاء)\s+.{0,20}(حدث|موعد|تذكير)"),
    re.compile(r"(عدل|غير|غيّر)\s+.{0,20}(حدث|موعد)"),
]

WRITE_UNAVAILABLE_MESSAGE = (
    "I can't create, edit, or delete anything yet — that's not available in this "
    "version. I can answer questions about your existing tasks, calendar, inbox, "
    "and life areas."
)


def detect_clear_write_intent(message: str) -> bool:
    """See module docstring above for what this does and does not
    guarantee."""
    return any(pattern.search(message) for pattern in _WRITE_INTENT_PATTERNS_EN + _WRITE_INTENT_PATTERNS_AR)
