"""Raw RFC 5322 messages to the tool's JSON message schema.

Decoding is defensive, because Russian mail arrives in every shape:
RFC 2047 encoded words (sometimes split in the middle of a character),
raw 8-bit headers in an undeclared charset, wrong charset labels, bodies
in koi8-r, windows-1251 or utf-8. Nothing here raises on malformed
input; text that cannot be decoded cleanly gets replacement characters.
"""

import binascii
import codecs
import email
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

from .htmltext import INVISIBLE_FILLERS, html_to_text

SNIPPET_CHARS = 200

_CHARSET_ALIASES = {
    "win-1251": "cp1251",
    "win1251": "cp1251",
    "cp-1251": "cp1251",
    "x-cp1251": "cp1251",
    "koi8": "koi8-r",
    "koi8r": "koi8-r",
    "utf8": "utf-8",
}
_UNKNOWN_CHARSETS = frozenset(
    {"unknown-8bit", "x-unknown", "unknown", "default", "x-user-defined"}
)

_ENCODED_WORD = re.compile(r"=\?([^?\s]+)\?([bBqQ])\?(.*?)\?=")
_MESSAGE_ID = re.compile(r"<[^<>\s]+>")
_HEADER_END = re.compile(rb"\r?\n\r?\n")
# Some senders (Mosenergosbyt, MosOblEIRC) put the HTML source into the
# text/plain part as well.
_HTML_DOCUMENT = re.compile(r"\A[\s﻿]*<(?:!doctype\s+html|html)[\s>]", re.IGNORECASE)


def parse(raw: bytes) -> Message:
    # compat32 keeps raw header values (8-bit bytes as surrogate escapes),
    # which the decoding below handles itself; the modern policy would
    # replace undeclared 8-bit text with U+FFFD before we see it.
    return email.message_from_bytes(raw, policy=policy.compat32)


def header_block(raw: bytes) -> bytes:
    """The header part of a raw message: enough for read_headers()."""
    end = _HEADER_END.search(raw)
    return raw[: end.end()] if end else raw


# --- charsets ---


def _codec(charset: str | None) -> str | None:
    if not charset:
        return None
    name = charset.strip().strip("\"'").lower()
    if name in _UNKNOWN_CHARSETS:
        return None
    try:
        return codecs.lookup(_CHARSET_ALIASES.get(name, name)).name
    except LookupError:
        return None


def _cyrillic_score(text: str) -> int:
    # koi8-r and windows-1251 put the lower- and uppercase Cyrillic letters
    # at swapped positions, so decoding with the wrong one turns ordinary
    # (mostly lowercase) Russian text into mostly uppercase.
    lower = sum(1 for ch in text if "а" <= ch <= "я" or ch == "ё")
    upper = sum(1 for ch in text if "А" <= ch <= "Я" or ch == "Ё")
    return lower - upper


def decode_bytes(data: bytes, charset: str | None = None) -> str:
    """Text from bytes: the declared charset if it fits, else a best guess."""
    codec = _codec(charset)
    if codec:
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            pass  # a wrong label: fall through to guessing
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    candidates = [data.decode(name, errors="replace") for name in ("cp1251", "koi8-r")]
    return max(candidates, key=_cyrillic_score)


def _unescape(value: str) -> str:
    """Decode raw 8-bit header bytes the parser kept as surrogate escapes."""
    if not any("\udc80" <= ch <= "\udcff" for ch in value):
        return value
    return decode_bytes(value.encode("utf-8", "surrogateescape"))


# --- headers ---


