"""The append-only .eml archive: first sync, increments, UIDVALIDITY, batches."""

import json
from datetime import date

import pytest
from conftest import FakeFolder, FakeIMAP, add, arrival, make_raw
from imapclient.exceptions import IMAPClientAbortError

from yandex_mail import archive, sync
from yandex_mail.cli import main
from yandex_mail.mailbox import ReadOnlyMailbox
from yandex_mail.sync import SyncError, run_sync


def box(server) -> ReadOnlyMailbox:
    return ReadOnlyMailbox(server, "owner@yandex.ru")


def eml(config_dir, folder_dir, uidvalidity, uid):
    return config_dir / "archive" / folder_dir / str(uidvalidity) / f"{uid}.eml"


def test_first_sync_needs_since(server):
    with pytest.raises(SyncError, match="--since"):
        run_sync(box(server), None, None)


def test_first_sync_archives_raw_messages_since_date(server, config_dir):
    old, new = make_raw(subject="old"), make_raw(subject="Счёт")
    add(server, "INBOX", 1, old, when=arrival(1, 10))
    add(server, "INBOX", 2, new, when=arrival(5))
    add(server, "Отправленные", 7, make_raw(subject="sent"), when=arrival(6))
    add(server, "Спам", 1, make_raw(subject="spam"), when=arrival(6))
    add(server, "Черновики", 1, make_raw(subject="draft"), when=arrival(6))

    result = run_sync(box(server), date(2026, 11, 1), None)

    assert eml(config_dir, "INBOX", 1000, 2).read_bytes() == new
    assert not eml(config_dir, "INBOX", 1000, 1).exists()
    assert eml(config_dir, "Отправленные", 2000, 7).exists()
    assert not (config_dir / "archive" / "Спам").exists()
    assert not (config_dir / "archive" / "Черновики").exists()
    assert eml(config_dir, "INBOX", 1000, 2).stat().st_mtime == arrival(5).timestamp()
    assert result["added"] == 2 and result["total"] == 2
    assert [f["name"] for f in result["folders"]] == ["INBOX", "Отправленные"]
    state = json.loads((config_dir / "archive" / "state.json").read_text("utf-8"))
    assert state["account"] == "owner@yandex.ru" and state["since"] == "2026-11-01"
    assert state["folders"]["INBOX"]["last_uid"] == 2


def test_incremental_sync_fetches_only_new_mail(server, config_dir):
    add(server, "INBOX", 1, make_raw(subject="one"), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), None)
    add(server, "INBOX", 2, make_raw(subject="two"), when=arrival(6))
    server.calls.clear()

    result = run_sync(box(server), None, None)

    assert result["added"] == 1
    bodies = [call for call in server.calls_of("fetch") if "BODY.PEEK[]" in call[3]]
    assert bodies == [("fetch", "INBOX", [2], ["BODY.PEEK[]", "INTERNALDATE"])]
    # The Sent folder had no new mail: one STATUS, never opened.
    assert ("select", "Отправленные", True) not in server.calls
    assert ("status", "Отправленные", ("UIDVALIDITY", "UIDNEXT")) in server.calls


def test_messages_deleted_on_server_stay_archived(server, config_dir):
    add(server, "INBOX", 1, make_raw(subject="one"), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), None)
    del server.folders["INBOX"].messages[1]
    server.folders["INBOX"].uidnext = 2

    result = run_sync(box(server), None, None)

    assert eml(config_dir, "INBOX", 1000, 1).exists()
    assert result["folders"][0]["total"] == 1


def test_uidvalidity_change_starts_new_generation(server, config_dir):
    add(server, "INBOX", 5, make_raw(subject="one"), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), None)
    server.folders["INBOX"] = FakeFolder(uidvalidity=1001)
    add(server, "INBOX", 1, make_raw(subject="one"), when=arrival(5))

    result = run_sync(box(server), None, None)

    assert eml(config_dir, "INBOX", 1000, 5).exists()
    assert eml(config_dir, "INBOX", 1001, 1).exists()
    inbox = result["folders"][0]
    assert inbox["uidvalidity_changed"] and inbox["uidvalidity"] == 1001
    assert result["total"] == 2


