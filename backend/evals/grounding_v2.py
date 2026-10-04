"""Checkpoint 5.5A — a hardened, deterministic, provider-free grounding
check: `check_on_topic_v2`.

======================================================================
WHY V1 WAS INSUFFICIENT (discovery, from the ACCEPTED 5.5 report only —
never from inspecting individual holdout candidate texts while
designing this module; see this module's own test file for the strict
data-separation statement)
======================================================================

V1 (`evals.proactive_narration.check_on_topic`, now preserved verbatim
as `check_on_topic_v1`) requires the ENTIRE `facts["title"]` string to
appear as an exact, byte-for-byte substring of the candidate. The
accepted Checkpoint 5.5 report recorded, at a high level, that its
dominant failure mode was natural grounded paraphrase (a title's own
words reordered, a function word dropped, a title nominalized rather
than quoted) being rejected on a technicality — not genuine wrong-topic
narration. That is the ONLY fact taken from the 5.5 report; no specific
candidate wording from it informed anything below.

======================================================================
V2 PHILOSOPHY: CONTENT ANCHORS, NOT WHOLE-PHRASE MATCHING
======================================================================

The core idea is linguistic, not holdout-derived: a task/event/concern
title is typically GENERIC-ACTION-VERB + CONTENT (a person, object, or
distinctive noun). The generic verb/noun (call, water, pay, submit,
task, meeting, مهمة, اجتماع, اتصل) carries little distinguishing
information on its own (the brief's own explicit "weak anchor"
examples); the CONTENT (a name, a specific object, a distinctive noun)
is what actually identifies WHICH concern is being discussed. V2 therefore:

1. Tries the cheap, strong signals first (raw exact substring — V1's
   own mechanism, kept as the strongest possible evidence; then a
   conservatively-normalized substring, handling only Unicode/
   diacritic/alef-variant/punctuation/case noise, never meaning-level
   paraphrase).
2. Falls back to CONTENT-ANCHOR matching only when neither succeeds:
   extracts the title's own non-generic tokens (above a minimum
   length, to avoid spurious short-substring hits) and requires EVERY
   one of them to appear in the normalized candidate. A title with NO
   extractable content anchors (all-generic or too-short) gets NO
   anchor-based pass path at all — conservative by construction, per
   the brief's own "if the evaluator cannot establish grounding
   confidently: FAIL conservatively, do not guess."
3. Even when grounded by one of the above, still checks for an
   explicit DISTRACTOR title (the existing, unchanged V1 mechanism)
   and for two bounded, conservative PIVOT signals — a different
   English proper-noun introduced as an apparent new target
   (`conflicting_entity`), and a different recognized domain action
   verb introduced in place of the title's own
   (`conflicting_action`) — because "the right entity is merely
   MENTIONED" is not the same claim as "the narration is actually
   CENTERED on the right concern" (the brief's own section 10
   distinction).

======================================================================
WHAT V2 STILL CANNOT PROVE (honest, documented blind spots — see this
module's own test file for the specific proofs)
======================================================================

- Cross-script name transliteration (an English title name vs. its
  Arabic transliteration, e.g. a Latin name vs. the same name written
  in Arabic script) is NOT attempted — no transliteration table is
  built. A candidate narrating the right concern entirely in a
  different script than the title was written in may still fail
  anchor-matching. This is a REAL, acknowledged limitation, not solved
  here, and not papered over.
- `conflicting_entity` only recognizes Western-capitalization-shaped
  English proper nouns — it has no Arabic proper-noun detection
  mechanism (Arabic has no capitalization to key off).
- `conflicting_action` only recognizes a small, closed, explicit list
  of domain action verbs — a synonym or action word outside that list
  is not detected as a conflict.
- This remains a LEXICAL/STRUCTURAL evidence check, not semantic
  understanding — "grounded by the mechanisms above" is reported, not
  "proved semantically correct."

V2 is a strictly ADDITIVE, separately-versioned check — it does not
replace, alias over, or change the behavior of `check_on_topic`/
`check_on_topic_v1` (see evals/proactive_narration.py, where the
historical name is preserved verbatim). Nothing in this module is
reachable from `evals.report`'s own frozen 5.4 suite.
"""

