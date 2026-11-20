"""Thread assembly: which messages belong to the same conversation.

Messages are linked by their Message-ID, References and In-Reply-To
headers. Mail from organizations often drops those headers (ticket
systems send each answer as a new message), so a message without
References and In-Reply-To is also linked to the messages with the same
normalized subject that share a correspondent with it. Requiring a
shared correspondent keeps generic subjects ("Information", "Notice")
from different senders apart.
"""

import re
from collections import defaultdict
from collections.abc import Hashable
from dataclasses import dataclass

from .message import Headers

_REPLY_PREFIX = re.compile(
    r"^\s*(?:(?:re|fwd?|aw|wg|sv|vs|отв|ответ|пересл|переслано)\.?\s*"
    r"(?:\[\d+\]|\(\d+\))?\s*:\s*)+",
    re.IGNORECASE,
)

# Domains under which Yandex delivers one mailbox ("name@ya.ru" and
# "name@yandex.ru" are the same person).
YANDEX_DOMAINS = (
    "yandex.ru", "ya.ru", "yandex.com", "yandex.by", "yandex.kz", "yandex.ua",
    "narod.ru",
)  # fmt: skip


def base_subject(subject: str | None) -> str:
    """The subject without reply and forward prefixes ("Re:", "Fwd:", "Отв:")."""
    return re.sub(r"\s+", " ", _REPLY_PREFIX.sub("", subject or "")).strip()


def subject_key(subject: str | None) -> str:
    return base_subject(subject).casefold().replace("ё", "е")


def own_addresses(login: str) -> set[str]:
    """The mailbox owner's own addresses, which every message shares."""
    login = login.strip().lower()
    local, _, domain = login.partition("@")
    if not domain or domain in YANDEX_DOMAINS:
        return {f"{local}@{d}" for d in YANDEX_DOMAINS} | {login}
    return {login}


@dataclass(frozen=True)
class Node:
    key: Hashable
    headers: Headers


def thread_keys(target: Hashable, nodes: list[Node], own: set[str]) -> set:
    """Keys of the nodes in the same conversation as the 'target' node.

    Nodes repeating a Message-ID seen earlier in the list (copies of one
    message in several folders) are dropped; put the target first.
    """
    parent: dict = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a, b):
        parent[find(a)] = find(b)

    seen_ids, unique = set(), []
    for node in nodes:
        message_id = node.headers.message_id
        if message_id and message_id in seen_ids and node.key != target:
            continue
        seen_ids.add(message_id)
        unique.append(node)

    by_subject = defaultdict(list)
    for node in unique:
        headers = node.headers
        find(("node", node.key))
        linked = list(headers.references)
        if headers.in_reply_to:
            linked.append(headers.in_reply_to)
        if headers.message_id:
            linked.append(headers.message_id)
        for message_id in linked:
            union(("node", node.key), ("id", message_id))
        key = subject_key(headers.subject)
        if key:
            by_subject[key].append(node)

    for group in by_subject.values():
        for orphan in group:
            if orphan.headers.references or orphan.headers.in_reply_to:
                continue
            mine = orphan.headers.participants - own
            for other in group:
                theirs = other.headers.participants - own
                if other is not orphan and (mine & theirs or not (mine or theirs)):
                    union(("node", orphan.key), ("node", other.key))

    root = find(("node", target))
    return {node.key for node in unique if find(("node", node.key)) == root}
