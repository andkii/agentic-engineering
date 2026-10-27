# Changelog

## 0.1.1

Fixes from three cold reviews of 0.1.0 and of the rewrites. The first bypassed the guard in many ways and found false positives; the second found control-structure, redirect, heredoc and directory-tracking gaps; the third (realistic payloads only) found that the common `git commit -m "$(cat <<'EOF' ... EOF)"` form was blocked when the message held an apostrophe, plus unquoted `$(...)` handling and per-directory branch tracking.

- `hooks/guard.py` (new): replace the regex guard with a tokenizing evaluator that tracks the working directory and chained branch switches. Covers `git -C`/`-c`/`--git-dir`, absolute git path, `commit -n` and `am -n`, `-c core.hooksPath`, `bash -c`, `eval`, `find -exec`, `xargs`/`sudo`/`env`/`timeout` wrappers, `if`/`for`/`while`/`case` bodies, `{ }`, functions, subshell scope, `cd` options and variable targets, redirects, here-strings, ANSI-C quoting, refspec forms (`+main`, `HEAD:main`, `@`, `heads/main`, `--repo`, `--all`, wildcards). Quoted text, `echo`, comments and heredoc bodies (including heredocs inside `"$(...)"`, apostrophes in them or in comments) no longer trigger it. Unquoted `$(...)` and backticks stay part of their word, so they no longer split a command. Branch tracking is per directory and sees `git worktree add` earlier in the chain; `git push --tags` and `git push origin "$(git branch --show-current)"` are understood. A fourth review (realistic payloads, about 550 run) found no realistic bypass; its one realism-3 finding, a false positive on `BRANCH=x; git push origin "$BRANCH"`, is fixed by tracking top-level same-command variable assignments (a protected name reaching a push through one is still blocked; the README lists the forms not followed). A fifth, delta-only review (281 payloads) returned SHIP with nothing at realism 4 or above. Also: `cd -`, `mkdir x && cd x` under a repo, DWIM/`--track` checkouts, attached `-ccore.hooksPath=` and `--config-env=`, `git config core.hooksPath`, a protected `${VAR:-default}` push destination, and `git push --help`. Known friction is listed in the README.
- `hooks/guard-bash.sh`: thin wrapper that fails closed (missing `python3`, internal error). Drops the undocumented `jq` dependency, whose absence silently disabled the guard.
- Behavior change: any push whose destination is `main`/`master` is now blocked, not only forced ones. When the directory or branch is unknown, `git commit` and a refspec-less push are blocked.
- `tests/test_guard.py` (new): regression corpus from both reviews. Out-of-scope cases are listed in the README.
- `agents/verifier.md`: state that it has no git or shell, so runtime criteria are `UNVERIFIABLE` by design; allow a one-line fix on FAIL; `UNVERIFIABLE` names a command or a manual check, matching the laws.
- Manifests: remove the claim of "ticket workflow skills" that does not exist.

## 0.1.0

- Plugin scaffold: `.claude-plugin/plugin.json`, `marketplace.json`.
- `hooks/guard-bash.sh`: blocks `git revert`, `--no-verify`, force-push to main/master, commit/push on main/master.
- `agents/verifier.md`: read-only acceptance-criteria evaluator.
- Laws: add Cold Review, generalize the webhook example, add worktree audit to Session End, Operational Scripts section, align Testing with CI-owns-full-suite.
