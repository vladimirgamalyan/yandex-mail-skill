## Language Requirements
- All code comments MUST be in English only
- All logging messages MUST be in English only
- All error messages MUST be in English only
- All docstrings MUST be in English only
- All variable names, function names, and class names MUST be in English only
- All git commit messages MUST be in English only
- Project documentation (README.md, files in docs/) MUST be in English only
- Exception: Yandex shows its settings in Russian to the mailbox owner, so
  messages and docs that walk through those settings may quote the Russian
  UI label in parentheses after its English name, e.g. "All settings
  (Все настройки)". Test data may be Russian too: decoding Russian mail is
  what the tests check.

## Code Guidelines
Follow the behavioral rules in @CODE_GUIDELINES.md

## Architecture Decision Records
Notable and debatable architecture/design decisions are logged in `adr/`
(see `adr/README.md` for the full process). In particular:
- Before proposing or re-proposing a debatable design change, check `adr/`
  for an existing decision on the topic. If one is `Accepted`, follow it
  instead of re-litigating it, unless you have genuinely new information.
- After making a debatable or architecturally significant decision, add a
  new ADR under `adr/` using `adr/template.md`.
- Never edit an old ADR's decision in place; supersede it with a new one
  instead, so the history of *why* is preserved.

## Versioning
The tool is installed straight from git, so `--version` is only truthful if
the version is actually maintained:
- Bump `version` in `pyproject.toml` (semver) in the same commit as any
  user-visible change: new command or flag, changed output schema, changed
  behavior or error contract.
- Run `uv lock` after bumping so `uv.lock` stays in sync (CI enforces this
  via `uv sync --locked`).
- Internal refactors, docs-only and test-only changes do not need a bump.


## Read-only by construction
The tool must never change the mailbox. Every server access goes through
`ReadOnlyMailbox` (`src/yandex_mail/mailbox.py`): folders are opened with
EXAMINE, message data is fetched with BODY.PEEK, and the FETCH items are
whitelisted. Do not call IMAPClient methods anywhere else (`session.py`
only connects, logs in and logs out), and do not add methods that send, store flags, copy, move, delete or expunge;
`tests/test_readonly.py` guards this.

## The real mailbox
Tests never touch the network (a fake IMAP client stands in for the
server). Do not run networked commands against the owner's real mailbox
without asking the owner first, and never print, log or commit the
credentials file.