def _decode_word(encoding: str, text: str) -> bytes | None:
    try:
        if encoding in "bB":
            text = text.strip()
            return binascii.a2b_base64(text + "=" * (-len(text) % 4))
        return binascii.a2b_qp(text.encode("ascii"), header=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return None


def decode_words(text: str) -> str:
    """Decode RFC 2047 encoded words in a header value.

    Adjacent encoded words in the same charset are joined as bytes before
    decoding, because some mailers split a multi-byte character between
    them. Whitespace between two encoded words is dropped, as RFC 2047
    says; an undecodable word is kept as written.
    """
    pieces: list[tuple[str | bytes, str | None]] = []
    position = 0
    for match in _ENCODED_WORD.finditer(text):
        between = text[position : match.start()]
        follows_word = bool(pieces) and pieces[-1][1] is not None
        if between and not (between.isspace() and follows_word):
            pieces.append((between, None))
        charset = match.group(1).split("*", 1)[0]  # drop an RFC 2231 language
        data = _decode_word(match.group(2), match.group(3))
        if data is None:
            pieces.append((match.group(0), None))
        elif (
            pieces
            and pieces[-1][1] is not None
            and pieces[-1][1].lower() == charset.lower()
        ):
            pieces[-1] = (pieces[-1][0] + data, pieces[-1][1])
        else:
            pieces.append((data, charset))
        position = match.end()
    pieces.append((text[position:], None))
    return "".join(
        piece if charset is None else decode_bytes(piece, charset)
        for piece, charset in pieces
    )


def raw_header(msg: Message, name: str) -> str | None:
    """First header of that name: unfolded, 8-bit text decoded, words not."""
    name = name.lower()
    for key, value in msg.raw_items():
        if key.lower() == name:
            return re.sub(r"\r?\n(?=[ \t])", "", _unescape(value)).strip()
    return None


def header_text(msg: Message, name: str) -> str | None:
    value = raw_header(msg, name)
    if value is None:
        return None
    return re.sub(r"\s+", " ", decode_words(value)).strip() or None


def addresses(value: str | None) -> list[dict]:
    """[{name, address}] from an address header (raw, words undecoded).

    Addresses are split before decoding, so a decoded display name with a
    comma in it cannot split one address into two.
    """
    if not value:
        return []
    result = []
    for name, address in getaddresses([value]):
        name = re.sub(r"\s+", " ", decode_words(name)).strip() or None
        address = address.strip() or None
        if name or address:
            result.append({"name": name, "address": address})
    if not result:
        # Unparsable (e.g. an unbalanced quote): keep the text, not nothing.
        result.append({"name": decode_words(value).strip(), "address": None})
    return result


def message_ids(value: str | None) -> list[str]:
    return _MESSAGE_ID.findall(value or "")


def message_date(msg: Message, fallback: datetime | None = None) -> datetime | None:
    """The Date header with its own time zone; 'fallback' if it is unusable."""
    value = raw_header(msg, "date")
    parsed = None
    if value:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            parsed = None
    if parsed is None:
        parsed = fallback
    if parsed is not None and parsed.tzinfo is None:
        # "-0000" means "zone unknown"; UTC is the honest reading.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class Headers:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    date: datetime | None
    sender: dict | None
    to: list[dict]
    cc: list[dict]
    subject: str | None

    @property
    def participants(self) -> set[str]:
        """Lowercased addresses of the sender and all recipients."""
        people = [self.sender] if self.sender else []
        return {
            person["address"].lower()
            for person in people + self.to + self.cc
            if person["address"]
        }


def read_headers(msg: Message, internal_date: datetime | None = None) -> Headers:
    message_id_value = raw_header(msg, "message-id")
    ids = message_ids(message_id_value)
    senders = addresses(raw_header(msg, "from"))
    in_reply_to = message_ids(raw_header(msg, "in-reply-to"))
    return Headers(
        message_id=ids[0] if ids else (message_id_value or None),
        in_reply_to=in_reply_to[0] if in_reply_to else None,
        references=message_ids(raw_header(msg, "references")),
        date=message_date(msg, internal_date),
        sender=senders[0] if senders else None,
        to=addresses(raw_header(msg, "to")),
        cc=addresses(raw_header(msg, "cc")),
        subject=header_text(msg, "subject"),
    )


# --- body and attachments ---


def _leaves(part: Message):
    """Leaf parts in document order; an attached message is one leaf."""
    payload = part.get_payload()
    if part.get_content_maintype() == "multipart" and isinstance(payload, list):
        for child in payload:
            yield from _leaves(child)
    else:
        yield part


def _filename(part: Message) -> str | None:
    name = part.get_filename()
    if name and "\ufffd" in name:
        # Raw 8-bit parameter: the parser has replaced its bytes. Redo the
        # parameter parsing on the header text decoded by _unescape.
        probe = Message()
        for key, value in part.raw_items():
            if key.lower() in ("content-type", "content-disposition"):
                probe[key] = _unescape(value)
        name = probe.get_filename() or name
    if not name:
        return None
    # Many mailers put RFC 2047 words into filenames, against the RFCs.
    return re.sub(r"\s+", " ", decode_words(_unescape(name))).strip() or None


def _is_body(part: Message) -> bool:
    return (
        part.get_content_type() in ("text/plain", "text/html")
        and part.get_content_disposition() != "attachment"
        and _filename(part) is None
    )


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    text = decode_bytes(payload, part.get_content_charset())
    return text.replace("\r\n", "\n").replace("\r", "\n")


def body_text(msg: Message) -> str | None:
    """text/plain body parts; if they are missing or blank, the HTML ones."""
    bodies = [part for part in _leaves(msg) if _is_body(part)]
    plain = [_part_text(p) for p in bodies if p.get_content_type() == "text/plain"]
    plain = [
        html_to_text(t) if _HTML_DOCUMENT.match(t) else t.translate(INVISIBLE_FILLERS)
        for t in plain
    ]
    text = "\n\n".join(t.strip() for t in plain if t.strip())
    if not text:
        html = [_part_text(p) for p in bodies if p.get_content_type() == "text/html"]
        text = "\n\n".join(t for t in (html_to_text(h) for h in html) if t)
    return text or None


@dataclass
class Attachment:
    index: int
    filename: str | None
    content_type: str
    inline: bool
    part: Message = field(repr=False)

    def payload(self) -> bytes:
        if self.content_type == "message/rfc822":
            inner = self.part.get_payload()
            if isinstance(inner, list) and inner:
                return inner[0].as_bytes(
                    policy=policy.compat32.clone(mangle_from_=False)
                )
        data = self.part.get_payload(decode=True)
        return data if isinstance(data, bytes) else b""

    def info(self) -> dict:
        return {
            "index": self.index,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": len(self.payload()),
            "inline": self.inline,
        }


def attachments(msg: Message) -> list[Attachment]:
    """Every leaf part that is not body text, numbered from 1."""
    result = []
    for part in _leaves(msg):
        if _is_body(part):
            continue
        disposition = part.get_content_disposition()
        result.append(
            Attachment(
                index=len(result) + 1,
                filename=_filename(part),
                content_type=part.get_content_type(),
                inline=disposition == "inline"
                or (disposition is None and part.get("content-id") is not None),
                part=part,
            )
        )
    return result


def snippet(text: str | None) -> str | None:
    if not text:
        return None
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1] + "…"


def message_dict(
    raw: bytes,
    *,
    folder: str,
    uid: int,
    uidvalidity: int,
    file: str | None = None,
    internal_date: datetime | None = None,
    seen: bool | None = None,
    size: int | None = None,
    content: str = "text",
) -> dict:
    """One message in the output schema.

    content: 'text' for the full body text, 'snippet' for its beginning,
    'headers' when 'raw' holds the header block only (no body fields).
    """
    msg = parse(raw)
    headers = read_headers(msg, internal_date)
    result = {
        "folder": folder,
        "uid": uid,
        "uidvalidity": uidvalidity,
        "file": file,
        "message_id": headers.message_id,
        "in_reply_to": headers.in_reply_to,
        "references": headers.references,
        "date": headers.date.isoformat() if headers.date else None,
        "from": headers.sender,
        "to": headers.to,
        "cc": headers.cc,
        "subject": headers.subject,
        "seen": seen,
        "size_bytes": size if size is not None else len(raw),
    }
    if content == "text":
        result["text"] = body_text(msg)
    else:
        result["snippet"] = None if content == "headers" else snippet(body_text(msg))
    result["attachments"] = (
        None if content == "headers" else [a.info() for a in attachments(msg)]
    )
    return result
