"""Command-line interface: argparse subcommands and agent-oriented help texts."""

import argparse
import errno
import json
import os
import re
import sys
from datetime import date
from importlib.metadata import version as package_version
from pathlib import Path

from .download import DEFAULT_MAX_SIZE_MB, download_offline, download_online
from .errors import PermanentError
from .folders import run_check, run_folders
from .message import SNIPPET_CHARS
from .read import MAX_THREAD_MESSAGES, read_offline, read_online
from .search import MAX_LIMIT, SEARCH_BODY_CAP, Query, search_offline, search_online
from .session import mailbox_session
from .sync import run_sync
from .throttle import RetryLaterError

EXIT_CODES = """\
errors and exit codes:
  All errors are printed to stderr; stdout stays empty.
  0  success
  1  permanent error: {permanent}. Do not retry with the same arguments.
     A missing credentials file or a rejected login is also permanent:
     the message says what the mailbox owner has to fix; an agent cannot
     fix it and must not ask for the password. Also reported as 'stdout
     closed early, output truncated' when the consumer of stdout stopped
     reading: the JSON on stdout is incomplete.
  2  temporarily unavailable, the stderr message ends with 'retry after
     Ns': wait that long, then retry the same command. Causes: another
     yandex-mail process is using the mailbox (runs wait up to 30s for
     each other), Yandex asked to slow down (too many connections, a
     temporary login block: the pause is remembered, and until it ends
     every networked run fails fast without connecting), or the network
     is down.{offline}"""

OFFLINE_NOTE = """
     --offline runs never touch the network and never exit with 2."""

MESSAGE_SCHEMA = """\
  folder        str       IMAP folder name, as listed by 'yandex-mail folders'
  uid           int       message UID within the folder; with 'folder' it is
                          the message's address for 'read' and 'download'
  uidvalidity   int       the folder's UIDVALIDITY: if the server renumbers a
                          folder, old UIDs stop being valid (pass it back with
                          --uidvalidity to be sure the UID still means the same
                          message)
  file          str|null  absolute path of the archived .eml file (--offline);
                          null when the message came from the server
  message_id    str|null  Message-ID header, e.g. "<abc@example.ru>"
  in_reply_to   str|null  In-Reply-To: Message-ID of the message answered
  references    [str]     References: Message-IDs of the thread, oldest first
  date          str|null  Date header in ISO 8601 with the sender's UTC
                          offset, e.g. "2026-11-18T10:23:45+03:00"; the server
                          arrival time when the header is missing or broken
  from          obj|null  sender: {"name": str|null, "address": str|null},
                          names decoded (RFC 2047, koi8-r, windows-1251, ...)
  to            [obj]     recipients, same objects as 'from'
  cc            [obj]     copy recipients, same objects as 'from'
  subject       str|null  decoded subject
  seen          bool|null whether the message is marked read on the server;
                          null offline (the archive keeps no flags). This
                          tool never changes it: reading here does not mark
                          a message read
  size_bytes    int       size of the raw message (attachments included)"""

ATTACHMENTS_SCHEMA = """\
  attachments   [obj]     every part that is not body text, in message order:
    index         int       1-based position; the same numbering in 'download'
    filename      str|null  decoded original file name; null if unnamed
    content_type  str       MIME type, e.g. "application/pdf"; an attached
                            email is "message/rfc822"
    size_bytes    int       decoded size of the file
    inline        bool      true for parts shown inside the message (e.g. a
                            logo in an HTML letter), false for real attachments"""

