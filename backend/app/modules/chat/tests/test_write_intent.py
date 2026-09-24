from app.modules.chat.write_intent import detect_clear_write_intent


def test_clear_english_write_request_is_detected() -> None:
    assert detect_clear_write_intent("please create a task called Buy milk") is True
    assert detect_clear_write_intent("delete my meeting with Bob") is True
    assert detect_clear_write_intent("mark the report task as done") is True


def test_clear_arabic_write_request_is_detected() -> None:
    """'ضيف مهمة أشتري لبن' = 'add a task: buy milk'. A good-faith
    attempt at common Arabic phrasings, NOT verified with native-speaker
    confidence the way the English patterns are — flagged for review
    before this ships to real usage.
    """
    assert detect_clear_write_intent("ضيف مهمة أشتري لبن") is True


def test_genuinely_ambiguous_phrasing_is_not_detected() -> None:
    """This function is deliberately NOT a general intent classifier —
    this phrasing clearly implies a desired change but matches none of
    the fixed patterns, and is expected to fall through to the model
    (see test_chat.py's adversarial test for what protects the database
    in exactly this case).
    """
    assert detect_clear_write_intent("I won't be free for my dentist appointment anymore") is False
