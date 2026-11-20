"""The local archive kept by 'sync': raw .eml files plus a state file.

Layout under <config dir>/archive/:

    state.json                          account, start dates, per-folder progress
    <folder dir>/<UIDVALIDITY>/<UID>.eml   one raw message per file

A file is written once and never changed or deleted: a message deleted
on the server stays in the archive, and a folder whose UIDVALIDITY
changes starts a new generation directory next to the old one.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config
from .errors import PermanentError
from .files import sanitize_name, write_atomically
from .folders import FolderNotFoundError

ARCHIVE_DIRNAME = "archive"
STATE_FILENAME = "state.json"
STATE_VERSION = 1

_NUMBER = re.compile(r"[0-9]+")


class ArchiveError(PermanentError):
    """The archive is missing, unreadable, unwritable or damaged."""


def archive_dir() -> Path:
    return config.config_dir() / ARCHIVE_DIRNAME


def _valid_entry(entry) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("dir"), str)
        and isinstance(entry.get("since"), str)
        and isinstance(entry.get("last_uid"), int)
        and (entry.get("uidvalidity") is None or isinstance(entry["uidvalidity"], int))
        and (entry.get("role") is None or isinstance(entry["role"], str))
    )


def load_state() -> dict | None:
    """The archive state, or None when no archive was started yet."""
    path = archive_dir() / STATE_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ArchiveError(f"Cannot read {path} ({error}).") from error
    try:
        state = json.loads(text)
    except ValueError:
        state = None
    if not (
        isinstance(state, dict)
        and state.get("version") == STATE_VERSION
        and isinstance(state.get("account"), str)
        and isinstance(state.get("since"), str)
        and isinstance(state.get("folders"), dict)
        and all(_valid_entry(entry) for entry in state["folders"].values())
    ):
        raise ArchiveError(
            f"The archive state file {path} is damaged. The .eml files next to "
            "it are intact; restore the file from a backup, or move the archive "
            "directory away and sync again."
        )
    return state


def require_state() -> dict:
    state = load_state()
    if state is None:
        raise ArchiveError(
            f"No archive at {archive_dir()} yet. Start one with "
            "'yandex-mail sync --since YYYY-MM-DD' (needs the network)."
        )
    return state


def save_state(state: dict) -> None:
    path = archive_dir() / STATE_FILENAME
    data = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    try:
        archive_dir().mkdir(parents=True, exist_ok=True)
        write_atomically(path, data.encode("utf-8"))
    except OSError as error:
        raise ArchiveError(f"Cannot write {path} ({error}).") from error


def allocate_dir(state: dict, folder_name: str) -> str:
    """A directory name for a new folder: sanitized and not taken yet.

    Compared case-insensitively, as Windows does: 'Bills' and 'bills' are
    two IMAP folders but would be one Windows directory.
    """
    taken = {entry["dir"].casefold() for entry in state["folders"].values()}
    taken.add(STATE_FILENAME.casefold())
    base = sanitize_name(folder_name) or "folder"
    candidate, number = base, 2
    while candidate.casefold() in taken:
        candidate, number = f"{base} ({number})", number + 1
    return candidate


def generation_dir(entry: dict, uidvalidity: int) -> Path:
    return archive_dir() / entry["dir"] / str(uidvalidity)


def message_path(entry: dict, uidvalidity: int, uid: int) -> Path:
    return generation_dir(entry, uidvalidity) / f"{uid}.eml"


@dataclass(frozen=True)
class ArchivedMessage:
    folder: str
    role: str | None
    uidvalidity: int
    uid: int
    path: Path
    # In the folder's current UIDVALIDITY generation (not an older one).
    current: bool

    def read(self) -> bytes:
        try:
            return self.path.read_bytes()
        except OSError as error:
            raise ArchiveError(f"Cannot read {self.path} ({error}).") from error


def _folder_names(state: dict, names: list[str] | None, skipped_roles) -> list[str]:
    folders = state["folders"]
    if not names:
        return [
            name
            for name, entry in folders.items()
            if entry.get("role") not in skipped_roles
        ]
    picked = []
    for name in names:
        match = next(
            (
                known
                for known in folders
                if known == name
                or (known.upper() == "INBOX" and name.upper() == "INBOX")
            ),
            None,
        )
        if match is None:
            raise FolderNotFoundError(
                f"Folder {name!r} is not in the archive. Archived folders: "
                f"{', '.join(sorted(folders)) or 'none'}."
            )
        picked.append(match)
    return picked


def archived_messages(
    state: dict, names: list[str] | None, skipped_roles: frozenset
) -> list[ArchivedMessage]:
    """Archived messages of the named folders, or of the default set."""
    result = []
    for name in _folder_names(state, names, skipped_roles):
        entry = state["folders"][name]
        folder_dir = archive_dir() / entry["dir"]
        try:
            generations = sorted(
                (
                    p
                    for p in folder_dir.iterdir()
                    if p.is_dir() and _NUMBER.fullmatch(p.name)
                ),
                key=lambda p: int(p.name),
            )
            for generation in generations:
                uidvalidity = int(generation.name)
                for path in generation.glob("*.eml"):
                    if _NUMBER.fullmatch(path.stem):
                        result.append(
                            ArchivedMessage(
                                folder=name,
                                role=entry.get("role"),
                                uidvalidity=uidvalidity,
                                uid=int(path.stem),
                                path=path,
                                current=uidvalidity == entry.get("uidvalidity"),
                            )
                        )
        except FileNotFoundError:
            continue  # the folder joined the archive but got no message yet
        except OSError as error:
            raise ArchiveError(f"Cannot list {folder_dir} ({error}).") from error
    return result


def find_message(
    state: dict, folder: str, uid: int, uidvalidity: int | None
) -> ArchivedMessage:
    """One archived message; by default from the folder's current generation."""
    name = _folder_names(state, [folder], frozenset())[0]
    entry = state["folders"][name]
    if uidvalidity is None:
        uidvalidity = entry.get("uidvalidity")
    path = message_path(entry, uidvalidity, uid) if uidvalidity is not None else None
    if path is None or not path.is_file():
        raise ArchiveError(
            f"UID {uid} of folder {name!r} (UIDVALIDITY {uidvalidity}) is not in "
            "the archive. Find archived messages with 'yandex-mail search "
            "--offline', or bring the archive up to date with 'yandex-mail sync'."
        )
    return ArchivedMessage(
        folder=name,
        role=entry.get("role"),
        uidvalidity=uidvalidity,
        uid=uid,
        path=path,
        current=uidvalidity == entry.get("uidvalidity"),
    )


def count_messages(directory: Path, pattern: str) -> int:
    try:
        return sum(1 for _ in directory.glob(pattern))
    except OSError as error:
        raise ArchiveError(f"Cannot list {directory} ({error}).") from error
