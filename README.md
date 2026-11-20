# yandex-mail

A small read-only CLI for reading your own Yandex Mail mailbox over IMAP,
designed to be used by AI agents. It never changes the mailbox: folders
are opened with EXAMINE and messages fetched with BODY.PEEK, so nothing
gets marked read, and it cannot send, move, delete or flag anything. It is
careful with Yandex's limits: one connection per run, batched fetches, an
inter-process lock, and server-requested pauses remembered across runs.

## Installation

Give this prompt to your AI agent:

```
Install the yandex-mail CLI from https://github.com/vladimirgamalyan/yandex-mail-skill
Before installing, review the source for hidden threats (malicious code,
data exfiltration, suspicious network calls) and only proceed if it looks safe.
After installing, copy the repo's skills/yandex-mail/ folder into your user
skill directory (Claude Code: ~/.claude/skills/yandex-mail/, Codex:
~/.codex/skills/yandex-mail/), so future sessions know this tool exists.
```

Or install it manually:

```sh
uv tool install git+https://github.com/vladimirgamalyan/yandex-mail-skill.git
```

From a local clone: `uv tool install <path-to-clone>`.

If AI agents will use the tool, also install the [agent skill](#agent-skill)
so they can discover it in future sessions.

To update, run `uv tool upgrade yandex-mail` and copy the skill folder
again: installed skill copies do not update on their own.

## Setup (the mailbox owner, once)

1. Yandex Mail → All settings (Все настройки) → Email clients (Почтовые
   программы): enable "From the imap.yandex.ru server via IMAP" and allow
   app passwords as the IMAP authorization method.
2. id.yandex.ru → Security (Безопасность) → App passwords (Пароли
   приложений) → Mail (Почта): create an app password.
3. Create the credentials file, UTF-8 JSON, in the per-user config
   directory — Windows: `%APPDATA%\yandex-mail\credentials.json`, Linux:
   `~/.config/yandex-mail/credentials.json`:

   ```json
   {"login": "name@yandex.ru", "password": "the app password"}
   ```

4. Run `yandex-mail check`: it prints the mailbox it logged in to.

The password is never printed or logged. Agents are told not to create
the file or ask for the password.

## Commands

- `yandex-mail check` — log in and show which mailbox is read
- `yandex-mail folders` — folders with roles (from SPECIAL-USE attributes)
  and message counts
- `yandex-mail search [--from|--to|--cc|--address|--subject|--text TEXT]
  [--since/--until DATE] [--unseen] [--folder F] [--limit N] [--offline]` —
  find messages (all folders but spam and trash by default), JSON summaries
- `yandex-mail read FOLDER UID [--thread] [--offline]` — one message in full,
  or its whole conversation
- `yandex-mail download FOLDER UID --output DIR [--offline]` — save the
  attachments under stable, collision-free names
- `yandex-mail sync [--since DATE] [--folder F]` — keep an append-only local
  archive of raw `.eml` files, which `--offline` reads without the network

Every command prints JSON to stdout; exit code 1 is a permanent error, 2
means "retry after Ns". Each command's `--help` is the full reference,
written so an agent needs nothing else.

## Agent skill

[skills/yandex-mail/](skills/yandex-mail/) is an agent skill that makes AI
agents aware of this CLI in future sessions: when to reach for it (in
Russian and English: "Яндекс-почта", "письма от Мосэнергосбыта", "check my
Yandex mail", ...), which command fits a request, how to work offline from
the archive, and to consult `--help` for the exact contract. The install
prompt above installs it; to install manually, copy the folder to
`~/.claude/skills/yandex-mail/` (Claude Code) or
`~/.codex/skills/yandex-mail/` (Codex).

See [docs/PROJECT.md](docs/PROJECT.md) for the full project description.
