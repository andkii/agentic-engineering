"""Append-only block log for the guard.

Location: ${CLAUDE_PLUGIN_DATA}/blocks.jsonl. Claude Code exports that variable to hook
processes (hooks reference, "Command hook fields"), and the directory is outside any
repository. If the variable is unset or anything fails, logging is skipped: a log
failure must never change a block decision.

One JSON object per line. `block` entries are written by the guard; `mark` entries are
written by report.py to record a verdict on an earlier block.

Privacy: the command is stored as its first line only, with credentials redacted and
capped at CMD_LIMIT characters; the cwd is its basename. The full raw command is never
written.
"""
import json
import os
import re
import secrets
import time

LOG_NAME = "blocks.jsonl"
CMD_LIMIT = 200

# Applied in order. Each pattern replaces only the secret part.
REDACTIONS = (
    (re.compile(r"(://[^/\s:@]*:)[^@\s/]+@"), r"\1<redacted>@"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[^\s'\"]+"), r"\1 <redacted>"),
    (re.compile(r"(?i)(--?[\w-]*(?:pass(?:word|wd)?|token|secret|key|auth|credential)[\w-]*)(\s+)(?!-)[^\s'\"]+"),
     r"\1\2<redacted>"),
    (re.compile(r"(?i)([\w-]*(?:pass(?:word|wd)?|pwd|token|secret|key|auth|credential)[\w-]*=)[^\s'\"]+"),
     r"\1<redacted>"),
    (re.compile(r"\b(?:gh[pousr]_|github_pat_|sk-|xox[abprs]-|AKIA|ASIA|AIza|glpat-)[\w-]{8,}"), "<redacted>"),
    (re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b"), "<redacted>"),
)


def log_path():
    directory = os.environ.get("CLAUDE_PLUGIN_DATA")
    return os.path.join(directory, LOG_NAME) if directory else None


def redact(command):
    text = command.splitlines()[0] if command.strip() else ""
    text = text.replace(os.path.expanduser("~"), "~")
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text[:CMD_LIMIT]


def record(rule, sub, cwd, command):
    """Append a block entry. Never raises."""
    try:
        path = log_path()
        if path is None:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "type": "block",
            "id": secrets.token_hex(4),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rule": rule.id,
            "sub": sub,
            "cwd": os.path.basename(str(cwd).rstrip("/")) if isinstance(cwd, str) else None,
            "cmd": redact(command) if isinstance(command, str) else None,
        }
        line = json.dumps(entry, ensure_ascii=True) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        return
