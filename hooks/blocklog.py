"""Append-only block log for the guard.

Location: ${CLAUDE_PLUGIN_DATA}/blocks.jsonl. Claude Code exports that variable to hook
processes (hooks reference, "Command hook fields"), and the directory is outside any
repository. If the variable is unset or anything fails, logging is skipped: a log
failure must never change a block decision.

One JSON object per line. `block` entries are written by the guard; `mark` entries are
written by report.py to record a verdict on an earlier block.

Privacy: the log records only the words of the blocked git invocation, never the rest of
the command line, so secrets in chained commands (curl, docker, exports) cannot reach it.
Those words are redacted token by token and capped; the cwd is its basename.
"""
import json
import os
import re
import secrets
import stat
import time

LOG_NAME = "blocks.jsonl"
CMD_LIMIT = 200
MAX_TOKENS = 16
TOKEN_LIMIT = 120  # bounds regex work per token

SECRET_WORD = r"(?:pass(?:word|wd)?|pwd|token|secret|key|auth|credential)"
# Applied to one token at a time, in order. Each replaces only the secret part.
REDACTIONS = (
    (re.compile(r"(://)[^/\s@]+@"), r"\1<redacted>@"),
    (re.compile(r"(?i)^([\w.-]*" + SECRET_WORD + r"[\w.-]*=).*$"), r"\1<redacted>"),
    (re.compile(r"(?:gh[pousr]_|github_pat_|sk[-_]|xox[abprs]-|AKIA|ASIA|AIza|glpat-|hvs\.|pypi-|npm_)[\w.-]{8,}"),
     "<redacted>"),
    (re.compile(r"[A-Za-z0-9+/_=-]{32,}"), "<redacted>"),
)


def log_path():
    directory = os.environ.get("CLAUDE_PLUGIN_DATA")
    return os.path.join(directory, LOG_NAME) if directory else None


def redact_token(token, home):
    token = token[:TOKEN_LIMIT]
    if any(c.isspace() for c in token):
        return "<text>"  # a message or header value: free text is never kept
    if home and token.startswith(home):
        token = "~" + token[len(home):]
    for pattern, replacement in REDACTIONS:
        token = pattern.sub(replacement, token)
    return token


def redact(argv):
    home = os.path.expanduser("~").rstrip("/")  # "" when HOME is "/": nothing to abbreviate
    return " ".join(redact_token(t, home) for t in argv[:MAX_TOKENS])[:CMD_LIMIT]


def record(rule, sub, cwd, argv):
    """Append a block entry. Never raises. `argv` is the blocked git invocation's words, or None."""
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
            "cmd": redact([str(t) for t in argv]) if argv else None,
        }
        line = json.dumps(entry, ensure_ascii=True) + "\n"
        # O_NONBLOCK: a FIFO with no reader fails to open instead of hanging the guard.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK, 0o600)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.write(fd, line.encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        return
