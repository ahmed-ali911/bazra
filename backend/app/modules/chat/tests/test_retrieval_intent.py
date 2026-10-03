import pytest

from app.modules.chat.retrieval_intent import DeterministicRetrievalIntent, detect_deterministic_retrieval_intent

# ---- recognition matrix: recognized phrases -------------------------------

_RECOGNIZED = [
    ("إيه التاسكات المفتوحة عندي؟", "tasks", "open", "ar"),
    ("ايه التاسكات المفتوحة عندي", "tasks", "open", "ar"),
    ("إيه المهام المفتوحة", "tasks", "open", "ar"),
    ("عندي تاسكات ايه", "tasks", "open", "ar"),
    ("what tasks do I have", "tasks", "open", "en"),
    ("What tasks do I have?", "tasks", "open", "en"),
    ("show my open tasks", "tasks", "open", "en"),
    ("Show me my open tasks", "tasks", "open", "en"),
    ("what are my open tasks", "tasks", "open", "en"),
    ("What are my tasks?", "tasks", "open", "en"),
    ("إيه التاسكات المتأخرة عندي؟", "tasks", "overdue", "ar"),
    ("عندي تاسكات متأخرة", "tasks", "overdue", "ar"),
    ("what tasks are overdue", "tasks", "overdue", "en"),
    ("show my overdue tasks", "tasks", "overdue", "en"),
    ("what's overdue", "tasks", "overdue", "en"),
    ("عندي مواعيد إيه النهارده؟", "calendar", "today", "ar"),
    ("مواعيدي النهارده", "calendar", "today", "ar"),
    ("what's on my calendar today", "calendar", "today", "en"),
    ("show my calendar today", "calendar", "today", "en"),
    ("what events do I have today", "calendar", "today", "en"),
    ("عندي كام حاجة في الـInbox؟", "inbox", "unread", "ar"),
    ("عندي كام حاجة في الانبوكس؟", "inbox", "unread", "ar"),
    ("what's in my inbox", "inbox", "unread", "en"),
    ("show my inbox", "inbox", "unread", "en"),
    ("how many things do I have in my inbox", "inbox", "unread", "en"),
    # whitespace/punctuation variants
    ("  إيه التاسكات المفتوحة عندي؟  ", "tasks", "open", "ar"),
    ("إيه   التاسكات   المفتوحة   عندي", "tasks", "open", "ar"),
    ("what tasks do I have???", "tasks", "open", "en"),
]


@pytest.mark.parametrize("phrase,domain,view,language", _RECOGNIZED)
def test_recognized_retrieval_phrases(phrase: str, domain: str, view: str, language: str) -> None:
    result = detect_deterministic_retrieval_intent(phrase)
    assert result == DeterministicRetrievalIntent(domain=domain, view=view, language=language)


# ---- negative matrix: must NEVER be recognized ----------------------------

_NOT_RECOGNIZED = [
    # judgment / recommendation
    "إيه أهم تاسك؟",
    "رتبلي التاسكات حسب الأهمية",
    "قولي أبدأ بإيه وليه",
    "what's the most important task",
    "which task should I start with",
    "recommend which task to do first",
    # mutation
    "اجلها لبكره",
    "خلص مهمة حسين",
    "احذف Water the plants",
    "ضيف تاسك جديد",
    "الغي التاسك",
    "امسحها",
    "أخرها لبكره",
    "create a task to buy milk",
    "delete the dentist task",
    "mark it done",
    # coreference / contextual
    "التانية خلصها",
    "احذف أول واحدة",
    "move that one",
    # mixed retrieval + mutation/recommendation — blocked by the
    # fullmatch-only positive patterns (any trailing clause makes the
    # whole message fail to match a whitelisted form), independent of
    # the negative-guard keyword list.
    "إيه التاسكات المفتوحة وأجل التانية",
    "عندي كام حاجة في الـInbox وأهمها إيه؟",
    "إيه أهم حاجة في الـInbox؟",
    # casual chat
    "عامل إيه؟",
    "أنا زهقان",
    "نتكلم في إيه؟",
    "how's it going",
    # broader/ambiguous personal-state query (section 30's own test)
    "إيه اللي ورايا؟",
    "what's ahead of me",
    # deliberately-excluded ambiguous Tasks+Calendar phrasing (section 7/30)
    "عندي ايه النهارده؟",
    "what do I have today",
    # calendar judgment
    "أنهي ميعاد محتاج أستعدله أكتر؟",
    "which event needs more prep",
    # empty / whitespace-only
    "",
    "   ",
    "؟؟؟",
]


@pytest.mark.parametrize("phrase", _NOT_RECOGNIZED)
def test_unrecognized_phrases_fall_through(phrase: str) -> None:
    assert detect_deterministic_retrieval_intent(phrase) is None


# ---- negative-guard precision: Arabic root-sharing false-positive check ---


def test_overdue_task_phrase_is_not_blocked_by_the_postpone_guard() -> None:
    """Regression for a real bug caught during this checkpoint's own
    implementation: "متأخرة" (overdue) and "أخر"/"أخّر" (postpone) share
    an Arabic root, and a naive substring guard incorrectly treated
    "متأخرة" as if it contained the mutation verb "أخر". Fixed via a
    left-boundary-only match (see retrieval_intent.py's own comment) —
    this test proves the fix holds, independent of reading the regex
    itself."""
    result = detect_deterministic_retrieval_intent("إيه التاسكات المتأخرة عندي؟")
    assert result == DeterministicRetrievalIntent(domain="tasks", view="overdue", language="ar")


def test_postpone_with_attached_pronoun_suffix_is_still_guarded() -> None:
    """The fix above must not reopen the gap it was meant to close:
    Arabic attaches pronoun suffixes directly with no separator
    ("أخرها" = postpone + it) — a left-boundary-only match must still
    catch this, never requiring a trailing boundary that would miss
    it."""
    assert detect_deterministic_retrieval_intent("أخرها لبكره") is None
    assert detect_deterministic_retrieval_intent("امسحها دلوقتي") is None


def test_calendar_excludes_ambiguous_broad_phrasing_even_with_retrieval_keywords() -> None:
    """Section 30's own named false-positive test: a phrase that merely
    LOOKS similar to an accepted retrieval form must not be assigned a
    narrow meaning the product's own prior usage contradicts."""
    assert detect_deterministic_retrieval_intent("إيه اللي ورايا النهارده؟") is None
