from evals.benchmarks.provider_evidence_ledger import AttemptLedger, BudgetExceededError, DuplicateAttemptError


def test_reserve_persists_across_new_instances(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    ledger1 = AttemptLedger(path, max_attempts=5)
    ledger1.reserve("x:1", "x", "anthropic", "claude-sonnet-5", 1)

    ledger2 = AttemptLedger(path, max_attempts=5)
    assert ledger2.already_attempted("x:1") is True
    assert ledger2.total_reserved() == 1


def test_remaining_budget_decreases_with_each_reservation(tmp_path) -> None:
    ledger = AttemptLedger(str(tmp_path / "ledger.jsonl"), max_attempts=3)
    assert ledger.remaining_budget() == 3
    ledger.reserve("x:1", "x", "anthropic", "claude-sonnet-5", 1)
    assert ledger.remaining_budget() == 2


def test_reserve_writes_one_json_line_per_attempt(tmp_path) -> None:
    path = str(tmp_path / "ledger.jsonl")
    ledger = AttemptLedger(path, max_attempts=5)
    ledger.reserve("x:1", "x", "anthropic", "claude-sonnet-5", 1)
    ledger.reserve("x:2", "x", "anthropic", "claude-haiku-4-5", 1)
    with open(path) as f:
        lines = [line for line in f.read().splitlines() if line.strip()]
    assert len(lines) == 2


def test_duplicate_reservation_raises() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ledger = AttemptLedger(f"{tmp}/ledger.jsonl", max_attempts=5)
        ledger.reserve("x:1", "x", "anthropic", "claude-sonnet-5", 1)
        try:
            ledger.reserve("x:1", "x", "anthropic", "claude-sonnet-5", 1)
            assert False, "expected DuplicateAttemptError"
        except DuplicateAttemptError:
            pass


def test_budget_exceeded_raises_and_does_not_write() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/ledger.jsonl"
        ledger = AttemptLedger(path, max_attempts=1)
        ledger.reserve("x:1", "x", "anthropic", "claude-sonnet-5", 1)
        try:
            ledger.reserve("x:2", "x", "anthropic", "claude-haiku-4-5", 1)
            assert False, "expected BudgetExceededError"
        except BudgetExceededError:
            pass
        with open(path) as f:
            lines = [line for line in f.read().splitlines() if line.strip()]
        assert len(lines) == 1