def test_batches_by_count(server, config_dir):
    for uid in range(1, 51):
        add(server, "INBOX", uid, make_raw(subject=f"m{uid}"), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), ["INBOX"])
    batches = [call[2] for call in server.calls_of("fetch") if "BODY.PEEK[]" in call[3]]
    assert [len(batch) for batch in batches] == [20, 20, 10]
    assert batches[0] == list(range(1, 21))


def test_batches_by_size(monkeypatch, server, config_dir):
    monkeypatch.setattr(sync, "BATCH_BYTES", 1000)
    for uid in range(1, 4):
        add(
            server, "INBOX", uid, make_raw(subject="x", body="y" * 600), when=arrival(5)
        )
    run_sync(box(server), date(2026, 11, 1), ["INBOX"])
    batches = [call[2] for call in server.calls_of("fetch") if "BODY.PEEK[]" in call[3]]
    assert batches == [[1], [2], [3]]


def test_interrupted_sync_resumes_after_saved_batch(monkeypatch, server, config_dir):
    for uid in range(1, 31):
        add(server, "INBOX", uid, make_raw(subject=f"m{uid}"), when=arrival(5))
    original = FakeIMAP.fetch
    calls = {"bodies": 0}

    def flaky(self, messages, data, modifiers=None):
        if "BODY.PEEK[]" in data:
            calls["bodies"] += 1
            if calls["bodies"] == 2:
                raise IMAPClientAbortError("socket error: EOF")
        return original(self, messages, data)

    monkeypatch.setattr(FakeIMAP, "fetch", flaky)
    assert main(["sync", "--since", "2026-11-01", "--folder", "INBOX"]) == 2
    state = archive.load_state()
    assert state["folders"]["INBOX"]["last_uid"] == 20
    assert len(list((config_dir / "archive" / "INBOX" / "1000").glob("*.eml"))) == 20

    assert main(["sync"]) == 0
    bodies = [call[2] for call in server.calls_of("fetch") if "BODY.PEEK[]" in call[3]]
    assert bodies[-1] == list(range(21, 31))


def test_earlier_since_extends_the_archive_back(server, config_dir):
    add(server, "INBOX", 1, make_raw(subject="oct"), when=arrival(10, 10))
    add(server, "INBOX", 2, make_raw(subject="nov"), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), ["INBOX"])
    assert not eml(config_dir, "INBOX", 1000, 1).exists()

    result = run_sync(box(server), date(2026, 10, 1), ["INBOX"])

    assert eml(config_dir, "INBOX", 1000, 1).exists() and result["added"] == 1
    assert result["since"] == "2026-10-01"


def test_archive_of_another_account_is_refused(server, config_dir):
    add(server, "INBOX", 1, make_raw(), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), None)
    with pytest.raises(SyncError, match="belongs to owner@yandex.ru"):
        run_sync(ReadOnlyMailbox(server, "other@yandex.ru"), None, None)


def test_folder_names_made_safe_and_unique(server, config_dir):
    server.folders["INBOX|Счета: 2026"] = FakeFolder(uidvalidity=7)
    server.folders["inbox|Счета? 2026"] = FakeFolder(uidvalidity=8)
    add(server, "INBOX|Счета: 2026", 1, make_raw(), when=arrival(5))
    add(server, "inbox|Счета? 2026", 1, make_raw(), when=arrival(5))
    run_sync(box(server), date(2026, 11, 1), ["INBOX|Счета: 2026", "inbox|Счета? 2026"])
    assert eml(config_dir, "INBOX_Счета_ 2026", 7, 1).exists()
    assert eml(config_dir, "inbox_Счета_ 2026 (2)", 8, 1).exists()
