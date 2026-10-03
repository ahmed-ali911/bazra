import re
from dataclasses import dataclass
from typing import Literal

# Checkpoint 5.2 — a deliberately NARROW, auditable recognizer for the
# small set of Chat messages whose meaning can be determined WITHOUT a
# model call: a pure, authoritative "list my existing X" question. This
# is NOT a general intent/NLU engine (see module docstring precedent:
# write_intent.py's own "deliberate, not general" framing) — it is
# closed-form pattern matching against a small, explicit set of phrase
# families, the same philosophy as write_intent.py and
# chat/service.py's own _classify_narrow_yes_no, extended from exact
# full-string membership to a small set of fullmatch regex patterns (to
# tolerate the handful of accepted word-order/article variants below)
# while remaining exactly as closed and auditable.
#
# PRECISION OVER RECALL (the 5.2 brief's own explicit safety mandate):
# a false-positive deterministic answer to a message that actually
# needed judgment/mutation/coreference handling is far more dangerous
# than missing an optimization opportunity and falling through to the
# existing model path. Two independent safety layers enforce this:
#   1. Negative guards (_NEGATIVE_GUARD_PATTERNS) — checked FIRST; any
#      hit on a mutation/judgment/recommendation keyword anywhere in
#      the message immediately returns None, before any positive
#      pattern is even attempted.
#   2. Positive patterns use `fullmatch`, not `search` — the ENTIRE
#      normalized message must match one of the small whitelisted
#      forms. A judgment/mutation clause appended to an otherwise
#      recognizable retrieval phrase ("إيه التاسكات المفتوحة وأنهي
#      واحدة أبدأ بيها؟") already fails to match on this basis alone,
#      independent of the negative guards above — defense in depth,
#      not a single point of failure.
RetrievalDomain = Literal["tasks", "calendar", "inbox"]
RetrievalView = Literal["open", "overdue", "today", "unread"]
RetrievalLanguage = Literal["ar", "en"]


@dataclass(frozen=True)
class DeterministicRetrievalIntent:
    """language (Checkpoint 5.2) is decided AT RECOGNITION TIME, from
    which phrase family matched — never a separate language-detection
    step. Because every positive pattern belongs to either the Arabic
    or the English family by construction, this is exact, not a guess
    (see module docstring, section 18 of the 5.2 brief)."""

    domain: RetrievalDomain
    view: RetrievalView
    language: RetrievalLanguage


