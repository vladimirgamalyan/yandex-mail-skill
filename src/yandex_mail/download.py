"""'download': save the attachments of one message into a directory."""

import hashlib
import mimetypes
from pathlib import Path

from . import archive
from .errors import PermanentError
from .files import sanitize_name, write_atomically
from .folders import find_folder, list_folders
from .mailbox import ReadOnlyMailbox
from .message import attachments, parse, read_headers
from .read import MessageNotFoundError, check_uidvalidity

DEFAULT_MAX_SIZE_MB = 50

# The stdlib's builtin MIME table only: the module-level mimetypes
# functions also read platform sources (the Windows registry), which
# would make generated names differ between machines.
_MIMETYPES = mimetypes.MimeTypes()


class DownloadError(PermanentError):
    """The attachments cannot be saved (permanent)."""


def _extension(content_type: str) -> str:
    if content_type == "message/rfc822":
        return ".eml"
    return _MIMETYPES.guess_extension(content_type, strict=False) or ".bin"


def _too_large(size: int, max_size_mb: int) -> DownloadError:
    return DownloadError(
        f"The message is {size / 1048576:.1f} MB, over the --max-size limit of "
        f"{max_size_mb} MB (the limit counts the whole message as stored on "
        "the server; attachments are base64-encoded there, about 1.37x their "
        "size). Retry with a higher --max-size if it is needed."
    )


def save_attachments(
    raw: bytes, *, folder: str, uid: int, uidvalidity: int, output: Path
) -> dict:
    """Write every attachment under a stable, collision-free name.

    Name: '<date>_<key>_<index>_<sanitized original name>'. The key is a
    hash of the Message-ID (or of folder, UIDVALIDITY and UID when there
    is none), so the same message gets the same names online, offline and
    from any folder; the attachment index tells same-named attachments of
    one message apart. A file that already exists with the same size is
    skipped.
    """
    msg = parse(raw)
    headers = read_headers(msg)
    identity = headers.message_id or f"{folder}\0{uidvalidity}\0{uid}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    day = headers.date.date().isoformat() if headers.date else "nodate"
    files = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for attachment in attachments(msg):
            data = attachment.payload()
            name = sanitize_name(attachment.filename or "") or (
                f"part{attachment.index}{_extension(attachment.content_type)}"
            )
            path = (output / f"{day}_{key}_{attachment.index}_{name}").resolve()
            exists = path.is_file() and path.stat().st_size == len(data)
            if not exists:
                write_atomically(path, data)
            files.append(
                {
                    "index": attachment.index,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size_bytes": len(data),
                    "file": str(path),
                    "status": "exists" if exists else "saved",
                }
            )
    except OSError as error:
        raise DownloadError(f"Cannot save into {output} ({error}).") from error
    return {
        "folder": folder,
        "uid": uid,
        "uidvalidity": uidvalidity,
        "message_id": headers.message_id,
        "files": files,
    }


def download_online(
    mailbox: ReadOnlyMailbox,
    folder_name: str,
    uid: int,
    uidvalidity: int | None,
    output: Path,
    max_size_mb: int,
) -> dict:
    folder = find_folder(list_folders(mailbox), folder_name)
    current = mailbox.examine(folder.name)
    check_uidvalidity(uidvalidity, current, folder.name)
    size = mailbox.fetch([uid], ["RFC822.SIZE"]).get(uid, {}).get("RFC822.SIZE")
    if size is None:
        raise MessageNotFoundError(
            f"No message with UID {uid} in folder {folder.name!r}."
        )
    if size > max_size_mb * 1048576:
        raise _too_large(size, max_size_mb)
    raw = mailbox.fetch([uid], ["BODY.PEEK[]"]).get(uid, {}).get("BODY.PEEK[]")
    if raw is None:
        raise MessageNotFoundError(
            f"No message with UID {uid} in folder {folder.name!r}."
        )
    return save_attachments(
        raw, folder=folder.name, uid=uid, uidvalidity=current, output=output
    )


def download_offline(
    folder_name: str, uid: int, uidvalidity: int | None, output: Path, max_size_mb: int
) -> dict:
    item = archive.find_message(archive.require_state(), folder_name, uid, uidvalidity)
    raw = item.read()
    if len(raw) > max_size_mb * 1048576:
        raise _too_large(len(raw), max_size_mb)
    return save_attachments(
        raw,
        folder=item.folder,
        uid=item.uid,
        uidvalidity=item.uidvalidity,
        output=output,
    )
