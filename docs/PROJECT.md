# yandex-mail — Project Description

## Purpose

A minimal, strictly read-only CLI for reading the owner's own Yandex Mail
mailbox over IMAP. The first consumer is the owner's correspondence with
organizations (utility companies and the like), which other projects read
and file into their own archives through wrappers around this CLI.

The primary consumer is AI agents and scripts, not humans: every command
prints JSON on stdout, errors go to stderr, exit codes tell permanent
errors from retryable ones, and every `--help` is self-sufficient.

## Scope

A single `yandex-mail` command with six subcommands:

1. **`check`** — log in and print which mailbox is read.
2. **`folders`** — the folders with their roles and counts.
3. **`search`** — find messages on the server or in the archive.
4. **`read`** — one message in full, or its whole thread.
5. **`download`** — save the attachments of one message.
6. **`sync`** — keep an append-only local archive of raw `.eml` files.

Non-goals: sending, deleting, moving, flagging, marking read, real-time
monitoring, OAuth, other mail providers (the server is fixed to
`imap.yandex.ru:993`).

## Stack

- Python 3.11+, managed with `uv`
- [IMAPClient](https://imapclient.readthedocs.io/) — IMAP over TLS (ADR-0003)
- platformdirs — user config directory
- filelock — inter-process lock
- stdlib `email` with the compat32 policy plus own decoding — MIME parsing
- pytest — tests against an in-memory fake IMAP client (no network)
- ruff — linter (default rules plus BLE) and formatter

## Installation

Distribution is via the git repository, installation via `uv`:

```
uv tool install git+<repo-url>     # puts yandex-mail on PATH
uv tool upgrade yandex-mail        # upgrade
uv tool install <path-to-clone>    # from a local clone
```

For development inside a clone: `uv run yandex-mail ...`. The agent skill
in `skills/yandex-mail/` is copied into the agent's skill directory
(ADR-0002).

## Configuration

All files live in a per-user directory, independent of the working
directory (on POSIX it is made private, mode 0700):

- Windows: `%APPDATA%\yandex-mail\`
- Linux: `~/.config/yandex-mail/`

Contents:

- `credentials.json` — `{"login": "...", "password": "..."}`, created by
  the mailbox owner by hand (ADR-0004). Read as UTF-8 with or without a
  byte order mark. The password must be a Yandex app password for Mail,
  and IMAP must be enabled in the mailbox settings.
- `throttle.json` — pacing state: last run time, an active server-imposed
  pause and the reason for the last one; managed automatically.
- `yandex_mail.lock` — the inter-process lock file.
- `archive/` — the local archive kept by `sync`.

The directory may be a symlink or a Windows junction to another drive,
such as an encrypted volume. While its target is missing (the drive is
not mounted), every command exits with 1 and says so, rather than
reporting the credentials file or the archive as missing.

The password is never printed or logged: error messages name the file and
its format, never its content; the `Credentials` object keeps the password
out of its repr; imapclient's DEBUG loggers (which would log the LOGIN
command) are pinned to WARNING.

## Read-only guarantee

All server access goes through `ReadOnlyMailbox` (ADR-0003):

- folders are opened with EXAMINE only (`select_folder(readonly=True)`);
  per RFC 3501 an EXAMINEd folder allows no changes, not even to \Seen;
- message data is fetched with `BODY.PEEK[]` / `BODY.PEEK[HEADER]`, and
  FETCH items are whitelisted (`BODY.PEEK[]`, `BODY.PEEK[HEADER]`,
  `RFC822.SIZE`, `INTERNALDATE`, `FLAGS`): `RFC822` or `BODY[]`, which set
  \Seen, are refused before reaching the server;
- the facade has no method that sends, stores flags, copies, moves,
  deletes or expunges. `tests/test_readonly.py` checks that only
  whitelisted IMAPClient methods are called anywhere.

## Messages

A message is addressed by its folder name and UID (with the folder's
UIDVALIDITY to detect renumbering). The JSON schema of a message:

```json
{
  "folder": "INBOX",
  "uid": 1234,
  "uidvalidity": 1700000000,
  "file": null,
  "message_id": "<a1@mosenergosbyt.ru>",
  "in_reply_to": "<q1@yandex.ru>",
  "references": ["<q1@yandex.ru>"],
  "date": "2026-11-18T10:23:45+03:00",
  "from": {"name": "Мосэнергосбыт", "address": "info@mosenergosbyt.ru"},
  "to": [{"name": "Иван", "address": "name@yandex.ru"}],
  "cc": [],
  "subject": "RE: Вопрос по счёту",
  "seen": true,
  "size_bytes": 45678,
  "text": "Перерасчёт выполнен.",
  "attachments": [
    {"index": 1, "filename": "Акт.pdf", "content_type": "application/pdf",
     "size_bytes": 23456, "inline": false}
  ]
}
```

- `file` — path of the archived `.eml` for `--offline` results, else null.
- `date` — the Date header with the sender's offset; the server arrival
  time (INTERNALDATE, or the archived file time offline) when the header
  is missing or broken; a zone-less date is read as UTC.
- `from` is one object (the first From address); `to`/`cc` are lists.
- `seen` — the server's \Seen flag; null offline (the archive keeps no
  flags). The tool never changes it.
- `text` — the text/plain parts; if they are missing or blank, the visible
  text of the text/html parts (scripts, styles and the head dropped, one
  block element per line). A text/plain part that holds an HTML document
  (Mosenergosbyt and MosOblEIRC send those) is converted the same way.
  Invisible characters that marketing mail pads its preview text with
  (zero-width spaces and non-joiners, braille blanks, soft hyphens) are
  dropped. `search` gives `snippet` (first 200 characters,
  whitespace collapsed) instead.
- `attachments` — every leaf part that is not body text, numbered from 1
  in message order. An attached email (message/rfc822) is one attachment.
  `inline` marks parts shown inside the message (Content-Disposition
  inline, or a Content-ID without a disposition).

Decoding (`message.py`) handles RFC 2047 encoded words (adjacent words in
one charset are joined as bytes first: some mailers split a UTF-8
character between them), RFC 2231 parameters, encoded words inside
filenames, raw 8-bit headers in an undeclared charset, wrong charset labels
and unknown ones (`unknown-8bit`, `win-1251`, ...). Undeclared or mislabeled
8-bit text is tried as UTF-8, then as windows-1251 and koi8-r, keeping the
decoding with more lowercase than uppercase Cyrillic letters (the two
charsets have the cases at swapped positions). Nothing raises on malformed
input.

## Commands

The exact contract of each command (arguments, output schema, exit codes)
is in its `--help`; this section records the behavior behind it.

### check, folders

`check` logs in, lists the folders and STATUSes INBOX: `{"account",
"server", "inbox": {"messages", "unseen"}, "folders"}`.

`folders` lists every folder with `name`, `role`, `searched_by_default`,
`archived_by_default`, `messages`, `unseen`, `delimiter`, `flags`. Names
are decoded from IMAP modified UTF-7 by IMAPClient. Roles come from the
SPECIAL-USE attributes (`\Sent`, `\Junk`, `\Trash`, `\Drafts`, `\Archive`,
`\All`, `\Flagged`) — never from names, since Yandex names folders by the
account language; INBOX is recognized by name, as IMAP defines it. If the
LIST answer carries no special-use attribute at all and the server supports
XLIST, XLIST is asked instead (it calls spam `\Spam`). Default folder sets
are in ADR-0009: search skips junk, trash and the virtual all/flagged
folders; sync skips drafts too.

### search

Filters (all must match): `--from`, `--to`, `--cc`, `--address` (any of
the three), `--subject`, `--text` (headers or body), `--since`/`--until`
(inclusive dates), `--unseen` (online only), `--folder` (repeatable),
`--limit` (1..100, default 20). Output: `{"total", "messages"}`, the newest
matches first, as summaries (ADR-0006).

Online, per folder: EXAMINE, one UID SEARCH, one FETCH of INTERNALDATE and
RFC822.SIZE for its newest `--limit` matches; then the merged newest
`--limit` are fetched per folder in one FETCH — `BODY.PEEK[]` for messages
up to 2 MB, `BODY.PEEK[HEADER]` above that (`snippet` and `attachments`
are then null). User strings are always sent as IMAP literals, and
`CHARSET UTF-8` is declared when one of them is not ASCII, so Cyrillic
reaches the server byte-exact. Online dates compare the server arrival
date (IMAP SINCE/BEFORE).

Offline, the archive's `.eml` files are matched locally with the same
semantics (case-insensitive substrings of the decoded headers and text);
dates compare the Date header; a message archived in several UIDVALIDITY
generations of a folder counts once, from the newest one.

### read

`read FOLDER UID` prints one full message. `--uidvalidity N` makes it
fail (permanent) instead of reading another message if the folder was
renumbered; offline it picks that archive generation.

`--thread` prints the conversation, oldest first, at most the newest 50
messages (ADR-0007). Online, candidates are searched in every default
folder plus the message's own: same base subject, or a References /
In-Reply-To / Message-ID header naming the thread root, the message or its
parent (if the server rejects HEADER search, the subject alone is used).
Their headers are fetched, the thread is assembled locally, and the
members are fetched in full. Offline, all archived messages are the
candidates.

### download

Saves every attachment into `--output DIR` as
`<date>_<key>_<index>_<sanitized name>` (ADR-0006): `key` is the first 10
hex digits of SHA-256 of the Message-ID (of folder, UIDVALIDITY and UID
when there is none), so the same message gets the same names online,
offline and from any folder; `index` keeps same-named attachments of one
message apart; unnamed parts get `part<index><ext>` with the extension
from the stdlib's builtin MIME table. Names are sanitized like folder
directory names (separators, Windows-forbidden and invisible bidi
characters replaced, trailing dots stripped, 100-byte cap keeping the
extension, reserved device names prefixed). Files are written via `.part`
and renamed; a file that already exists with the same size is skipped
(`status: "exists"`). `--max-size MB` (default 50) refuses messages larger
than that, measured as the raw message (checked from RFC822.SIZE before
the transfer).

### sync

Keeps the archive (ADR-0005):

```
archive/
  state.json                         account, start date, per-folder progress
  <folder dir>/<UIDVALIDITY>/<UID>.eml
```

- `<folder dir>` is the sanitized folder name, made unique
  case-insensitively (`Bills`, `bills (2)`); the mapping is in
  `state.json`.
- Each `.eml` is the raw message exactly as fetched; its modification time
  is set to the server's INTERNALDATE.
- Files are written once (via `.part` and rename) and never changed or
  deleted, even when the message is deleted on the server.
- First sync: `--since` is required; every default folder (or each
  `--folder`) is searched with `SINCE <date>` and fetched.
- Later syncs: one STATUS per folder; a folder whose UIDVALIDITY is
  unchanged and whose UIDNEXT shows no new UID is not even opened.
  Otherwise `UID SEARCH UID <last+1>:* SINCE <start>` finds the new
  messages.
- UIDVALIDITY changed: the old generation directory stays; the folder is
  archived again from its start date into a new one.
- An earlier `--since` extends the archive back (`SINCE new BEFORE old`
  per folder); a later one is ignored.
- Messages are fetched in batches of consecutive UIDs (up to 20 messages
  and 5 MB of RFC822.SIZE per FETCH, two seconds apart: Yandex drops the
  connection on large FETCH responses, ADR-0010). `state.json`
  is saved after every batch, so an interrupted sync resumes after the last
  saved message.
- A folder first seen later (created or renamed on the server) starts
  from the archive's start date. The archive belongs to one account; a
  sync as another login is refused.

Output: `{"archive", "account", "since", "added", "total", "folders":
[{"name", "role", "dir", "uidvalidity", "uidvalidity_changed", "added",
"total"}]}`.

## Errors and server friendliness

Exit codes: `0` success; `1` permanent (usage errors included: argparse's
code 2 is remapped); `2` retry later, the stderr message ends with
`retry after Ns`.

- **One connection per run**, under a global file lock: a second process
  waits up to 30 s, then exits with 2. Networked runs are spaced at least
  2 s apart. `--offline` runs take no lock and never touch the network.
- **Login rejected** (`LoginError` without a limit marker): exit 1 with an
  explanation of the usual causes in order — IMAP disabled, not an app
  password, revoked password or typo — and the settings path to fix each.
  Yandex answers all of them with the same "Invalid credentials or IMAP is
  disabled".
- **Server limits** (ADR-0008): answers matching "too many connections"
  pause 120 s, a temporary login block 900 s, other limits (`[LIMIT]`,
  `[UNAVAILABLE]`, "try later", ...) 300 s, unless the answer names a
  duration. The pause is persisted in `throttle.json`; until it ends every
  networked run exits with 2 without connecting.
- **Network failures** (connection refused, timeouts, dropped connections,
  a BYE without a limit marker): exit 2, retry after 30 s, nothing
  persisted. Socket timeout: 60 s per answer. An unverifiable TLS
  certificate is permanent.
- Local file errors (archive, download directory) are permanent and never
  reported as network failures.

## Testing

Unit tests only, no network: `tests/conftest.py` provides `FakeIMAP`, an
in-memory stand-in for IMAPClient with a call log and a small SEARCH
evaluator, plugged in at the `session.connect` seam. Covered: decoding
(RFC 2047 in several charsets, split multi-byte words, raw 8-bit headers,
koi8-r and windows-1251 bodies, wrong labels, HTML-only bodies, every
filename encoding, attached emails, addresses with commas, dates), thread
assembly, folder roles and default sets, the archive (first sync,
increments, deletions kept, UIDVALIDITY generations, batching by count and
size, resume after an interrupted batch, extending `--since`, name
collisions), search/read/download online and offline, the read-only
guarantee, login and limit classification, persisted pauses, the lock and
the exit codes.

CI (GitHub Actions) runs ruff and the suite on Ubuntu and Windows, smoke-
tests the packaged entry point (`uvx --from . yandex-mail ... --help`) and
checks that `check` without a credentials file exits with 1.

## Layout

```
src/yandex_mail/
    cli.py        argparse subcommands, --help texts, exit codes
    session.py    lock, pause gate, connect, login, error classification
    mailbox.py    ReadOnlyMailbox: the only path to the IMAP server
    folders.py    folder listing, roles, default sets; check and folders
    message.py    raw message -> JSON schema, all decoding
    htmltext.py   HTML body -> plain text
    thread.py     thread assembly
    search.py     search online and offline
    read.py       read and read --thread, online and offline
    download.py   attachment saving and naming
    sync.py       archive sync
    archive.py    archive layout, state file, archived message lookup
    files.py      name sanitizing, atomic writes
    throttle.py   lock, persisted pauses, pacing
    config.py     config directory, credentials file
    errors.py     PermanentError (exit code 1)
skills/yandex-mail/   the agent skill (ADR-0002)
adr/                  architecture decision records
tests/
```
