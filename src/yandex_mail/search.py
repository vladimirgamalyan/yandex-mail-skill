"""'search': find messages on the server (IMAP SEARCH) or in the archive."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from . import archive
from .errors import PermanentError
from .folders import SEARCH_SKIPPED_ROLES, list_folders, pick_folders
from .mailbox import ReadOnlyMailbox, or_all, text
from .message import body_text, header_block, message_dict, parse, read_headers

MAX_LIMIT = 100
# Search results larger than this are fetched as headers only: a search
# should not pull a 30 MB scan to show its subject line.
SEARCH_BODY_CAP = 2 * 1024 * 1024

_OLDEST = datetime.min.replace(tzinfo=UTC)


@dataclass
class Query:
    sender: str | None = None
    to: str | None = None
    cc: str | None = None
    address: str | None = None
    subject: str | None = None
    text: str | None = None
    since: date | None = None
    until: date | None = None
    unseen: bool = False


def imap_criteria(query: Query) -> list:
    """IMAP SEARCH keys for the query; all of them must match (AND)."""
    criteria = []
    for key, value in (
        ("FROM", query.sender),
        ("TO", query.to),
        ("CC", query.cc),
        ("SUBJECT", query.subject),
        ("TEXT", query.text),
    ):
        if value:
            criteria += [key, text(value)]
    if query.address:
        criteria += or_all([[key, text(query.address)] for key in ("FROM", "TO", "CC")])
    if query.since:
        criteria += ["SINCE", query.since]
    if query.until:
        # BEFORE is exclusive; --until is inclusive.
        criteria += ["BEFORE", query.until + timedelta(days=1)]
    if query.unseen:
        criteria.append("UNSEEN")
    return criteria or ["ALL"]


def _aware(moment: datetime | None) -> datetime | None:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def search_online(
    mailbox: ReadOnlyMailbox, query: Query, folder_names: list[str] | None, limit: int
) -> dict:
    """Newest 'limit' matches across folders, by server arrival time.

    Per folder: EXAMINE, one UID SEARCH, one FETCH of the arrival times of
    its newest matches. Then one FETCH per folder for the messages that
    made the cut.
    """
    folders = pick_folders(list_folders(mailbox), folder_names, SEARCH_SKIPPED_ROLES)
    criteria = imap_criteria(query)
    total, hits = 0, []
    for folder in folders:
        uidvalidity = mailbox.examine(folder.name)
        uids = mailbox.search(criteria)
        total += len(uids)
        for uid, item in mailbox.fetch(
            uids[-limit:], ["INTERNALDATE", "RFC822.SIZE"]
        ).items():
            hits.append(
                (
                    _aware(item["INTERNALDATE"]),
                    folder.name,
                    uidvalidity,
                    uid,
                    item["RFC822.SIZE"],
                )
            )
    hits.sort(key=lambda hit: (hit[0] or _OLDEST, hit[1], hit[3]), reverse=True)
    hits = hits[:limit]

    summaries = {}
    for folder_name in dict.fromkeys(hit[1] for hit in hits):
        mailbox.examine(folder_name)
        mine = [hit for hit in hits if hit[1] == folder_name]
        small = [hit[3] for hit in mine if (hit[4] or 0) <= SEARCH_BODY_CAP]
        large = [hit[3] for hit in mine if (hit[4] or 0) > SEARCH_BODY_CAP]
        fetched = mailbox.fetch(small, ["BODY.PEEK[]", "FLAGS"])
        headers_only = mailbox.fetch(large, ["BODY.PEEK[HEADER]", "FLAGS"])
        for internal_date, _, uidvalidity, uid, size in mine:
            if uid in fetched and fetched[uid]["BODY.PEEK[]"] is not None:
                raw, flags, content = (
                    fetched[uid]["BODY.PEEK[]"],
                    fetched[uid]["FLAGS"],
                    "snippet",
                )
            elif (
                uid in headers_only
                and headers_only[uid]["BODY.PEEK[HEADER]"] is not None
            ):
                item = headers_only[uid]
                raw, flags, content = (
                    item["BODY.PEEK[HEADER]"],
                    item["FLAGS"],
                    "headers",
                )
            else:
                continue  # expunged meanwhile
            summaries[(folder_name, uid)] = message_dict(
                raw,
                folder=folder_name,
                uid=uid,
                uidvalidity=uidvalidity,
                internal_date=internal_date,
                seen=b"\\Seen" in (flags or ()),
                size=size,
                content=content,
            )
    messages = [summaries[(h[1], h[3])] for h in hits if (h[1], h[3]) in summaries]
    return {"total": total, "messages": messages}


def _matches(needle: str | None, *haystacks: str | None) -> bool:
    if not needle:
        return True
    needle = needle.casefold()
    return any(needle in (h or "").casefold() for h in haystacks)


def _person(person: dict | None) -> str:
    if not person:
        return ""
    return f"{person['name'] or ''} <{person['address'] or ''}>"


def search_offline(query: Query, folder_names: list[str] | None, limit: int) -> dict:
    """Newest 'limit' matches in the archive, by the messages' Date header.

    Matching mirrors IMAP SEARCH: case-insensitive substrings of the
    decoded headers and text. A message archived in several UIDVALIDITY
    generations of a folder counts once, from the newest generation.
    """
    if query.unseen:
        raise PermanentError(
            "--unseen needs the server: the archive does not keep read/unread flags."
        )
    state = archive.require_state()
    matches = {}
    for item in archive.archived_messages(state, folder_names, SEARCH_SKIPPED_ROLES):
        raw = item.read()
        msg = parse(raw if query.text else header_block(raw))
        try:
            arrival = datetime.fromtimestamp(item.path.stat().st_mtime, UTC)
        except OSError:
            arrival = None
        headers = read_headers(msg, arrival)
        people = [headers.sender] + headers.to + headers.cc
        day = headers.date.date() if headers.date else None
        if not (
            _matches(query.sender, _person(headers.sender))
            and _matches(query.to, *map(_person, headers.to))
            and _matches(query.cc, *map(_person, headers.cc))
            and _matches(query.address, *map(_person, people))
            and _matches(query.subject, headers.subject)
            and (query.since is None or (day is not None and day >= query.since))
            and (query.until is None or (day is not None and day <= query.until))
        ):
            continue
        if query.text and not _matches(
            query.text, headers.subject, body_text(msg), *map(_person, people)
        ):
            continue
        dedupe = (
            item.folder,
            headers.message_id or ("uid", item.uidvalidity, item.uid),
        )
        known = matches.get(dedupe)
        if known is None or (item.current, item.uidvalidity) > (
            known[1].current,
            known[1].uidvalidity,
        ):
            matches[dedupe] = (headers.date or _OLDEST, item, arrival)
    ranked = sorted(
        matches.values(),
        key=lambda match: (match[0], match[1].folder, match[1].uid),
        reverse=True,
    )
    messages = [
        message_dict(
            item.read(),
            folder=item.folder,
            uid=item.uid,
            uidvalidity=item.uidvalidity,
            file=str(item.path),
            internal_date=arrival,
            content="snippet",
        )
        for _, item, arrival in ranked[:limit]
    ]
    return {"total": len(ranked), "messages": messages}
