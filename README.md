# eng-laws

Source of truth for engineering laws that apply across all projects using AI-assisted development.

## What this is

These conventions govern how work is created, executed, reviewed, and recorded in any project where Claude Code (or equivalent agentic tools) are part of the development workflow. They are not tied to any specific stack, language, or codebase.

Each project's `CLAUDE.md` incorporates the laws from `ENGINEERING_LAWS.md` via Claude Code's native `@import` syntax, alongside project-specific overrides (verification commands, architecture notes, resource lifecycle rules, etc.).

## How to use in a project

Claude Code supports `@path/to/file.md` imports in `CLAUDE.md`. Add this repo as a git submodule at `.eng-laws/` and reference it at the top of the project's `CLAUDE.md`:

```markdown
@.eng-laws/ENGINEERING_LAWS.md

# Project-specific overrides below
```

Or, if both repos live under the same parent directory (e.g. `~/dev/`):

```markdown
@../eng-laws/ENGINEERING_LAWS.md
```

The laws are inlined at read time — no copying, no drift.

## What goes here vs in project CLAUDE.md

| Here (`ENGINEERING_LAWS.md`) | Per-project `CLAUDE.md` |
|---|---|
| Record Laws (session close, work tracking) | Verification command (`./scripts/check`, `pytest`, etc.) |
| Worktree workflow | Resource lifecycle rules (Redis, DB, HTTP patterns) |
| Plane ticket discipline | Architecture overview |
| Code quality laws (read-before-claim, declarative/imperative, debt-in-band) | Domain-specific key rules |
| Review gate rules | Project structure |

## Repos using these conventions

