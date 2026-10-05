"""Checkpoint 5.6 — the INDEPENDENT grounding-v3 development/validation
dataset, organized by the brief's own lettered categories A-J (section
10). Every case below was authored from first principles (person names
and phrasings invented for this module specifically — Yara, Tarek,
Nour, Rania, Khaled, Dalia, Sami, Hossam, Mariam, Youssef — deliberately
distinct from both `evals.grounding_v2_fixtures`'s own names (Omar,
Sara, Mona, Layla, Karim) and the 5.5 holdout's own candidate wording)
BEFORE any holdout artifact was re-inspected for this checkpoint's own
hardening work. The only facts carried over from the accepted 5.5/5.5A
evidence are the PROBLEM CLASSES themselves (named-entity erosion,
short-title permissiveness, numeral-script mismatch) — never a
solution vocabulary or specific phrasing.

Per this checkpoint's own mandatory ordering (mirroring 5.5A, section
15): this file and `evals/grounding_v3.py` are both authored and frozen
(all tests passing) BEFORE any new replay module re-evaluates the
stored 5.5 holdout artifact through V3. See
`evals/tests/test_grounding_v3.py`'s own fixture-fingerprint test for
the frozen-state proof.

Each entry: (label, category, title, distractor_titles, candidate,
expected_pass, expected_reason_substring). `category` is one of the
brief's own lettered categories (A-J); `expected_reason_substring` is
checked as a substring of the actual CheckResult.reason.
"""