MAIN_DESCRIPTION = """\
Read your own Yandex Mail mailbox over IMAP (imap.yandex.ru:993, SSL, with
an app password). Strictly read-only: folders are opened with EXAMINE and
messages fetched with BODY.PEEK, so nothing is ever marked read; nothing is
sent, moved, deleted or flagged.

This tool is designed for use by AI agents:
  - every command prints machine-readable JSON to stdout and nothing else;
  - errors go to stderr and the exit code is non-zero on failure:
    1 means a permanent error (do not retry with the same arguments),
    2 means temporarily unavailable (wait and retry; the stderr message
    says 'retry after Ns');
  - the login and the app password live in a file the mailbox owner
    creates by hand (see 'yandex-mail check --help'); an agent never
    handles the password.

A message is addressed by its folder name and UID, both taken from the
'search' output. 'sync' keeps a local archive of raw .eml files; 'search',
'read' and 'download' work on that archive with --offline, without the
network.
"""

MAIN_EPILOG = """\
examples:
  yandex-mail check                         is the mailbox reachable, whose is it
  yandex-mail folders                       folders with roles and counts
  yandex-mail search --from mosenergosbyt   newest letters from that sender
  yandex-mail search --unseen               unread letters
  yandex-mail read INBOX 1234               one letter in full
  yandex-mail read INBOX 1234 --thread      the whole conversation
  yandex-mail download INBOX 1234 --output ./files
                                            save the attachments of a letter
  yandex-mail sync --since 2026-01-01       start a local archive
  yandex-mail search --offline --address mosenergosbyt.ru
                                            search the archive, no network
  yandex-mail <command> --help              full reference of a command
"""

CHECK_DESCRIPTION = """\
Log in and print which mailbox this tool reads, with the inbox counts.
Use it to verify the setup, or to tell a login problem from other errors.

Setup (done once by the mailbox owner, not by an agent):
  1. Yandex Mail -> All settings (Все настройки) -> Email clients
     (Почтовые программы): enable 'From the imap.yandex.ru server via
     IMAP' and allow app passwords as the IMAP authorization method.
  2. id.yandex.ru -> Security (Безопасность) -> App passwords (Пароли
     приложений) -> Mail (Почта): create an app password.
  3. Create the credentials file in the per-user config directory
     (Windows: %APPDATA%\\yandex-mail\\credentials.json, Linux:
     ~/.config/yandex-mail/credentials.json), UTF-8:
       {"login": "name@yandex.ru", "password": "the app password"}

The password is never printed, logged or put into error messages.
"""

CHECK_EPILOG = """\
output schema (single JSON object on stdout):
  account   str   the login the tool is connected as
  server    str   "imap.yandex.ru:993"
  inbox     obj   {"messages": int, "unseen": int} of INBOX
  folders   int   number of folders in the mailbox

""" + EXIT_CODES.format(
    permanent="no credentials file, a rejected login (IMAP disabled, "
    "not an app password, revoked password, typo: the message lists what "
    "to check)",
    offline="",
)

FOLDERS_DESCRIPTION = """\
List the mailbox folders with their roles and message counts.

Roles come from the folders' SPECIAL-USE attributes, not from their names
(Yandex names folders by the account language: 'Sent' or 'Отправленные').
Folder names are printed decoded (IMAP modified UTF-7 is handled); pass
them to the other commands exactly as printed.
"""

FOLDERS_EPILOG = """\
output schema (JSON array on stdout, in server order), each element:
  name                 str       folder name; hierarchy levels are joined by
                                 'delimiter'
  role                 str|null  inbox|sent|drafts|junk|trash|archive|all|
                                 flagged; null for ordinary folders
  searched_by_default  bool      'search' and 'read --thread' look here when
                                 no --folder is given (all but junk, trash
                                 and the virtual all/flagged folders)
  archived_by_default  bool      'sync' archives it when no --folder is given
                                 (the same, minus drafts)
  messages             int|null  number of messages; null if the folder
                                 cannot hold messages (\\Noselect)
  unseen               int|null  number of unread messages
  delimiter            str|null  hierarchy delimiter (Yandex: '|')
  flags                [str]     raw LIST attributes, e.g. "\\\\HasNoChildren"

""" + EXIT_CODES.format(permanent="a rejected login", offline="")

