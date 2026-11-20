# 0010. Small sync batches: Yandex drops the connection on large FETCH responses

Status: Accepted

## Context

`sync` fetched messages in batches of up to 50 messages and 20 MB, about a
second apart (ADR-0005, ADR-0008). The first full sync of the owner's
mailbox (eleven years, about 1250 messages) broke off with

    the connection to imap.yandex.ru broke
    (command: FETCH => problems with connection); retry after 30s

The server closed the connection (`BYE problems with connection`) in the
middle of a large FETCH. No limit answer was involved, and rerunning
`sync` completed the archive. Yandex is known to drop connections on
large FETCH responses.

Alternative considered: reconnect and continue inside the same run after a
pause. It would hide the breaks from callers, but it breaks the "one
connection per run" rule the tool was built around, and a rerun already
continues from the saved state.

## Decision

Batches hold up to 20 messages or 5 MB (a larger message still goes alone)
and are 2 seconds apart. A broken connection keeps exit code 2 with
`retry after 30s`; `sync --help` and the agent skill tell callers to rerun
`sync` until it exits with 0.

## Consequences

- Each FETCH response is a quarter of the previous maximum, so a dropped
  connection is less likely and loses less work.
- A full first sync takes longer: about a thousand messages need some fifty
  batches, which adds under two minutes of pauses.
- If breaks still happen, the constants at the top of `sync.py` are the
  place to tune.
