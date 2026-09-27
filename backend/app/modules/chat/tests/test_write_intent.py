from app.modules.chat.write_intent import detect_clear_write_intent


def test_clear_english_write_request_is_detected() -> None:
    """Checkpoint 3.3: task CREATION no longer matches here (it now
    reaches the Orchestrator, which may propose it). Checkpoint 3.10:
    task UPDATE (mark-done, reschedule, edit) similarly no longer
    matches. Checkpoint 3.13: task DELETION similarly no longer
    matches. Checkpoint 3.15: event/meeting CREATION similarly no
    longer matches — see below for all four. Calendar/meeting/reminder
    DELETION, reminder CREATION, and Life-Area edits are unaffected by
    any of these checkpoints and still decline deterministically."""
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
    decline deterministically exactly as before. Unaffected by 3.15,
    which only touches the CREATE pattern."""
    assert detect_clear_write_intent("delete my meeting with Bob") is True
    assert detect_clear_write_intent("remove that event from my calendar") is True
    assert detect_clear_write_intent("cancel the reminder") is True
    assert detect_clear_write_intent("delete this life area") is True


def test_create_event_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """Checkpoint 3.15: creating a CalendarEvent (or "meeting") is no
    longer a deterministic decline — it's a real capability now,
    reached through the Orchestrator's propose_create_event tool."""
    assert detect_clear_write_intent("please create an event called Standup") is False
    assert detect_clear_write_intent("add a meeting tomorrow") is False
    assert detect_clear_write_intent("schedule a meeting with Hussein") is False


def test_create_reminder_phrasing_still_declines() -> None:
    """Checkpoint 3.15 narrows ONLY 'event'/'meeting' out of the create
    noun list — 'reminder' stays declined, since reminders/notifications
    remain an unimplemented, out-of-scope concept distinct from a
    CalendarEvent."""
    assert detect_clear_write_intent("create a reminder to water the plants") is True
    assert detect_clear_write_intent("add a reminder for tomorrow") is True


def test_clear_arabic_write_request_is_detected() -> None:
    """Delete/edit phrasings in Arabic are unchanged by 3.3/3.10/3.13/
    3.15 for anything other than tasks/events — only the CREATE
    pattern's noun list dropped 'مهمة' (task) in 3.3 and 'حدث'/'موعد'
    (event/appointment) in 3.15, and the DELETE pattern's noun list
    dropped 'مهمة' in 3.13 (see the dedicated tests below)."""
    assert detect_clear_write_intent("الغاء التذكير") is True


def test_arabic_unsupported_deletion_phrasing_still_declines() -> None:
    """Checkpoint 3.13 narrows ONLY 'مهمة' (task) out of the Arabic
    delete pattern — event/meeting/reminder deletion in Arabic must
    continue to decline deterministically exactly as before."""
    assert detect_clear_write_intent("احذف حدث اجتماع الفريق") is True
    assert detect_clear_write_intent("الغاء التذكير") is True


def test_arabic_create_event_phrasing_is_no_longer_detected_as_a_decline() -> None:
    """Checkpoint 3.15: 'ضيف حدث اجتماع الساعة ٥' = 'add an event,
    meeting at 5' — used to trigger the deterministic decline; now
    falls through to the Orchestrator, matching the English
    create-event behavior change above."""
    assert detect_clear_write_intent("ضيف حدث اجتماع الساعة ٥") is False
    assert detect_clear_write_intent("ضيف موعد بكرة الساعة ١١") is False


def test_arabic_create_reminder_phrasing_still_declines() -> None:
    """Checkpoint 3.15 narrows ONLY 'حدث'/'موعد' out of the Arabic
    create noun list — 'تذكير' (reminder) stays declined."""
    assert detect_clear_write_intent("ضيف تذكير اشرب مية") is True


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


def test_update_event_phrasing_was_never_declined_no_narrowing_needed() -> None:
    """Checkpoint 3.18's own inspection found (and this locks in): the
    UPDATE pattern's noun group has only ever contained 'life area' —
    'event'/'meeting' were never in it, so every CalendarEvent-update
    phrasing already reached the Orchestrator before this checkpoint,
    unlike create (3.15) and delete (3.13), which both required
    narrowing. No write_intent.py change was made for update_event."""
    assert detect_clear_write_intent("Move my meeting with Hussein tomorrow to 2 PM.") is False
    assert detect_clear_write_intent("Change tomorrow's meeting to 3.") is False
    assert detect_clear_write_intent("Push the Hussein meeting back one hour.") is False
    assert detect_clear_write_intent("Reschedule my meeting.") is False
    assert detect_clear_write_intent("Rename tomorrow's meeting to Project Review.") is False


def test_arabic_update_event_phrasing_was_never_declined_no_narrowing_needed() -> None:
    assert detect_clear_write_intent("انقل اجتماع حسين بكرة للساعة ٢.") is False
    assert detect_clear_write_intent("أخر الاجتماع ساعة.") is False
    assert detect_clear_write_intent("غير ميعاد الاجتماع.") is False
