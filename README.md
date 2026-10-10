# eng-laws

Source of truth for engineering laws that apply across all projects using AI-assisted development.

## What this is

These conventions govern how work is created, executed, reviewed, and recorded in any project where Claude Code (or equivalent agentic tools) are part of the development workflow. They are not tied to any specific stack, language, or codebase.

Each project's `CLAUDE.md` should incorporate the laws from `ENGINEERING_LAWS.md` alongside project-specific overrides (verification commands, architecture notes, resource lifecycle rules, etc.).

## What goes here vs in project CLAUDE.md

| Here (`ENGINEERING_LAWS.md`) | Per-project `CLAUDE.md` |
|---|---|
| Record Laws (session close, work tracking) | Verification command (`./scripts/check`, `pytest`, etc.) |
| Worktree workflow | Resource lifecycle rules (Redis, DB, HTTP patterns) |
| Plane ticket discipline | Architecture overview |
| Code quality laws (read-before-claim, declarative/imperative, debt-in-band) | Domain-specific key rules |
| Review gate rules | Project structure |

## Repos using these conventions

