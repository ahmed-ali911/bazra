import re

# Deterministic, NOT a general intent classifier. Catches only phrasings
# explicit enough to match a fixed regex with confidence — "create a
# task called X", "delete my meeting with Bob", "mark X as done". It is
# EXPECTED to miss less direct phrasings ("I won't be free Tuesday
# anymore, can you sort that out"). Those still reach the model — this
# function protects the user experience for the clear cases; it does
# not and cannot protect the database from a false claim on a phrasing
# it misses. That's what the adversarial test in chat/tests/test_chat.py
# is for, and it's a genuinely separate guarantee (structural, not
# dependent on this detection).
_WRITE_INTENT_PATTERNS_EN = [
    re.compile(r"\b(create|add|schedule|make)\b.{0,40}\b(task|event|meeting|reminder)\b", re.IGNORECASE),
    re.compile(r"\b(delete|remove|cancel)\b.{0,40}\b(task|event|meeting|reminder|life area)\b", re.IGNORECASE),
    re.compile(r"\b(update|edit|change|reschedule|rename|move)\b.{0,40}\b(task|event|due date|life area)\b", re.IGNORECASE),
    re.compile(r"\bmark\b.{0,40}\b(done|complete|finished)\b", re.IGNORECASE),
]

# A good-faith attempt at common, unambiguous Arabic write phrasings —
# NOT verified with native-speaker confidence the way the English list
# above is. Arabic's rich verb conjugation and dialectal variation make
# a simple keyword-regex approach considerably weaker here than for
# English; this is a real, stated limitation, not a solved problem.
# Flagged for native-speaker review before this ships to real usage.
_WRITE_INTENT_PATTERNS_AR = [
    re.compile(r"(ضيف|أضف|اضافة|سجل)\s+.{0,20}(مهمة|حدث|موعد|تذكير)"),
    re.compile(r"(احذف|امسح|الغاء|إلغاء)\s+.{0,20}(مهمة|حدث|موعد|تذكير)"),
    re.compile(r"(عدل|غير|غيّر)\s+.{0,20}(مهمة|حدث|موعد|تاريخ)"),
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
