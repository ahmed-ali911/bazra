"""Checkpoint 5.5A — the INDEPENDENT grounding-v2 development/validation
dataset (section 2's strict data-separation rule).

Every case below was authored from first principles (person names,
objects, and phrasings invented for this module specifically — Sara,
Omar, Mona, Layla, Karim, the gym/dentist/invoice/report examples
below) BEFORE any candidate text from the completed Checkpoint 5.5
benchmark artifact was inspected for wording. None of these names or
phrasings were copied from, or chosen to resemble, that artifact's own
candidate outputs. The only fact carried over from the accepted 5.5
report is the PROBLEM CLASS itself ("natural paraphrase can fail
literal matching") — never a solution vocabulary.

Per the brief's own mandatory ordering (section 15): this file, and
`evals/grounding_v2.py`, are BOTH authored and frozen (all tests
passing) BEFORE `evals/benchmarks/retrospective.py` (the holdout
re-evaluation module) is written or run. See
`evals/tests/test_grounding_v2.py`'s own fixture-fingerprint test for
the frozen-state proof.

Each entry: (label, title, distractor_titles, candidate, expected_pass,
expected_reason_substring). `expected_reason_substring` is checked as a
substring of the actual CheckResult.reason — it names which mechanism
SHOULD have produced the verdict, not merely that some verdict occurred.
"""

