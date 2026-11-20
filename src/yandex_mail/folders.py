"""Folder listing, roles from SPECIAL-USE attributes, default folder sets."""

from dataclasses import dataclass

from . import config
from .errors import PermanentError
from .mailbox import ReadOnlyMailbox

# RFC 6154 SPECIAL-USE attributes, lowercased, to the tool's role names.
SPECIAL_USE_ROLES = {
    "\\sent": "sent",
    "\\junk": "junk",
    "\\trash": "trash",
    "\\drafts": "drafts",
    "\\archive": "archive",
    "\\all": "all",
    "\\flagged": "flagged",
}
# The older XLIST extension names some of them differently.
XLIST_ROLES = {
    **SPECIAL_USE_ROLES,
    "\\spam": "junk",
    "\\inbox": "inbox",
    "\\allmail": "all",
    "\\starred": "flagged",
}

# Searched by default: every folder except spam, trash and the virtual
# folders that only repeat messages stored elsewhere.
SEARCH_SKIPPED_ROLES = frozenset({"junk", "trash", "all", "flagged"})
# Archived by default: the same, minus drafts too (every autosave of a
# draft is a new message, which the append-only archive would keep).
SYNC_SKIPPED_ROLES = SEARCH_SKIPPED_ROLES | {"drafts"}


class FolderNotFoundError(PermanentError):
    """A folder named on the command line does not exist."""


@dataclass(frozen=True)
class Folder:
    name: str
    delimiter: str | None
    flags: tuple[str, ...]
    role: str | None

    @property
    def selectable(self) -> bool:
        lowered = {flag.lower() for flag in self.flags}
        return not lowered & {"\\noselect", "\\nonexistent"}


def _decode(value) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _folders(entries: list[tuple], roles: dict[str, str]) -> list[Folder]:
    folders = []
    for flags, delimiter, name in entries:
        flags = tuple(_decode(flag) for flag in flags)
        role = next(
            (roles[flag.lower()] for flag in flags if flag.lower() in roles), None
        )
        if name.upper() == "INBOX":
            # INBOX is the one name IMAP defines (case-insensitively).
            role = "inbox"
        folders.append(Folder(name, _decode(delimiter), flags, role))
    return folders


def list_folders(mailbox: ReadOnlyMailbox) -> list[Folder]:
    """All folders of the mailbox, with roles from their attributes.

    Roles come from SPECIAL-USE attributes in the LIST answer, never from
    folder names (Yandex names them by the account language). A server that
    reports no such attribute in LIST but supports XLIST is asked again with
    XLIST.
    """
    folders = _folders(mailbox.list_folders(), SPECIAL_USE_ROLES)
    if all(folder.role in (None, "inbox") for folder in folders) and (
        mailbox.has_capability("XLIST")
    ):
        folders = _folders(mailbox.xlist_folders(), XLIST_ROLES)
    return folders


def find_folder(folders: list[Folder], name: str) -> Folder:
    for folder in folders:
        if folder.name == name or (
            folder.name.upper() == "INBOX" and name.upper() == "INBOX"
        ):
            return folder
    raise FolderNotFoundError(
        f"No folder named {name!r}. Folder names are case-sensitive; "
        "'yandex-mail folders' lists them."
    )


def pick_folders(
    folders: list[Folder], names: list[str] | None, skipped_roles: frozenset
) -> list[Folder]:
    """The folders named on the command line, or the default set."""
    if names:
        picked = []
        for name in names:
            folder = find_folder(folders, name)
            if not folder.selectable:
                raise FolderNotFoundError(
                    f"Folder {name!r} holds no messages (\\Noselect)."
                )
            if folder not in picked:
                picked.append(folder)
        return picked
    return [f for f in folders if f.selectable and f.role not in skipped_roles]


def run_folders(mailbox: ReadOnlyMailbox) -> list[dict]:
    """The 'folders' command: every folder with its role and counts."""
    result = []
    for folder in list_folders(mailbox):
        counts = {"MESSAGES": None, "UNSEEN": None}
        if folder.selectable:
            counts = mailbox.status(folder.name, ["MESSAGES", "UNSEEN"])
        result.append(
            {
                "name": folder.name,
                "role": folder.role,
                "searched_by_default": folder.selectable
                and folder.role not in SEARCH_SKIPPED_ROLES,
                "archived_by_default": folder.selectable
                and folder.role not in SYNC_SKIPPED_ROLES,
                "messages": counts.get("MESSAGES"),
                "unseen": counts.get("UNSEEN"),
                "delimiter": folder.delimiter,
                "flags": list(folder.flags),
            }
        )
    return result


def run_check(mailbox: ReadOnlyMailbox) -> dict:
    """The 'check' command: who is logged in, and the inbox counts."""
    inbox = mailbox.status("INBOX", ["MESSAGES", "UNSEEN"])
    return {
        "account": mailbox.login,
        "server": f"{config.IMAP_HOST}:{config.IMAP_PORT}",
        "inbox": {"messages": inbox.get("MESSAGES"), "unseen": inbox.get("UNSEEN")},
        "folders": len(list_folders(mailbox)),
    }
