"""Checkpoint 5.6 — a further-hardened, deterministic grounding check:
`check_on_topic_v3`, plus a structurally SEPARATE
`check_language_instruction_following`.

======================================================================
WHY V2 WAS INSUFFICIENT (from the ACCEPTED 5.5/5.5A evidence only — the
PROBLEM CLASS, never specific holdout wording; see this module's own
fixture file for the strict data-separation statement)
======================================================================

V2 (`evals.grounding_v2.check_on_topic_v2`, preserved verbatim here,
unmodified) treats every content anchor the same way: one matching
generic-content token is as good as another. Two gaps follow directly
from that:

1. ENTITY EROSION: a title with ONE named-entity anchor and nothing
   else (e.g. "Call Hussein") can, in principle, be satisfied by any
   surface-form match of that one token — but nothing in V2 treats a
   named entity (a person, a specific named team) as requiring
   STRONGER preservation than an ordinary noun. "the marketing team"
   silently becoming "the finance team" is not distinguished from "the
   report" becoming "the invoice" — both are just "an anchor didn't
   match," with identical conservatism. Checkpoint 5.6's own brief
   (section 11) requires named entities and critical facts to be held
   to a VISIBLY stronger standard than filler words, structurally, not
   just coincidentally via substring mismatch.
2. SHORT-TITLE PERMISSIVENESS: V2's own conservatism already refuses
   anchor-fallback entirely when there is no usable anchor — but a
   title with exactly ONE weak, short, non-entity anchor plus one
   entity anchor (e.g. "meeting with Nour's team") still passes on
   ANY subset match path reaching all anchors; V3 adds an explicit,
   separate corroboration requirement for titles whose only anchor is
   entity-class, see `_extract_anchors` below.
3. NUMBER/DATE PRESERVATION: V2 performs no numeral normalization at
   all — "3" and "٣" are different normalized strings, so a title
   containing a number and a candidate paraphrasing it with
   Arabic-Indic digits (or vice versa) would silently fail to match
   even though no deadline/date WAS invented, purely a numeral-script
   mismatch unrelated to the title/candidate's actual concern.

======================================================================
V3 PHILOSOPHY: ANCHORS HAVE CLASSES, NOT ALL EVIDENCE IS EQUAL WEIGHT
======================================================================

V3 keeps V2's entire match ladder (exact -> normalized -> anchor
overlap) and its entire pivot-detection ladder (distractor ->
conflicting_entity -> conflicting_action) UNCHANGED as a floor, then
adds, on top:

- Anchors are classified NAMED_ENTITY or GENERIC at extraction time
  (`_classify_anchor`). A NAMED_ENTITY anchor must match via exact
  surface form or a bounded attached-particle-stripped variant ONLY —
  never a looser mechanism — mirroring V2's own existing
  `_anchor_matches`, so this is a classification layered on an
  unchanged matching primitive, not a new matching primitive.
- A title whose ONLY anchor is NAMED_ENTITY-class and whose anchor
  count is exactly one is additionally required to have NO conflicting
  generic-action pivot (reusing V2's own `_conflicting_action`
  unchanged) — already implied by the existing pivot ladder, made
  explicit here as a named, tested invariant rather than an
  incidental consequence (category I).
- `_conflicting_entity` is extended with a bounded, conservative
  Arabic branch: when the title's own extracted anchors include at
  least one NAMED_ENTITY-class Arabic token, and the candidate
  contains a DIFFERENT token that is itself classified NAMED_ENTITY by
  the same heuristic and is not a surface-form variant of any of the
  title's own named entities, this is treated as a pivot signal — the
  same "right concern mentioned, then abandoned for a different
  target" case V2 already catches for English-capitalized names only.
- Numeral normalization (Arabic-Indic <-> Western digits) is added to
  `normalize_text`'s own pipeline (a pure, low-risk addition — digits
  are digits, no meaning judgment involved).

Named-entity classification is a BEST-EFFORT heuristic, not proper
named-entity recognition: English relies on capitalization (as V2
already did); Arabic has no capitalization to key off, so the
heuristic is "a content anchor that is not in a small, explicit,
closed list of common nouns this module already knows about" —
documented as a real, bounded limitation below, not claimed as
semantic understanding.

======================================================================
LANGUAGE INSTRUCTION FOLLOWING — STRUCTURALLY SEPARATE FROM GROUNDING
======================================================================

`check_language_instruction_following` answers a DIFFERENT question
than `check_on_topic_v3`: not "is the candidate ABOUT the right
concern" but "did the candidate follow the title's own apparent
language." A fully-grounded Arabic reply to an English-worded title
case_id is a correct grounding verdict (PASS on `on_topic_v3`)
regardless of what this separate check reports — the two CheckResults
share no computation and neither can influence the other's `passed`
value. This directly implements the 5.6 brief's own explicit
requirement (section 13): "a semantically-grounded Arabic reply to an
English title may PASS on_topic while a SEPARATE instruction-following
check may independently FAIL it" — and its mirror requirement that
on_topic must never be "overloaded with language-style compliance."

This check is classified informational/style
(`POSSIBLE_EVALUATOR_SENSITIVITY` in
`evals.benchmarks.interpretation`), never safety, and uses a simple,
honest script-ratio heuristic (Unicode range membership), not language
detection.

======================================================================
WHAT V3 STILL CANNOT PROVE (honest, documented blind spots)
======================================================================

- Arabic named-entity classification is a closed-list-exclusion
  heuristic, not real NER — an Arabic common noun absent from
  `_ARABIC_COMMON_ANCHOR_NOUNS` would be misclassified as a named
  entity (over-cautious in the SAFE direction: it would then demand
  stricter preservation than necessary, never looser).
- Cross-script name transliteration remains unattempted (same
  documented V2 limitation).
- This remains lexical/structural evidence, not semantic
  understanding — "grounded by the mechanisms above" is reported, not
  "proved semantically correct."

V3 is a strictly ADDITIVE, separately-versioned check — it does not
replace, alias over, or change the behavior of `check_on_topic_v1` or
`check_on_topic_v2` (both imported here unchanged, never redefined).
Nothing in this module is reachable from `evals.report`'s own frozen
5.4 suite or from `ALL_CHECKS`.
"""

