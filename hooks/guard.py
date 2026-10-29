#!/usr/bin/env python3
"""PreToolUse guard for Bash: blocks the commands a git hook cannot see.

Contract: https://code.claude.com/docs/en/hooks.md
  stdin  JSON with tool_input.command
  exit 2 blocks the tool call, stderr is the reason; exit 0 allows it.

Scope: three bans only. `git revert`, `--no-verify` (including `commit -n`, `am -n`,
abbreviations), and changing `core.hooksPath`. Protection of the default branch is the
job of the git hooks in githooks/, which see the real branch and the real remote refs.

Design: tokenize the command into simple commands (quote-aware), then judge only
what sits at command position. Mentions inside quotes, echo arguments, comments and
heredoc bodies are therefore never matched. Out of scope: variable indirection
(`g=git; $g revert`), aliases, scripts, `ssh`/`watch`/`parallel` wrappers, and
deliberate obfuscation. A command that mentions git and cannot be parsed is blocked
(fail closed).
"""
import codecs
import json
import os
import re
import sys

import blocklog
import rules

SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "fish"}
# Shell keywords that precede a command: `if x; then CMD`, `{ CMD; }`, `! CMD`.
KEYWORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "}", "fi", "done", "esac", "coproc"}
# Wrapper commands and the options that take a value (so the value is not read as the command).
WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "-D", "--chdir", "-r", "-t"},
    "command": set(),
    "exec": {"-a"},
    "builtin": set(),
    "nohup": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "setsid": set(),
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a", "--max-args", "--replace", "--max-lines",
              "--max-procs", "--delimiter", "--eof", "--max-chars", "--arg-file"},
}
GIT_VALUE_OPTS = {"--namespace", "--super-prefix", "--exec-path"}
GIT_PATH_OPTS = {"--git-dir", "--work-tree"}
# Per-subcommand short-option letters that carry a value (attached, or the next token).
NEXT_TOKEN_LETTERS = {"commit": "mFCct", "am": ""}
ATTACHED_LETTERS = {"commit": "Su", "am": "pCS"}
COMMIT_VALUE_LONG = {"--message", "--file", "--reuse-message", "--reedit-message",
                     "--author", "--date", "--template", "--cleanup", "--fixup", "--squash"}
NO_VERIFY_COMMANDS = {"commit", "push", "merge", "rebase", "cherry-pick", "am", "pull"}
MAX_DEPTH = 5

SHELL_BEFORE_HEREDOC = re.compile(
    r"(?:^|[;&|(`]|\$\()\s*(?:[\w./-]*/)?(?:bash|sh|zsh|dash|ksh|ash)\b[^;&|]*$")
HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([^\s'\"<>;&|()]+)\2")


class Unparseable(Exception):
    pass


class Block(Exception):
    """A rule violation. `rule` is a rules.Rule; `sub` is the git subcommand and `argv` the git
    invocation's words (the only part of the command the block log records)."""

    def __init__(self, rule, detail):
        super().__init__(detail)
        self.rule = rule
        self.sub = None
        self.argv = None

# ---------------------------------------------------------------- tokenizing

def strip_heredocs(text):
    """Drop heredoc bodies; they are data. Keep them when the target is a shell
    (`bash <<EOF`), because the shell executes the body. `<<<` and quoted `<<` are
    not heredocs. Command substitutions start a fresh quoting context, so the
    `"$(cat <<'EOF' ... EOF)"` commit-message form works. Comments are copied
    verbatim so an apostrophe in one does not flip the quote state."""
    out, line = [], []
    pending = []
    quote = None
    contexts = []  # [saved_quote, paren_depth] for each open $( ... )
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\n" and pending:
            out.append("\n")
            i += 1
            for delim, dash, keep in pending:
                while i < n:
                    j = text.find("\n", i)
                    j = n if j < 0 else j
                    body = text[i:j]
                    i = min(j + 1, n)
                    if (body.strip("\t") if dash else body) == delim:
                        break
                    if keep:
                        out.append(body + "\n")
            pending, line = [], []
            continue
        if quote == "'":
            if c == "'":
                quote = None
        elif c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            line.append(text[i:i + 2])
            i += 2
            continue
        elif c == "#" and quote is None and (i == 0 or text[i - 1] in " \t\n;&|("):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j])
            line.append(text[i:j])
            i = j
            continue
        elif text.startswith("$(", i):
            contexts.append([quote, 1])
            quote = None
            out.append("$(")
            line.append("$(")
            i += 2
            continue
        elif quote is None and contexts and c in "()":
            contexts[-1][1] += 1 if c == "(" else -1
            if contexts[-1][1] == 0:
                quote = contexts.pop()[0]
        elif c == '"':
            quote = None if quote == '"' else (quote or '"')
        elif c == "'" and quote is None:
            quote = "'"
        elif (quote is None and text.startswith("<<", i) and not text.startswith("<<<", i)
              and (i == 0 or text[i - 1] != "<")):
            m = HEREDOC.match(text, i)
            if m:
                keep = bool(SHELL_BEFORE_HEREDOC.search("".join(line)))
                pending.append((m.group(3), m.group(1) == "-", keep))
                out.append(m.group(0))
                line.append(m.group(0))
                i = m.end()
                continue
        if c == "\n":
            line = []
        else:
            line.append(c)
        out.append(c)
        i += 1
    return "".join(out)


def matching_paren(text, start):
    """Index of the ')' closing the '(' just before start; quotes are skipped."""
    depth, i, n = 1, start, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise Unparseable("unterminated single quote")
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 2
                elif text.startswith("$(", i):
                    i = matching_paren(text, i + 2) + 1
                else:
                    i += 1
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise Unparseable("unbalanced $(")


def ansi_c(raw):
    try:
        return codecs.decode(raw.encode("utf-8", "surrogatepass"), "unicode_escape")
    except (UnicodeError, ValueError):
        return raw


def tokenize(text):
    """Return items in execution order: a word list (one simple command), ["("] / [")"]
    scope markers, or ("sub", source) for a command substitution (evaluated before the
    command that contains it). A substitution stays in its word as literal text, so
    `git -C $(pwd) commit` keeps `commit` as an argument of git."""
    items, cur, word = [], [], []
    in_word = drop_next = False
    i, n = 0, len(text)

    def end_word():
        nonlocal word, in_word, drop_next
        if in_word:
            if drop_next:
                drop_next = False
            else:
                cur.append("".join(word))
        word, in_word = [], False

    def end_command():
        nonlocal cur, drop_next
        end_word()
        drop_next = False
        if cur:
            items.append(cur)
        cur = []

    while i < n:
        c = text[i]
        if c == "\\":
            if text[i + 1:i + 2] == "\n":
                i += 2
                continue
            if i + 1 < n:
                word.append(text[i + 1])
                in_word = True
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise Unparseable("unterminated single quote")
            word.append(text[i + 1:j])
            in_word = True
            i = j + 1
        elif c == "$" and text.startswith("$'", i):
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise Unparseable("unterminated $'")
            word.append(ansi_c(text[i + 2:j]))
            in_word = True
            i = j + 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                elif text.startswith("$(", j):
                    k = matching_paren(text, j + 2)
                    items.append(("sub", text[j + 2:k]))
                    buf.append(text[j:k + 1])
                    j = k + 1
                elif text[j] == "`":
                    k = text.find("`", j + 1)
                    if k < 0:
                        raise Unparseable("unterminated backtick")
                    items.append(("sub", text[j + 1:k]))
                    buf.append(text[j:k + 1])
                    j = k + 1
                else:
                    buf.append(text[j])
                    j += 1
            if j >= n:
                raise Unparseable("unterminated double quote")
            word.append("".join(buf))
            in_word = True
            i = j + 1
        elif c == "#" and not in_word:
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "$" and text.startswith("$(", i):
            k = matching_paren(text, i + 2)
            items.append(("sub", text[i + 2:k]))
            word.append(text[i:k + 1])
            in_word = True
            i = k + 1
        elif c == "`":
            k = text.find("`", i + 1)
            if k < 0:
                raise Unparseable("unterminated backtick")
            items.append(("sub", text[i + 1:k]))
            word.append(text[i:k + 1])
            in_word = True
            i = k + 1
        elif c in "<>" and text[i + 1:i + 2] == "(":
            end_command()
            items.append(["("])
            i += 2
        elif c in "<>":
            if in_word and "".join(word).isdigit():
                word, in_word = [], False
            else:
                end_word()
            i += 1
            while i < n and text[i] in "<>&|":
                i += 1
            drop_next = True
        elif c == "(":
            end_command()
            items.append(["("])
            i += 1
        elif c == ")":
            end_command()
            items.append([")"])
            i += 1
        elif c in ";&|\n":
            end_command()
            i += 1
        elif c in " \t\r":
            end_word()
            i += 1
        else:
            word.append(c)
            in_word = True
            i += 1
    end_command()
    return items