import re
import unicodedata

from evals.schemas import CheckResult, EvaluationCase

GROUNDING_VERSION = "v2"

# ---- normalization (conservative — see module docstring, section 8) --
#
# Documented, individually, per the brief's own explicit requirement.

_TASHKEEL_RE = re.compile(r"[ً-ْٰـ]")  # diacritics + tatweel
_ALEF_VARIANTS_RE = re.compile(r"[إأآٱ]")
_ALEF_MAKSURA_RE = re.compile(r"ى")
_PUNCTUATION_RE = re.compile(r"[؟،؛!\.\,\?\!\"'“”‘’:;()\[\]\-—–]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Unicode NFKC, diacritic/tatweel removal, alef-variant and alef-
    maksura unification (standard, low-risk Arabic preprocessing — see
    module docstring), punctuation stripping, whitespace collapsing,
    and casefolding. Deliberately does NOT touch ة/ه (a feminine-noun
    marker vs. a pronoun suffix — conflating them risks real meaning
    loss) and does NOT strip the ال- definite article in general (only
    one narrow, documented exception inside `_anchor_matches`, never
    here)."""
    text = unicodedata.normalize("NFKC", text)
    text = _TASHKEEL_RE.sub("", text)
    text = _ALEF_VARIANTS_RE.sub("ا", text)
    text = _ALEF_MAKSURA_RE.sub("ي", text)
    text = _PUNCTUATION_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.casefold()


# ---- content-anchor extraction ----------------------------------------
#
# Two closed, explicit, small word lists — chosen from the brief's own
# worked examples (sections 5/9), never from holdout inspection.

_FUNCTION_WORDS = frozenset({
    # Arabic prepositions/conjunctions/particles
    "في", "من", "إلى", "الى", "على", "عن", "مع", "و", "أو", "او", "ب", "ل",
    # English function words/prepositions (including topic-introducing
    # ones — "about"/"regarding" commonly precede the real anchor, the
    # same role "مع" already plays in the brief's own worked example)
    "the", "a", "an", "to", "for", "of", "your", "you", "is", "are", "this", "that", "it",
    "about", "regarding", "on", "up", "with", "from",
})

# Domain "meta" nouns the brief itself names as generic (section 9),
# plus the domain action VERBS the brief names as generic (section 5:
# "task, meeting, call, appointment, مهمة, اجتماع, مكالمة"). A title
# token matching one of these (after normalization) is never, by
# itself, a usable content anchor.
# Checkpoint 5.5A — mapped to a small set of CANONICAL ACTION FAMILIES,
# not treated as flat, mutually-distinct strings: "اتصل"/"اتصال"/
# "مكالمة" are three real Arabic surface forms of the exact same
# "calling" action (verb, nominalized/verbal noun, and the plain noun
# itself) — without this grouping, a title using "اتصل" and a
# candidate's own correct nominalized paraphrase "الاتصال" would be
# wrongly treated as TWO DIFFERENT actions by _conflicting_action
# below. Only genuinely DIFFERENT families (e.g. "call" vs "email")
# are meant to conflict — same-family surface-form variation must not.
# "cancel"/"delete"/"postpone"/"reschedule" mirror this repository's
# own pre-existing mutation-verb vocabulary (chat/write_intent.py), an
# independent, non-holdout source already established before this
# checkpoint.
_ACTION_FAMILIES: dict[str, str] = {
    "call": "call", "اتصل": "call", "اتصال": "call", "مكالمة": "call",
    "water": "water", "اسقي": "water", "اسق": "water",
    "pay": "pay", "ادفع": "pay",
    "submit": "submit", "سلم": "submit",
    "book": "book",
    "renew": "renew", "جدد": "renew",
    "return": "return", "ارجع": "return",
    "clean": "clean", "نظف": "clean",
    "reply": "reply", "رد": "reply",
    "finish": "finish", "خلص": "finish",
    "send": "send", "email": "send", "text": "send", "message": "send", "ابعت": "send",
    "meet": "meet", "قابل": "meet",
    "follow": "follow",
    "cancel": "cancel", "الغي": "cancel",
    "delete": "delete", "احذف": "delete",
    "postpone": "postpone", "reschedule": "postpone", "أجل": "postpone", "اجل": "postpone",
    "update": "update",
}

_DOMAIN_ACTION_VERBS = frozenset(_ACTION_FAMILIES.keys())

# Domain "meta" nouns the brief itself names as generic (section 9) —
# a SEPARATE set from the action verbs above (nouns, not verbs), both
# excluded from content-anchor extraction.
_GENERIC_DOMAIN_NOUNS = frozenset({
    "task", "event", "meeting", "reminder", "appointment", "thing", "item",
    "مهمة", "موعد", "اجتماع", "تذكير", "حدث", "حاجة",
})

_GENERIC_DOMAIN_WORDS = _GENERIC_DOMAIN_NOUNS | _DOMAIN_ACTION_VERBS

_MIN_ANCHOR_LENGTH = 3


def _tokenize(normalized_text: str) -> list[str]:
    return [t for t in normalized_text.split(" ") if t]


def _extract_anchors(title: str) -> list[str]:
    """The title's own non-generic, non-function-word, sufficiently
    long tokens — the "content anchors" grounding is checked against.
    An empty result means the title offers no usable anchor at all
    (all-generic or too-short) — the caller must then refuse to pass
    via anchor-matching at all (conservative by construction)."""
    normalized = normalize_text(title)
    anchors = []
    for token in _tokenize(normalized):
        if token in _FUNCTION_WORDS or token in _GENERIC_DOMAIN_WORDS:
            continue
        if len(token) < _MIN_ANCHOR_LENGTH:
            continue
        anchors.append(token)
    return anchors


# Closed, explicit set of Arabic single-letter "attached clitic"
# prefixes (inseparable prepositions/conjunctions) plus the definite
# article — standard, well-known Arabic orthography, not derived from
# any holdout text. Ordered longest-first so "بال"/"وال"/etc. (particle
# + article) are tried before the bare single-letter particle alone.
_ATTACHED_PARTICLES = ("بال", "وال", "فال", "كال", "لل", "ال", "ب", "ل", "و", "ف", "ك")


def _anchor_surface_forms(anchor: str) -> list[str]:
    """The anchor itself, plus — ONLY when it starts with one of the
    closed attached-particle set above and enough of the word remains
    — the bare form with that particle stripped. Bounded (at most a
    handful of variants per anchor) and applied ONLY to the anchor
    extracted from the TITLE, never by mutating the candidate text
    itself. This is the same "safe, meaning-preserving function-word
    variation" principle the brief's own "اجتماع مع فريق التسويق" vs
    "اجتماع فريق التسويق" example establishes for a dropped
    preposition, generalized to the attached (not space-separated)
    case — a title's own "السباك"/"بمنى" matching a candidate's bare
    "سباك"/"منى". A real, acknowledged residual risk (documented in the
    module docstring): a name that genuinely BEGINS with one of these
    letters (e.g. "بسام"/Bassam) would also generate a stripped variant
    ("سام"/Sam) that happens to be a different real name — not solved
    here.
    """
    forms = [anchor]
    for particle in _ATTACHED_PARTICLES:
        if anchor.startswith(particle) and len(anchor) - len(particle) >= _MIN_ANCHOR_LENGTH:
            forms.append(anchor[len(particle):])
    return forms


def _anchor_matches(anchor: str, normalized_candidate: str) -> bool:
    """Substring containment of the anchor, or any of its bounded
    attached-particle-stripped surface forms, against the normalized
    candidate text — see `_anchor_surface_forms` for exactly which
    variants are tried and why. No other stemming/morphological
    transformation is performed anywhere in this module."""
    return any(form in normalized_candidate for form in _anchor_surface_forms(anchor))


# ---- pivot/distractor signals -----------------------------------------

_ENGLISH_NAME_RE = re.compile(r"\b[A-Z][a-z]+\b")
# Common capitalized English words that are NOT proper nouns — sentence
# starters, pronouns, and the domain action verbs themselves (which are
# frequently capitalized at the start of a title/sentence). Kept small
# and explicit; a real name colliding with this list is an acknowledged
# limitation, not a silent failure.
_ENGLISH_NAME_STOPLIST = frozenset({
    "the", "this", "that", "your", "you", "it", "is", "are", "do", "does",
    "want", "need", "still", "looks", "call", "water", "pay", "submit",
    "book", "renew", "return", "clean", "reply", "finish", "send", "meet",
    "email", "text", "done", "ok", "okay",
})


def _english_names(text: str) -> set[str]:
    return {m.lower() for m in _ENGLISH_NAME_RE.findall(text)} - _ENGLISH_NAME_STOPLIST


def _conflicting_entity(title: str, candidate: str) -> bool:
    """English-only (documented limitation — see module docstring): if
    the title names a specific capitalized English person/entity, and
    the candidate introduces a DIFFERENT such name not present in the
    title, treat this as a pivot to a different target — even though
    the title's own name may ALSO still be present (the brief's own
    "Call Hussein" / "call Sarah next" example: both names appear, but
    the narration pivoted)."""
    title_names = _english_names(title)
    if not title_names:
        return False
    candidate_names = _english_names(candidate)
    return bool(candidate_names - title_names)


def _conflicting_action(title: str, candidate: str) -> bool:
    """If the title's own action verb belongs to one of the closed
    `_ACTION_FAMILIES`, and the candidate contains a verb from a
    DIFFERENT family, treat this as an action-pivot signal. Compares
    FAMILIES, not raw surface strings — "اتصل" (title) and "الاتصال"
    (candidate's own correct nominalized paraphrase) are the same
    family and must never conflict with each other; only a genuinely
    different family (e.g. "email") does.

    Uses substring containment (not exact token equality) against each
    normalized text — deliberately, so an inflected English form
    ("renewing"/"renewed") still matches its base entry ("renew") the
    same way Arabic attached forms already need substring handling
    elsewhere in this module; this is NOT a general stemmer, only a
    prefix-of-the-whole-word containment check against a small closed
    vocabulary. Deliberately narrow: an action-verb substitution using
    a word outside this closed set (e.g. a true synonym) is NOT
    detected — see module docstring.
    """
    normalized_title = normalize_text(title)
    normalized_candidate = normalize_text(candidate)
    title_families = {_ACTION_FAMILIES[v] for v in _DOMAIN_ACTION_VERBS if v in normalized_title}
    if not title_families:
        return False
    candidate_families = {_ACTION_FAMILIES[v] for v in _DOMAIN_ACTION_VERBS if v in normalized_candidate}
    return bool(candidate_families - title_families)


def check_on_topic_v2(case: EvaluationCase) -> CheckResult:
    """The hardened grounding check — see module docstring for the full
    design rationale and documented blind spots."""
    title = case.facts.get("title", "")
    candidate = str(case.candidate)

    reason_code: str | None = None
    if title and title in candidate:
        reason_code = "exact_title_match"
    else:
        normalized_title = normalize_text(title) if title else ""
        normalized_candidate = normalize_text(candidate)
        if normalized_title and normalized_title in normalized_candidate:
            reason_code = "normalized_title_match"
        else:
            anchors = _extract_anchors(title) if title else []
            if anchors and all(_anchor_matches(a, normalized_candidate) for a in anchors):
                reason_code = "high_confidence_grounded_overlap"

    if reason_code is None:
        return CheckResult(
            "on_topic_v2", False,
            f"insufficient_grounding_evidence: no exact, normalized, or anchor-based match for {title!r}",
        )

    for distractor in case.facts.get("distractor_titles", ()):
        if distractor in candidate:
            return CheckResult("on_topic_v2", False, f"distractor_detected: mentions unrelated concern {distractor!r}")

    if _conflicting_entity(title, candidate):
        return CheckResult("on_topic_v2", False, "conflicting_entity: candidate introduces a different named target")

    if _conflicting_action(title, candidate):
        return CheckResult("on_topic_v2", False, "conflicting_action: candidate describes a different action")

    return CheckResult("on_topic_v2", True, f"{reason_code}: grounded in {title!r}")
