#!/usr/bin/env python3
"""PreToolUse guard for Bash: blocks commands that violate ENGINEERING_LAWS.md.

Contract: https://code.claude.com/docs/en/hooks.md
  stdin  JSON with cwd and tool_input.command
  exit 2 blocks the tool call, stderr is the reason; exit 0 allows it.

Design: tokenize the command into simple commands (quote-aware), then judge only
what sits at command position. Mentions inside quotes, echo arguments, and
comments are therefore never matched. The evaluator tracks the working directory
(cd, pushd, subshell scope, -C, env -C), a branch moved by a chained
switch/checkout (per directory), and worktrees created earlier in the chain, so
`cd <repo> && git commit` and `git switch main && git commit` are judged against
the right branch.

Scope: catches the spellings an agent realistically writes. It is a guard against
mistakes, not a sandbox: variable indirection (`g=git; $g revert`), aliases,
scripts, `ssh`/`watch`/`parallel` wrappers, and deliberate obfuscation are out of
scope. When the working directory or branch cannot be determined, `git commit`
and a push without a refspec are blocked (fail closed).
"""
import codecs
import json
import os
import re
import subprocess
import sys

import blocklog
import rules

PROTECTED = {"main", "master"}
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
    "time": set(),
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
PUSH_VALUE_OPTS = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}
NO_VERIFY_COMMANDS = {"commit", "push", "merge", "rebase", "cherry-pick", "am", "pull"}
# Expansions that evaluate to the current branch; resolved instead of failing closed.
CURRENT_BRANCH_QUERIES = ("$(git branch --show-current)", "`git branch --show-current`",
                          "$(git rev-parse --abbrev-ref HEAD)", "`git rev-parse --abbrev-ref HEAD`")
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
VARIABLE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
EXPANSION = re.compile(r"\$\([^()]*\)|`[^`]*`|\$\{\w+\}|\$\w+")
DECLARATIONS = {"export", "declare", "readonly", "local", "typeset"}
MAX_DEPTH = 5
MAX_VALUE_LENGTH = 4096

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


class State:
    """Working directory (None = unknown); a branch set by a chained switch/checkout and the
    directory it applies to; worktrees created in the chain; the pushd stack; saved scopes
    for ( ... ) subshells."""

    def __init__(self, directory):
        self.directory = directory
        self.previous = directory  # target of `cd -`
        self.branch = None
        self.branch_dir = None
        self.worktrees = {}
        self.vars = {}  # NAME=value assignments seen earlier in the command
        self.dirstack = []
        self.scopes = []

    def copy(self):
        clone = State(self.directory)
        clone.previous = self.previous
        clone.branch, clone.branch_dir = self.branch, self.branch_dir
        clone.worktrees = dict(self.worktrees)
        clone.vars = dict(self.vars)
        clone.dirstack = list(self.dirstack)
        return clone


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


# ------------------------------------------------------------ paths & branch

def toplevel(directory):
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=directory,
                           capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def same_repo(a, b):
    if a is None or b is None:
        return False
    if a == b:
        return True
    top = toplevel(a)
    return top is not None and top == toplevel(b)


PWD_TOKENS = ("${PWD}", "$PWD", "$(pwd)", "`pwd`")
TOPLEVEL_TOKENS = ("$(git rev-parse --show-toplevel)", "`git rev-parse --show-toplevel`")


def expand_dir_tokens(text, base):
    """Replace $PWD, $(pwd) and the repository toplevel with the directory `base`, and $HOME with the
    home directory. With an unknown `base` they become an unresolvable marker, never a guess."""
    for token in PWD_TOKENS:
        text = text.replace(token, base if base is not None else "$__UNKNOWN_DIR__")
    for token in ("${HOME}", "$HOME"):
        text = text.replace(token, os.path.expanduser("~"))
    for token in TOPLEVEL_TOKENS:
        if token in text:
            top = toplevel(base) if base is not None else None
            if top is not None:
                text = text.replace(token, top)
            elif base is None:
                text = text.replace(token, "$__UNKNOWN_DIR__")
    return text


def resolve_dir(target, base, variables=None):
    """Resolve a cd/-C target against base, expanding variables assigned earlier in the same
    command. None = unknown (an expansion that cannot be resolved)."""
    if base is None:
        return None
    t = expand_dir_tokens(expand_vars(target, variables or {}), base)
    if "$" in t or "`" in t:
        return None
    return os.path.normpath(os.path.join(base, os.path.expanduser(t)))