def _normalize(content: str) -> str:
    """Minimal, deterministic normalization only — trim, collapse
    internal whitespace, drop a single trailing run of
    question/exclamation/period marks (Arabic or Latin). Never
    casefolds Arabic (it has no case) and never touches Arabic
    orthography beyond this — no alef/ya normalization subsystem is
    introduced here; the patterns below instead spell out the small
    number of accepted definite-article variants explicitly (see
    `ال?` in the patterns), which is auditable in a way a general
    normalizer would not be. (The pattern comments below spell out
    "(ال)?" — a fully optional definite-article prefix — not "ال?",
    which would wrongly make only the second letter optional.)
    """
    normalized = content.strip()
    normalized = re.sub(r"[؟?!.]+$", "", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


# ---- negative guards ---------------------------------------------------
#
# Checked against the FULL normalized message (search, not fullmatch —
# these are deliberately broad keyword hits anywhere in the message,
# the opposite precision direction from the positive patterns below).
# Illustrative, not exhaustive — inspected against this repository's
# own accepted vocabulary (write_intent.py's mutation-verb lists,
# orchestrator_service._PROACTIVE_NARRATION_INSTRUCTIONS' framing of
# "judgment"), not invented from scratch.

_MUTATION_GUARD_EN = re.compile(
    r"\b(create|add|schedule|make|delete|remove|cancel|postpone|move|reschedule|"
    r"change|update|edit|rename|mark|complete|finish|confirm|reject|approve|decline|set)\b",
    re.IGNORECASE,
)
#
# \b (LEFT boundary only, deliberately no trailing \b): Arabic verbs
# commonly take a directly-attached pronoun suffix with no separator
# ("أجلها" = postpone + it, "امسحها" = delete + it) — a trailing \b
# would miss exactly these real mutation phrasings. A LEFT boundary
# alone is still enough to prevent a root-sharing false hit: "أخر"
# (postpone) must not match inside "متأخرة" (overdue) merely because
# they share a root, since there is no word boundary immediately
# before the "أخر" substring embedded inside "متأخرة" (confirmed
# against the real match engine, not assumed from Arabic grammar).
_MUTATION_GUARD_AR = re.compile(
    r"\b(أجل|اجل|أجّل|أخر|أخّر|غير|غيّر|احذف|امسح|الغاء|إلغاء|اعمل|ضيف|أضف|اضافة|"
    r"خلي|خلّي|سجل|اكمل|خلص|وافق|ارفض)"
)

_JUDGMENT_GUARD_EN = re.compile(
    r"\b(most important|which (one|task|event)|recommend|suggest|priorit|why|should i|"
    r"what do you think|advice|rank|order them|start with)\b",
    re.IGNORECASE,
)
_JUDGMENT_GUARD_AR = re.compile(r"\b(أهم|ابدأ|ليه|رأيك|رايك|تنصح|رتب|الأولوية|أولوية)")

_NEGATIVE_GUARD_PATTERNS = [_MUTATION_GUARD_EN, _MUTATION_GUARD_AR, _JUDGMENT_GUARD_EN, _JUDGMENT_GUARD_AR]


def _has_negative_guard(normalized: str) -> bool:
    return any(pattern.search(normalized) for pattern in _NEGATIVE_GUARD_PATTERNS)


# ---- positive patterns --------------------------------------------------
#
# Each entry: (domain, view, language, [compiled fullmatch patterns]).
# A SMALL, explicit, closed set — deliberately not a catalog. New
# phrasings are added here one at a time, with the same scrutiny as
# any other entry, never via a generic synonym-expansion mechanism (see
# module docstring's "forbidden" list, mirrored from the 5.2 brief's
# own section 6).

_TASKS_OPEN_AR = [
    re.compile(r"(ايه|إيه) (ال)?تاسكات (ال)?مفتوحة( عندي)?"),
    re.compile(r"(ايه|إيه) (ال)?مهام (ال)?مفتوحة"),
    re.compile(r"عندي تاسكات (ايه|إيه)"),
]
_TASKS_OPEN_EN = [
    re.compile(r"what tasks do i have", re.IGNORECASE),
    re.compile(r"show (me )?my open tasks", re.IGNORECASE),
    re.compile(r"what are my (open )?tasks", re.IGNORECASE),
]

_TASKS_OVERDUE_AR = [
    re.compile(r"(ايه|إيه) (ال)?تاسكات (ال)?متأخرة( عندي)?"),
    re.compile(r"عندي تاسكات متأخرة"),
]
_TASKS_OVERDUE_EN = [
    re.compile(r"what tasks are overdue", re.IGNORECASE),
    re.compile(r"show (me )?my overdue tasks", re.IGNORECASE),
    re.compile(r"what'?s overdue", re.IGNORECASE),
]

# "عندي ايه النهارده" / "what do I have today" are DELIBERATELY excluded
# — the 5.2 brief's own section 30/7 warning: this phrase shape has
# real prior-usage evidence of meaning a BROADER personal-state query
# (Tasks + Calendar + Life Areas combined), not Calendar alone. Only
# the unambiguous "مواعيد"/"calendar"/"events" forms are recognized.
_CALENDAR_TODAY_AR = [
    re.compile(r"عندي مواعيد (ايه|إيه) النهارده"),
    re.compile(r"مواعيدي النهارده"),
]
_CALENDAR_TODAY_EN = [
    re.compile(r"what'?s on my calendar today", re.IGNORECASE),
    re.compile(r"show (me )?my calendar today", re.IGNORECASE),
    re.compile(r"what events do i have today", re.IGNORECASE),
]

_INBOX_UNREAD_AR = [
    re.compile(r"عندي كام حاجة في ال(انبوكس|ـ?inbox)", re.IGNORECASE),
    re.compile(r"في ال(انبوكس|ـ?inbox) ايه", re.IGNORECASE),
]
_INBOX_UNREAD_EN = [
    re.compile(r"what'?s in my inbox", re.IGNORECASE),
    re.compile(r"show (me )?my inbox", re.IGNORECASE),
    re.compile(r"how many (things |items )?(do i have )?in my inbox", re.IGNORECASE),
]

_RETRIEVAL_PATTERNS: list[tuple[RetrievalDomain, RetrievalView, RetrievalLanguage, list[re.Pattern]]] = [
    ("tasks", "open", "ar", _TASKS_OPEN_AR),
    ("tasks", "open", "en", _TASKS_OPEN_EN),
    ("tasks", "overdue", "ar", _TASKS_OVERDUE_AR),
    ("tasks", "overdue", "en", _TASKS_OVERDUE_EN),
    ("calendar", "today", "ar", _CALENDAR_TODAY_AR),
    ("calendar", "today", "en", _CALENDAR_TODAY_EN),
    ("inbox", "unread", "ar", _INBOX_UNREAD_AR),
    ("inbox", "unread", "en", _INBOX_UNREAD_EN),
]


def detect_deterministic_retrieval_intent(content: str) -> DeterministicRetrievalIntent | None:
    """Returns the recognized intent, or None — meaning "fall through
    to the existing Context Assembly / Orchestrator / Model Router
    path", never a secondary classification attempt. See module
    docstring for the two independent precision mechanisms (negative
    guards, checked first; fullmatch-only positive patterns)."""
    normalized = _normalize(content)
    if not normalized:
        return None
    if _has_negative_guard(normalized):
        return None
    for domain, view, language, patterns in _RETRIEVAL_PATTERNS:
        if any(pattern.fullmatch(normalized) for pattern in patterns):
            return DeterministicRetrievalIntent(domain=domain, view=view, language=language)
    return None
