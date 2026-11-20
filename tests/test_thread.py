"""Thread assembly from References/In-Reply-To and the subject fallback."""

from yandex_mail.message import Headers
from yandex_mail.thread import Node, base_subject, own_addresses, thread_keys

OWN = own_addresses("owner@yandex.ru")


def node(
    key,
    subject="Вопрос",
    message_id=None,
    in_reply_to=None,
    references=(),
    sender="org@example.ru",
    to="owner@yandex.ru",
):
    return Node(
        key,
        Headers(
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=list(references),
            date=None,
            sender={"name": None, "address": sender},
            to=[{"name": None, "address": to}],
            cc=[],
            subject=subject,
        ),
    )


def test_base_subject_strips_reply_prefixes():
    assert base_subject("Re: RE[2]: Fwd: Отв: Счёт  за май") == "Счёт за май"
    assert base_subject("Пересл.: тест") == "тест"
    assert base_subject("Пересл: тест") == "тест"


def test_own_addresses_cover_yandex_alias_domains():
    assert {"owner@ya.ru", "owner@yandex.com", "owner@yandex.ru"} <= OWN
    assert own_addresses("me@company.ru") == {"me@company.ru"}


def test_references_chain_with_changed_subject():
    nodes = [
        node(3, "Re: другая тема", "<c@x>", "<b@x>", ["<a@x>", "<b@x>"]),
        node(1, "Вопрос", "<a@x>"),
        node(2, "Re: Вопрос", "<b@x>", "<a@x>", ["<a@x>"]),
        node(9, "Вопрос", "<z@x>", sender="other@example.ru"),
    ]
    assert thread_keys(3, nodes, OWN) == {1, 2, 3}


def test_siblings_linked_through_missing_root():
    # The root itself is gone, both replies still reference it.
    nodes = [
        node(1, "Re: X", "<b@x>", "<a@x>", ["<a@x>"]),
        node(2, "Re: X", "<c@x>", "<a@x>", ["<a@x>"]),
    ]
    assert thread_keys(1, nodes, OWN) == {1, 2}


def test_orphan_reply_joins_by_subject_and_shared_correspondent():
    nodes = [
        node(1, "Вопрос по счёту", "<a@x>", sender="owner@yandex.ru", to="info@org.ru"),
        # The organization answers without In-Reply-To/References.
        node(2, "RE: Вопрос по счету", "<b@org>", sender="info@org.ru"),
        # My follow-up references their answer properly.
        node(
            3,
            "Re: Вопрос по счёту",
            "<c@x>",
            "<b@org>",
            ["<b@org>"],
            sender="owner@ya.ru",
            to="info@org.ru",
        ),
    ]
    assert thread_keys(1, nodes, OWN) == {1, 2, 3}
    assert thread_keys(3, nodes, OWN) == {1, 2, 3}


def test_generic_subject_from_different_senders_not_merged():
    nodes = [
        node(1, "Информация", "<a@x>", sender="a@one.ru"),
        node(2, "Информация", "<b@x>", sender="b@two.ru"),
    ]
    assert thread_keys(1, nodes, OWN) == {1}


def test_copies_of_one_message_counted_once():
    nodes = [
        node(("INBOX", 1), "Вопрос", "<a@x>"),
        node(("Bills", 7), "Вопрос", "<a@x>"),
    ]
    assert thread_keys(("INBOX", 1), nodes, OWN) == {("INBOX", 1)}


def test_message_without_ids_or_subject_is_alone():
    nodes = [node(1, None), node(2, None)]
    assert thread_keys(1, nodes, OWN) == {1}
