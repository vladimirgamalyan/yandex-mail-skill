# 0002. Ship the agent skill in the repo, installed by the install prompt

Status: Accepted

## Context

The CLI is meant for AI agents, but installing it only puts a binary on
PATH: nothing tells an agent in a future session that the tool exists, that
"письма от Мосэнергосбыта" or "check my Yandex mail" means this tool, or
which command fits a request. That knowledge has to live in something the
agent loads every session — an agent skill. tg-reader faced the same
question (its ADR-0004) and chose to ship the skill in the repo.

Alternatives considered: a Claude Code plugin with a marketplace (covers
one agent only, a second distribution channel to maintain), skill text in
the README for the agent to recreate (every user gets a different skill),
a note in per-project AGENTS.md (project-scoped, while the tool is
per-user).

## Decision

We will keep the canonical skill in `skills/yandex-mail/` (an
agent-agnostic `SKILL.md` plus Codex's `agents/openai.yaml`), and the
README install prompt tells the installing agent to copy it into the user
skill directory of the running agent. The skill does not restate the CLI
contract: it defers to `--help`, which is written to be agent-sufficient,
and carries only what the CLI cannot say about itself — when to use it,
which command fits which request, how to treat credentials problems, and
the offline workflow.

## Consequences

One source of truth, versioned with the CLI. Installed copies do not
update on `uv tool upgrade`; accepted because the volatile part (the
contract) lives in `--help`.
