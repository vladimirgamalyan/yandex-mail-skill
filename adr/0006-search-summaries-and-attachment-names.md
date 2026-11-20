# 0006. Search returns summaries; stable attachment file names

Status: Accepted

## Context

Emails are long: HTML letters from organizations turn into kilobytes of
text, replies quote the whole history. Twenty full messages in a search
result would flood an agent's context, while the agent needs only enough
to pick the message to read. Search results should still show attachments
("find the letter with the PDF bill"). Fetching attachment lists without
the body needs BODYSTRUCTURE parsing, a second classification of parts
that would drift from the one `read` uses.

For `download`, names must be stable (re-running is idempotent, wrappers
can skip known files), collision-free in a shared directory, and safe
(names come from senders).

## Decision

`search` returns `{"total", "messages"}` where each message has the full
header fields and attachment list but a 200-character `snippet` instead of
`text`; `read` gives the full text. Online, search fetches `BODY.PEEK[]`
of the result messages and parses them with the same code as `read`;
messages over 2 MB get `BODY.PEEK[HEADER]` only, with `snippet` and
`attachments` null, so a search never pulls large scans.

`download` names files `<date>_<key>_<index>_<sanitized name>`, where `key`
is a hash of the Message-ID (of folder, UIDVALIDITY and UID without one)
and `index` the attachment's position. Existing files of the same size are
skipped. `--max-size` limits the raw message size, checked before the
transfer.

## Consequences

- One parsing path for read, search and download: attachment lists agree.
- A search costs the result messages' full size (bounded by `--limit` and
  the 2 MB cap), more than a header-only search would.
- Attachment names are not human-chosen; the original name is kept at the
  end, and `filename` in the output gives it exactly.
