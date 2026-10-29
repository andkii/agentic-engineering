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
UNPARSEABLE_GIT = Rule("unparseable-git", "git command could not be parsed (fail closed)")
UNREADABLE_PAYLOAD = Rule("unreadable-payload", "hook payload could not be read (fail closed)")

RULES = (GIT_REVERT, NO_VERIFY, HOOKS_PATH, UNPARSEABLE_GIT, UNREADABLE_PAYLOAD)
