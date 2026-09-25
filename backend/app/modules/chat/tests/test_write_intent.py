from app.modules.chat.write_intent import detect_clear_write_intent


def test_clear_english_write_request_is_detected() -> None:
    """Checkpoint 3.3: task CREATION no longer matches here (it now
    reaches the Orchestrator, which may propose it) — delete/edit/mark-
    done phrasings for tasks are unaffected and still decline
    deterministically."""
    assert detect_clear_write_intent("please create an event called Standup") is True
    assert detect_clear_write_intent("delete my meeting with Bob") is True
    assert detect_clear_write_intent("mark the report task as done") is True


def test_create_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """The Checkpoint 3.3 behavior change: creating a TASK is no longer
    a deterministic decline — it's a real (if limited) capability now,
    reached through the Orchestrator's propose_create_task tool."""
    assert detect_clear_write_intent("please create a task called Buy milk") is False
    assert detect_clear_write_intent("add a task to call Hussein") is False


def test_clear_arabic_write_request_is_detected() -> None:
    """Delete/edit phrasings in Arabic are unchanged by 3.3 — only the
    CREATE pattern's noun list dropped 'مهمة' (task)."""
    assert detect_clear_write_intent("احذف مهمة تسجيل الفاتورة") is True
    assert detect_clear_write_intent("ضيف حدث اجتماع الساعة ٥") is True


def test_arabic_create_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """'ضيف مهمة أشتري لبن' = 'add a task: buy milk' — used to trigger
    the deterministic decline; now falls through to the Orchestrator,
    matching the English create-task behavior change above."""
    assert detect_clear_write_intent("ضيف مهمة أشتري لبن") is False


def test_genuinely_ambiguous_phrasing_is_not_detected() -> None:
    """This function is deliberately NOT a general intent classifier —
    this phrasing clearly implies a desired change but matches none of
    the fixed patterns, and is expected to fall through to the model
    (see test_chat.py's adversarial test for what protects the database
    in exactly this case).
    """
    assert detect_clear_write_intent("I won't be free for my dentist appointment anymore") is False