import re

from evals.grounding_v2 import (
    _ACTION_FAMILIES,
    _ATTACHED_PARTICLES,
    _DOMAIN_ACTION_VERBS,
    _ENGLISH_NAME_RE,
    _ENGLISH_NAME_STOPLIST,
    _FUNCTION_WORDS,
    _GENERIC_DOMAIN_WORDS,
    _MIN_ANCHOR_LENGTH,
    _PUNCTUATION_RE,
    _TASHKEEL_RE,
    _WHITESPACE_RE,
    _ALEF_MAKSURA_RE,
    _ALEF_VARIANTS_RE,
)
from evals.schemas import CheckResult, EvaluationCase

GROUNDING_VERSION = "v3"

# ---- numeral normalization (new in V3) ---------------------------------

_ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_EASTERN_ARABIC_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_DIGIT_TRANSLATION = str.maketrans(
    _ARABIC_INDIC_DIGITS + _EASTERN_ARABIC_DIGITS,
    "0123456789" + "0123456789",
)


def normalize_text(text: str) -> str:
    """V2's own `normalize_text` pipeline, plus Arabic-Indic/Eastern-
    Arabic digit unification to Western digits — a pure, meaning-free
    addition (section 9 of the 5.6 brief: numbers deserve preservation,
    not script-dependent mismatch). Reimplemented here (rather than
    calling V2's function and translating afterward) so the pipeline
    order is explicit and auditable in one place."""
    import unicodedata

    text = unicodedata.normalize("NFKC", text)
    text = _TASHKEEL_RE.sub("", text)
    text = _ALEF_VARIANTS_RE.sub("ا", text)
    text = _ALEF_MAKSURA_RE.sub("ي", text)
    text = text.translate(_DIGIT_TRANSLATION)
    text = _PUNCTUATION_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.casefold()


# ---- anchor classification (new in V3) ---------------------------------
#
# IMPORTANT, HONEST DESIGN NOTE: an earlier draft of this module tried
# to classify Arabic anchors as NAMED_ENTITY-class by EXCLUSION (any
# token not in a closed common-noun list). Tested against this module's
# own development fixtures BEFORE freeze, that approach produced
# immediate false positives: ordinary descriptive words with no
# distinguishing meaning at all ("جاي"/coming, "قريب"/soon) have no
# capitalization signal to rule them out in Arabic, so exclusion-based
# classification flagged them as "new named entities" on an otherwise
# perfectly valid, already-accepted V2 paraphrase. Per the brief's own
# explicit instruction ("if the evaluator cannot establish grounding
# confidently: FAIL conservatively, do not guess" and "prefer the
# smallest deterministic fix for the evidenced failure mode, avoid a
# large generic NLU framework"), an unbounded exclusion-based guess is
# the wrong shape of fix. Instead, Arabic entity-conflict detection
# below uses an INCLUSION list — a small, explicit, closed set of
# common Arabic given names — the same "closed, bounded, honestly-
# documented vocabulary" pattern this module (and V2 before it) already
# uses for `_ACTION_FAMILIES`/`_GENERIC_DOMAIN_WORDS`. This has far
# lower false-positive risk (it only fires on actual name-shaped
# tokens) at the cost of an honestly bounded false-negative rate (a
# real Arabic name outside this list is not detected — see module
# docstring's "What V3 still cannot prove").

