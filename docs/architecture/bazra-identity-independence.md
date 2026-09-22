# BAZRA Identity Independence

**This is a standing architecture principle, not a numbered ADR.** It
records no implementation decision and resolves nothing about code that
exists today — unlike `docs/adr/0001` and `docs/adr/0002`, which each
locked a specific choice against specific code at the time it was made.
This note exists so the reasoning is on record *before* it's needed, for
decisions not yet made.

## The principle

BAZRA's long-term identity and meaningful persistent state must not be
owned by a specific LLM provider, frontend implementation, rendering
technology, voice provider, or device.

Models, interfaces, rendering systems, voice systems, and future
embodiments are replaceable capabilities around BAZRA — not BAZRA's
identity itself. The user's relationship is with BAZRA, not with
whichever model, framework, or device happens to be running it at a
given time.

## What this principle does and doesn't do

It constrains *future* decisions, at the point those systems are
actually built — for example, how a Model Router boundary is shaped once
one exists, or how persistent state is structured once memory/personality
systems are implemented.

It does **not** justify building interfaces, schemas, registries, adapters,
or speculative enums today in anticipation of those systems. Nothing in
the current codebase (Phase 0–2) has been found to conflict with this
principle, and this note does not request or imply any change to it.