# ------------------------------------------------------------------- rules

def is_no_verify(tok):
    name = tok.split("=", 1)[0]
    return name == "--no-verify" or (name.startswith("--no-v") and "--no-verify".startswith(name))


def check_no_verify(sub, rest):
    skip = False
    for tok in rest:
        if tok == "--":
            break
        if skip:
            skip = False
        elif tok.startswith("--"):
            if is_no_verify(tok):
                raise Block(rules.NO_VERIFY, "--no-verify is banned unless the user explicitly requests it.")
            skip = sub == "commit" and tok in COMMIT_VALUE_LONG
        elif sub in NEXT_TOKEN_LETTERS and re.fullmatch(r"-[A-Za-z0-9]+", tok):
            for idx, ch in enumerate(tok[1:], 1):
                if ch == "n":
                    raise Block(rules.NO_VERIFY, f"{sub} -n (--no-verify) is banned unless the user explicitly requests it.")
                if ch in NEXT_TOKEN_LETTERS[sub]:
                    skip = idx == len(tok) - 1
                    break
                if ch in ATTACHED_LETTERS[sub]:
                    break


# ------------------------------------------------------------------- rules

def check_git(args):
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-c", "--config-env") and i + 1 < len(args):
            if "core.hookspath" in args[i + 1].lower():
                raise Block(rules.HOOKS_PATH, "changing core.hooksPath disables hooks; that is banned like --no-verify.")
            i += 2
        elif a == "-C" or a in GIT_PATH_OPTS or a in GIT_VALUE_OPTS:
            i += 2
        elif (a.startswith("-c") and not a.startswith("--") and len(a) > 2) or a.startswith("--config-env="):
            if "core.hookspath" in a.lower():
                raise Block(rules.HOOKS_PATH, "changing core.hooksPath disables hooks; that is banned like --no-verify.")
            i += 1
        elif a.startswith("-"):
            i += 1
        else:
            break
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    try:
        check_subcommand(sub, rest)
    except Block as block:
        block.sub = sub
        raise


def removes_core_section(rest):
    """Dropping or renaming the core config section takes core.hooksPath with it, and renaming another
    section to `core` creates one. Covers `--remove-section`/`--rename-section` and the newer
    `remove-section`/`rename-section` subcommands: any operand named core."""
    for i, tok in enumerate(rest):
        if tok in ("--remove-section", "--rename-section", "remove-section", "rename-section"):
            if any(op.lower() == "core" for op in rest[i + 1:i + 3]):
                return True
    return False


def check_subcommand(sub, rest):
    if sub == "revert" and not {"-h", "--help"} & set(rest):
        raise Block(rules.GIT_REVERT, "git revert is banned. Use git reset to undo commits.")
    if sub == "config" and ("core.hookspath" in " ".join(rest).lower() or removes_core_section(rest)):
        raise Block(rules.HOOKS_PATH, "changing core.hooksPath disables hooks; that is banned like --no-verify.")
    if sub in NO_VERIFY_COMMANDS:
        check_no_verify(sub, rest)