SEARCH_DESCRIPTION = """\
Find messages and print the newest matches (summaries, without the full
text) as JSON. All given filters must match. Text filters are
case-insensitive substrings and may be in Russian.

By default the search covers every folder except spam, trash and virtual
folders (see 'yandex-mail folders', field searched_by_default); --folder
narrows it, and is the way to search spam or trash.

Online (default), Yandex's own IMAP SEARCH does the matching and
--since/--until compare the server arrival date. With --offline the local
archive made by 'sync' is searched instead, without the network:
--since/--until then compare the date of the Date header, and only
archived folders and messages are found.

Use the 'folder' and 'uid' of a result with 'yandex-mail read' to get the
full text, or with 'yandex-mail download' to save its attachments.
"""

SEARCH_EPILOG = (
    f"""\
output schema (single JSON object on stdout):
  total     int    number of matching messages (can exceed --limit)
  messages  [obj]  the newest --limit matches, newest first, each with:
{MESSAGE_SCHEMA}
  snippet       str|null  the first {SNIPPET_CHARS} characters of the text, whitespace
                          collapsed; null when the message has no text, or
                          when it is larger than {SEARCH_BODY_CAP // 1048576} MB
                          online (only its headers are fetched then)
{ATTACHMENTS_SCHEMA}
                          null for messages over {SEARCH_BODY_CAP // 1048576} MB
                          online; 'read' lists them

To page further back, narrow the search (e.g. --until the date of the oldest
result) rather than raising --limit.

"""
    + EXIT_CODES.format(
        permanent="bad arguments, unknown --folder, a server that rejects the "
        "search, --unseen with --offline, no archive yet (--offline), a "
        "rejected login",
        offline=OFFLINE_NOTE,
    )
    + """

examples:
  yandex-mail search --from mosenergosbyt.ru --since 2026-10-01
  yandex-mail search --subject "квитанция" --limit 5
  yandex-mail search --address info@mosobleirc.ru --folder INBOX
  yandex-mail search --text "лицевой счёт" --offline
"""
)

READ_DESCRIPTION = f"""\
Print one message in full as JSON: headers, the text and the attachment
list. With --thread, print its whole conversation instead.

FOLDER and UID come from the 'search' output ('folder', 'uid'). The text is
the text/plain body; for HTML-only letters, and for letters whose plain
part holds HTML source, it is the visible text of the HTML. Encodings (RFC 2047 headers, koi8-r, windows-1251, ...) are decoded.

--thread finds the other messages of the conversation in all searched
folders (sent mail included): linked by the References and In-Reply-To
headers, and, for messages that lack both (common with mail from
organizations), by the same subject without 'Re:'/'Fwd:' prefixes and a
shared correspondent. At most the newest {MAX_THREAD_MESSAGES} messages are printed.
"""

READ_EPILOG = (
    f"""\
output schema: a single JSON object; with --thread a JSON array of such
objects, oldest first:
{MESSAGE_SCHEMA}
  text          str|null  the message text; null if it has none
{ATTACHMENTS_SCHEMA}

"""
    + EXIT_CODES.format(
        permanent="bad arguments, unknown folder, no message with that UID "
        "(deleted, or the folder was renumbered: --uidvalidity mismatch), the "
        "message is not in the archive (--offline), a rejected login",
        offline=OFFLINE_NOTE,
    )
    + """

examples:
  yandex-mail read INBOX 1234
  yandex-mail read "Отправленные" 77 --thread
  yandex-mail read INBOX 1234 --offline
"""
)

DOWNLOAD_DESCRIPTION = """\
Save every attachment of one message into --output DIR (created if
missing) and print what was saved as JSON. FOLDER and UID come from the
'search' output.

File names are stable and collision-free:
  <date>_<key>_<index>_<original name>, e.g.
  2026-11-18_3f9a0c1b2d_1_Квитанция.pdf
where date is the message date, key is derived from the Message-ID (the
same message gets the same names online, offline and from any folder) and
index is the attachment's position in the 'attachments' list (it keeps
same-named attachments apart). Original names are sanitized; an unnamed
part gets a generated name such as 'part2.pdf'. An attachment whose file
already exists with the same size is skipped (status 'exists'), so
re-running a download is cheap and idempotent.
"""

