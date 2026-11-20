"""Folder roles from SPECIAL-USE attributes and the default folder sets."""

import pytest
from conftest import FakeFolder, FakeIMAP

from yandex_mail.folders import (
    SEARCH_SKIPPED_ROLES,
    SYNC_SKIPPED_ROLES,
    FolderNotFoundError,
    find_folder,
    list_folders,
    pick_folders,
    run_folders,
)
from yandex_mail.mailbox import ReadOnlyMailbox


def mailbox(folders, **kwargs) -> ReadOnlyMailbox:
    return ReadOnlyMailbox(FakeIMAP(folders, **kwargs), "owner@yandex.ru")


def test_roles_come_from_attributes_not_names():
    box = mailbox(
        {
            "INBOX": FakeFolder(),
            "Отправленные": FakeFolder(flags=(b"\\Sent",)),
            "Спам": FakeFolder(flags=(b"\\HasNoChildren", b"\\Junk")),
            "Удаленные": FakeFolder(flags=(b"\\Trash",)),
            # A user folder named like a special one is still ordinary.
            "Spam": FakeFolder(),
        }
    )
    roles = {folder.name: folder.role for folder in list_folders(box)}
    assert roles == {
        "INBOX": "inbox",
        "Отправленные": "sent",
        "Спам": "junk",
        "Удаленные": "trash",
        "Spam": None,
    }


def test_xlist_used_when_list_has_no_special_use():
    fake = FakeIMAP(
        {"INBOX": FakeFolder(), "Spam": FakeFolder()}, capabilities=("XLIST",)
    )
    fake.xlist = [((b"\\Inbox",), b"|", "INBOX"), ((b"\\Spam",), b"|", "Spam")]
    roles = {f.name: f.role for f in list_folders(ReadOnlyMailbox(fake, "x"))}
    assert roles == {"INBOX": "inbox", "Spam": "junk"}


def test_default_sets_skip_spam_trash_and_drafts_for_sync():
    box = mailbox(
        {
            "INBOX": FakeFolder(),
            "Sent": FakeFolder(flags=(b"\\Sent",)),
            "Spam": FakeFolder(flags=(b"\\Junk",)),
            "Trash": FakeFolder(flags=(b"\\Trash",)),
            "Drafts": FakeFolder(flags=(b"\\Drafts",)),
            "Parent": FakeFolder(flags=(b"\\Noselect", b"\\HasChildren")),
            "Parent|Bills": FakeFolder(),
        }
    )
    folders = list_folders(box)
    searched = [f.name for f in pick_folders(folders, None, SEARCH_SKIPPED_ROLES)]
    synced = [f.name for f in pick_folders(folders, None, SYNC_SKIPPED_ROLES)]
    assert searched == ["INBOX", "Sent", "Drafts", "Parent|Bills"]
    assert synced == ["INBOX", "Sent", "Parent|Bills"]
    # Naming a skipped folder explicitly searches it.
    assert [f.name for f in pick_folders(folders, ["Spam"], SEARCH_SKIPPED_ROLES)] == [
        "Spam"
    ]
    with pytest.raises(FolderNotFoundError):
        pick_folders(folders, ["Parent"], SEARCH_SKIPPED_ROLES)


def test_find_folder_inbox_case_insensitive_others_exact():
    folders = list_folders(mailbox({"INBOX": FakeFolder(), "Счета": FakeFolder()}))
    assert find_folder(folders, "inbox").name == "INBOX"
    with pytest.raises(FolderNotFoundError, match="case-sensitive"):
        find_folder(folders, "счета")


def test_run_folders_counts_and_noselect():
    fake = FakeIMAP(
        {
            "INBOX": FakeFolder(messages={1: None, 2: None}),
            "Parent": FakeFolder(flags=(b"\\Noselect",)),
        }
    )
    fake.folders["INBOX"].messages = {}
    result = run_folders(ReadOnlyMailbox(fake, "x"))
    assert result[0]["name"] == "INBOX" and result[0]["messages"] == 0
    assert result[0]["searched_by_default"] and result[0]["archived_by_default"]
    assert result[1]["messages"] is None and not result[1]["searched_by_default"]
    assert [call[1] for call in fake.calls_of("status")] == ["INBOX"]