GROUNDING_V2_FIXTURES: tuple[tuple[str, str, tuple, str, bool, str], ...] = (
    # ---- exact title match (the cheapest, strongest signal) ----------
    ("exact_en", "Submit expense report", (), "Your task 'Submit expense report' is still open. Want to take care of it?", True, "exact_title_match"),
    ("exact_ar", "اجتماع مع فريق المبيعات", (), 'عندك "اجتماع مع فريق المبيعات" قريب.', True, "exact_title_match"),
    ("exact_mixed", "راجع Q3 budget", (), 'لسه محتاج تـ "راجع Q3 budget".', True, "exact_title_match"),
    ("exact_with_trailing_punctuation_in_candidate", "Renew gym membership", (), "Renew gym membership! Don't forget.", True, "exact_title_match"),

    # ---- normalized match: diacritics / alef variants / punctuation / case ----
    ("normalized_diacritics", "اجتماع مع فريق المبيعات", (), "عندك اِجْتِمَاعٌ مَعَ فَرِيقِ الْمَبِيعَاتِ النهارده.", True, "normalized_title_match"),
    ("normalized_alef_variant", "إرسال العقد لأحمد", (), "لسه محتاج ارسال العقد لاحمد.", True, "normalized_title_match"),
    ("normalized_alef_maksura", "راجع الفتوى القانونية", (), "راجع الفتوي القانونية لسه مفتوحة.", True, "normalized_title_match"),
    ("normalized_case_en", "Renew Gym Membership", (), "renew gym membership is still due today.", True, "normalized_title_match"),
    ("normalized_punctuation_en", "Renew gym membership", (), 'Your "Renew, gym-membership!" task is open.', True, "normalized_title_match"),
    ("normalized_tatweel", "تجديد رخصة القيادة", (), "تجـــديد رخصة القيادة لسه متأخر.", True, "normalized_title_match"),

    # ---- safe word-order / function-word variation (paraphrase, PASS) ----
    ("paraphrase_dropped_preposition_ar", "اجتماع مع فريق المبيعات", (), "اجتماع فريق المبيعات جاي قريب، حابب تاخد لمحة؟", True, "high_confidence_grounded_overlap"),
    ("paraphrase_reordered_ar", "مراجعة عقد مع عمر", (), "عقد عمر لسه محتاج مراجعة منك.", True, "high_confidence_grounded_overlap"),
    ("paraphrase_nominalized_ar", "اتصل بمنى", (), 'مهمة "الاتصال بمنى" لسه مفتوحة ومتأخرة.', True, "high_confidence_grounded_overlap"),
    ("paraphrase_dropped_article_ar", "جدد رخصة المحل", (), "رخصة محل لسه محتاجة تجديد.", True, "high_confidence_grounded_overlap"),
    ("paraphrase_en_reworded", "Pay electricity bill", (), "The electricity bill is still waiting on you.", True, "high_confidence_grounded_overlap"),
    ("paraphrase_en_possessive", "Call Omar", (), "Omar's call is still pending from your side.", True, "high_confidence_grounded_overlap"),
    ("paraphrase_mixed_lang", "راجع invoice من الشركة", (), "الـ invoice اللي من الشركة لسه محتاج مراجعة.", True, "high_confidence_grounded_overlap"),
    ("paraphrase_ar_different_verb_form", "سلم تقرير المبيعات", (), "تقرير المبيعات لسه متسلمش.", True, "high_confidence_grounded_overlap"),

    # ---- person-name preservation (paraphrased sentence, same person, PASS) ----
    ("person_preserved_en", "Email Layla about the invoice", (), "Layla is still waiting on that invoice email from you.", True, "high_confidence_grounded_overlap"),
    ("person_preserved_ar", "قابل كريم بخصوص المشروع", (), "كريم لسه مستني بخصوص المشروع.", True, "high_confidence_grounded_overlap"),

    # ---- different person (FAIL — conflicting_entity) -----------------
    ("different_person_en_1", "Call Omar", (), "Omar's call is handled — reach out to Sara next.", False, "conflicting_entity"),
    ("different_person_en_2", "Email Layla about the invoice", (), "Layla's invoice email is fine — send one to Mona too.", False, "conflicting_entity"),
    ("different_person_en_3", "Submit Karim's feedback form", (), "Karim's feedback form is fine — check Omar's form too.", False, "conflicting_entity"),

    # ---- different object, same generic action (FAIL — insufficient_grounding_evidence) ----
    ("different_object_en_1", "Pay electricity bill", (), "Your internet bill is still unpaid.", False, "insufficient_grounding_evidence"),
    ("different_object_en_2", "Renew gym membership", (), "Your parking permit needs renewal.", False, "insufficient_grounding_evidence"),
    ("different_object_ar_1", "جدد رخصة المحل", (), "رخصة القيادة لسه محتاجة تجديد.", False, "insufficient_grounding_evidence"),
    ("different_object_ar_2", "راجع عقد الإيجار", (), "عقد التأمين لسه محتاج مراجعة.", False, "insufficient_grounding_evidence"),

    # ---- same object/person, different action (FAIL — conflicting_action) ----
    ("different_action_same_person_en", "Call Omar", (), "You should email Omar about this instead.", False, "conflicting_action"),
    ("different_action_same_object_en", "Pay electricity bill", (), "Submit the electricity bill for reimbursement.", False, "conflicting_action"),
    ("different_action_same_person_ar", "اتصل بمنى", (), "ابعت إيميل لمنى بدل ما تتصل بيها.", False, "conflicting_action"),
    ("different_action_same_object_ar", "ادفع فاتورة الكهرباء", (), "سلم فاتورة الكهرباء للمحاسب.", False, "conflicting_action"),

    # ---- distractor title present (FAIL — distractor_detected) --------
    ("distractor_en", "Submit expense report", ("Renew passport",), "Submit expense report is open — also, don't forget Renew passport.", False, "distractor_detected"),
    ("distractor_ar", "اتصل بمنى", ("جدد رخصة المحل",), "اتصل بمنى لسه مفتوحة، وكمان جدد رخصة المحل.", False, "distractor_detected"),

    # ---- generic-word-only overlap (FAIL — insufficient_grounding_evidence) ----
    ("generic_only_en_1", "Call Omar", (), "You have a task to take care of today.", False, "insufficient_grounding_evidence"),
    ("generic_only_en_2", "Renew gym membership", (), "There's a reminder waiting for your attention.", False, "insufficient_grounding_evidence"),
    ("generic_only_ar_1", "اتصل بمنى", (), "عندك مهمة لازم تتعامل معاها.", False, "insufficient_grounding_evidence"),
    ("generic_only_ar_2", "جدد رخصة المحل", (), "في حاجة في التذكيرات بتستنى منك.", False, "insufficient_grounding_evidence"),

    # ---- partial overlap that should still fail (one anchor missing) ----
    ("partial_overlap_fail_en", "Submit Karim's feedback form", (), "Karim is doing well these days.", False, "insufficient_grounding_evidence"),
    ("partial_overlap_fail_ar", "مراجعة عقد مع عمر", (), "عمر بعتلك رسالة إمبارح.", False, "insufficient_grounding_evidence"),

    # ---- short titles (conservative — exact/normalized only, no anchor fallback) ----
    ("short_title_exact_pass_en", "Call", (), "Call is still on your list.", True, "exact_title_match"),
    ("short_title_paraphrase_fail_en", "Call", (), "You should reach out soon.", False, "insufficient_grounding_evidence"),
    ("short_title_exact_pass_ar", "رد", (), "لسه محتاج ترد عليه.", True, "exact_title_match"),
    ("short_title_too_short_anchor_fail_ar", "رد", (), "في حاجة لازم تعملها.", False, "insufficient_grounding_evidence"),

    # ---- titles with multiple meaningful anchors ----------------------
    ("multi_anchor_both_present_pass_en", "Email Layla about the Q3 invoice", (), "Layla and the Q3 invoice both still need your attention.", True, "high_confidence_grounded_overlap"),
    ("multi_anchor_one_missing_fail_en", "Email Layla about the Q3 invoice", (), "Layla sent you a message yesterday.", False, "insufficient_grounding_evidence"),
    ("multi_anchor_both_present_pass_ar", "ابعت تقرير المبيعات لمنى", (), "منى لسه مستنية تقرير المبيعات منك.", True, "high_confidence_grounded_overlap"),
    ("multi_anchor_one_missing_fail_ar", "ابعت تقرير المبيعات لمنى", (), "منى سألت عنك إمبارح.", False, "insufficient_grounding_evidence"),

    # ---- mixed Arabic/English titles -----------------------------------
    ("mixed_title_pass", "راجع Q3 budget مع عمر", (), "عمر لسه مستني مراجعة الـ Q3 budget.", True, "high_confidence_grounded_overlap"),
    ("mixed_title_fail_wrong_person", "راجع Q3 budget مع عمر", (), "منى بعتت الـ Q3 budget بتاعها.", False, "insufficient_grounding_evidence"),

    # ---- candidate mentions title concern then clearly pivots away ----
    ("pivot_after_mention_en", "Call Omar", (), "Omar's call can wait — focus on emailing Sara first.", False, "conflicting_entity"),
    ("pivot_after_mention_ar", "اتصل بمنى", ("جدد رخصة المحل",), "اتصل بمنى بسيطة، الأهم دلوقتي جدد رخصة المحل.", False, "distractor_detected"),

    # ---- safe punctuation-only quoting of the title (should pass exact/normalized) ----
    ("quoted_title_en", "Submit expense report", (), 'Reminder: "Submit expense report" is still pending.', True, "exact_title_match"),
    ("quoted_title_ar", "اتصل بمنى", (), 'تذكير: "اتصل بمنى" لسه معلقة.', True, "exact_title_match"),

    # ---- case where candidate repeats a GENERIC word from title but nothing else ----
    ("generic_word_echo_only_en", "Renew gym membership", (), "This task reminder is about your membership benefits in general.", False, "insufficient_grounding_evidence"),
    ("generic_word_echo_only_ar", "جدد رخصة المحل", (), "في تذكير عندك محتاج تراجعه.", False, "insufficient_grounding_evidence"),

    # ---- English function-word variation ("about"/"regarding" dropped, PASS) ----
    ("function_word_dropped_about_en", "Follow up on the Mona contract", (), "The Mona contract still needs follow up from you.", True, "high_confidence_grounded_overlap"),
    ("function_word_dropped_regarding_en", "Message Karim regarding the budget", (), "Karim and the budget topic are both still waiting on you.", True, "high_confidence_grounded_overlap"),

    # ---- additional different-person negatives (English) --------------
    ("different_person_en_4", "Follow up on the Mona contract", (), "The Mona contract is fine — check on Layla's contract instead.", False, "conflicting_entity"),
    ("different_person_en_5", "Message Karim regarding the budget", (), "Karim's budget is fine — maybe message Sara instead.", False, "conflicting_entity"),

    # ---- additional different-object negatives (Arabic) ----------------
    ("different_object_ar_3", "ابعت تقرير المبيعات لمنى", (), "تقرير المخزون لسه محتاج مراجعة.", False, "insufficient_grounding_evidence"),
    ("different_object_ar_4", "راجع عقد الإيجار", (), "فاتورة الكهرباء لسه ما اتدفعتش.", False, "insufficient_grounding_evidence"),

    # ---- additional same-person/object different-action negatives -----
    ("different_action_same_object_en_2", "Renew gym membership", (), "Cancel the gym membership instead of renewing it.", False, "conflicting_action"),
    ("different_action_same_person_en_2", "Message Karim regarding the budget", (), "Call Karim about the budget instead of messaging him.", False, "conflicting_action"),

    # ---- known, acknowledged over-conservative blind spot: negation is
    # NOT understood — a distractor-shaped name that is explicitly
    # negated/dismissed still trips conflicting_entity. This is an
    # intentional, documented limitation (the brief's own tolerance for
    # "fail conservatively" over guessing) — NOT a desired behavior to
    # fix in this checkpoint, and NOT silently hidden: it is a frozen,
    # asserted expectation.
    ("blind_spot_negation_not_understood_en", "Call Omar", (), "No need to message Sara — Omar's call is still what matters.", False, "conflicting_entity"),

    # ---- additional multi-anchor / partial-overlap coverage -----------
    ("multi_anchor_both_present_pass_en_2", "Submit Karim's feedback form", (), "Karim's feedback form is still sitting there, untouched.", True, "high_confidence_grounded_overlap"),
    ("partial_overlap_fail_en_2", "Follow up on the Mona contract", (), "Mona asked about something unrelated yesterday.", False, "insufficient_grounding_evidence"),

    # ---- additional short-title conservatism ---------------------------
    ("short_title_exact_pass_en_2", "Submit", (), "Submit is still on your plate.", True, "exact_title_match"),
    ("short_title_paraphrase_fail_en_2", "Submit", (), "There's something still pending.", False, "insufficient_grounding_evidence"),
)
