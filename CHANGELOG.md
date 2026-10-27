# Changelog

## Unreleased

- Laws: add Regression Proof (failing test first, incident corpus, replay on change, checkable acceptance criteria, no retry-until-green). Law text only; no hook or agent behavior change, so no version bump.

## 0.1.0

- Plugin scaffold: `.claude-plugin/plugin.json`, `marketplace.json`.
- `hooks/guard-bash.sh`: blocks `git revert`, `--no-verify`, force-push to main/master, commit/push on main/master.
- `agents/verifier.md`: read-only acceptance-criteria evaluator.
- Laws: add Cold Review, generalize the webhook example, add worktree audit to Session End, Operational Scripts section, align Testing with CI-owns-full-suite.
