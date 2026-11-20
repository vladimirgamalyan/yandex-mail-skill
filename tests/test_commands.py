"""search, read and download through the CLI, online and --offline."""

import json

import pytest
from conftest import add, arrival, make_raw
from imapclient.imapclient import literal

from yandex_mail import search, session
from yandex_mail.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out else None), captured.err


@pytest.fixture
def mail(server):
    """A small mailbox: a bill thread with the organization, plus noise."""
    add(server, "INBOX", 10, make_raw(
        subject="Вопрос по счёту", sender="Иван <owner@yandex.ru>", to="info@mosenergosbyt.ru",
        message_id="<q1@yandex.ru>", body="Почему счёт такой большой?"), when=arrival(10))  # fmt: skip
    add(server, "INBOX", 11, make_raw(
        subject="RE: Вопрос по счёту", sender="Мосэнергосбыт <info@mosenergosbyt.ru>",
        message_id="<a1@mes.ru>", body="Перерасчёт выполнен.",
        attachments=[("Акт сверки.pdf", "application/pdf", b"%PDF-1.4 act")]),
        when=arrival(12), flags=(b"\\Seen",))  # fmt: skip
    add(server, "Отправленные", 3, make_raw(
        subject="Re: Вопрос по счёту", sender="owner@yandex.ru", to="info@mosenergosbyt.ru",
        message_id="<q2@yandex.ru>", in_reply_to="<a1@mes.ru>", references="<a1@mes.ru>",
        body="Спасибо!"), when=arrival(13))  # fmt: skip
    add(
        server,
        "INBOX",
        12,
        make_raw(subject="Newsletter", sender="news@shop.ru"),
        when=arrival(14),
    )
    add(
        server,
        "Спам",
        1,
        make_raw(subject="Вопрос по счёту", sender="spam@x.ru"),
        when=arrival(15),
    )
    return server


def test_search_default_folders_newest_first(mail, capsys):
    code, result, _ = run(capsys, "search", "--address", "mosenergosbyt")
    assert code == 0
    assert result["total"] == 3
    assert [(m["folder"], m["uid"]) for m in result["messages"]] == [
        ("Отправленные", 3),
        ("INBOX", 11),
        ("INBOX", 10),
    ]
    first = result["messages"][1]
    assert first["from"] == {
        "name": "Мосэнергосбыт",
        "address": "info@mosenergosbyt.ru",
    }
    assert first["snippet"] == "Перерасчёт выполнен." and "text" not in first
    assert first["attachments"][0]["filename"] == "Акт сверки.pdf"
    assert first["seen"] is True and result["messages"][2]["seen"] is False
    assert ("select", "Спам", True) not in mail.calls


def test_search_sends_cyrillic_as_utf8_literal(mail, capsys):
    code, result, _ = run(capsys, "search", "--subject", "счёт", "--limit", "1")
    assert code == 0 and result["total"] == 3 and len(result["messages"]) == 1
    criteria, charset = mail.calls_of("search")[0][2:]
    assert charset == "UTF-8"
    assert criteria[0] == "SUBJECT" and isinstance(criteria[1], literal)
    assert criteria[1] == "счёт".encode()


def test_search_ascii_has_no_charset_and_dates_are_inclusive(mail, capsys):
    code, result, _ = run(
        capsys,
        "search",
        "--from",
        "shop",
        "--since",
        "2026-11-14",
        "--until",
        "2026-11-14",
    )
    assert code == 0 and [m["uid"] for m in result["messages"]] == [12]
    criteria, charset = mail.calls_of("search")[0][2:]
    assert charset is None
    assert (
        "BEFORE" in criteria
        and str(criteria[criteria.index("BEFORE") + 1]) == "2026-11-15"
    )


def test_search_large_message_fetches_headers_only(mail, capsys, monkeypatch):
    monkeypatch.setattr(search, "SEARCH_BODY_CAP", 10)
    code, result, _ = run(capsys, "search", "--from", "shop")
    assert code == 0
    message = result["messages"][0]
    assert message["subject"] == "Newsletter"
    assert message["snippet"] is None and message["attachments"] is None


def test_search_named_folder_and_unknown_folder(mail, capsys):
    code, result, _ = run(capsys, "search", "--folder", "Спам")
    assert code == 0 and [m["folder"] for m in result["messages"]] == ["Спам"]
    code, _, err = run(capsys, "search", "--folder", "Nope")
    assert code == 1 and "No folder named 'Nope'" in err


