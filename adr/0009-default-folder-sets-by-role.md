# 0009. Default folder sets chosen by SPECIAL-USE role

Status: Accepted

## Context

Search should cover the owner's mail, not spam or deleted mail. Yandex
names folders by the account language ("Sent" or "Отправленные"), and a
user may create a folder called "Spam", so names cannot tell the role.
RFC 6154 SPECIAL-USE attributes in the LIST answer can. Virtual folders
(`\All`, `\Flagged`, if a server has them) only repeat messages stored
elsewhere. Drafts change constantly: every autosave is a new message with a
new UID, which an append-only archive would keep forever.

## Decision

Roles come from SPECIAL-USE attributes (INBOX by name, as IMAP defines it;
XLIST attributes if LIST carries none and the server supports XLIST).

- `search` and `read --thread` default to every selectable folder except
  junk, trash, all and flagged.
- `sync` defaults to the same set minus drafts.
- `--folder` overrides the default with an explicit list, which is how
  spam or trash is searched or archived.

`folders` prints `searched_by_default` and `archived_by_default` so
callers see the effect.

## Consequences

- Correct regardless of the account language and of user folder names.
- If a server reports no special-use attributes at all, spam and trash
  look like ordinary folders and are searched; `folders` makes that
  visible (all roles null).
