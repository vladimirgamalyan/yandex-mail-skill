"""Shared fixtures: an in-memory IMAP server stand-in and message builders.

No test touches the network: FakeIMAP replaces imapclient.IMAPClient at the
session.connect seam and records every call, so tests can also assert what
was (and was not) sent to the server.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest
from imapclient.exceptions import IMAPClientError, LoginError

from yandex_mail import config, session, sync, throttle
from yandex_mail.message import header_text, parse, raw_header

MSK = timezone(timedelta(hours=3))
ALLOWED_FETCH = {
    "BODY.PEEK[]",
    "BODY.PEEK[HEADER]",
    "RFC822.SIZE",
    "INTERNALDATE",
    "FLAGS",
}


@dataclass
class FakeMessage:
    raw: bytes
    internal_date: datetime
    flags: tuple = ()


@dataclass
class FakeFolder:
    flags: tuple = (b"\\HasNoChildren",)
    uidvalidity: int = 1000
    messages: dict = field(default_factory=dict)
    uidnext: int | None = None

    def next_uid(self) -> int:
        if self.uidnext is not None:
            return self.uidnext
        return max(self.messages, default=0) + 1


def _value(token) -> str:
    return token.decode("utf-8") if isinstance(token, bytes) else str(token)


class FakeIMAP:
    """Just enough of imapclient.IMAPClient for the tool, with a call log."""

    def __init__(self, folders: dict[str, FakeFolder], capabilities=("IMAP4REV1",)):
        self.folders = folders
        self.capabilities = {c.upper() for c in capabilities}
        self.calls: list[tuple] = []
        self.selected = None
        self.normalise_times = True
        self.login_error: str | None = None
        self.reject_header_search = False
        self.xlist = None

    # session
    def login(self, username, password):
        self.calls.append(("login", username))
        if self.login_error:
            raise LoginError(self.login_error)

    def logout(self):
        self.calls.append(("logout",))

    def has_capability(self, name):
        return name.upper() in self.capabilities

    # folders
    def list_folders(self, directory="", pattern="*"):
        self.calls.append(("list",))
        return [(f.flags, b"|", name) for name, f in self.folders.items()]

    def xlist_folders(self, directory="", pattern="*"):
        self.calls.append(("xlist",))
        return self.xlist

    def folder_status(self, folder, what=None):
        self.calls.append(("status", folder, tuple(what)))
        f = self._folder(folder)
        values = {
            "MESSAGES": len(f.messages),
            "UNSEEN": sum(1 for m in f.messages.values() if b"\\Seen" not in m.flags),
            "UIDNEXT": f.next_uid(),
            "UIDVALIDITY": f.uidvalidity,
        }
        return {item.encode(): values[item] for item in what}

    def select_folder(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        f = self._folder(folder)
        self.selected = folder
        return {b"UIDVALIDITY": f.uidvalidity, b"EXISTS": len(f.messages)}

    def _folder(self, name):
        if name not in self.folders:
            raise IMAPClientError(f"select failed: NO [NONEXISTENT] no folder {name}")
        return self.folders[name]

    # messages
    def search(self, criteria="ALL", charset=None):
        self.calls.append(("search", self.selected, list(criteria), charset))
        if self.reject_header_search and "HEADER" in criteria:
            raise IMAPClientError(
                "SEARCH command error: BAD [b'Unsupported search key']"
            )
        folder = self.folders[self.selected]
        return [
            uid
            for uid, message in sorted(folder.messages.items())
            if _matches_all(list(criteria), uid, message)
        ]

    def fetch(self, messages, data, modifiers=None):
        self.calls.append(("fetch", self.selected, list(messages), list(data)))
        assert set(data) <= ALLOWED_FETCH, data
        folder = self.folders[self.selected]
        result = {}
        for uid in messages:
            message = folder.messages.get(uid)
            if message is None:
                continue
            item = {b"SEQ": uid}
            for name in data:
                if name == "BODY.PEEK[]":
                    item[b"BODY[]"] = message.raw
                elif name == "BODY.PEEK[HEADER]":
                    head, _, _ = message.raw.partition(b"\r\n\r\n")
                    item[b"BODY[HEADER]"] = head + b"\r\n\r\n"
                elif name == "RFC822.SIZE":
                    item[b"RFC822.SIZE"] = len(message.raw)
                elif name == "INTERNALDATE":
                    item[b"INTERNALDATE"] = message.internal_date
                elif name == "FLAGS":
                    item[b"FLAGS"] = message.flags
            result[uid] = item
        return result

    def calls_of(self, kind):
        return [call for call in self.calls if call[0] == kind]


def _matches_all(tokens, uid, message) -> bool:
    position = 0
    while position < len(tokens):
        matched, position = _evaluate(tokens, position, uid, message)
        if not matched:
            return False
    return True


def _evaluate(tokens, i, uid, message):
    key = _value(tokens[i]).upper()
    if key == "ALL":
        return True, i + 1
    if key == "OR":
        left, j = _evaluate(tokens, i + 1, uid, message)
        right, k = _evaluate(tokens, j, uid, message)
        return left or right, k
    if key == "UNSEEN":
        return b"\\Seen" not in message.flags, i + 1
    if key in ("SINCE", "BEFORE"):
        day = tokens[i + 1]
        arrival = message.internal_date.date()
        return (arrival >= day if key == "SINCE" else arrival < day), i + 2
    if key == "UID":
        low, _, high = _value(tokens[i + 1]).partition(":")
        high_uid = 10**9 if high in ("*", "") else int(high)
        return int(low) <= uid <= high_uid, i + 2
    msg = parse(message.raw)
    if key == "HEADER":
        name, needle = _value(tokens[i + 1]), _value(tokens[i + 2]).casefold()
        return needle in (raw_header(msg, name) or "").casefold(), i + 3
    needle = _value(tokens[i + 1]).casefold()
    if key in ("FROM", "TO", "CC", "SUBJECT"):
        haystack = header_text(msg, key.lower()) or ""
    elif key == "TEXT":
        haystack = message.raw.decode("utf-8", "replace")
    else:
        raise AssertionError(f"unsupported search key {key}")
    return needle in haystack.casefold(), i + 2


def make_raw(
    subject="Hello",
    sender="Sender <sender@example.ru>",
    to="Owner <owner@yandex.ru>",
    cc=None,
    date=None,
    message_id=None,
    in_reply_to=None,
    references=None,
    body="Body text",
    html=None,
    attachments=(),
) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Date"] = format_datetime(date or datetime(2026, 11, 18, 10, 0, tzinfo=MSK))
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    for filename, content_type, data in attachments:
        maintype, subtype = content_type.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def arrival(day: int, month: int = 11, year: int = 2026) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=MSK)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    directory.mkdir()
    monkeypatch.setattr(config, "config_dir", lambda: directory)
    return directory


@pytest.fixture
def credentials(config_dir):
    path = config_dir / config.CREDENTIALS_FILENAME
    path.write_text(
        '{"login": "owner@yandex.ru", "password": "secret-app-pass"}', "utf-8"
    )
    return path


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(throttle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sync, "BATCH_PAUSE", 0)


@pytest.fixture
def server(monkeypatch, credentials, no_sleep):
    """A FakeIMAP with INBOX, Sent, Spam, Trash and Drafts, wired into the session."""
    fake = FakeIMAP(
        {
            "INBOX": FakeFolder(),
            "Отправленные": FakeFolder(
                flags=(b"\\HasNoChildren", b"\\Sent"), uidvalidity=2000
            ),
            "Спам": FakeFolder(flags=(b"\\HasNoChildren", b"\\Junk"), uidvalidity=3000),
            "Удаленные": FakeFolder(
                flags=(b"\\HasNoChildren", b"\\Trash"), uidvalidity=4000
            ),
            "Черновики": FakeFolder(
                flags=(b"\\HasNoChildren", b"\\Drafts"), uidvalidity=5000
            ),
        }
    )
    monkeypatch.setattr(session, "connect", lambda: fake)
    return fake


def add(fake: FakeIMAP, folder: str, uid: int, raw: bytes, when=None, flags=()):
    fake.folders[folder].messages[uid] = FakeMessage(raw, when or arrival(18), flags)


@pytest.fixture
def today():
    return date(2026, 11, 20)
