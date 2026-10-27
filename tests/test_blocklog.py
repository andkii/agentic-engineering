"""Tests for the guard's block log (hooks/blocklog.py, hooks/rules.py, hooks/report.py).

The log lives in ${CLAUDE_PLUGIN_DATA}/blocks.jsonl. Every test points that variable at a
temp directory. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOKS = os.path.join(os.path.dirname(__file__), "..", "hooks")
GUARD = os.path.join(HOOKS, "guard-bash.sh")
REPORT = os.path.join(HOOKS, "report.py")
sys.path.insert(0, HOOKS)

from test_guard import make_repo  # noqa: E402


def read_text(path):
    with open(path) as f:
        return f.read()


def read_log(data_dir):
    path = os.path.join(data_dir, "blocks.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class BlockLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="blocklog-test-")
        cls.main = make_repo(cls.root, "m", "main")
        cls.feat = make_repo(cls.root, "f", "feat")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="blocklog-data-")
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)

    def guard(self, cwd, command, raw=None, data="default"):
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_DATA", None)
        if data == "default":
            env["CLAUDE_PLUGIN_DATA"] = self.data
        elif data is not None:
            env["CLAUDE_PLUGIN_DATA"] = data
        payload = raw if raw is not None else json.dumps(
            {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command}})
        return subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)

    def report(self, *args):
        return subprocess.run([sys.executable, REPORT, "--file", os.path.join(self.data, "blocks.jsonl"), *args],
                              capture_output=True, text=True)

    # ------------------------------------------------------------ what is logged

    def test_each_rule_is_logged_with_its_id(self):
        cases = [
            (self.feat, "git revert HEAD", "git-revert", "revert"),
            (self.feat, "git commit -n -m x", "no-verify", "commit"),
            (self.feat, "git -c core.hooksPath=/x commit -m x", "hooks-path", None),
            (self.feat, "git push origin main", "push-protected", "push"),
            (self.feat, "git push --all", "push-all", "push"),
            (self.feat, "git push origin 'v*'", "push-wildcard", "push"),
            (self.feat, "git push origin $(whoami)", "push-unknown-branch", "push"),
            (self.main, "git commit -m x", "commit-protected", "commit"),
            (self.feat, "git -C 'a\x00b' commit -m x", "commit-unknown-branch", "commit"),
            (self.feat, "git commit -m 'unterminated", "unparseable-git", None),
        ]
        for cwd, command, rule, sub in cases:
            with self.subTest(command=command):
                before = len(read_log(self.data))
                r = self.guard(cwd, command)
                self.assertEqual(r.returncode, 2, r.stderr)
                entries = read_log(self.data)
                self.assertEqual(len(entries), before + 1)
                entry = entries[-1]
                self.assertEqual(entry["type"], "block")
                self.assertEqual(entry["rule"], rule)
                if sub is not None:
                    self.assertEqual(entry["sub"], sub)

    def test_unreadable_payload_is_logged(self):
        r = self.guard("", "", raw="not json")
        self.assertEqual(r.returncode, 2)
        entries = read_log(self.data)
        self.assertEqual([e["rule"] for e in entries], ["unreadable-payload"])

    def test_entry_fields(self):
        self.guard(self.feat, "git revert HEAD")
        (entry,) = read_log(self.data)
        self.assertEqual(set(entry), {"type", "id", "ts", "rule", "sub", "cwd", "cmd"})
        self.assertRegex(entry["id"], r"^[0-9a-f]{8}$")
        self.assertRegex(entry["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(entry["cwd"], "f")
        self.assertEqual(entry["cmd"], "git revert HEAD")

    def test_cwd_is_basename_only(self):
        self.guard(self.feat, "git revert HEAD")
        raw = read_text(os.path.join(self.data, "blocks.jsonl"))
        self.assertNotIn(self.root, raw)

    def test_allowed_command_writes_nothing(self):
        self.assertEqual(self.guard(self.feat, "git status").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.data, "blocks.jsonl")))

    def test_rule_ids_come_from_the_registry(self):
        import rules
        ids = [r.id for r in rules.RULES]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in rules.RULES:
            self.assertTrue(rule.summary)
        source = read_text(os.path.join(HOOKS, "guard.py"))
        self.assertIsNone(re.search(r"Block\(\s*f?[\"']", source), "Block() must take a registry rule, not a string")

    # ----------------------------------------------------------------- redaction

    def test_secrets_are_redacted(self):
        secrets = ["s3cretpass", "abcDEF1234567890xyz", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                   "hunter2hunter2", "AKIAABCDEFGHIJKLMNOP", "0123456789abcdef0123456789abcdef0123"]
        command = ("git revert HEAD; curl https://user:s3cretpass@host/x -H 'Authorization: Bearer abcDEF1234567890xyz' "
                   "-d token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 --password hunter2hunter2 "
                   "AWS_KEY=AKIAABCDEFGHIJKLMNOP 0123456789abcdef0123456789abcdef0123")
        self.guard(self.feat, command)
        raw = read_text(os.path.join(self.data, "blocks.jsonl"))
        for secret in secrets:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, raw)
        self.assertIn("git revert HEAD", raw)

    def test_only_first_line_is_kept_and_length_is_capped(self):
        self.guard(self.feat, "git revert HEAD\nsecond-line-body")
        self.guard(self.feat, "git revert HEAD " + "x" * 1000)
        first, second = read_log(self.data)
        self.assertNotIn("second-line-body", first["cmd"])
        self.assertLessEqual(len(second["cmd"]), 200)

    # ----------------------------------------------------------------- fail open

    def test_log_failure_never_changes_the_decision(self):
        blocker = os.path.join(self.data, "afile")
        open(blocker, "w").close()
        unwritable = os.path.join(self.data, "ro")
        os.makedirs(unwritable)
        os.chmod(unwritable, 0o500)
        self.addCleanup(os.chmod, unwritable, 0o700)
        for label, data in [("unset", None), ("path is a file", blocker), ("under a file", os.path.join(blocker, "x")),
                            ("read-only", unwritable), ("empty", "")]:
            with self.subTest(case=label):
                r = self.guard(self.feat, "git revert HEAD", data=data)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("eng-laws guard: git revert is banned", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(self.guard(self.feat, "git status", data=data).returncode, 0)

    def test_blocks_dir_is_a_directory_does_not_crash(self):
        os.makedirs(os.path.join(self.data, "blocks.jsonl"))
        r = self.guard(self.feat, "git revert HEAD")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_data_dir_is_created_on_first_block(self):
        fresh = os.path.join(self.data, "new", "dir")
        self.guard(self.feat, "git revert HEAD", data=fresh)
        self.assertEqual(len(read_log(fresh)), 1)

    # -------------------------------------------------------------------- report

    def test_report_summarizes_by_rule_and_day(self):
        path = os.path.join(self.data, "blocks.jsonl")
        rows = [
            {"type": "block", "id": "aaaaaaaa", "ts": "2026-10-20T10:00:00Z", "rule": "git-revert", "sub": "revert", "cwd": "f", "cmd": "git revert HEAD"},
            {"type": "block", "id": "bbbbbbbb", "ts": "2026-10-20T11:00:00Z", "rule": "git-revert", "sub": "revert", "cwd": "f", "cmd": "git revert x"},
            {"type": "block", "id": "cccccccc", "ts": "2026-10-21T09:00:00Z", "rule": "push-protected", "sub": "push", "cwd": "f", "cmd": "git push origin main"},
        ]
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        r = self.report("summary")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"git-revert\s+2\s+0\s+0\s+2")
        self.assertRegex(r.stdout, r"push-protected\s+1\s+0\s+0\s+1")
        self.assertRegex(r.stdout, r"2026-10-20\s+2")
        self.assertRegex(r.stdout, r"2026-10-21\s+1")
        self.assertRegex(r.stdout, r"total\s+3")

    def test_mark_false_positive_updates_summary(self):
        self.guard(self.feat, "git revert HEAD")
        (entry,) = read_log(self.data)
        r = self.report("mark", entry["id"], "--note", "was a dry run")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.report("summary").stdout
        self.assertRegex(out, r"git-revert\s+1\s+1\s+0\s+0")
        self.report("mark", entry["id"], "--verdict", "true-block")
        out = self.report("summary").stdout
        self.assertRegex(out, r"git-revert\s+1\s+0\s+1\s+0")

    def test_mark_unknown_id_fails(self):
        self.guard(self.feat, "git revert HEAD")
        r = self.report("mark", "deadbeef")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("deadbeef", r.stderr)

    def test_list_shows_ids_and_unreviewed_filter(self):
        self.guard(self.feat, "git revert HEAD")
        self.guard(self.feat, "git revert HEAD~1")
        first, second = read_log(self.data)
        self.report("mark", first["id"])
        out = self.report("list", "--unreviewed").stdout
        self.assertIn(second["id"], out)
        self.assertNotIn(first["id"], out)
        self.assertIn(first["id"], self.report("list").stdout)

    def test_report_tolerates_corrupt_lines_and_missing_file(self):
        self.assertEqual(self.report("summary").returncode, 0)
        with open(os.path.join(self.data, "blocks.jsonl"), "w") as f:
            f.write("not json\n")
        r = self.report("summary")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_report_uses_plugin_data_env_by_default(self):
        self.guard(self.feat, "git revert HEAD")
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=self.data)
        r = subprocess.run([sys.executable, REPORT, "summary"], capture_output=True, text=True, env=env)
        self.assertRegex(r.stdout, r"git-revert\s+1")


if __name__ == "__main__":
    unittest.main()