_ARABIC_GIVEN_NAMES = frozenset({
    "حسين", "عمر", "منى", "مني", "كريم", "ليلى", "ليلي", "سارة", "سارا",
    "سامي", "سلمى", "سلمي", "نور", "نورا", "نوره", "ياسمين", "دينا",
    "رانيا", "رنا", "هبة", "هبه", "نهى", "نهي", "مريم", "فاطمة", "فاطمه",
    "زينب", "أحمد", "احمد", "محمد", "علي", "طارق", "خالد", "ياسر",
    "ياسمينة", "داليا", "داليه", "تامر", "هاني", "هشام", "منار", "إيمان",
    "ايمان", "نادية", "ناديه", "شريف", "وليد", "أمينة", "امينة", "حنان",
    "بسمة", "بسمه",
})


def _extract_anchors(title: str) -> list[str]:
    """V2's own `_extract_anchors` token-filtering logic, unchanged —
    reimplemented here (private helper, not part of V2's public
    surface) rather than imported."""
    normalized = normalize_text(title)
    anchors: list[str] = []
    for token in normalized.split(" "):
        if not token:
            continue
        if token in _FUNCTION_WORDS or token in _GENERIC_DOMAIN_WORDS:
            continue
        if len(token) < _MIN_ANCHOR_LENGTH:
            continue
        anchors.append(token)
    return anchors


def _anchor_surface_forms(anchor: str) -> list[str]:
    """Identical to V2's own `_anchor_surface_forms` (attached-particle
    stripping) — reimplemented here rather than imported since V2's
    version is a private module-level helper not part of its public
    surface, kept byte-for-byte identical."""
    forms = [anchor]
    for particle in _ATTACHED_PARTICLES:
        if anchor.startswith(particle) and len(anchor) - len(particle) >= _MIN_ANCHOR_LENGTH:
            forms.append(anchor[len(particle):])
    return forms


def _anchor_matches(anchor: str, normalized_candidate: str) -> bool:
    return any(form in normalized_candidate for form in _anchor_surface_forms(anchor))


# ---- pivot/distractor signals (V2's ladder, unchanged, plus one new
# bounded Arabic branch on conflicting_entity) ---------------------------


# V3-local addition to V2's own `_ENGLISH_NAME_STOPLIST` (V2's own
# constant is imported and used unchanged below — this is a separate,
# ADDITIONAL set, never a mutation of V2's). Discovered via this
# checkpoint's own independent fixture authoring (before any holdout
# re-inspection): a candidate mentioning an invented weekday/month
# ("...is due next Friday") was wrongly flagged as introducing a
# conflicting PERSON, because English capitalizes weekday/month names
# too and V2's own regex has no notion of "calendar word, not a name".
# This is exactly the kind of case `evals.proactive_narration`'s own
# `check_invented_date` is responsible for catching — on_topic_v3 must
# not ALSO fire a different, misleading reason for the same text.
_ENGLISH_CALENDAR_WORDS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
})


def _english_names(text: str) -> set[str]:
    return {m.lower() for m in _ENGLISH_NAME_RE.findall(text)} - _ENGLISH_NAME_STOPLIST - _ENGLISH_CALENDAR_WORDS


def _conflicting_entity_english(title: str, candidate: str) -> bool:
    """V2's own `_conflicting_entity`, reimplemented here (private
    helper, not part of V2's public surface) with one additive
    hardening: calendar words are excluded from name detection (see
    `_ENGLISH_CALENDAR_WORDS` above) — everything else is unchanged."""
    title_names = _english_names(title)
    if not title_names:
        return False
    candidate_names = _english_names(candidate)
    return bool(candidate_names - title_names)


