"""Read-only facade over one IMAP connection.

Every mailbox access of the tool goes through ReadOnlyMailbox, and it can
only read: folders are opened with EXAMINE (never SELECT), message data
is fetched with BODY.PEEK (which never sets the \\Seen flag), and the
class has no method that could send, store flags, copy, move, delete or
expunge. Keeping the IMAP client private to this class makes the
read-only guarantee checkable in one place.
"""

from imapclient.imapclient import literal

# The only FETCH data items the tool may request, mapped to the key the
# server answers with: BODY.PEEK[...] comes back as BODY[...].
FETCH_ITEMS = {
    "BODY.PEEK[]": b"BODY[]",
    "BODY.PEEK[HEADER]": b"BODY[HEADER]",
    "RFC822.SIZE": b"RFC822.SIZE",
    "INTERNALDATE": b"INTERNALDATE",
    "FLAGS": b"FLAGS",
}

# Upper bound on UIDs per FETCH command, so a command line stays short.
MAX_UIDS_PER_FETCH = 200


def text(value: str) -> literal:
    """A user-supplied SEARCH string, always sent as an IMAP literal.

    A literal carries any byte (Cyrillic, quotes, parentheses) verbatim,
    where an unquoted atom would break the command.
    """
    return literal(value.encode("utf-8"))


def or_all(criteria: list[list]) -> list:
    """Combine search keys with OR, in prefix form: OR a OR b c."""
    if len(criteria) == 1:
        return criteria[0]
    return ["OR", *criteria[0], *or_all(criteria[1:])]


class ReadOnlyMailbox:
    def __init__(self, client, login: str):
        self._client = client
        # Keep INTERNALDATE time zones instead of converting to naive local time.
        client.normalise_times = False
        self.login = login
        self._selected: str | None = None
        self._uidvalidity: int | None = None

    def has_capability(self, name: str) -> bool:
        return self._client.has_capability(name)

    def list_folders(self) -> list[tuple]:
        """LIST "" "*": (flags, delimiter, name) tuples, names decoded."""
        return self._client.list_folders()

    def xlist_folders(self) -> list[tuple]:
        return self._client.xlist_folders()

    def status(self, folder: str, items: list[str]) -> dict[str, int]:
        """STATUS of a folder without opening it."""
        raw = self._client.folder_status(folder, items)
        return {key.decode("ascii").upper(): value for key, value in raw.items()}

    def examine(self, folder: str) -> int:
        """Open a folder read-only (EXAMINE) and return its UIDVALIDITY."""
        if folder != self._selected:
            info = self._client.select_folder(folder, readonly=True)
            self._selected = folder
            self._uidvalidity = int(info[b"UIDVALIDITY"])
        return self._uidvalidity

    def search(self, criteria: list) -> list[int]:
        """UID SEARCH in the open folder; ascending UIDs.

        User strings must come wrapped by text(); CHARSET UTF-8 is declared
        only when one of them is not ASCII.
        """
        charset = "UTF-8" if _has_8bit(criteria) else None
        return sorted(self._client.search(criteria, charset))

    def fetch(self, uids, items: list[str]) -> dict[int, dict]:
        """UID FETCH of whitelisted items, in batches of consecutive UIDs.

        Returns {uid: {item: value}} for the UIDs that still exist.
        """
        unknown = set(items) - FETCH_ITEMS.keys()
        if unknown:
            raise ValueError(f"FETCH items not allowed: {sorted(unknown)}")
        uids = sorted(uids)
        result = {}
        for start in range(0, len(uids), MAX_UIDS_PER_FETCH):
            chunk = uids[start : start + MAX_UIDS_PER_FETCH]
            for uid, data in self._client.fetch(chunk, list(items)).items():
                result[uid] = {item: data.get(FETCH_ITEMS[item]) for item in items}
        return result


def _has_8bit(criteria) -> bool:
    for item in criteria:
        if isinstance(item, list) and _has_8bit(item):
            return True
        if isinstance(item, (bytes, str)):
            data = item.encode("utf-8") if isinstance(item, str) else item
            if any(byte > 127 for byte in data):
                return True
    return False
