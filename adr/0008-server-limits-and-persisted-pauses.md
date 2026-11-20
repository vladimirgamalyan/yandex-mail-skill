# 0008. Classify server limit answers and persist conservative pauses

Status: Accepted

## Context

Yandex limits IMAP clients: too many simultaneous connections, too many
logins, temporary login blocks after failures. Unlike Telegram's
FLOOD_WAIT, the answers carry no machine-readable duration and their
wording is not documented (and may be Russian). Retrying through a limit
extends it; a login block can lock the owner out of other mail clients
too. Callers are agents and scheduled wrappers that retry on their own.

Yandex also answers a wrong password and disabled IMAP identically
("Invalid credentials or IMAP is disabled").

## Decision

Server answers (login failures, BYE, NO/BAD responses) are classified by
response codes and wording in English and Russian:

- "too many connections" → pause 120 s;
- a temporary login block ("blocked", "too many login attempts") → 900 s;
- other limits (`[LIMIT]`, `[UNAVAILABLE]`, "rate limit", "try later",
  "temporarily") → 300 s;
- a duration named in the answer ("try again in 10 minutes") wins.

A pause is persisted in `throttle.json` (with the reason, for humans) and
reported as exit code 2 with `retry after Ns`; until it ends, every
networked run exits with 2 before connecting, so parallel callers and
retries cannot extend it. A rejected login without a limit marker is
permanent (exit 1) with the causes to check in order and where to fix
them. Network failures are exit 2 without a persisted pause.

Further, as in tg-reader: one connection per run under a global file lock,
runs spaced at least 2 s apart, sync batches about a second apart.

## Consequences

- The pause lengths are guesses on the safe side; they are constants at
  the top of `session.py`, easy to tune once real answers are seen.
- An unrecognized limit answer is reported as a permanent IMAP error;
  adding its wording to the rules fixes that.
- A permanent account block matching "blocked" is retried every 15 minutes
  instead of failing permanently; accepted, it is rare and visible.