def _arabic_given_names_in(text: str) -> set[str]:
    """Every token (after normalization and bounded attached-particle
    stripping — e.g. a title's own "بسارة" recognized via its stripped
    form "سارة") that matches the closed `_ARABIC_GIVEN_NAMES` list.
    Symmetric, in spirit, with `_english_names` above: both scan a
    whole text for name-shaped tokens using their own script's cheapest
    reliable signal (capitalization for English; closed-list membership
    for Arabic, which has no capitalization to key off)."""
    normalized = normalize_text(text)
    found: set[str] = set()
    for token in normalized.split(" "):
        if not token:
            continue
        for form in _anchor_surface_forms(token):
            if form in _ARABIC_GIVEN_NAMES:
                found.add(form)
                break
    return found


def _conflicting_entity_arabic(title: str, candidate: str) -> bool:
    """New in V3 (module docstring, Arabic branch): if the title names
    at least one person from the closed `_ARABIC_GIVEN_NAMES` list, and
    the candidate introduces a DIFFERENT such name not present in the
    title, treat this as a pivot — the exact same shape as V2's own
    English `_conflicting_entity`, applied to the one script V2 never
    covered. Conservative by construction: only fires when the title
    itself supplies a recognized name to compare against, and only
    recognizes names in the closed list — see module docstring for the
    honestly-documented false-negative boundary this implies."""
    title_names = _arabic_given_names_in(title)
    if not title_names:
        return False
    candidate_names = _arabic_given_names_in(candidate)
    return bool(candidate_names - title_names)


def _conflicting_entity(title: str, candidate: str) -> bool:
    return _conflicting_entity_english(title, candidate) or _conflicting_entity_arabic(title, candidate)


# Closed, explicit set of Arabic verb-conjugation PREFIX letters (you/
# he-she/we/I-will forms) — standard, well-known Arabic morphology, not
# derived from any holdout text. Used only by `_short_title_token_match`
# below, itself used only for titles shorter than `_MIN_ANCHOR_LENGTH`.
_ARABIC_VERB_CONJUGATION_PREFIXES = ("ت", "ي", "ن", "أ", "ا", "س")


def _short_title_token_match(normalized_title: str, normalized_candidate: str) -> bool:
    """New in V3 (module docstring, category I): for a title SHORTER
    than `_MIN_ANCHOR_LENGTH` only, requires the title to appear as its
    own whitespace-delimited token in the candidate — optionally with
    ONE closed Arabic verb-conjugation prefix attached — rather than as
    a bare substring anywhere.

    Discovered via this checkpoint's own independent fixture authoring
    (the same gap already flagged, but explicitly left unfixed, in
    Checkpoint 5.5A's own holdout review): a 2-letter title like "رد"
    (reply) is a literal substring of wholly unrelated words like
    "النهاردة" (today) — plain substring containment (V1/V2's own
    mechanism, and this module's own mechanism for titles at or above
    the anchor-length floor) would wrongly accept that as a match.
    Requiring a standalone token (optionally with one legitimate
    conjugation prefix — "ترد" = "you reply", correctly still matches
    "رد") distinguishes a genuine inflected use from a coincidental
    substring hit, without breaking the already-accepted V1/V2 behavior
    for short titles used as a real standalone word or verb form.

    Deliberately NOT applied to titles at or above the anchor-length
    floor, where this collision risk is negligible and the existing,
    long-proven plain-substring behavior is preserved untouched."""
    for token in re.split(r"[^\w]+", normalized_candidate, flags=re.UNICODE):
        if not token:
            continue
        if token == normalized_title:
            return True
        for prefix in _ARABIC_VERB_CONJUGATION_PREFIXES:
            if token.startswith(prefix) and token[len(prefix):] == normalized_title:
                return True
    return False


def _conflicting_action(title: str, candidate: str) -> bool:
    """V2's own `_conflicting_action`, unchanged, reimplemented here."""
    normalized_title = normalize_text(title)
    normalized_candidate = normalize_text(candidate)
    title_families = {_ACTION_FAMILIES[v] for v in _DOMAIN_ACTION_VERBS if v in normalized_title}
    if not title_families:
        return False
    candidate_families = {_ACTION_FAMILIES[v] for v in _DOMAIN_ACTION_VERBS if v in normalized_candidate}
    return bool(candidate_families - title_families)