# --------------------------------------------------------------- evaluation

DECLARATIONS = {"export", "declare", "typeset", "readonly", "local"}


def check_git_config_env(word):
    """`GIT_CONFIG_KEY_n=core.hooksPath` (or GIT_CONFIG_PARAMETERS) sets core.hooksPath for git."""
    if word.upper().startswith("GIT_CONFIG") and "core.hookspath" in word.lower():
        raise Block(rules.HOOKS_PATH, "setting core.hooksPath through GIT_CONFIG_* disables hooks; that is banned like --no-verify.")


def strip_prefix(words):
    """Drop VAR=value, shell keywords, and wrapper commands."""
    i = 0
    while i < len(words):
        w = words[i]
        name = os.path.basename(w)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in KEYWORDS:
            check_git_config_env(w)
            i += 1
        elif w == "function":
            i += 2
        elif name in WRAPPERS:
            i += 1
            value_opts = WRAPPERS[name]
            while i < len(words) and words[i].startswith("-") and words[i] != "-":
                if words[i] == "--":
                    i += 1
                    break
                opt = words[i]
                takes_value = opt in value_opts and "=" not in opt
                i += 2 if takes_value else 1
            if name == "timeout":
                i += 1
        else:
            break
    return words[i:]


def run_command(words, depth):
    words = strip_prefix(words)
    if not words:
        return
    head = os.path.basename(words[0])
    if head in DECLARATIONS:
        for w in words[1:]:
            check_git_config_env(w)
    elif head == "git":
        try:
            check_git(words[1:])
        except Block as block:
            block.argv = ["git", *words[1:]]
            raise
    elif head in SHELLS:
        for j, w in enumerate(words[1:], 1):
            if re.fullmatch(r"-[A-Za-z]*c", w):
                k = j + 2 if words[j + 1:j + 2] == ["--"] else j + 1
                if k < len(words):
                    evaluate(words[k], depth + 1)
                break
    elif head == "eval":
        evaluate(" ".join(words[1:]), depth + 1)
    elif head == "find":
        for j, w in enumerate(words):
            if w in ("-exec", "-execdir", "-ok", "-okdir"):
                inner = []
                for x in words[j + 1:]:
                    if x in (";", "+"):
                        break
                    inner.append(x)
                run_command(inner, depth + 1)


def evaluate(text, depth=0):
    if depth > MAX_DEPTH:
        raise Unparseable("nesting too deep")
    for item in tokenize(strip_heredocs(text)):
        if isinstance(item, tuple):
            evaluate(item[1], depth + 1)
        elif item not in (["("], [")"]):
            run_command(item, depth)


def git_prefix(command):
    """The first few words from the first `git` word on: all that can be logged of a command that
    would not parse."""
    words = command.split()
    for i, word in enumerate(words):
        if os.path.basename(word) == "git":
            return words[i:i + 4]
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"].get("command")
        cwd = payload.get("cwd")
    except (ValueError, KeyError, AttributeError, TypeError):
        print("eng-laws guard: unreadable hook payload; failing closed.", file=sys.stderr)
        blocklog.record(rules.UNREADABLE_PAYLOAD, None, None, None)
        return 2
    if not isinstance(command, str) or not command.strip():
        return 0
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    try:
        evaluate(command)
    except Block as reason:
        print(f"eng-laws guard: {reason}", file=sys.stderr)
        blocklog.record(reason.rule, reason.sub, cwd, reason.argv)
        return 2
    except Unparseable as reason:
        if re.search(r"\bgit\b", command):
            print(f"eng-laws guard: cannot parse a git command ({reason}); failing closed. "
                  "Simplify the command.", file=sys.stderr)
            blocklog.record(rules.UNPARSEABLE_GIT, None, cwd, git_prefix(command))
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
