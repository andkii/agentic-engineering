"""Regression corpus for hooks/guard-bash.sh: the three bans (git revert, --no-verify, core.hooksPath).

Every BLOCK case is a spelling that must exit 2; every ALLOW case must exit 0.
The cases come from a cold review that bypassed the first, regex-based guard.
Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

GUARD = os.path.join(os.path.dirname(__file__), "..", "hooks", "guard-bash.sh")


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


def make_repo(root, name, branch):
    path = os.path.join(root, name)
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
    if branch != "main":
        git(path, "checkout", "-q", "-b", branch)
    return path


# (cwd key, command). {MAIN} and {FEAT} expand to the temp repos' paths.
BLOCK = [
    # git revert, every spelling the review found
    ("feat", "git revert HEAD"),
    ("feat", "git -C /x revert HEAD"),
    ("feat", "git -c a=b revert HEAD"),
    ("feat", "/usr/bin/git revert HEAD"),
    ("feat", "git --no-pager revert HEAD"),
    ("feat", "(git revert HEAD)"),
    ("feat", "echo $(git revert HEAD)"),
    ("feat", 'echo "$(git revert HEAD)"'),
    ("feat", "echo `git revert HEAD`"),
    ("feat", 'git "revert" HEAD'),
    ("feat", "git --git-dir=x revert HEAD"),
    ("feat", "bash -c 'git revert HEAD'"),
    ("feat", 'eval "git revert HEAD"'),
    ("feat", "true && git revert HEAD"),
    ("feat", "true; git revert HEAD"),
    ("feat", "true | git revert HEAD"),
    ("feat", "FOO=1 git revert HEAD"),
    ("feat", "env git revert HEAD"),
    ("feat", "sudo git revert HEAD"),
    ("feat", "command git revert HEAD"),
    ("feat", "cd /tmp && git revert HEAD"),
    ("feat", "true\ngit revert HEAD"),
    # --no-verify and the -n short form
    ("feat", "git commit --no-verify -m x"),
    ("feat", "git commit -n -m x"),
    ("feat", "git commit -nm x"),
    ("feat", "git commit -am x -n"),
    ("feat", "git commit -an"),
    ("feat", "git commit --amend -n"),
    ("feat", "git -C p commit --no-verify"),
    ("feat", "git -c a=b commit --no-verify"),
    ("feat", 'git commit -m "a;b" --no-verify'),
    ("feat", 'git commit -m "fix | x" --no-verify'),
    ("feat", "git push --no-verify origin feat"),
    ("feat", "git merge --no-verify x"),
    ("feat", "git rebase --no-verify x"),
    # shell control structures: git inside a body is still judged
    ("feat", "if true; then git commit --no-verify -m x; fi"),
    ("feat", "{ git revert HEAD; }"),
    ("feat", "true && { git revert HEAD; }"),
    ("feat", "! git revert HEAD"),
    ("feat", "until false; do git revert HEAD; break; done"),
    ("feat", "if true; then :; else git revert HEAD; fi"),
    ("feat", "f() { git revert HEAD; }; f"),
    ("feat", "function f { git revert HEAD; }; f"),
    # heredoc edge cases
    ("feat", "cat <<< hi\ngit revert HEAD"),
    ("feat", "echo '<<EOF'\ngit revert HEAD"),
    ("feat", 'echo "a << b"\ngit revert HEAD'),
    ("feat", "bash <<'EOF'\ngit revert HEAD\nEOF"),
    # unquoted command substitution splits nothing: the rest of the command is still judged
    ("feat", "git commit -m $(cat msg.txt) --no-verify"),
    ("feat", "git commit -m $(date) -n"),
    ("feat", "git merge $(git branch --show-current) --no-verify"),
    # git config that disables hooks, including dropping or renaming the core section
    ("feat", "git config core.hooksPath /dev/null"),
    ("feat", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x"),
    ("feat", "env GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git push origin HEAD"),
    ("feat", "/usr/bin/time -f %e git push --no-verify origin HEAD"),
    ("feat", "time -o /tmp/t git commit -n -m x"),
    ("feat", "git config --remove-section core"),
    ("feat", "git config remove-section core"),
    ("feat", "git config rename-section core disabled"),
    ("feat", "git config rename-section include core"),
    ("feat", "git config --rename-section include core"),
    ("feat", "git config --rename-section core disabled"),
    ("feat", "git -C . config --local --remove-section CORE"),
    ("feat", "git -C . config --local core.hooksPath x"),
    # attached -c and --config-env spellings that disable hooks
    ("feat", "git -ccore.hooksPath=/dev/null commit -m x"),
    ("feat", "git --config-env=core.hooksPath=H commit -m x"),
    # other spellings that skip or disable hooks
    ("feat", "git am -3n x.patch"),
    ("main", "git am -n x.patch"),
    ("feat", "git am -n x.patch"),
    ("feat", "git -c core.hooksPath=/dev/null commit -m x"),
    ("feat", "git commit --no-verif -m x"),
    # wrappers and their options
    ("feat", "timeout -s KILL 5 git revert HEAD"),
    ("feat", "sudo --user root git revert HEAD"),
    ("feat", "ionice -c 3 git revert HEAD"),
    ("feat", "stdbuf -oL git revert HEAD"),
    ("feat", "echo HEAD | xargs git revert"),
    ("feat", "find . -exec git revert {} \\;"),
    ("feat", "bash -c -- 'git revert HEAD'"),
    # redirects glued to words
    ("feat", "2>&1 git revert HEAD"),
    ("feat", ">/tmp/x git revert HEAD"),
    ("feat", "git>/dev/null revert HEAD"),
    ("feat", "git commit -m x --no-verify>/dev/null"),
    # ANSI-C quoting
    ("feat", "git $'revert' HEAD"),
    ("feat", "git commit $'--no-verify'"),
]

ALLOW = [
    ("feat", "git revert --help"),
    ("feat", "git config --remove-section user"),
    ("feat", "git config remove-section user"),
    ("feat", "git config user.name x"),
    ("feat", "git revert -h"),
    # everyday git that must stay allowed
    ("feat", "git commit -m x"),
    ("feat", "git commit -am 'x'"),
    ("feat", "git fetch origin && git rebase origin/main"),
    ("feat", "git reset --hard origin/main"),
    ("feat", "git merge --ff-only origin/main"),
    ("main", "git status"),
    ("main", "git log --oneline"),
    ("main", "git fetch origin"),
    ("main", "git merge --ff-only origin/main"),
    # commit forms and redirects that must stay allowed
    ("feat", "git commit -m x 2>&1"),
    ("feat", "git commit -F msg.txt > /dev/null"),
    ("main", "git commit --dry-run"),
    ("main", "git commit -h"),
    ("main", "git commit --help"),
    ("feat", "git commit -uno -m x"),
    ("feat", "git commit -m x -- -n"),
    ("feat", 'git commit -m "$(echo ")")"'),
    # everyday commit/PR forms: heredoc message with apostrophes and unbalanced parens
    ("feat", "git commit -m \"$(cat <<'EOF'\nfix: it's here\n\nbody (note\nEOF\n)\""),
    ("feat", "git tag -a v1 -m \"$(cat <<'EOF'\nit's a tag\nEOF\n)\""),
    ("feat", "git add -A && git commit -m \"$(cat <<EOF\nit's\nEOF\n)\""),
    ("feat", "gh pr create --title t --body \"$(cat <<'EOF'\nit's a git body\nEOF\n)\""),
    ("feat", "# don't forget\ngit commit -F - <<'EOF'\nit's body\nEOF"),
    ("feat", "git add . # it's done\ngit commit -F - <<'EOF'\nit's body\nEOF"),
    # heredocs and redirects: data is data, here-strings are not heredocs
    ("feat", "cat <<'EOF.TXT' > f.sh\ngit revert HEAD\nEOF.TXT"),
    ("feat", "cat <<'END-X' > f.sh\ngit revert HEAD\nEND-X"),
    ("feat", "cat <<< hi"),
    ("feat", 'echo "a << b"'),
    ("feat", 'git commit -m "a << b"'),
    # strings that merely mention the banned forms
    ("feat", "echo 'git revert HEAD'"),
    ("feat", "# git revert later"),
    ("feat", "true; # git revert later"),
    ("feat", "grep -r 'git revert' ."),
    ("feat", "git log --grep='revert'"),
    ("feat", 'git commit -m "document git revert ban"'),
    ("feat", 'git commit -m "x; git revert y"'),
    ("feat", 'git commit -m "mention --no-verify"'),
    ("feat", 'git commit -m "x" # --no-verify'),
    ("feat", 'git commit -m "push --force origin main"'),
    ("feat", "git commit -m \"$(cat <<'EOF'\nuse git revert x and --no-verify\nEOF\n)\""),
    ("feat", "git revert-foo"),
    ("feat", "git reverts"),
    ("feat", "git commit-tree HEAD^{tree}"),
    ("feat", "ls -la"),
    ("feat", "python3 -c \"print('git revert')\""),
    ("feat", ""),
]


class GuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="guard-test-")
        cls.repos = {
            "main": make_repo(cls.root, "m", "main"),
            "feat": make_repo(cls.root, "f", "feat"),
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_guard(self, cwd, command, raw=None):
        payload = raw if raw is not None else json.dumps(
            {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command}})
        # the corpus must not write blocks to a real plugin data directory
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=os.path.join(self.root, "plugin-data"))
        return subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)

    def expand(self, cmd):
        return cmd.replace("{MAIN}", self.repos["main"]).replace("{FEAT}", self.repos["feat"])

    def test_block(self):
        for key, cmd in BLOCK:
            with self.subTest(cwd=key, cmd=cmd):
                r = self.run_guard(self.repos[key], self.expand(cmd))
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("eng-laws guard", r.stderr)

    def test_allow(self):
        for key, cmd in ALLOW:
            with self.subTest(cwd=key, cmd=cmd):
                r = self.run_guard(self.repos[key], self.expand(cmd))
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_fail_closed_on_malformed_payload(self):
        for raw in ["not json", "{", "[]", '{"tool_input": "x"}x']:
            with self.subTest(raw=raw):
                r = self.run_guard(self.repos["feat"], "", raw=raw)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_non_bash_payload_allowed(self):
        r = self.run_guard(self.repos["feat"], "", raw=json.dumps({"cwd": self.repos["feat"], "tool_input": {}}))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_cwd_still_applies_command_rules(self):
        raw = json.dumps({"tool_input": {"command": "git revert HEAD"}})
        self.assertEqual(self.run_guard("", "", raw=raw).returncode, 2)

    def test_unparseable_git_command_fails_closed(self):
        r = self.run_guard(self.repos["feat"], "git commit -m 'unterminated")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unparseable_non_git_command_allowed(self):
        r = self.run_guard(self.repos["feat"], "echo 'unterminated")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_string_cwd_does_not_crash(self):
        raw = json.dumps({"cwd": 5, "tool_input": {"command": "git revert HEAD"}})
        self.assertEqual(self.run_guard("", "", raw=raw).returncode, 2)


if __name__ == "__main__":
    unittest.main()
