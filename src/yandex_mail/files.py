"""Safe local names and atomic file writes."""

import os
import re
from pathlib import Path

# Path separators, Windows-forbidden punctuation, C0/C1 controls, and the
# invisible zero-width and bidi-control characters a sender can use to
# spoof an extension ('invoice_<U+202E>fdp.exe' renders as 'invoice_exe.pdf').
_FORBIDDEN_CHARS = re.compile(
    r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f'
    r"\u061c\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]"
)
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate to at most max_bytes of UTF-8 without splitting a character."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def sanitize_name(name: str, max_bytes: int = 100) -> str:
    """A file or directory name safe on Windows and POSIX; '' if nothing is left.

    Names come from senders and folder owners, so they are never used
    verbatim. The cap is counted in UTF-8 bytes (filesystem limits are) and
    keeps the extension.
    """
    name = _FORBIDDEN_CHARS.sub("_", name)
    # Windows silently drops trailing dots and spaces; strip them ourselves
    # so the name on disk matches the name we report.
    name = name.strip().rstrip(". ")
    if len(name.encode("utf-8")) > max_bytes:
        stem, extension = os.path.splitext(name)
        extension = _truncate_utf8(extension, 20)
        stem = _truncate_utf8(stem, max_bytes - len(extension.encode("utf-8")))
        name = (stem + extension).rstrip(". ")
    if not name.strip("_"):
        return ""
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = "_" + name
    return name


def write_atomically(path: Path, data: bytes, mtime: float | None = None) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a file."""
    temp_path = path.with_name(path.name + ".part")
    try:
        temp_path.write_bytes(data)
        if mtime is not None:
            os.utime(temp_path, (mtime, mtime))
        temp_path.replace(path)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