def test_read_one_message(mail, capsys):
    code, message, _ = run(capsys, "read", "INBOX", "11")
    assert code == 0
    assert message["text"] == "Перерасчёт выполнен."
    assert message["date"] == "2026-11-18T10:00:00+03:00"
    assert message["uidvalidity"] == 1000 and message["file"] is None


def test_read_missing_uid_and_renumbered_folder(mail, capsys):
    code, _, err = run(capsys, "read", "INBOX", "99")
    assert code == 1 and "No message with UID 99" in err
    code, _, err = run(capsys, "read", "INBOX", "11", "--uidvalidity", "5")
    assert code == 1 and "renumbered" in err


def test_read_thread_across_folders(mail, capsys):
    code, thread, _ = run(capsys, "read", "INBOX", "10", "--thread")
    assert code == 0
    assert [(m["folder"], m["uid"]) for m in thread] == [
        ("INBOX", 10),
        ("INBOX", 11),
        ("Отправленные", 3),
    ]


def test_thread_falls_back_to_subject_when_header_search_unsupported(mail, capsys):
    mail.reject_header_search = True
    code, thread, _ = run(capsys, "read", "Отправленные", "3", "--thread")
    assert code == 0 and len(thread) == 3


def test_download_stable_names_and_skip_existing(mail, capsys, tmp_path):
    out = tmp_path / "out"
    code, result, _ = run(capsys, "download", "INBOX", "11", "--output", str(out))
    assert code == 0
    (saved,) = result["files"]
    assert saved["status"] == "saved" and saved["filename"] == "Акт сверки.pdf"
    name = saved["file"].replace("\\", "/").rsplit("/", 1)[1]
    assert name.startswith("2026-11-18_") and name.endswith("_1_Акт сверки.pdf")
    assert (out / name).read_bytes() == b"%PDF-1.4 act"

    code, again, _ = run(capsys, "download", "INBOX", "11", "--output", str(out))
    assert (
        again["files"][0]["status"] == "exists"
        and again["files"][0]["file"] == saved["file"]
    )


def test_download_refuses_oversized_message(mail, capsys, tmp_path):
    code, _, err = run(
        capsys, "download", "INBOX", "11", "--output", str(tmp_path), "--max-size", "1"
    )
    assert code == 0
    mail.folders["INBOX"].messages[11].raw += b"x" * (1024 * 1024)
    code, _, err = run(
        capsys, "download", "INBOX", "11", "--output", str(tmp_path), "--max-size", "1"
    )
    assert code == 1 and "--max-size" in err


def test_offline_commands_work_without_network(mail, capsys, tmp_path, monkeypatch):
    assert run(capsys, "sync", "--since", "2026-11-01")[0] == 0

    def no_network():
        raise AssertionError("offline commands must not connect")

    monkeypatch.setattr(session, "connect", no_network)

    code, result, _ = run(capsys, "search", "--offline", "--address", "mosenergosbyt")
    assert code == 0 and result["total"] == 3
    archived = result["messages"][0]
    assert archived["seen"] is None and archived["file"].endswith("3.eml")

    code, result, _ = run(capsys, "search", "--offline", "--text", "перерасчёт")
    assert [m["uid"] for m in result["messages"]] == [11]

    code, message, _ = run(capsys, "read", "INBOX", "11", "--offline")
    assert code == 0 and message["text"] == "Перерасчёт выполнен."

    code, thread, _ = run(capsys, "read", "INBOX", "10", "--offline", "--thread")
    assert [m["uid"] for m in thread] == [10, 11, 3]

    code, result, _ = run(
        capsys, "download", "INBOX", "11", "--offline", "--output", str(tmp_path / "o")
    )
    assert code == 0 and result["files"][0]["status"] == "saved"

    code, _, err = run(capsys, "search", "--offline", "--unseen")
    assert code == 1 and "--unseen needs the server" in err


def test_offline_without_archive(config_dir, capsys):
    code, _, err = run(capsys, "search", "--offline")
    assert code == 1 and "No archive" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["read", "INBOX", "0"],
        ["read", "INBOX", "1_0"],
        ["search", "--limit", "101"],
        ["search", "--since", "2026-13-01"],
        ["search", "--subject", " "],
        ["download", "INBOX", "1"],
        [],
    ],
)
def test_usage_errors_exit_1(argv, capsys):
    # Exit code 2 is reserved for "retry later"; argparse's default would be 2.
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 1
