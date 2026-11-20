# 0001. Record architecture decisions as ADRs

Status: Accepted

## Context

Debatable design choices (CLI contract shape, exit-code scheme, archive
format, how threads are built, how server limits are handled) tend to get
re-discussed from scratch in later sessions, sometimes landing on a
different answer with no record of why the previous one was chosen. The
sibling project tg-reader solved this with an ADR log, and this project
follows its conventions.

## Decision

We will keep a lightweight ADR log in `adr/`, one Markdown file per
decision, numbered sequentially. `AGENTS.md` instructs agents to check this
log before proposing a debatable change and to add a record after making
one.

## Consequences

Debatable decisions are made once and referenced afterward instead of
being re-argued. A decision worth recording needs a short file written for
it.