GROUNDING_V3_FIXTURES: tuple[tuple[str, str, str, tuple, str, bool, str], ...] = (
    # ======================================================================
    # A — valid Arabic paraphrases
    # ======================================================================
    ("a_dropped_preposition", "A", "اجتماع مع فريق التسويق", (), "اجتماع فريق التسويق جاي بعد شوية.", True, "high_confidence_grounded_overlap"),
    ("a_nominalized_verb", "A", "اتصل بهشام", (), 'مهمة "الاتصال بهشام" لسه مفتوحة.', True, "high_confidence_grounded_overlap"),
    ("a_dropped_article", "A", "جدد رخصة العربية", (), "رخصة عربية لسه محتاجة تجديد.", True, "high_confidence_grounded_overlap"),
    ("a_reordered", "A", "مراجعة عقد مع تامر", (), "عقد تامر لسه محتاج مراجعة منك.", True, "high_confidence_grounded_overlap"),
    ("a_different_verb_form", "A", "سلم تقرير الصيانة", (), "تقرير الصيانة لسه متسلمش.", True, "high_confidence_grounded_overlap"),
    ("a_exact_with_diacritics", "A", "اجتماع مع فريق التسويق", (), "عندك اِجْتِمَاعٌ مَعَ فَرِيقِ التَّسْوِيقِ.", True, "normalized_title_match"),
    ("a_tatweel", "A", "تجديد رخصة القيادة", (), "تجـــديد رخصة القيادة لسه متأخر.", True, "normalized_title_match"),

    # ======================================================================
    # B — valid English paraphrases
    # ======================================================================
    ("b_reworded", "B", "Pay internet bill", (), "The internet bill is still waiting on you.", True, "high_confidence_grounded_overlap"),
    ("b_possessive", "B", "Call Hossam", (), "Hossam's call is still pending from your side.", True, "high_confidence_grounded_overlap"),
    ("b_function_word_dropped", "B", "Follow up on the Youssef contract", (), "The Youssef contract still needs follow up from you.", True, "high_confidence_grounded_overlap"),
    ("b_regarding_dropped", "B", "Message Mariam regarding the budget", (), "Mariam and the budget topic are both still waiting on you.", True, "high_confidence_grounded_overlap"),
    ("b_case_insensitive", "B", "Renew Office Lease", (), "renew office lease is still due today.", True, "normalized_title_match"),
    ("b_punctuation_quoted", "B", "Submit tax form", (), 'Reminder: "Submit tax form" is still pending.', True, "exact_title_match"),

    # ======================================================================
    # C — valid mixed Arabic/English paraphrases
    # ======================================================================
    ("c_mixed_pass", "C", "راجع Q2 budget مع هشام", (), "هشام لسه مستني مراجعة الـ Q2 budget.", True, "high_confidence_grounded_overlap"),
    ("c_mixed_invoice", "C", "ابعت invoice لتامر", (), "الـ invoice اللي لتامر لسه محتاج يتبعت.", True, "high_confidence_grounded_overlap"),
    ("c_mixed_exact", "C", "راجع Q2 budget مع هشام", (), 'لسه محتاج تـ "راجع Q2 budget مع هشام".', True, "exact_title_match"),
    ("c_mixed_english_reply_to_mixed_title", "C", "Email Nour عن المشروع", (), "Nour still needs that email about المشروع.", True, "high_confidence_grounded_overlap"),

    # ======================================================================
    # D — invalid wrong-concern examples (insufficient_grounding_evidence)
    # ======================================================================
    ("d_wrong_object_en_1", "D", "Pay internet bill", (), "Your electricity bill is still unpaid.", False, "insufficient_grounding_evidence"),
    ("d_wrong_object_en_2", "D", "Renew office lease", (), "Your parking permit needs renewal.", False, "insufficient_grounding_evidence"),
    ("d_wrong_object_ar_1", "D", "جدد رخصة العربية", (), "رخصة القيادة لسه محتاجة تجديد.", False, "insufficient_grounding_evidence"),
    ("d_wrong_object_ar_2", "D", "راجع عقد الصيانة", (), "عقد التأمين لسه محتاج مراجعة.", False, "insufficient_grounding_evidence"),
    ("d_generic_only_en", "D", "Call Hossam", (), "You have a task to take care of today.", False, "insufficient_grounding_evidence"),
    ("d_generic_only_ar", "D", "اتصل بهشام", (), "عندك مهمة لازم تتعامل معاها.", False, "insufficient_grounding_evidence"),
    ("d_distractor_en", "D", "Submit tax form", ("Renew passport",), "Submit tax form is open — also, don't forget Renew passport.", False, "distractor_detected"),
    ("d_distractor_ar", "D", "اتصل بهشام", ("جدد رخصة العربية",), "اتصل بهشام لسه مفتوحة، وكمان جدد رخصة العربية.", False, "distractor_detected"),

    # ======================================================================
    # E — invalid changed-entity examples (conflicting_entity)
    # ======================================================================
    ("e_different_person_en_1", "E", "Call Hossam", (), "Hossam's call is handled — reach out to Khaled next.", False, "conflicting_entity"),
    ("e_different_person_en_2", "E", "Email Nour about the invoice", (), "Nour's invoice email is fine — send one to Dalia too.", False, "conflicting_entity"),
    ("e_different_person_en_3", "E", "Submit Youssef's feedback form", (), "Youssef's feedback form is fine — check Tarek's form too.", False, "conflicting_entity"),
    ("e_different_person_ar_1", "E", "اتصل بهشام", (), "هشام لسه مستني، بس خليك تتصل بسامي بدل كده.", False, "conflicting_entity"),
    ("e_different_person_ar_2", "E", "قابل تامر بخصوص المشروع", (), "تامر وخالد اتنينهم لسه مستنيين بخصوص المشروع.", False, "conflicting_entity"),
    ("e_different_person_ar_3", "E", "ابعت تقرير المبيعات لمريم", (), "مريم ونهى لازم ياخدوا تقرير المبيعات.", False, "conflicting_entity"),
    ("e_marketing_vs_finance_team", "E", "Meeting with the marketing team", (), "Here's your update on the finance team's budget.", False, "insufficient_grounding_evidence"),
    ("e_pivot_after_mention_en", "E", "Call Hossam", (), "Hossam's call can wait — focus on emailing Khaled first.", False, "conflicting_entity"),
    ("e_pivot_after_mention_ar", "E", "اتصل بهشام", ("جدد رخصة العربية",), "اتصل بهشام بسيطة، الأهم دلوقتي جدد رخصة العربية.", False, "distractor_detected"),

    # ======================================================================
    # F — invalid invented-date examples
    #
    # NOTE: invented dates are caught by the SEPARATE `check_invented_date`
    # check (evals.proactive_narration), not by on_topic_v3 at all — a
    # candidate can be perfectly ON-TOPIC (grounded in the right concern)
    # while still inventing a date. These fixtures prove on_topic_v3's
    # OWN verdict is unaffected by an invented date one way or the other
    # (it is not on_topic_v3's job to catch this), matching the brief's
    # own "grounding != factual-invention-safety" separation.
    # ======================================================================
    ("f_invented_date_still_on_topic_en", "F", "Call Hossam", (), "Hossam's birthday call is due next Friday.", True, "high_confidence_grounded_overlap"),
    ("f_invented_date_still_on_topic_ar", "F", "اتصل بهشام", (), "عندك مهمة متأخرة من يوم الجمعة: اتصل بهشام.", True, "exact_title_match"),

    # ======================================================================
    # G — valid reordering / light morphological variation
    # ======================================================================
    ("g_reorder_ar_1", "G", "مراجعة عقد مع تامر", (), "عقد تامر محتاج منك مراجعة.", True, "high_confidence_grounded_overlap"),
    ("g_morphology_ar_submit", "G", "سلم تقرير الصيانة", (), "تقرير الصيانة متسلمش لسه.", True, "high_confidence_grounded_overlap"),
    ("g_morphology_en_renew", "G", "Renew office lease", (), "You still haven't gotten around to renewing the office lease.", True, "high_confidence_grounded_overlap"),
    ("g_attached_particle_stripped", "G", "اتصل بالسباك", (), "السباك لسه محتاج تتصله.", True, "high_confidence_grounded_overlap"),

    # ======================================================================
    # H — multi-topic leakage (distractor_detected)
    # ======================================================================
    ("h_multi_topic_en", "H", "Submit tax form", ("Renew passport",), "Submit tax form, and also Renew passport while you're at it.", False, "distractor_detected"),
    ("h_multi_topic_ar", "H", "اتصل بهشام", ("جدد رخصة العربية",), "اتصل بهشام، وكمان جدد رخصة العربية.", False, "distractor_detected"),
    ("h_multi_topic_candidate_leads_with_distractor", "H", "Call Hossam", ("Water the office plants",), "Don't forget to Water the office plants — also Call Hossam.", False, "distractor_detected"),

    # ======================================================================
    # I — short titles (must not become dangerously permissive)
    # ======================================================================
    ("i_short_exact_pass_en", "I", "Call", (), "Call is still on your list.", True, "exact_title_match"),
    ("i_short_paraphrase_fail_en", "I", "Call", (), "You should reach out soon.", False, "insufficient_grounding_evidence"),
    ("i_short_exact_pass_ar", "I", "رد", (), "لسه محتاج ترد عليه.", True, "exact_title_match"),
    ("i_short_too_short_anchor_fail_ar", "I", "رد", (), "في حاجة لازم تعملها.", False, "insufficient_grounding_evidence"),
    ("i_short_single_generic_anchor_fail", "I", "Submit", (), "There's something still pending.", False, "insufficient_grounding_evidence"),
    ("i_short_single_entity_anchor_pass", "I", "Hossam", (), "Hossam is still waiting on you.", True, "exact_title_match"),
    # A single-word entity title has no real "mention, then pivot"
    # signal to detect here (the title's own name never appears at
    # all) — insufficient_grounding_evidence is the correct, more
    # specific reason; conflicting_entity is reserved for the title's
    # own entity being PRESENT alongside a newly-introduced one (see
    # the "e_pivot_after_mention_*" fixtures above).
    ("i_short_single_entity_anchor_full_swap_fail", "I", "Hossam", (), "Khaled is still waiting on you.", False, "insufficient_grounding_evidence"),
    ("i_two_char_verb_not_confused_with_unrelated_word", "I", "رد", (), "النهاردة الجو حلو.", False, "insufficient_grounding_evidence"),
    ("i_two_char_verb_still_matches_inflected_form", "I", "رد", (), "لسه محتاج ترد عليه.", True, "exact_title_match"),

    # ======================================================================
    # J — English title -> Arabic response (grounding evaluated
    # separately from instruction-following; see
    # test_language_instruction_following.py for the SEPARATE check)
    # ======================================================================
    # HONEST, ACKNOWLEDGED LIMITATION: a full-TRANSLATION reply (English
    # title, candidate correctly translates its meaning into Arabic
    # words with zero lexical overlap) is NOT recognized as grounded —
    # this module performs lexical/structural matching only, never
    # cross-language semantic translation (no dictionary, no model).
    # This is a real, bounded gap, not silently hidden: it is exactly
    # why `on_topic_v3` and `check_language_instruction_following` stay
    # structurally separate (a translated reply could be the CORRECT,
    # desired behavior in a real product, yet this check cannot confirm
    # it is grounded) — asserted here as a frozen, documented
    # expectation.
    ("j_english_title_arabic_translation_not_recognized", "J", "Book flight tickets", (), "لسه محتاج تحجز تذاكر الطيران.", False, "insufficient_grounding_evidence"),
    ("j_english_title_arabic_reply_wrong_concern", "J", "Book flight tickets", (), "لسه محتاج تدفع فاتورة الكهرباء.", False, "insufficient_grounding_evidence"),
    ("j_arabic_title_english_translation_not_recognized", "J", "اتصل بهشام", (), "You still need to call Hossam back.", False, "insufficient_grounding_evidence"),
    ("j_mixed_script_name_preserved", "J", "Email Hossam the report", (), "Hossam's report email is still pending from your side.", True, "high_confidence_grounded_overlap"),
    # A title/reply pair that shares an untranslated proper noun (the
    # name itself, kept in Latin script even in an Arabic reply) CAN
    # still be grounded via the shared name anchor alone — this is
    # genuinely different from the full-translation case above, where
    # NOTHING in the title's own script/words survives into the reply.
    ("j_shared_latin_name_anchor_in_arabic_reply", "J", "Call Hossam", (), "Hossam لسه مستني اتصال منك.", True, "high_confidence_grounded_overlap"),

    # ======================================================================
    # Extra: numeral-script normalization (new V3 mechanism, section 9)
    # ======================================================================
    ("numeral_western_title_eastern_candidate", "numerals", "ادفع فاتورة رقم 456", (), "فاتورة رقم ٤٥٦ لسه محتاجة دفع.", True, "high_confidence_grounded_overlap"),
    ("numeral_eastern_title_western_candidate", "numerals", "راجع عقد رقم ٧٨٩", (), "عقد رقم 789 لسه محتاج مراجعة.", True, "high_confidence_grounded_overlap"),
    ("numeral_mismatch_different_number_still_fails", "numerals", "ادفع فاتورة رقم 456", (), "فاتورة رقم ١٢٣ لسه محتاجة دفع.", False, "insufficient_grounding_evidence"),

    # ======================================================================
    # Extra: same object/person, different action (conflicting_action,
    # unchanged mechanism from V2 — regression coverage under V3)
    # ======================================================================
    ("action_conflict_en", "action", "Call Hossam", (), "You should email Hossam about this instead.", False, "conflicting_action"),
    ("action_conflict_ar", "action", "اتصل بهشام", (), "ابعت إيميل لهشام بدل ما تتصل بيه.", False, "conflicting_action"),
    ("action_family_synonym_not_a_conflict_ar", "action", "اتصل بهشام", (), 'مهمة "الاتصال بهشام" لسه مفتوحة.', True, "high_confidence_grounded_overlap"),

    # KNOWN, ACKNOWLEDGED, INTENTIONALLY-NOT-FIXED blind spot: inherited
    # unchanged from V2's own `_conflicting_action` (substring, not
    # whole-word, containment — deliberately, so English inflections
    # like "renewing"/"renew" still match their base family). The same
    # substring mechanism means an ORDINARY auxiliary use of "finished"
    # ("haven't finished renewing...") coincidentally contains the
    # unrelated domain-action-verb substring "finish", and is wrongly
    # treated as a pivot to a different action family. This was NOT
    # introduced by V3 and is NOT fixed by V3 (fixing it would require
    # abandoning V2's own deliberate substring-matching choice, out of
    # this checkpoint's scope) — documented and frozen as an asserted,
    # known limitation, exactly like 5.5A's own two documented gaps.
    ("blind_spot_finish_substring_collision_en", "blind_spot", "Renew office lease", (), "You still haven't finished renewing the office lease.", False, "conflicting_action"),

    # ======================================================================
    # Extra: documented, intentional Arabic-given-name blind spot — a
    # real name OUTSIDE the closed _ARABIC_GIVEN_NAMES list is not
    # detected as a conflicting entity (acknowledged, not fixed — see
    # module docstring). This is a frozen, ASSERTED expectation, not a
    # silently hidden gap.
    # ======================================================================
    ("blind_spot_name_outside_closed_list_ar", "blind_spot", "اتصل بزكريا", (), "زكريا لسه مستني، بس خليك تتصل بفلان بدل كده.", True, "high_confidence_grounded_overlap"),
)