def expand_vars(text, variables):
    """Substitute variables assigned earlier in the same command; others stay as written."""
    def sub(m):
        name = m.group(1) or m.group(2)
        return variables[name] if name in variables else m.group(0)
    return VARIABLE.sub(sub, text)


def existing_ancestor(directory):
    """`directory` itself, or its nearest existing parent (a `mkdir x && cd x` target
    does not exist yet when the guard runs, but lives in the same repository)."""
    try:
        directory.encode("utf-8")
    except UnicodeError:
        return None
    if "\x00" in directory:
        return None  # not a valid path at all; never fall back to a parent
    d = directory
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return d or None


def branch_of(directory, git_args):
    """Current branch name; '' when detached; None when it cannot be determined."""
    directory = existing_ancestor(directory) if directory else None
    if directory is None:
        return None
    try:
        r = subprocess.run(["git", *git_args, "symbolic-ref", "--short", "-q", "HEAD"], cwd=directory,
                           capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return r.stdout.strip()
    return "" if r.returncode == 1 else None


def branch_exists(directory, git_args, name, remote=None):
    ref = f"refs/remotes/{remote}/{name}" if remote else f"refs/heads/{name}"
    try:
        r = subprocess.run(["git", *git_args, "rev-parse", "--verify", "-q", ref], cwd=directory,
                           capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def effective_branch(state, directory, git_args):
    """The branch a commit or refspec-less push would use in `directory`, accounting for
    worktrees created and branches switched earlier in the same command."""
    if directory is not None:
        for path, branch in state.worktrees.items():
            if directory == path or directory.startswith(path + os.sep):
                return branch
        if state.branch is not None and same_repo(directory, state.branch_dir):
            return state.branch
    return branch_of(directory, git_args)


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


def check_push(rest, state, directory, git_args):
    flags, positional, has_repo = [], [], False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--":
            positional.extend(rest[i + 1:])
            break
        if tok in PUSH_VALUE_OPTS:
            has_repo = has_repo or tok == "--repo"
            i += 2
            continue
        if tok.startswith("--repo="):
            has_repo = True
        if tok.startswith("-"):
            flags.append(tok)
        else:
            positional.append(tok)
        i += 1
    if "--mirror" in flags or "--all" in flags:
        raise Block(rules.PUSH_ALL, "git push --all/--mirror includes protected branches.")
    refspecs = positional if has_repo else positional[1:]
    destinations = []
    for ref in refspecs:
        ref = ref.lstrip("+")
        parts = re.split(r":(?![^{}]*\})", ref, maxsplit=1)  # a colon inside ${VAR:-x} is not a refspec colon
        dst = parts[1] if len(parts) > 1 else ref
        if dst.startswith("refs/tags/"):
            continue
        if "*" in dst:
            raise Block(rules.PUSH_WILDCARD, "wildcard push destinations can reach protected branches.")
        for prefix in ("refs/heads/", "heads/"):
            if dst.startswith(prefix):
                dst = dst[len(prefix):]
        dst = expand_vars(dst, state.vars)
        default = re.fullmatch(r"\$\{\w+:-([^}]*)\}", dst)
        if default:
            dst = default.group(1)  # assume the default applies: a protected default is suspect either way
        for query in CURRENT_BRANCH_QUERIES:
            if query in dst:
                current = effective_branch(state, directory, git_args)
                dst = dst.replace(query, current) if current is not None else dst
        if dst in ("HEAD", "@"):
            dst = effective_branch(state, directory, git_args)
        elif EXPANSION.search(dst):
            literal = EXPANSION.sub("", dst)
            if literal:
                dst = literal  # a literal part (feat/$(date)) means it is not exactly a protected name
            elif re.fullmatch(r"\$\{?\w+\}?", dst):
                continue  # a variable with no assignment in this command: environment value, out of scope
            else:
                dst = None  # a bare command substitution could be anything
        destinations.append(dst)
    if not refspecs and "--tags" not in flags:
        destinations.append(effective_branch(state, directory, git_args))
    for dst in destinations:
        if dst is None:
            raise Block(rules.PUSH_UNKNOWN_BRANCH, "cannot determine the branch being pushed (unresolved directory or expansion). "
                        "Use a literal branch name and an absolute path.")
        if dst in PROTECTED:
            raise Block(rules.PUSH_PROTECTED, f"pushing to '{dst}' is banned. The default branch only changes through merged PRs.")


def track_branch_switch(sub, rest, state, directory, git_args):
    positional, new, detach, j = [], None, False, 0
    tracking = any(t in ("-t", "--track") for t in rest)
    if sub == "checkout" and "--" in rest:
        return  # path checkout: the branch does not move
    while j < len(rest):
        tok = rest[j]
        if tok == "--":
            break
        if tok in ("-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan") and j + 1 < len(rest):
            new = rest[j + 1]
            j += 2
            continue
        if tok in ("--detach", "-d"):
            detach = True
        elif not tok.startswith("-") or tok == "-":
            positional.append(tok)
        j += 1
    if new:
        state.branch = new
    elif detach:
        state.branch = ""
    elif len(positional) == 1 and positional[0] == "-":
        state.branch = None
        return
    elif len(positional) == 1:
        name = positional[0]
        if tracking and "/" in name:
            name = name.split("/", 1)[1]  # --track origin/main creates local main
        if sub == "switch" or tracking:
            state.branch = name
        elif branch_exists(directory, git_args, name) or branch_exists(directory, git_args, name, remote="origin"):
            state.branch = name  # an existing branch, or one DWIM creates from origin/<name>
        else:
            return
    else:
        return
    state.branch_dir = directory


def track_worktree(rest, state, directory, git_args):
    """`git worktree add [-b NAME] PATH [COMMIT-ISH]`: remember the branch the new
    directory will hold, since it does not exist yet when the guard runs."""
    if not rest or rest[0] != "add":
        return
    args, positional, new, detach, j = rest[1:], [], None, False, 0
    while j < len(args):
        tok = args[j]
        if tok in ("-b", "-B", "--orphan") and j + 1 < len(args):
            new = args[j + 1]
            j += 2
            continue
        if tok in ("--detach", "-d"):
            detach = True
        elif not tok.startswith("-"):
            positional.append(tok)
        j += 1
    if not positional:
        return
    path = resolve_dir(positional[0], directory, state.vars)
    if path is None:
        return
    if new:
        branch = new
    elif detach:
        branch = ""
    elif len(positional) > 1:
        branch = positional[1] if branch_exists(directory, git_args, positional[1]) else ""
    else:
        branch = os.path.basename(path.rstrip("/"))
    state.worktrees[path] = branch


def check_git(args, state):
    directory, git_args = state.directory, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            directory = resolve_dir(args[i + 1], directory, state.vars)
            i += 2
        elif a in ("-c", "--config-env") and i + 1 < len(args):
            if "core.hookspath" in args[i + 1].lower():
                raise Block(rules.HOOKS_PATH, "changing core.hooksPath disables hooks; that is banned like --no-verify.")
            i += 2
        elif a in GIT_PATH_OPTS and i + 1 < len(args):
            git_args += [a, args[i + 1]]
            i += 2
        elif a in GIT_VALUE_OPTS:
            i += 2
        elif (a.startswith("-c") and not a.startswith("--") and len(a) > 2) or a.startswith("--config-env="):
            if "core.hookspath" in a.lower():
                raise Block(rules.HOOKS_PATH, "changing core.hooksPath disables hooks; that is banned like --no-verify.")
            i += 1
        elif a.startswith("-"):
            if a.split("=", 1)[0] in GIT_PATH_OPTS:
                git_args.append(a)
            i += 1
        else:
            break
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    try:
        check_subcommand(sub, rest, state, directory, git_args)
    except Block as block:
        block.sub = sub
        raise


def check_subcommand(sub, rest, state, directory, git_args):
    if sub == "revert":
        raise Block(rules.GIT_REVERT, "git revert is banned. Use git reset to undo commits.")
    if sub == "config" and "core.hookspath" in " ".join(rest).lower():
        raise Block(rules.HOOKS_PATH, "changing core.hooksPath disables hooks; that is banned like --no-verify.")
    if sub in NO_VERIFY_COMMANDS:
        check_no_verify(sub, rest)
    if sub == "commit" and not {"--dry-run", "-h", "--help"} & set(rest):
        branch = effective_branch(state, directory, git_args)
        if branch is None:
            raise Block(rules.COMMIT_UNKNOWN_BRANCH, "cannot determine the branch for this commit (unresolved directory). "
                        "Use an absolute path in cd/-C.")
        if branch in PROTECTED:
            raise Block(rules.COMMIT_PROTECTED, f"on protected branch '{branch}'. Every code change goes in a worktree on its own branch: "
                        "git worktree add .claude/worktrees/<slug> -b <branch> origin/<default-branch>")
    elif sub == "push" and not {"-h", "--help"} & set(rest):
        check_push(rest, state, directory, git_args)
    elif sub in ("switch", "checkout"):
        track_branch_switch(sub, rest, state, directory, git_args)
    elif sub == "worktree":
        track_worktree(rest, state, directory, git_args)


# --------------------------------------------------------------- evaluation

def strip_prefix(words, directory, variables=None):
    """Drop VAR=value, shell keywords, and wrapper commands. Returns (words, directory)."""
    i = 0
    while i < len(words):
        w = words[i]
        name = os.path.basename(w)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in KEYWORDS:
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
                if takes_value and i + 1 < len(words) and name == "env" and opt in ("-C", "--chdir"):
                    directory = resolve_dir(words[i + 1], directory, variables)
                i += 2 if takes_value else 1
            if name == "timeout":
                i += 1
        else:
            break
    return words[i:], directory


def record_assignments(words, state):
    """Remember NAME=value so a later `git push origin "$NAME"` or `cd "$NAME"` can be judged.
    $PWD, $(pwd) and the toplevel are expanded NOW: they mean the directory at assignment time,
    not whichever directory is current when the variable is used. An oversized value (a
    self-doubling `A=$A$A` chain) is dropped, so the variable counts as unassigned and fails closed."""
    for w in words:
        m = ASSIGNMENT.match(w)
        if not m:
            continue
        value = expand_dir_tokens(expand_vars(m.group(2), state.vars), state.directory)
        if len(value) > MAX_VALUE_LENGTH:
            state.vars.pop(m.group(1), None)
        else:
            state.vars[m.group(1)] = value


def run_command(words, state, depth):
    leading = []
    for w in words:
        if not ASSIGNMENT.match(w):
            break
        leading.append(w)
    words, state.directory = strip_prefix(words, state.directory, state.vars)
    if not words:
        record_assignments(leading, state)  # a bare `NAME=value` statement sets a shell variable
        return
    head = os.path.basename(words[0])
    if head in DECLARATIONS:
        record_assignments(words[1:], state)
    elif head == "unset":
        for name in words[1:]:
            state.vars.pop(name, None)  # a stale value must not outlive unset
    elif head in ("for", "select") and len(words) > 2:
        state.vars.pop(words[1], None)  # the loop variable shadows any earlier assignment
    elif head == "read":
        for name in words[1:]:
            if not name.startswith("-"):
                state.vars.pop(name, None)  # read overwrites them; forgetting too much only fails closed
    elif head in ("cd", "pushd"):
        args, k = words[1:], 0
        while k < len(args) and args[k].startswith("-") and args[k] != "-":
            k += 1
            if args[k - 1] == "--":
                break
        before = state.directory
        if head == "pushd":
            state.dirstack.append(state.directory)
        if k >= len(args):
            state.directory = os.path.expanduser("~")
        elif args[k] == "-":
            state.directory = state.previous
        else:
            state.directory = resolve_dir(args[k], state.directory, state.vars)
        state.previous = before
    elif head == "popd":
        state.directory = state.dirstack.pop() if state.dirstack else None
    elif head == "git":
        try:
            check_git(words[1:], state)
        except Block as block:
            block.argv = ["git", *words[1:]]
            raise
    elif head in SHELLS:
        for j, w in enumerate(words[1:], 1):
            if re.fullmatch(r"-[A-Za-z]*c", w):
                k = j + 2 if words[j + 1:j + 2] == ["--"] else j + 1
                if k < len(words):
                    evaluate(words[k], state.copy(), depth + 1)
                break
    elif head == "eval":
        evaluate(" ".join(words[1:]), state, depth + 1)
    elif head == "find":
        for j, w in enumerate(words):
            if w in ("-exec", "-execdir", "-ok", "-okdir"):
                inner = []
                for x in words[j + 1:]:
                    if x in (";", "+"):
                        break
                    inner.append(x)
                run_command(inner, state.copy(), depth + 1)


def evaluate(text, state, depth=0):
    if depth > MAX_DEPTH:
        raise Unparseable("nesting too deep")
    for item in tokenize(strip_heredocs(text)):
        if isinstance(item, tuple):
            evaluate(item[1], state.copy(), depth + 1)
        elif item == ["("]:
            state.scopes.append((state.directory, state.branch, state.branch_dir, dict(state.vars)))
        elif item == [")"]:
            if state.scopes:
                state.directory, state.branch, state.branch_dir, state.vars = state.scopes.pop()
        else:
            run_command(item, state, depth)


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
        evaluate(command, State(cwd))
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
