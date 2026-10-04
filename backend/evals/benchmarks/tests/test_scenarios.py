from evals.benchmarks.scenarios import PROACTIVE_NARRATION_BENCHMARK_SCENARIOS

_TASK_SIGNAL_TYPES = {"TASK_OVERDUE", "TASK_DUE_TODAY", "TASK_DUE_SOON"}
_NON_TASK_SIGNAL_TYPES = {"EVENT_UPCOMING", "INBOX_NEEDS_ATTENTION"}
_VALID_SIGNAL_TYPES = _TASK_SIGNAL_TYPES | _NON_TASK_SIGNAL_TYPES


def test_scenario_count_is_within_the_target_range() -> None:
    assert 20 <= len(PROACTIVE_NARRATION_BENCHMARK_SCENARIOS) <= 40


def test_every_case_id_is_unique() -> None:
    ids = [s.case_id for s in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS]
    assert len(ids) == len(set(ids))


def test_every_signal_type_is_one_of_the_locked_v1_values() -> None:
    for scenario in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS:
        assert scenario.signal_type in _VALID_SIGNAL_TYPES


def test_task_sourced_signals_always_have_a_real_priority_never_none() -> None:
    """Checkpoint 5.5's own discovery: Task.priority is a non-nullable
    column with a server-side default — a task-sourced signal can never
    have priority=None in real production data."""
    for scenario in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS:
        if scenario.signal_type in _TASK_SIGNAL_TYPES:
            assert scenario.priority in ("low", "normal", "high"), scenario.case_id


def test_non_task_sourced_signals_always_have_priority_none() -> None:
    for scenario in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS:
        if scenario.signal_type in _NON_TASK_SIGNAL_TYPES:
            assert scenario.priority is None, scenario.case_id


def test_every_signal_type_is_represented_at_least_once() -> None:
    present = {s.signal_type for s in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS}
    assert present == _VALID_SIGNAL_TYPES


def test_titles_contain_no_obvious_personal_data_markers() -> None:
    """A cheap, narrow sanity guard (not a privacy proof) — these are
    synthetic placeholders, never real user content."""
    forbidden_markers = ("@", "password", "ssn", "credit card")
    for scenario in PROACTIVE_NARRATION_BENCHMARK_SCENARIOS:
        lowered = scenario.title.lower()
        assert not any(marker in lowered for marker in forbidden_markers)
