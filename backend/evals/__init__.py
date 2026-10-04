"""Checkpoint 5.4 — the offline evaluation harness foundation.

Deliberately a TOP-LEVEL package, a sibling of `app/` and `tests/`
(mirroring this repository's own existing precedent: `tests/` already
holds cross-cutting tests that aren't owned by any one `app.modules.*`
package) — never nested inside `app/modules/`, so production code has
no natural import path toward it. Nothing under `app/` imports from
`evals/`; this is a structural guarantee, not just a convention — see
this package's own tests for a verifying grep.

This package MEASURES candidate outputs that are supplied to it as
plain fixture data. It does not generate them, does not call a model
or any external network, and is not imported by — or wired into — any
production request path. See evals/schemas.py for the case/result
contract and evals/proactive_narration.py for the first real suite.
"""
