"""'read': one message in full, or its whole thread, from the server or the archive."""

from datetime import UTC, datetime

from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from . import archive
from .errors import PermanentError
from .folders import SEARCH_SKIPPED_ROLES, find_folder, list_folders, pick_folders
from .mailbox import ReadOnlyMailbox, or_all, text
from .message import Headers, header_block, message_dict, parse, read_headers
from .thread import Node, base_subject, own_addresses, thread_keys

# A thread shows at most this many messages (the newest ones).
MAX_THREAD_MESSAGES = 50
# Per folder, at most this many search candidates are inspected.
MAX_THREAD_CANDIDATES = 300

_OLDEST = datetime.min.replace(tzinfo=UTC)


class MessageNotFoundError(PermanentError):
    """No message with that UID in that folder."""


def check_uidvalidity(expected: int | None, actual: int, folder: str) -> None:
    if expected is not None and expected != actual:
        raise MessageNotFoundError(
            f"Folder {folder!r} now has UIDVALIDITY {actual}, not {expected}: its "
            "UIDs were renumbered on the server. Find the message again with "
            "'yandex-mail search'."
        )


FULL_ITEMS = ["BODY.PEEK[]", "INTERNALDATE", "FLAGS", "RFC822.SIZE"]


def _full_dict(folder: str, uidvalidity: int, uid: int, item: dict) -> dict:
    return message_dict(
        item["BODY.PEEK[]"],
        folder=folder,
        uid=uid,
        uidvalidity=uidvalidity,
        internal_date=item["INTERNALDATE"],
        seen=b"\\Seen" in (item["FLAGS"] or ()),
        size=item["RFC822.SIZE"],
    )


def _thread_criteria(headers: Headers) -> tuple[list, list | None]:
    """SEARCH keys for thread candidates, and a subject-only fallback.

    Candidates: the same base subject, the messages that reference the
    thread root or this message, and the ancestors it references.
    """
    subject = base_subject(headers.subject)
    by_subject = [["SUBJECT", text(subject)]] if subject else []
    root = headers.references[0] if headers.references else headers.message_id
    by_id = []
    for message_id in dict.fromkeys(filter(None, [root, headers.message_id])):
        by_id += [["HEADER", "References", text(message_id)]]
    if headers.message_id:
        by_id.append(["HEADER", "In-Reply-To", text(headers.message_id)])
    for message_id in dict.fromkeys(filter(None, [root, headers.in_reply_to])):
        by_id.append(["HEADER", "Message-ID", text(message_id)])
    keys = by_subject + by_id
    return (or_all(keys) if keys else None), (
        or_all(by_subject) if by_subject else None
    )


def _search_candidates(mailbox: ReadOnlyMailbox, criteria, fallback) -> list[int]:
    try:
        return mailbox.search(criteria)
    except IMAPClientAbortError:
        raise
    except IMAPClientError:
        # A server that does not support HEADER search answers BAD; the
        # subject alone still finds most of a thread.
        return mailbox.search(fallback) if fallback else []


def read_online(
    mailbox: ReadOnlyMailbox,
    folder_name: str,
    uid: int,
    uidvalidity: int | None,
    thread: bool,
):
    folders = list_folders(mailbox)
    folder = find_folder(folders, folder_name)
    current = mailbox.examine(folder.name)
    check_uidvalidity(uidvalidity, current, folder.name)
    item = mailbox.fetch([uid], FULL_ITEMS).get(uid)
    if item is None or item["BODY.PEEK[]"] is None:
        raise MessageNotFoundError(
            f"No message with UID {uid} in folder {folder.name!r}. UIDs come from "
            "'yandex-mail search' output; the message may have been deleted."
        )
    target = _full_dict(folder.name, current, uid, item)
    if not thread:
        return target

    target_headers = read_headers(parse(item["BODY.PEEK[]"]), item["INTERNALDATE"])
    criteria, fallback = _thread_criteria(target_headers)
    nodes = [Node((folder.name, uid), target_headers)]
    validities = {folder.name: current}
    searched = pick_folders(folders, None, SEARCH_SKIPPED_ROLES)
    if folder not in searched:
        searched.insert(0, folder)
    for candidate_folder in searched if criteria else []:
        validities[candidate_folder.name] = mailbox.examine(candidate_folder.name)
        uids = _search_candidates(mailbox, criteria, fallback)[-MAX_THREAD_CANDIDATES:]
        heads = mailbox.fetch(uids, ["BODY.PEEK[HEADER]", "INTERNALDATE"])
        for candidate_uid, item in heads.items():
            if item["BODY.PEEK[HEADER]"] is None:
                continue
            headers = read_headers(
                parse(item["BODY.PEEK[HEADER]"]), item["INTERNALDATE"]
            )
            nodes.append(Node((candidate_folder.name, candidate_uid), headers))
    members = _newest_members(nodes, (folder.name, uid), mailbox.login)

    messages = {(folder.name, uid): target}
    for member_folder in dict.fromkeys(key[0] for key in members):
        mailbox.examine(member_folder)
        wanted = [
            key[1] for key in members if key[0] == member_folder and key not in messages
        ]
        for member_uid, fetched in mailbox.fetch(wanted, FULL_ITEMS).items():
            if fetched["BODY.PEEK[]"] is not None:
                messages[(member_folder, member_uid)] = _full_dict(
                    member_folder, validities[member_folder], member_uid, fetched
                )
    return [messages[key] for key in members if key in messages]


def _newest_members(nodes: list[Node], target, login: str) -> list:
    """Thread members, oldest first, at most the newest MAX_THREAD_MESSAGES."""
    keys = thread_keys(target, nodes, own_addresses(login))
    dates = {node.key: node.headers.date or _OLDEST for node in nodes}
    ordered = sorted(keys, key=lambda key: (dates[key], str(key)))
    return ordered[-MAX_THREAD_MESSAGES:]


def _arrival(item: archive.ArchivedMessage) -> datetime | None:
    try:
        return datetime.fromtimestamp(item.path.stat().st_mtime, UTC)
    except OSError:
        return None


def _archived_dict(item: archive.ArchivedMessage) -> dict:
    return message_dict(
        item.read(),
        folder=item.folder,
        uid=item.uid,
        uidvalidity=item.uidvalidity,
        file=str(item.path),
        internal_date=_arrival(item),
    )


def read_offline(folder_name: str, uid: int, uidvalidity: int | None, thread: bool):
    state = archive.require_state()
    target = archive.find_message(state, folder_name, uid, uidvalidity)
    if not thread:
        return _archived_dict(target)
    key = (target.folder, target.uidvalidity, target.uid)
    nodes = [Node(key, read_headers(parse(target.read()), _arrival(target)))]
    items = {key: target}
    # Newest generations first, so a message archived twice counts once,
    # from its newest copy.
    candidates = sorted(
        archive.archived_messages(state, None, SEARCH_SKIPPED_ROLES),
        key=lambda item: (item.current, item.uidvalidity),
        reverse=True,
    )
    for item in candidates:
        item_key = (item.folder, item.uidvalidity, item.uid)
        if item_key != key:
            items[item_key] = item
            nodes.append(
                Node(
                    item_key,
                    read_headers(parse(header_block(item.read())), _arrival(item)),
                )
            )
    members = _newest_members(nodes, key, state["account"])
    return [_archived_dict(items[member]) for member in members]
