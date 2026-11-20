"""'sync': bring the append-only local archive up to date."""

import time
from datetime import date

from . import archive
from .errors import PermanentError
from .files import write_atomically
from .folders import SYNC_SKIPPED_ROLES, Folder, list_folders, pick_folders
from .mailbox import ReadOnlyMailbox

# A FETCH batch holds up to this many messages and, beyond its first
# message, up to this many bytes; batches are this many seconds apart.
BATCH_MESSAGES = 50
BATCH_BYTES = 20 * 1024 * 1024
BATCH_PAUSE = 1.0


class SyncError(PermanentError):
    """The archive cannot be synced as requested (permanent)."""


def _batches(uids: list[int], sizes: dict[int, int]) -> list[list[int]]:
    batches, current, current_bytes = [], [], 0
    for uid in uids:
        size = sizes.get(uid) or 0
        if current and (
            len(current) >= BATCH_MESSAGES or current_bytes + size > BATCH_BYTES
        ):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(uid)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def _download(
    mailbox: ReadOnlyMailbox,
    state: dict,
    entry: dict,
    uidvalidity: int,
    uids: list[int],
) -> int:
    """Fetch messages in batches of consecutive UIDs, oldest first.

    The state is saved after every batch, so an interrupted sync resumes
    after the last saved message instead of fetching it again.
    """
    if not uids:
        return 0
    sizes = {
        uid: item["RFC822.SIZE"]
        for uid, item in mailbox.fetch(uids, ["RFC822.SIZE"]).items()
    }
    directory = archive.generation_dir(entry, uidvalidity)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SyncError(f"Cannot create {directory} ({error}).") from error
    added = 0
    for number, batch in enumerate(_batches([u for u in uids if u in sizes], sizes)):
        if number:
            time.sleep(BATCH_PAUSE)
        fetched = mailbox.fetch(batch, ["BODY.PEEK[]", "INTERNALDATE"])
        for uid in batch:
            item = fetched.get(uid)
            if item is None or item["BODY.PEEK[]"] is None:
                continue  # expunged since the search
            internal_date = item["INTERNALDATE"]
            path = archive.message_path(entry, uidvalidity, uid)
            try:
                # The file time records the server's arrival time
                # (INTERNALDATE), which the .eml itself does not carry.
                write_atomically(
                    path,
                    item["BODY.PEEK[]"],
                    mtime=internal_date.timestamp() if internal_date else None,
                )
            except OSError as error:
                raise SyncError(f"Cannot write {path} ({error}).") from error
            added += 1
        entry["last_uid"] = max(entry["last_uid"], batch[-1])
        archive.save_state(state)
    return added


def _sync_folder(
    mailbox: ReadOnlyMailbox, state: dict, folder: Folder, since: date | None
) -> dict:
    entry = state["folders"].get(folder.name)
    if entry is None:
        entry = {
            "dir": archive.allocate_dir(state, folder.name),
            "role": folder.role,
            "since": (since or date.fromisoformat(state["since"])).isoformat(),
            "uidvalidity": None,
            "last_uid": 0,
        }
        state["folders"][folder.name] = entry
    entry["role"] = folder.role
    entry_since = date.fromisoformat(entry["since"])
    # An earlier --since extends the folder back in time: fetch the
    # messages between the new and the old start date as well.
    extend_before = entry_since if since is not None and since < entry_since else None
    start = since if extend_before else entry_since

    status = mailbox.status(folder.name, ["UIDVALIDITY", "UIDNEXT"])
    # UIDNEXT - 1 bounds the UIDs in the folder; a server that omits
    # UIDNEXT gets a search every time.
    uidnext = status.get("UIDNEXT")
    has_new = uidnext is None or uidnext - 1 > entry["last_uid"]
    changed = False
    added = 0
    if status.get("UIDVALIDITY") != entry["uidvalidity"] or has_new or extend_before:
        uidvalidity = mailbox.examine(folder.name)
        if uidvalidity != entry["uidvalidity"]:
            # UIDs of the old generation mean nothing now. Its files stay
            # where they are; the folder is archived again from the start
            # date into a new generation directory.
            changed = entry["uidvalidity"] is not None
            entry["uidvalidity"], entry["last_uid"] = uidvalidity, 0
            wanted = mailbox.search(["SINCE", start])
        else:
            wanted = []
            if has_new:
                # "n:*" always matches the highest UID, even below n.
                wanted = [
                    uid
                    for uid in mailbox.search(
                        ["UID", f"{entry['last_uid'] + 1}:*", "SINCE", entry_since]
                    )
                    if uid > entry["last_uid"]
                ]
            if extend_before:
                wanted += mailbox.search(["SINCE", since, "BEFORE", extend_before])
        wanted = sorted(
            uid
            for uid in set(wanted)
            if not archive.message_path(entry, uidvalidity, uid).exists()
        )
        added = _download(mailbox, state, entry, uidvalidity, wanted)
        entry["since"] = start.isoformat()
    if uidnext is not None:
        entry["last_uid"] = max(entry["last_uid"], uidnext - 1)
    archive.save_state(state)
    directory = archive.generation_dir(entry, entry["uidvalidity"])
    return {
        "name": folder.name,
        "role": folder.role,
        "dir": str(directory),
        "uidvalidity": entry["uidvalidity"],
        "uidvalidity_changed": changed,
        "added": added,
        "total": archive.count_messages(directory, "*.eml"),
    }


def run_sync(
    mailbox: ReadOnlyMailbox, since: date | None, folder_names: list[str] | None
) -> dict:
    """The 'sync' command."""
    state = archive.load_state()
    if state is None:
        if since is None:
            raise SyncError(
                "No archive yet; pass --since YYYY-MM-DD to start one from that date."
            )
        state = {
            "version": archive.STATE_VERSION,
            "account": mailbox.login,
            "since": since.isoformat(),
            "folders": {},
        }
    elif state["account"].casefold() != mailbox.login.casefold():
        raise SyncError(
            f"The archive at {archive.archive_dir()} belongs to {state['account']}, "
            f"not to {mailbox.login}. Move that directory away to archive another "
            "mailbox."
        )
    elif since is not None and since < date.fromisoformat(state["since"]):
        state["since"] = since.isoformat()
    folders = pick_folders(list_folders(mailbox), folder_names, SYNC_SKIPPED_ROLES)
    report = [_sync_folder(mailbox, state, folder, since) for folder in folders]
    return {
        "archive": str(archive.archive_dir()),
        "account": state["account"],
        "since": state["since"],
        "added": sum(folder["added"] for folder in report),
        "total": archive.count_messages(archive.archive_dir(), "*/*/*.eml"),
        "folders": report,
    }
