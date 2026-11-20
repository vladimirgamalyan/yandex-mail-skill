# 0004. Credentials in a file the owner creates by hand

Status: Accepted

## Context

IMAP access to Yandex needs a login and an app password. tg-reader has an
interactive `auth` command; here the request was for a file the owner
creates, outside the repository, so that agents never see or type the
password. Alternatives: an interactive `auth` command that prompts and
stores the password (agents tend to run it, or ask the user to paste the
password into the chat), the OS keyring (an extra dependency and harder
for the owner to inspect or fix), OAuth (a registered application and a
token refresh flow, out of proportion for reading one's own mailbox).

## Decision

We will read `credentials.json` (`{"login": ..., "password": ...}`) from
the per-user config directory (`%APPDATA%\yandex-mail\` on Windows). The
tool never writes it. The command that verifies the setup is `check`, not
`auth`: it only logs in and prints the account. Messages about a missing
or broken file name its path and format, never its content, and tell
agents not to create it or ask for the password. The file is read as
UTF-8 with an optional byte order mark (Windows Notepad writes one).

## Consequences

- The password sits in a plain file in the user profile, protected only by
  the profile's permissions (on POSIX the directory is made 0700). Accepted:
  an app password only grants mail access and can be revoked in one click.
- The owner has to create a JSON file by hand; `check --help`, the README
  and the error messages spell out the exact steps.
