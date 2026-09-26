from app.modules.chat.write_intent import detect_clear_write_intent


def test_clear_english_write_request_is_detected() -> None:
    """Checkpoint 3.3: task CREATION no longer matches here (it now
    reaches the Orchestrator, which may propose it). Checkpoint 3.10:
    task UPDATE (mark-done, reschedule, edit) similarly no longer
    matches. Checkpoint 3.13: task DELETION similarly no longer
    matches — see below for all three. Calendar/meeting/reminder/
    Life-Area deletes and edits are unaffected by any of these
    checkpoints and still decline deterministically, since only Task
    writes are implemented so far."""
    assert detect_clear_write_intent("please create an event called Standup") is True
    assert detect_clear_write_intent("delete my meeting with Bob") is True
    assert detect_clear_write_intent("change the life area for this task") is True


def test_create_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """The Checkpoint 3.3 behavior change: creating a TASK is no longer
    a deterministic decline — it's a real (if limited) capability now,
    reached through the Orchestrator's propose_create_task tool."""
    assert detect_clear_write_intent("please create a task called Buy milk") is False
    assert detect_clear_write_intent("add a task to call Hussein") is False


def test_update_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """The Checkpoint 3.10 behavior change: mark-done/reopen/reschedule/
    rename/edit-description for an EXISTING task is no longer a
    deterministic decline — it's a real capability now, reached
    through the Orchestrator's propose_update_task tool."""
    assert detect_clear_write_intent("mark the report task as done") is False
    assert detect_clear_write_intent("reschedule my task to tomorrow") is False
    assert detect_clear_write_intent("edit the due date on that task") is False
    assert detect_clear_write_intent("rename the task to Buy oat milk") is False


def test_delete_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """Checkpoint 3.13: removing an EXISTING task is no longer a
    deterministic decline — it's a real capability now, reached
    through the Orchestrator's propose_delete_task tool. Only "task"
    was dropped from the delete/remove/cancel noun list — event/
    meeting/reminder/life-area deletion are unaffected (see
    test_unsupported_deletion_phrasing_still_declines below)."""
    assert detect_clear_write_intent("delete my report task") is False
    assert detect_clear_write_intent("remove the Buy milk task") is False
    assert detect_clear_write_intent("cancel that task") is False


def test_unsupported_deletion_phrasing_still_declines() -> None:
    """Checkpoint 3.13 narrows ONLY the task noun out of the delete/
    remove/cancel pattern — event/meeting/reminder/life-area deletion,
    none of which are implemented capabilities, must continue to
    decline deterministically exactly as before."""
    assert detect_clear_write_intent("delete my meeting with Bob") is True
    assert detect_clear_write_intent("remove that event from my calendar") is True
    assert detect_clear_write_intent("cancel the reminder") is True
    assert detect_clear_write_intent("delete this life area") is True


def test_clear_arabic_write_request_is_detected() -> None:
    """Delete/edit phrasings in Arabic are unchanged by 3.3/3.10/3.13
    for anything other than tasks — only the CREATE pattern's noun list
    dropped 'مهمة' (task) in 3.3, and the DELETE pattern's noun list
    dropped 'مهمة' in 3.13 (see test_arabic_delete_task_phrasing_is_no_
    longer_detected_as_a_decline below)."""
    assert detect_clear_write_intent("ضيف حدث اجتماع الساعة ٥") is True


def test_arabic_unsupported_deletion_phrasing_still_declines() -> None:
    """Checkpoint 3.13 narrows ONLY 'مهمة' (task) out of the Arabic
    delete pattern — event/meeting/reminder deletion in Arabic must
    continue to decline deterministically exactly as before."""
    assert detect_clear_write_intent("احذف حدث اجتماع الفريق") is True
    assert detect_clear_write_intent("الغاء التذكير") is True


def test_arabic_create_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """'ضيف مهمة أشتري لبن' = 'add a task: buy milk' — used to trigger
    the deterministic decline; now falls through to the Orchestrator,
    matching the English create-task behavior change above."""
    assert detect_clear_write_intent("ضيف مهمة أشتري لبن") is False


def test_arabic_update_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """'عدل مهمة الفاتورة' = 'edit the invoice task' — used to trigger
    the deterministic decline; now falls through to the Orchestrator,
    matching the English update-task behavior change above."""
    assert detect_clear_write_intent("عدل مهمة الفاتورة") is False
    assert detect_clear_write_intent("غير تاريخ مهمة الفاتورة") is False


def test_arabic_delete_task_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """Checkpoint 3.13: 'احذف مهمة تسجيل الفاتورة' = 'delete the invoice
    task' — used to trigger the deterministic decline; now falls
    through to the Orchestrator, matching the English delete-task
    behavior change above."""
    assert detect_clear_write_intent("احذف مهمة تسجيل الفاتورة") is False
    assert detect_clear_write_intent("امسح مهمة تسجيل الفاتورة") is False


def test_genuinely_ambiguous_phrasing_is_not_detected() -> None:
    """This function is deliberately NOT a general intent classifier —
    this phrasing clearly implies a desired change but matches none of
    the fixed patterns, and is expected to fall through to the model
    (see test_chat.py's adversarial test for what protects the database
    in exactly this case).
    """
    assert detect_clear_write_intent("I won't be free for my dentist appointment anymore") is False
