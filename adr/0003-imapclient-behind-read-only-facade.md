# 0003. IMAPClient behind a read-only facade

Status: Accepted

## Context

The tool must never change the mailbox: no message may become read, and
nothing may be sent, moved, deleted or flagged. IMAP makes mistakes easy:
SELECT (instead of EXAMINE) plus a plain `BODY[]` or `RFC822` fetch sets
\Seen, and a client library exposes write commands next to read ones.

Library choice: the stdlib `imaplib` returns raw response lines (FETCH
literals, LIST attributes, modified UTF-7 names) that we would have to
parse ourselves, and it cannot send more than one literal per command,
which a SEARCH with several Cyrillic strings needs. IMAPClient parses
responses into Python types, decodes folder names from modified UTF-7,
sends 8-bit SEARCH arguments as literals, and is maintained.

## Decision

We will use IMAPClient, and the rest of the code will reach the server
only through `ReadOnlyMailbox` (`mailbox.py`), which offers exactly:
LIST/XLIST, STATUS, EXAMINE (`select_folder(readonly=True)`), UID SEARCH
and UID FETCH of whitelisted items (`BODY.PEEK[]`, `BODY.PEEK[HEADER]`,
`RFC822.SIZE`, `INTERNALDATE`, `FLAGS`). User search strings are always
sent as literals (any byte is safe in a literal; an atom breaks on
quotes and parentheses), with `CHARSET UTF-8` when one is not ASCII.
`session.py` only connects, logs in and logs out.

## Consequences

- Read-only is enforced twice (EXAMINE forbids changes per RFC 3501;
  PEEK does not set \Seen), and checkable in one file;
  `tests/test_readonly.py` asserts that only whitelisted client methods are
  called anywhere.
- IMAPClient 4.x drops FETCH responses for message IDs it did not ask for,
  so a UID range ("1:50") cannot be passed; batches are explicit lists of
  consecutive UIDs, which is the same server work.
- Adding a capability (e.g. BODYSTRUCTURE) means extending the whitelist
  deliberately.
