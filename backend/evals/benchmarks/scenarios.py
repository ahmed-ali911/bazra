"""Checkpoint 5.5, sections 11/12 — the benchmark's own frozen scenario
set, derived directly from the REAL production narration input
boundary and the REAL constraints on it (both confirmed by discovery,
not assumed):

- signal_type is one of the five locked v1 values
  (attention/schemas.py::SIGNAL_TYPE_ORDER): TASK_OVERDUE,
  TASK_DUE_TODAY, TASK_DUE_SOON, EVENT_UPCOMING, INBOX_NEEDS_ATTENTION.
- priority is populated ONLY for task-sourced signals, and for those it
  is ALWAYS one of "low"/"normal"/"high" — never None, because
  Task.priority is a non-nullable column with a server-side default
  (confirmed: `tasks/models.py::Task.priority` is
  `nullable=False, default="normal"`). For EVENT_UPCOMING/
  INBOX_NEEDS_ATTENTION, priority is always None (Signal's own
  docstring: "None for calendar_event/inbox_item sources").

24 scenarios — within the 20-40 target range, each with a distinct
title to avoid near-duplicates, covering every signal_type, every real
priority value for task-sourced signals, Arabic/English/mixed titles,
and a few titles deliberately chosen because their own semantic content
could plausibly tempt a model toward invented history, action-offering
wording, or urgency exaggeration (the check that actually fires, if
any, still depends on what the model ACTUALLY writes — these are
temptation opportunities, not guaranteed failures).

All titles are synthetic (section 27) — "Call Hussein"/"Water the
plants" are this repository's own already-established generic test
fixture names (narration/tests/test_narration.py, chat/tests/
test_chat.py), reused rather than invented fresh; every other title
here is a plain, generic, non-personal placeholder.
"""

from evals.benchmarks.schemas import BenchmarkScenario

SUITE_NAME = "proactive_narration_compare"
SUITE_VERSION = "v1"

PROACTIVE_NARRATION_BENCHMARK_SCENARIOS: tuple[BenchmarkScenario, ...] = (
    # ---- TASK_OVERDUE x {low, normal, high} ----
    BenchmarkScenario("overdue_low", "TASK_OVERDUE", "Return library book", "low"),
    BenchmarkScenario("overdue_normal_ar", "TASK_OVERDUE", "اتصل بالسباك", "normal"),
    BenchmarkScenario("overdue_high", "TASK_OVERDUE", "Call Hussein", "high"),
    BenchmarkScenario("overdue_high_urgency_tempting", "TASK_OVERDUE", "ادفع فاتورة الكهرباء", "high"),
    BenchmarkScenario("overdue_normal_history_tempting", "TASK_OVERDUE", "Reply to Hussein's email", "normal"),
    BenchmarkScenario("overdue_low_mixed_lang", "TASK_OVERDUE", "تجديد Gym membership", "low"),
    # ---- TASK_DUE_TODAY x {low, normal, high} ----
    BenchmarkScenario("due_today_low_ar", "TASK_DUE_TODAY", "ارجع المكتبة", "low"),
    BenchmarkScenario("due_today_normal", "TASK_DUE_TODAY", "Pay internet bill", "normal"),
    BenchmarkScenario("due_today_high_ar", "TASK_DUE_TODAY", "سلم تقرير المشروع", "high"),
    BenchmarkScenario("due_today_high_action_tempting", "TASK_DUE_TODAY", "Finish the Q3 report", "high"),
    BenchmarkScenario("due_today_normal_en2", "TASK_DUE_TODAY", "Book flight tickets", "normal"),
    # ---- TASK_DUE_SOON x {low, normal, high} ----
    BenchmarkScenario("due_soon_low", "TASK_DUE_SOON", "Water the plants", "low"),
    BenchmarkScenario("due_soon_normal_ar", "TASK_DUE_SOON", "جدد رخصة القيادة", "normal"),
    BenchmarkScenario("due_soon_high", "TASK_DUE_SOON", "Submit tax documents", "high"),
    BenchmarkScenario("due_soon_low_ar2", "TASK_DUE_SOON", "نظف الجراج", "low"),
    BenchmarkScenario("due_soon_high_ar2", "TASK_DUE_SOON", "اعمل تجديد لرخصة المحل", "high"),
    # ---- EVENT_UPCOMING (priority always None) ----
    BenchmarkScenario("event_en", "EVENT_UPCOMING", "Dentist appointment", None),
    BenchmarkScenario("event_ar", "EVENT_UPCOMING", "اجتماع مع فريق التسويق", None),
    BenchmarkScenario("event_en2", "EVENT_UPCOMING", "Meeting with Sarah", None),
    BenchmarkScenario("event_mixed_name", "EVENT_UPCOMING", "Hussein's birthday dinner", None),
    BenchmarkScenario("event_ar2", "EVENT_UPCOMING", "مكالمة مع العميل الجديد", None),
    # ---- INBOX_NEEDS_ATTENTION (priority always None) ----
    BenchmarkScenario("inbox_ar", "INBOX_NEEDS_ATTENTION", "رسالة من شركة التأمين", None),
    BenchmarkScenario("inbox_en", "INBOX_NEEDS_ATTENTION", "Invoice from supplier", None),
    # Mirrors the REAL production InboxItem title pattern for a
    # completed-task-derived item (tasks_service.update_task's own
    # f"Completed: {task.title}" — confirmed by discovery), not an
    # invented shape.
    BenchmarkScenario("inbox_completed_task_pattern", "INBOX_NEEDS_ATTENTION", "Completed: Water the plants", None),
)
