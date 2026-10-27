"""Registry of guard rules: the one place a block reason gets its id.

The block log and the report group by `Rule.id`, so ids are stable data, never ad hoc
strings in the guard. Add a rule here first, then raise `Block(RULE)` in guard.py.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    summary: str


GIT_REVERT = Rule("git-revert", "git revert is banned; use git reset")
NO_VERIFY = Rule("no-verify", "--no-verify / commit -n / am -n skips hooks")
HOOKS_PATH = Rule("hooks-path", "changing core.hooksPath disables hooks")
PUSH_PROTECTED = Rule("push-protected", "push whose destination is main/master")
PUSH_ALL = Rule("push-all", "git push --all/--mirror includes protected branches")
PUSH_WILDCARD = Rule("push-wildcard", "wildcard push destination can reach protected branches")
PUSH_UNKNOWN_BRANCH = Rule("push-unknown-branch", "push destination could not be determined (fail closed)")
COMMIT_PROTECTED = Rule("commit-protected", "git commit while on main/master")
COMMIT_UNKNOWN_BRANCH = Rule("commit-unknown-branch", "commit branch could not be determined (fail closed)")
UNPARSEABLE_GIT = Rule("unparseable-git", "git command could not be parsed (fail closed)")
UNREADABLE_PAYLOAD = Rule("unreadable-payload", "hook payload could not be read (fail closed)")

RULES = (GIT_REVERT, NO_VERIFY, HOOKS_PATH, PUSH_PROTECTED, PUSH_ALL, PUSH_WILDCARD, PUSH_UNKNOWN_BRANCH,
         COMMIT_PROTECTED, COMMIT_UNKNOWN_BRANCH, UNPARSEABLE_GIT, UNREADABLE_PAYLOAD)