DOWNLOAD_EPILOG = (
    """\
output schema (single JSON object on stdout):
  folder        str       folder name
  uid           int       message UID
  uidvalidity   int       the folder's UIDVALIDITY
  message_id    str|null  Message-ID header
  files         [obj]     one per attachment, in message order (empty if the
                          message has none):
    index         int       position in the message's attachment list
    filename      str|null  original name; null if unnamed
    content_type  str       MIME type
    size_bytes    int       size of the file
    file          str       absolute path of the file
    status        str       'saved' or 'exists' (already there, not rewritten)

"""
    + EXIT_CODES.format(
        permanent="bad arguments, unknown folder, no message with that UID, the "
        "message exceeds --max-size (the message names its size), a local file "
        "problem (unwritable --output, disk full), a rejected login",
        offline=OFFLINE_NOTE,
    )
    + """

examples:
  yandex-mail download INBOX 1234 --output ./bills
  yandex-mail download INBOX 1234 --output ./bills --offline
"""
)

SYNC_DESCRIPTION = """\
Bring the local mail archive up to date, then print a JSON summary. The
archive is what --offline searches and reads, and what scripts can process
without the network.

Layout, under the per-user config directory (Windows:
%APPDATA%\\yandex-mail\\archive, Linux: ~/.config/yandex-mail/archive):
  state.json                            progress per folder (do not edit)
  <folder>\\<UIDVALIDITY>\\<UID>.eml       one raw message per file, exactly
                                        as the server stores it; the file
                                        time is the server arrival time
<folder> is the folder name made safe for the file system. The archive only
grows: nothing is deleted from it, even when the message is deleted on the
server. If the server renumbers a folder (UIDVALIDITY changes), the folder
is archived again into a new <UIDVALIDITY> directory; the old one stays.

The first sync needs --since and archives every message that arrived on or
after that date. Later syncs fetch only messages newer than the last
archived one per folder; a folder without new mail costs one STATUS
request. A later, earlier --since extends the archive back to that date;
a later --since is ignored. Messages are fetched in small batches (up to
20 messages or 5 MB, two seconds apart), and an interrupted sync keeps
what it fetched and resumes after it.

By default all folders except spam, trash, drafts and virtual folders are
archived (see 'yandex-mail folders', field archived_by_default); --folder
picks folders explicitly.
"""

SYNC_EPILOG = (
    """\
output schema (single JSON object on stdout):
  archive   str    absolute path of the archive directory
  account   str    the mailbox the archive belongs to
  since     str    the archive's start date, YYYY-MM-DD
  added     int    messages added by this run
  total     int    messages in the whole archive
  folders   [obj]  one per synced folder:
    name                 str       folder name
    role                 str|null  folder role, as in 'folders'
    dir                  str       directory of its current generation
    uidvalidity          int       the folder's UIDVALIDITY
    uidvalidity_changed  bool      true if this run started a new generation
    added                int       messages added by this run
    total                int       messages in the current generation

"""
    + EXIT_CODES.format(
        permanent="bad arguments, --since missing for the first sync, unknown "
        "--folder, the archive belongs to another mailbox, a damaged or "
        "unwritable archive, a rejected login",
        offline="",
    )
    + """
  Messages fetched before a failure are kept; the retry continues after them.
  Yandex sometimes drops the connection during a long sync: run it again
  after the pause, as many times as needed, until it exits with 0.

server load (automatic):
  One connection per run, one STATUS per folder, then one SEARCH and FETCH
  batches only where there is new mail, two seconds apart. The run holds
  the inter-process lock throughout, so a long first sync makes other
  yandex-mail runs wait and fail with exit code 2: start with a recent
  --since and extend it later if needed.

examples:
  yandex-mail sync --since 2026-01-01      start the archive
  yandex-mail sync                         fetch what is new
  yandex-mail sync --since 2024-01-01      extend the archive back to 2024
"""
)


class Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on usage errors.

    The default argparse exit code for usage errors is 2, which this tool
    reserves for "temporarily unavailable, retry later".
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"error: {message}\n")


# ASCII digits only: int() alone also accepts "1_0", "+1", surrounding
# whitespace and non-ASCII digits; a mistyped UID must be rejected, not
# silently read as a different message.
_DIGITS = re.compile(r"[0-9]+")


def positive_int_type(value: str) -> int:
    if not _DIGITS.fullmatch(value) or int(value) < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


def limit_type(value: str) -> int:
    number = positive_int_type(value)
    if number > MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_LIMIT}")
    return number


def date_type(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be a date in YYYY-MM-DD format"
        ) from None


def text_type(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    if any(ch in value for ch in "\r\n\0"):
        raise argparse.ArgumentTypeError("must be a single line")
    return value


def _subparser(subparsers, name, help_text, description, epilog):
    return subparsers.add_parser(
        name,
        help=help_text,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _add_message_address(parser) -> None:
    parser.add_argument(
        "folder", metavar="FOLDER", help="folder name ('folder' in search output)"
    )
    parser.add_argument(
        "uid",
        metavar="UID",
        type=positive_int_type,
        help="message UID ('uid' in search output)",
    )
    parser.add_argument(
        "--uidvalidity",
        metavar="N",
        type=positive_int_type,
        help="fail instead of reading another message if the folder was "
        "renumbered ('uidvalidity' in search output); offline: read from that "
        "archive generation",
    )


def _add_offline(parser, what: str) -> None:
    parser.add_argument(
        "--offline",
        action="store_true",
        help=f"{what} the local archive made by 'sync' instead of the server",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="yandex-mail",
        description=MAIN_DESCRIPTION,
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version('yandex-mail')}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _subparser(
        subparsers,
        "check",
        "log in and show which mailbox is read (setup instructions in --help)",
        CHECK_DESCRIPTION,
        CHECK_EPILOG,
    )
    _subparser(
        subparsers,
        "folders",
        "list folders with roles and counts",
        FOLDERS_DESCRIPTION,
        FOLDERS_EPILOG,
    )

    search = _subparser(
        subparsers,
        "search",
        "find messages, JSON summaries",
        SEARCH_DESCRIPTION,
        SEARCH_EPILOG,
    )
    search.add_argument("--from", dest="sender", metavar="TEXT", type=text_type,
                        help="sender name or address contains TEXT")  # fmt: skip
    search.add_argument("--to", metavar="TEXT", type=text_type,
                        help="a To recipient contains TEXT")  # fmt: skip
    search.add_argument("--cc", metavar="TEXT", type=text_type,
                        help="a Cc recipient contains TEXT")  # fmt: skip
    search.add_argument("--address", metavar="TEXT", type=text_type,
                        help="the sender, a To or a Cc recipient contains TEXT "
                        "(correspondence with someone in both directions)")  # fmt: skip
    search.add_argument("--subject", metavar="TEXT", type=text_type,
                        help="the subject contains TEXT")  # fmt: skip
    search.add_argument("--text", metavar="TEXT", type=text_type,
                        help="the headers or the text contain TEXT")  # fmt: skip
    search.add_argument("--since", metavar="YYYY-MM-DD", type=date_type,
                        help="on or after this date")  # fmt: skip
    search.add_argument("--until", metavar="YYYY-MM-DD", type=date_type,
                        help="on or before this date")  # fmt: skip
    search.add_argument("--unseen", action="store_true",
                        help="only messages not marked read (online only)")  # fmt: skip
    search.add_argument("--folder", metavar="FOLDER", action="append",
                        help="search this folder (repeatable); default: all "
                        "but spam, trash and virtual folders")  # fmt: skip
    search.add_argument("--limit", metavar="N", type=limit_type, default=20,
                        help=f"return at most N newest matches, 1..{MAX_LIMIT} "
                        "(default: %(default)s)")  # fmt: skip
    _add_offline(search, "search")

    read = _subparser(
        subparsers,
        "read",
        "one message in full, or its thread, JSON",
        READ_DESCRIPTION,
        READ_EPILOG,
    )
    _add_message_address(read)
    read.add_argument(
        "--thread", action="store_true", help="print the whole conversation"
    )
    _add_offline(read, "read from")

    download = _subparser(
        subparsers,
        "download",
        "save the attachments of one message, JSON",
        DOWNLOAD_DESCRIPTION,
        DOWNLOAD_EPILOG,
    )
    _add_message_address(download)
    download.add_argument("--output", metavar="DIR", required=True,
                          help="destination directory; created if missing")  # fmt: skip
    download.add_argument("--max-size", metavar="MB", type=positive_int_type,
                          default=DEFAULT_MAX_SIZE_MB,
                          help="refuse messages larger than this many MB "
                          "(default: %(default)s)")  # fmt: skip
    _add_offline(download, "take the message from")

    sync = _subparser(
        subparsers,
        "sync",
        "update the local .eml archive, JSON summary",
        SYNC_DESCRIPTION,
        SYNC_EPILOG,
    )
    sync.add_argument("--since", metavar="YYYY-MM-DD", type=date_type,
                      help="start date; required for the first sync, an "
                      "earlier date later extends the archive back")  # fmt: skip
    sync.add_argument("--folder", metavar="FOLDER", action="append",
                      help="archive this folder (repeatable); default: all but "
                      "spam, trash, drafts and virtual folders")  # fmt: skip
    return parser


def _query(args) -> Query:
    return Query(
        sender=args.sender,
        to=args.to,
        cc=args.cc,
        address=args.address,
        subject=args.subject,
        text=args.text,
        since=args.since,
        until=args.until,
        unseen=args.unseen,
    )


def run(args):
    """Run one parsed command and return its JSON-ready result."""
    offline = getattr(args, "offline", False)
    if args.command == "search" and offline:
        return search_offline(_query(args), args.folder, args.limit)
    if args.command == "read" and offline:
        return read_offline(args.folder, args.uid, args.uidvalidity, args.thread)
    if args.command == "download" and offline:
        return download_offline(
            args.folder, args.uid, args.uidvalidity, Path(args.output), args.max_size
        )
    with mailbox_session() as mailbox:
        if args.command == "check":
            return run_check(mailbox)
        if args.command == "folders":
            return run_folders(mailbox)
        if args.command == "search":
            return search_online(mailbox, _query(args), args.folder, args.limit)
        if args.command == "read":
            return read_online(
                mailbox, args.folder, args.uid, args.uidvalidity, args.thread
            )
        if args.command == "download":
            return download_online(
                mailbox,
                args.folder,
                args.uid,
                args.uidvalidity,
                Path(args.output),
                args.max_size,
            )
        return run_sync(mailbox, args.since, args.folder)


def _exit_closed_stdout() -> int:
    """Exit path for a stdout consumer that closed the pipe early."""
    try:
        print("error: stdout closed early, output truncated", file=sys.stderr)
    except OSError:
        pass
    try:
        sys.stdout.close()
    except OSError:
        pass
    return 1


def main(argv: list[str] | None = None) -> int:
    # On Windows, redirected stdout/stderr default to the legacy ANSI code
    # page, which cannot encode Cyrillic. JSON output and error messages
    # must always be UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
        # Flush now, while a consumer that already closed the pipe still
        # surfaces here as BrokenPipeError (EINVAL on Windows) and maps to
        # the documented "output truncated" instead of escaping at shutdown.
        sys.stdout.flush()
    except RetryLaterError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PermanentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return _exit_closed_stdout()
    except OSError as error:
        if os.name == "nt" and error.errno == errno.EINVAL:
            # Windows reports a write to a closed pipe as EINVAL.
            return _exit_closed_stdout()
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - CLI boundary, report and exit
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
