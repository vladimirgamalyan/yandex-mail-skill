---
name: yandex-mail
description: "Read the user's own Yandex Mail mailbox (read-only, IMAP) through the yandex-mail CLI: search letters, read a letter or a whole conversation, save attachments, keep a local archive of the mail. Use whenever a task needs the user's Yandex mail or their correspondence with organizations, for example: Яндекс-почта, яндекс почта, письма от Мосэнергосбыта, письмо от МосОблЕИРЦ, что пришло на Яндекс, найди письмо в Яндексе, переписка с ..., вложения из письма, check my Yandex mail, find the email from ..., what's new in my Yandex inbox. Works from any project or folder."
---

# yandex-mail

`yandex-mail` is a CLI (already on PATH) that reads the user's own Yandex
Mail mailbox over IMAP. It is strictly read-only: it never marks letters
read and cannot send, move, delete or flag anything. Every command prints
JSON to stdout.

**Always check `yandex-mail <command> --help` for the exact flags, output
schema and exit codes** — it is written to be self-sufficient; do not rely
on memory for the contract.

## 1. Pick the command

| The user wants | Run |
|---|---|
| what's new / "что пришло" | `yandex-mail search --unseen` (or `--since <date>`) |
| letters from someone ("письма от Мосэнергосбыта") | `yandex-mail search --from <name or domain>` |
| the whole correspondence with someone, both directions | `yandex-mail search --address <name or domain>` |
| a letter about something | `yandex-mail search --subject <word>` or `--text <word>` |
| the full text of a letter | `yandex-mail read <folder> <uid>` |
| the whole conversation | `yandex-mail read <folder> <uid> --thread` |
| the attached files | `yandex-mail download <folder> <uid> --output <dir>` |
| which folders exist | `yandex-mail folders` |

- `folder` and `uid` always come from the `search` output; never guess
  them. Folder names are case-sensitive and may be Russian
  ("Отправленные"): pass them exactly as printed, quoted.
- Search filters are case-insensitive substrings and may be Russian. For an
  organization, try a distinctive part of its name or its mail domain
  (`--from mosenergosbyt`, `--address mosobleirc`); if nothing comes back,
  try the Russian name ("мосэнергосбыт"), another spelling, or `--text`.
- Search covers every folder except spam and trash, including sent mail.
  Results are summaries (a `snippet`, not the full text): `read` the ones
  that matter. `total` larger than the shown messages means more matches:
  narrow with `--since`/`--until` rather than raising `--limit`.

Present the result readably (date, sender, subject, the gist of the text,
attachments) — do not dump raw JSON unless asked. Long official letters:
summarize, and quote the figures that matter (amounts, dates, account
numbers) exactly.

## 2. Offline: the local archive

`yandex-mail sync` keeps an append-only archive of raw `.eml` files (in
`%APPDATA%\yandex-mail\archive` on Windows). With `--offline`, `search`,
`read` and `download` work on that archive without the network.

- To bring it up to date: `yandex-mail sync`. If there is no archive yet
  it fails and asks for `--since YYYY-MM-DD`: take the date from the task,
  or ask the user how far back to go; prefer a recent date (a first sync of
  years of mail takes a long time and blocks other runs meanwhile).
- Use `--offline` when the task is about going through a lot of old mail,
  when a script must work without the network, or when the server is
  unavailable (exit code 2) and the archive is recent enough. Offline
  results carry the `.eml` path in `file`; `seen` is unknown (null).
- `sync --help` describes the archive layout for scripts that read it.

## 3. Errors

- Exit code 2 (`retry after Ns`): another run holds the mailbox, Yandex
  asked to slow down, or the network is down. Wait the given time before
  one retry; do not loop. Mention it if the wait is long. Exception:
  `sync` keeps what it fetched and Yandex sometimes drops the connection
  during a long sync, so rerun `sync` after each wait until it exits 0.
- Exit code 1: a permanent error; do not retry the same command.
  - **"No credentials file"** or **"Yandex rejected the login"**: the
    setup is the mailbox owner's job. Relay the message: where the file
    goes and what to check in Yandex settings (IMAP enabled; an app
    password for Mail from id.yandex.ru → Security → App passwords). Never
    create the credentials file, never ask the user to paste the password
    into the chat, never print it.
  - Unknown folder: run `yandex-mail folders` and use a printed name.

## 4. What not to do

- Do not try to reply, send, delete, move or mark letters: the tool cannot,
  by design. Say so if asked, and offer to draft the text instead.
- Do not run `yandex-mail` in a tight loop over many letters online: use
  `sync` once and work `--offline`.
