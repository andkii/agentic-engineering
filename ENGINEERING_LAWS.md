# Engineering Laws

Project-agnostic laws for all agentic engineering work. These are non-negotiable and apply to every repo that uses Claude Code or equivalent AI-assisted development tooling.

---

## Record Laws

Context that isn't written down doesn't exist for the next session.

### Work Creation

Two modes — pick the right one:

**Planned work** (features, multi-session initiatives, architecture changes): A Plane ticket must exist with problem, rationale, acceptance criteria, and a link to `docs/designs/FEATURE-NAME.md` for anything touching architecture or data schema. The ticket description is the agent brief — if incomplete, ask, and put the answer in the ticket.

**Reactive work** (prod bugs, user-reported issues, quick fixes): No Plane ticket required. Log the outcome in `docs/feedback/YYYY-MM-DD-slug.md` when done.

### Session End

Before a session closes:
- **Decisions or constraints** not obvious from the code → `docs/sessions/YYYY-MM-DD-slug.md`
- **User complaints that drove a fix** → `docs/feedback/YYYY-MM-DD-slug.md` (one paragraph: what was reported, what was done, what was deferred)
- Plane ticket state updated
- Audit `git worktree list`. Remove only clean worktrees whose exact branch HEAD is verified merged; preserve dirty, unmerged, or changed worktrees, then run `git worktree prune`.

Run `/session-close` to walk through this checklist interactively.

### Adversarial Review

Before moving any ticket from In Progress → Blocked/In Review, run `/code-review medium` and post findings as a Plane comment. Hotfixes may skip only if time-critical — document that it was skipped and why.

**`/code-review medium` is the permanent ceiling. Never use `high`, `max`, or `ultra` — they spawn multiple parallel cloud agents and consume more tokens than the work itself.**

### Cold Review

The adversarial review above must be cold. An author's context biases their judgment of their own work, so:

- **Different context**: the reviewer is a separate agent or session. The author never reviews their own diff.
- **Minimal input**: the reviewer receives only the diff and the acceptance criteria. Never the author's plan, reasoning, or session history.
- **Read-only**: the reviewer reports; it does not edit or fix.
- **Fixed output**: one line per acceptance criterion (`PASS`, `FAIL`, or `UNVERIFIABLE`) with `path:line` evidence, then one line per finding. `UNVERIFIABLE` names the command that would prove it.
- **Disputes are settled by evidence**: if the author disagrees with a finding, they cite `path:line`, not argument.

### Provenance

When claiming "this works because X" or "we decided Y", cite a doc. If no doc exists, write one before implementing.

---

## Code Quality Laws

### Code Claims Require Code Reads

Before stating anything about how code behaves — what a function does, what a field contains, what a query returns, how two components interact — read that code first. Every behavioral claim must be backed by a file and line number that was actually read in the current session.

**The rule in one line: read the code, then state what it does. Never state then read.**

This applies even when the answer seems obvious from a name or pattern. If the read contradicts the assumption, the read wins. Partial reads do not count — trace the full path before claiming to understand it.

### Declarative Before Imperative

Before writing a third instance of any repeating imperative pattern (hardcoded labels, scattered conditionals, parallel if/else blocks for variants), stop and define an abstraction: a registry entry, a dataclass, a dispatch table. The threshold is **3 instances** — not "many". Write the abstraction in the design doc first; only then implement. Features built without a declared structure accrue immediate tech debt and will require a structural refactor before they can grow.

### Clear Debt In-Band

When a code review or inspection surfaces a finding that is **directly related to the current change** (touches files already in the diff) and the fix is **bounded in scope** (small and self-contained), fix it in the same PR — do not defer it. "We'll clean this up later" is how debt accumulates and never gets paid.

The test: if the debt lives in a file the current PR already touches, and the fix is ≤ ~20 lines, do it now. Only defer when the fix would require a separate design decision, a new migration, or pulls in files entirely outside the current change's scope.

### No Silent Exit Paths

Every exit path from a handler, background job, or event callback must produce a log line. A `return` with no preceding log in a non-nominal path is always a bug.

This applies especially to:
- **Null checks**: subscription not found, event ID missing, record lookup returned None
- **Early returns from state guards**: duplicate event, stale event, terminal-status block
- **Worker error paths**: per-cycle failures caught and swallowed without capture

A silent 200 OK from a webhook that took no action is diagnosed by the absence of logs — which is no basis for diagnosis. Webhook handlers that returned 200 without acting have left state changes unprocessed with nothing in the logs to show it.

### Design Docs Must Specify the Data Model

For any feature involving a repeated pattern (N insight types, N notification templates, N scoring rules), the design doc must include the **concrete struct/dataclass/schema** that all instances conform to — not just prose describing intent. If the design doc lacks this, the ticket is incomplete. Stop and ask before implementing.

This is the upstream gate for the Declarative Before Imperative law. The abstraction must exist on paper before any instance is written in code.

---

## Worktrees — Mandatory for All Code Changes

**Every code change, without exception, must be made in a worktree on its own branch. Never commit to the main branch directly.**

