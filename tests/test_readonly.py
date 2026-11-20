"""The tool never changes the mailbox: EXAMINE, BODY.PEEK, no write commands."""

import re
from pathlib import Path

import pytest
from conftest import FakeFolder, FakeIMAP, add, make_raw

from yandex_mail.cli import main
from yandex_mail.mailbox import ReadOnlyMailbox

SOURCE = Path(__file__).parents[1] / "src" / "yandex_mail"

# The only IMAPClient methods the tool may call; none of them writes.
READ_METHODS = {
    "login", "logout", "has_capability", "list_folders", "xlist_folders",
    "folder_status", "select_folder", "search", "fetch",
}  # fmt: skip


def test_client_calls_are_whitelisted():
    pattern = re.compile(r"\bclient\.(\w+)\(")
    called = {
        match.group(1)
        for name in ("mailbox.py", "session.py")
        for match in pattern.finditer((SOURCE / name).read_text("utf-8"))
    }
    assert called and called <= READ_METHODS
    assert re.findall(
        r"select_folder\(([^)]*)\)", (SOURCE / "mailbox.py").read_text("utf-8")
    ) == ["folder, readonly=True"]


def test_only_mailbox_module_touches_the_client():
    # Every other module reaches the server through ReadOnlyMailbox only.
    for path in SOURCE.glob("*.py"):
        if path.name not in ("mailbox.py", "session.py"):
            assert "_client" not in path.read_text("utf-8"), path.name
            assert "select_folder" not in path.read_text("utf-8"), path.name


def test_fetch_rejects_items_that_set_seen():
    box = ReadOnlyMailbox(FakeIMAP({"INBOX": FakeFolder()}), "x")
    box.examine("INBOX")
    for item in ("BODY[]", "RFC822", "BODY[TEXT]", "RFC822.TEXT"):
        with pytest.raises(ValueError):
            box.fetch([1], [item])


def test_every_command_examines_and_peeks(server, tmp_path, capsys):
    add(server, "INBOX", 1, make_raw(subject="Счёт", message_id="<a@x>",
        attachments=[("a.pdf", "application/pdf", b"%PDF")]))  # fmt: skip
    add(server, "Отправленные", 1, make_raw(subject="Re: Счёт", in_reply_to="<a@x>",
        references="<a@x>", message_id="<b@x>"))  # fmt: skip
    commands = [
        ["check"],
        ["folders"],
        ["search", "--subject", "Счёт"],
        ["read", "INBOX", "1"],
        ["read", "INBOX", "1", "--thread"],
        ["download", "INBOX", "1", "--output", str(tmp_path / "out")],
        ["sync", "--since", "2026-01-01"],
    ]
    for command in commands:
        assert main(command) == 0, command
    capsys.readouterr()
    selects = server.calls_of("select")
    assert selects and all(readonly is True for _, _, readonly in selects)
    # FakeIMAP.fetch asserts every item is whitelisted; check PEEK was used.
    bodies = [
        item for call in server.calls_of("fetch") for item in call[3] if "BODY" in item
    ]
    assert bodies and all(item.startswith("BODY.PEEK[") for item in bodies)
    assert all(
        not m.flags for f in server.folders.values() for m in f.messages.values()
    )
