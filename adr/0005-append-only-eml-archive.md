# 0005. Append-only archive of raw .eml files, read by `--offline`

Status: Accepted

## Context

Wrappers in other projects file the owner's correspondence into their own
archives, and must work without the network. They need a local copy of the
mail that only grows: a message deleted on the server must stay. tg-reader
keeps one gap-free JSONL file per chat; mail differs in ways that matter:

- the raw message is the authoritative record (attachments, original
  encodings), and `.eml` is a standard format any tool opens;
- UIDs are per folder and only valid within one UIDVALIDITY;
- messages are not strictly ordered by UID in time (moved messages get
  new UIDs), and folders appear and get renamed.

Alternatives: JSONL of decoded messages (loses attachments and the
original bytes, and freezes today's decoding bugs into the archive);
one file per Message-ID across folders (deduplicates, but the request asked
for files by folder, and Message-IDs are missing or duplicated in practice);
SQLite (needs schema migrations, and is opaque to other tools).

## Decision

We will keep `archive/<folder dir>/<UIDVALIDITY>/<UID>.eml`, raw bytes
exactly as fetched, plus `archive/state.json` with the account, the start
date and, per folder, its directory, role, start date, current UIDVALIDITY
and last archived UID. Files are written once and never changed or
deleted. A UIDVALIDITY change starts a new generation directory and
re-archives the folder from its start date; the old one stays. The file
time of each `.eml` is set to the server's INTERNALDATE.

`sync` is incremental per folder (STATUS first; `UID last+1:* SINCE start`
only when UIDNEXT shows new mail), fetches in batches, and saves the state
after every batch. `--since` is required once; an earlier date later
extends the archive back, a later one is ignored. `search`, `read` and
`download` take `--offline` to work on the archive with the same output
schema, plus the `.eml` path in `file`.

## Consequences

- The archive is a set of standard files: wrappers can copy `.eml` files or
  use `--offline` JSON, and decoding improvements apply to old mail too.
- Offline search parses files on every run; fine for the expected few
  thousand messages, an index could come later if needed.
- After a UIDVALIDITY change the same message exists in two generations;
  offline search and threads deduplicate by Message-ID, preferring the
  newest generation.
- Flags (read/unread) are not archived; offline `seen` is null.
- The archive belongs to one account; syncing as another login is refused.