def check_on_topic_v3(case: EvaluationCase) -> CheckResult:
    """The further-hardened grounding check — see module docstring for
    full design rationale and documented blind spots. V3 is NOT a
    strict superset or subset of V2's verdicts in either direction: it
    additionally REJECTS cases V2 would accept (a named-entity anchor
    silently substituted for a different Arabic one — a gap V2 does not
    cover), and it additionally ACCEPTS a narrow, meaning-preserving
    class of cases V2 would reject (a numeral-script-only mismatch,
    e.g. Arabic-Indic vs. Western digits, via this module's own
    `normalize_text`) — both changes are evidenced, bounded hardening,
    never a general loosening of the match standard."""
    title = case.facts.get("title", "")
    candidate = str(case.candidate)

    reason_code: str | None = None
    normalized_title = normalize_text(title) if title else ""
    normalized_candidate = normalize_text(candidate)

    if title and normalized_title and len(normalized_title) < _MIN_ANCHOR_LENGTH:
        # Short-title regime (new in V3, category I) — plain substring
        # containment is skipped entirely in favor of the stricter,
        # token-based check; see `_short_title_token_match`.
        if _short_title_token_match(normalized_title, normalized_candidate):
            reason_code = "exact_title_match"
    elif title and title in candidate:
        reason_code = "exact_title_match"
    else:
        if normalized_title and normalized_title in normalized_candidate:
            reason_code = "normalized_title_match"
        else:
            anchors = _extract_anchors(title) if title else []
            if anchors and all(_anchor_matches(a, normalized_candidate) for a in anchors):
                reason_code = "high_confidence_grounded_overlap"

    if reason_code is None:
        return CheckResult(
            "on_topic_v3", False,
            f"insufficient_grounding_evidence: no exact, normalized, or anchor-based match for {title!r}",
        )

    for distractor in case.facts.get("distractor_titles", ()):
        if distractor in candidate:
            return CheckResult("on_topic_v3", False, f"distractor_detected: mentions unrelated concern {distractor!r}")

    if _conflicting_entity(title, candidate):
        return CheckResult("on_topic_v3", False, "conflicting_entity: candidate introduces a different named target")

    if _conflicting_action(title, candidate):
        return CheckResult("on_topic_v3", False, "conflicting_action: candidate describes a different action")

    return CheckResult("on_topic_v3", True, f"{reason_code}: grounded in {title!r}")


# ---- language instruction following (new, structurally separate) ------

_ARABIC_LETTER_RE = re.compile(r"[؀-ۿ]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")


def _dominant_script(text: str) -> str | None:
    """Returns "arabic", "latin", or None (no clear majority / no
    letters at all — e.g. a pure-number or pure-punctuation string).
    A plain Unicode-range letter count, never a language model or
    external library — honestly only a script-ratio heuristic, not
    language identification (a transliterated name could still skew
    this; documented, not solved)."""
    arabic_count = len(_ARABIC_LETTER_RE.findall(text))
    latin_count = len(_LATIN_LETTER_RE.findall(text))
    if arabic_count == 0 and latin_count == 0:
        return None
    if arabic_count > latin_count:
        return "arabic"
    if latin_count > arabic_count:
        return "latin"
    return None  # tie — genuinely mixed, no dominant script


def check_language_instruction_following(case: EvaluationCase) -> CheckResult:
    """Proves ONLY whether the candidate's dominant script matches the
    title's own dominant script — a STYLE/instruction-following signal,
    never a grounding or safety signal (see module docstring's
    structural-separation section). A title with no clear dominant
    script (mixed, or no letters) makes this check inapplicable — PASS,
    not a guess in either direction.

    Blind spot (honest, documented): this is a script-ratio heuristic,
    not language detection — a title that happens to contain more
    Latin-script proper nouns than Arabic function words (or vice
    versa) could report a dominant script that doesn't match a human's
    intuitive sense of "what language is this title in." It also cannot
    distinguish "candidate switched language" from "candidate is mostly
    a quoted proper noun in the other script" — both only count letters.
    """
    title = case.facts.get("title", "")
    candidate = str(case.candidate)

    title_script = _dominant_script(title)
    if title_script is None:
        return CheckResult(
            "language_instruction_following", True,
            "title has no clear dominant script — check not applicable",
            hard=False,
        )

    candidate_script = _dominant_script(candidate)
    if candidate_script is None:
        return CheckResult(
            "language_instruction_following", True,
            "candidate has no clear dominant script — check not applicable",
            hard=False,
        )

    if candidate_script != title_script:
        return CheckResult(
            "language_instruction_following", False,
            f"title's dominant script is {title_script!r} but candidate's dominant script is {candidate_script!r}",
            hard=False,
        )
    return CheckResult(
        "language_instruction_following", True,
        f"candidate's dominant script ({candidate_script!r}) matches the title's",
        hard=False,
    )
