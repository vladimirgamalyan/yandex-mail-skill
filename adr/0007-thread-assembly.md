# 0007. Threads from message IDs, with a subject fallback that needs a shared correspondent

Status: Accepted

## Context

`read --thread` must show a conversation. The standard links are the
References and In-Reply-To headers. Mail from organizations often lacks
them: ticket and CRM systems answer with a new message whose subject is
"RE: <original subject>". A pure subject match, though, merges unrelated
messages with generic subjects ("Информация", "Уведомление") from
different senders. The server-side THREAD extension is not something to
rely on, and it would not cover the sent folder together with the inbox.

## Decision

Threads are assembled locally (`thread.py`) with union-find:

1. every message is linked to its Message-ID and to every ID in its
   References and In-Reply-To (so siblings link through a missing root);
2. a message with neither References nor In-Reply-To is also linked to
   messages with the same normalized subject (reply/forward prefixes in
   English and Russian removed, case folded, ё = е) if they share a
   correspondent other than the owner (the owner's Yandex alias domains
   count as the owner);
3. copies of one Message-ID in several folders count once.

Online candidates come from one UID SEARCH per searched folder: the same
base subject, or a References / In-Reply-To / Message-ID header naming the
thread root, the message or its parent; if the server rejects HEADER
search, the subject alone. At most the newest 50 members are printed,
oldest first.

## Consequences

- Organization replies without threading headers join their thread; equal
  generic subjects from different senders do not.
- Monthly letters with the same subject from the same sender (bills) form
  one "thread"; accepted, they are one topic.
- A reply whose subject changed and that references nothing the search
  found is missed online; offline, every archived message is a candidate.
