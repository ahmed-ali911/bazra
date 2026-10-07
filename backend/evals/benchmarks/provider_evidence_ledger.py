"""Checkpoint 5.8B, section 2 — a durable, crash-safe attempt ledger.
The budget-safety core of the whole checkpoint: "Reserve and record an
attempt BEFORE issuing the external request so an interrupted run cannot
silently repeat it."

Mechanism: an append-only JSONL file. `reserve()` writes one line,
flushes, and fsyncs BEFORE returning — only once that line is durably on
disk does the caller go on to make the real network call. If the process
is killed mid-call (network hang, crash, Ctrl-C), the reservation record
already survives; re-running the same script re-loads the ledger,
recognizes that attempt_id as already spent (`already_attempted`), and
skips it rather than re-issuing it — "no retries, no replacement
attempts" is enforced by construction, not by operator discipline.

Zero network/provider/DB import anywhere in this module — pure file I/O.
"""

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    case_id: str
    provider: str
    model: str
    rep: int


class BudgetExceededError(Exception):
    pass


class DuplicateAttemptError(Exception):
    pass


class AttemptLedger:
    """One ledger instance per benchmark run, backed by one JSONL file.
    `max_attempts` is enforced here, not by the caller counting on its
    own — `reserve()` itself refuses once the cap is reached, so a bug
    in the orchestration loop cannot silently exceed the approved
    budget."""

    def __init__(self, path: str, max_attempts: int):
        self._path = path
        self._max_attempts = max_attempts
        self._reserved_ids: set[str] = set()
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                self._reserved_ids.add(record["attempt_id"])

    def already_attempted(self, attempt_id: str) -> bool:
        return attempt_id in self._reserved_ids

    def total_reserved(self) -> int:
        return len(self._reserved_ids)

    def remaining_budget(self) -> int:
        return self._max_attempts - len(self._reserved_ids)

    def reserve(self, attempt_id: str, case_id: str, provider: str, model: str, rep: int) -> None:
        """Durably records intent to attempt this exact (case, provider,
        model, rep) BEFORE any network call is made. Raises
        DuplicateAttemptError if this exact attempt_id was already
        reserved (by this run or a prior interrupted one) — the caller
        must treat that as "already spent," never retry it.
        Raises BudgetExceededError if this reservation would exceed
        `max_attempts` — refuses to write the record at all in that
        case, so the ledger never shows more reservations than the
        approved cap."""
        if attempt_id in self._reserved_ids:
            raise DuplicateAttemptError(f"attempt {attempt_id!r} already reserved — refusing to repeat it")
        if len(self._reserved_ids) >= self._max_attempts:
            raise BudgetExceededError(
                f"budget cap of {self._max_attempts} already reached — refusing to reserve attempt {attempt_id!r}"
            )
        record = {"attempt_id": attempt_id, "case_id": case_id, "provider": provider, "model": model, "rep": rep}
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._reserved_ids.add(attempt_id)
