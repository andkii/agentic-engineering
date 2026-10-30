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

    def test_only_the_blocked_git_invocation_is_logged(self):
        """Secrets in other commands of the same line never reach the log (cold review, realism 4-5)."""
        chained = [
            'export PASSWORD="hunter2pass" && git revert HEAD',
            "API_TOKEN=\"abc123tokenvalue\" ./run.sh; git revert HEAD",
            "curl -H 'X-Api-Key: sekret_value_12345' -u admin:hunter2pass x && git revert HEAD",
            "curl -d '{\"password\":\"hunter2pass\"}' https://x; git revert HEAD",
            "docker login -u me -p s3cretpw registry.io && git revert HEAD",
            "mysql -u root -pdbpass123 db; git revert HEAD",
            "echo hunter2pass | gh secret set FOO && git revert HEAD",
            "cd /w\ngit revert HEAD",
            "\n\ngit revert HEAD",
        ]
        for command in chained:
            with self.subTest(command=command):
                r = self.guard(self.feat, command)
                self.assertEqual(r.returncode, 2, r.stderr)
                entry = read_log(self.data)[-1]
                self.assertEqual(entry["cmd"], "git revert HEAD")

    def test_secrets_inside_the_git_command_are_redacted(self):
        cases = [
            ("git push --no-verify https://tok3n123456secret@github.com/o/r main", "tok3n123456secret"),
            ("git push --no-verify https://user:hunter2pass@github.com/o/r main", "hunter2pass"),
            ("git -c http.extraHeader='Authorization: Bearer abcDEF1234567890' revert HEAD", "abcDEF1234567890"),
            ("git -c credential.token=hunter2pass revert HEAD", "hunter2pass"),
            ("git commit -n -m 'rotate password hunter2pass'", "hunter2pass"),
            ("git revert HEAD ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdef"),
            ("git revert HEAD sk_live_abcdefghijklmnop1234", "sk_live_abcdef"),
            ("git revert HEAD hvs.CAESIabcdefghijklmnop", "CAESIabcdef"),
        ]
        for command, secret in cases:
            with self.subTest(command=command):
                self.assertEqual(self.guard(self.feat, command).returncode, 2)
                raw = read_text(os.path.join(self.data, "blocks.jsonl"))
                self.assertNotIn(secret, raw)

    def test_unparseable_logs_only_a_short_git_prefix(self):
        self.guard(self.feat, "echo hunter2pass; git commit -m 'unterminated secret=hunter2pass")
        (entry,) = read_log(self.data)
        self.assertEqual(entry["rule"], "unparseable-git")
        self.assertNotIn("hunter2pass", json.dumps(entry))
        self.assertTrue(entry["cmd"].startswith("git commit"))

    def test_slow_input_does_not_delay_the_guard(self):
        import time
        for command in ["git revert HEAD && echo " + "A" * 20000, "git revert " + "A" * 1000000]:
            with self.subTest(size=len(command)):
                start = time.monotonic()
                r = self.guard(self.feat, command)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertLess(time.monotonic() - start, 5)

    def test_fifo_at_log_path_does_not_hang(self):
        os.mkfifo(os.path.join(self.data, "blocks.jsonl"))
        r = subprocess.run([GUARD], input=json.dumps({"cwd": self.feat, "tool_input": {"command": "git revert HEAD"}}),
                           capture_output=True, text=True, timeout=5,
                           env=dict(os.environ, CLAUDE_PLUGIN_DATA=self.data))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_home_of_slash_does_not_mangle_the_command(self):
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=self.data, HOME="/")
        subprocess.run([GUARD], input=json.dumps({"cwd": self.feat, "tool_input": {"command": "git revert HEAD /a/b"}}),
                       capture_output=True, text=True, env=env)
        self.assertEqual(read_log(self.data)[-1]["cmd"], "git revert HEAD /a/b")

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

    def test_report_tolerates_wrong_shaped_and_non_utf8_entries(self):
        good = {"type": "block", "id": "aaaaaaaa", "ts": "2026-10-20T10:00:00Z", "rule": "git-revert", "sub": "revert",
                "cwd": "f", "cmd": "git revert HEAD"}
        with open(os.path.join(self.data, "blocks.jsonl"), "wb") as f:
            f.write(b"\xff\xfe\n")
            f.write(json.dumps({"type": "block", "id": ["l"]}).encode() + b"\n")
            f.write(json.dumps({"type": "block", "id": "a1", "ts": None, "rule": None}).encode() + b"\n")
            f.write(json.dumps(good).encode())  # no trailing newline
        for args in (["summary"], ["list"], ["list", "--unreviewed"]):
            with self.subTest(args=args):
                r = self.report(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.report("mark", "aaaaaaaa").returncode, 0)
        self.assertRegex(self.report("summary").stdout, r"git-revert\s+1\s+1\s+0\s+0")

    def test_report_handles_unreadable_log_and_odd_verdicts(self):
        path = os.path.join(self.data, "blocks.jsonl")
        os.makedirs(path)  # a directory where the log should be
        r = self.report("summary")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        os.rmdir(path)
        good = {"type": "block", "id": "aaaaaaaa", "ts": "2026-10-20T10:00:00Z", "rule": "git-revert"}
        with open(path, "w") as f:
            f.write(json.dumps(good) + "\n")
            f.write(json.dumps({"type": "mark", "id": "aaaaaaaa", "verdict": [1]}) + "\n")
            f.write("[" * 100000 + "\n")
        for args in (["summary"], ["list"]):
            with self.subTest(args=args):
                r = self.report(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_mark_on_unwritable_log_reports_an_error(self):
        self.guard(self.feat, "git revert HEAD")
        (entry,) = read_log(self.data)
        path = os.path.join(self.data, "blocks.jsonl")
        os.chmod(path, 0o400)
        r = self.report("mark", entry["id"])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_report_uses_plugin_data_env_by_default(self):
        self.guard(self.feat, "git revert HEAD")
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=self.data)
        r = subprocess.run([sys.executable, REPORT, "summary"], capture_output=True, text=True, env=env)
        self.assertRegex(r.stdout, r"git-revert\s+1")


if __name__ == "__main__":
    unittest.main()