This is non-negotiable. The reason: the user may switch the main checkout to any worktree branch at any time to review or test work. If changes land on main, they break that workflow and contaminate the main branch with unreviewed code.

- **Reactive fixes**: still need a worktree. Create one, make the change, open a PR.
- **No exceptions**: not for one-liners, not for "obvious" fixes, not for hotfixes unless the user explicitly says to push to main.

### Convention

```bash
# Fetch first — always branch from remote origin/main, not local main (local may be stale)
git fetch origin

# Create a worktree on a new branch from remote main
git worktree add .claude/worktrees/<slug> -b <branch-name> origin/main

# Create a worktree on an existing branch
git worktree add .claude/worktrees/<slug> <branch-name>

# List all active worktrees
git worktree list

# Remove when done (after merging)
git worktree remove .claude/worktrees/<slug>
git branch -d <branch-name>
```

- **Location**: always under `.claude/worktrees/<slug>/` — this directory should be gitignored.
- **Each worktree is an independent working tree**: separate `git status`, separate checkout, runs project verification independently.
- **Never `cd` into a worktree for long-running work** — spawn an agent pointing at that directory instead, so the main session stays on its branch.
- **Agents working in a worktree** must be briefed with the absolute path as their working root.

### Typical flow

```
main checkout (main — read-only for agents)
  └── agent A → .claude/worktrees/fix-foo/  [fix/foo branch → PR]
  └── agent B → .claude/worktrees/feat-bar/ [feat/bar branch → PR]
```

---

## Plane Workflow

- **Planned work**: Plane ticket required before starting (problem, rationale, AC, design doc link for arch changes).
- **Reactive work**: no ticket required — log outcome in `docs/feedback/YYYY-MM-DD-slug.md`.
- Session start: read the ticket description and any linked design docs before touching code.
- Move to **In Progress** when starting. Run `/code-review medium` and post findings before moving to **Blocked/In Review**.
- Never move to **Done** — reserved for user approval.
- Commit format: `<type>(<scope>): <description> [PLANE-###]`

---

## Testing

- **Test real behavior at real system boundaries.** Don't mock the database — mocked tests have passed while real migrations failed in production. Integration tests must hit a real DB.
- **New behavior gets a test in the same commit.** Not "we'll add tests later." If you add a code path, cover it.
- **Run the full test suite sparingly** — never in parallel, never as a development loop. Prefer targeted checks proportional to risk (single file or test, lint, build, smoke). Run the full local suite only on request or for broad/high-risk changes; pull-request CI owns the full matrix. Report the checks run and any relevant check that could not run.
- **Type checking and test suites verify code correctness, not feature correctness.** Passing tests do not mean the feature works end-to-end. Test the golden path and edge cases manually for any UI or behavioral change.

### Regression Proof

A fix is not done until a test proves it, and the proof comes from real failures, not imagined ones.

- **Failing test first.** A bug fix starts with a test built from the real failing input (captured from production, a log, or the report), sanitized of secrets and personal data. Run it and watch it fail for the reported reason, then fix, then watch it pass. Report both runs.
- **Every incident adds a case.** The input that caused the failure becomes a permanent fixture in the repo. The fixture set is the project's regression corpus.
- **Replay the corpus when the system under test changes**: a dependency upgrade, a refactor, a model or prompt change for anything LLM-driven. A rule that survives a replay with the guard removed is dead weight; delete it.
- **Acceptance criteria are checkable.** Each criterion names a command, query, or observable whose result decides pass or fail. A criterion that cannot be checked mechanically is marked `UNVERIFIABLE` and names the manual check, as in Cold Review.
- **No retry-until-green.** A test that passes on rerun is a failing test. Quarantining a flaky test requires a tracked ticket in the same PR; deleting it silently is not allowed.

---

## Operational Scripts

One-shot scripts (backfills, migrations, data fixes) must let an operator tell at a glance that work is happening and how much is left:

- **Periodic count output**: print a running tally every N rows (e.g. every 10,000). Silent scripts that only print on completion are unacceptable for anything that takes more than a few seconds.
- **Final summary line**: always print total rows changed and total elapsed time on exit.
- **Structured output**: `print(f"[{i}/{total}] ...")` or similar so progress is unambiguous.

---

## Production Diligence

- Zero tolerance for sloppy commits — every commit in a production-tracked repo deploys immediately or is one merge away from deploying.
- For prod incidents, deliver structural fixes that make the failure impossible, not manual intervention or band-aids.
- Never skip pre-commit hooks (`--no-verify`) or bypass signing unless the user explicitly requests it.
- Never use `git revert` — use `git reset` to undo commits.
- Never force-push to main/master.

---

## Resource Lifecycle

These are patterns, not law — each project overrides with its own rules. The principle is: **always use the project's established context managers and singletons. Never instantiate raw clients or sessions.**

Leaked connections and unclosed resources have caused production outages. Any new resource type (HTTP client, cache client, queue connection) must have an explicit lifecycle defined before it's used in more than one place.
