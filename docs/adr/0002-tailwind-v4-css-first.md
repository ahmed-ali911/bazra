# ADR 0002: Tailwind v4 (CSS-first) instead of the v3 config-file model

## Status
Accepted

## Context

Section 6 of the implementation brief lists `design-system/tailwind.config.ts`
as a Phase 0 file, and Section 8 lists `tailwindcss`, `postcss`, `autoprefixer`
as separate Phase 0 dependencies — this describes Tailwind v3's config-file
model. By the time Checkpoint 5 (design tokens) started, `tailwindcss@latest`
resolves to v4.3.3, which changed the configuration model significantly:
CSS-first via `@import "tailwindcss";` and an `@theme` block, no config file
by default (a JS/TS config can still be loaded via `@config` for advanced
cases), and no separate `postcss`/`autoprefixer` dependencies — vendor
prefixing is handled internally, and integration is typically via the
`@tailwindcss/vite` plugin rather than a PostCSS pipeline.

## Decision

Adopt Tailwind v4 rather than pinning to v3-lts. Use `@tailwindcss/vite` and
define the mapping from BAZRA's semantic CSS custom properties
(`--color-accent`, `--color-text-heading`, etc., defined in
`design-system/tokens.css` per Section 5) into Tailwind's `@theme` block in
that same file, rather than a separate `tailwind.config.ts`. `postcss.config.js`
and `autoprefixer` are dropped — not needed under v4's default pipeline.

This does not change the locked decision in Section 5 that CSS custom
properties are the source of truth and components reference semantic tokens,
never raw hex values or Tailwind's default palette directly — it changes only
how Tailwind itself is wired to consume those tokens.

## Consequences

- No `design-system/tailwind.config.ts` file exists; Tailwind theme
  configuration lives inside `design-system/tokens.css` via `@theme`.
- If a future need arises for JS-side theme logic (e.g. programmatic
  variants), a config file can be reintroduced via v4's `@config` directive
  without reversing this decision.
- `postcss`/`autoprefixer` are not installed. If some other tool later needs
  a PostCSS pipeline for an unrelated reason, `@tailwindcss/postcss` is the
  v4-compatible bridge back into PostCSS.
