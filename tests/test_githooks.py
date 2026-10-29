"""Tests for githooks/pre-commit and githooks/pre-push in scratch repositories.

The hooks are activated the way a user does it: core.hooksPath set to the absolute
githooks directory. Run: python3 -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HOOKS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "githooks"))
IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=check)


class GitHooksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="githooks-test-")
        cls.remote = os.path.join(cls.root, "remote.git")
        git(cls.root, "init", "-q", "--bare", "-b", "main", cls.remote)
        cls.work = os.path.join(cls.root, "work")
        git(cls.root, "clone", "-q", cls.remote, cls.work)
        git(cls.work, "checkout", "-q", "-b", "main")
        git(cls.work, *IDENT, "commit", "-q", "--allow-empty", "-m", "seed")
        git(cls.work, "push", "-q", "origin", "main")
        git(cls.work, "config", "core.hooksPath", HOOKS)
        git(cls.work, "checkout", "-q", "-b", "feature")
        git(cls.work, *IDENT, "commit", "-q", "--allow-empty", "-m", "i")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_commit_on_main_is_refused(self):
        git(self.work, "checkout", "-q", "main")
        try:
            r = git(self.work, *IDENT, "commit", "--allow-empty", "-m", "x", check=False)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("pre-commit: refusing", r.stderr)
        finally:
            git(self.work, "checkout", "-q", "feature")

    def test_merge_commit_on_main_is_refused(self):
        git(self.work, "checkout", "-q", "main")
        try:
            r = git(self.work, *IDENT, "merge", "--no-ff", "-m", "m", "feature", check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("refusing", r.stderr)
        finally:
            git(self.work, "merge", "--abort", check=False)
            git(self.work, "checkout", "-q", "feature")

    def test_am_commit_on_main_is_refused(self):
        git(self.work, "checkout", "-q", "-b", "patchsrc", "main")
        path = os.path.join(self.work, "p.txt")
        with open(path, "w") as handle:
            handle.write("p\n")
        git(self.work, "add", "p.txt")
        git(self.work, *IDENT, "commit", "-q", "-m", "patch")
        patch = git(self.work, "format-patch", "-1", "--stdout").stdout
        git(self.work, "checkout", "-q", "main")
        patch_file = os.path.join(self.root, "p.patch")
        with open(patch_file, "w") as handle:
            handle.write(patch)
        try:
            r = git(self.work, *IDENT, "am", patch_file, check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("refusing", r.stderr + r.stdout)
        finally:
            git(self.work, "am", "--abort", check=False)
            git(self.work, "checkout", "-q", "feature")
            git(self.work, "branch", "-q", "-D", "patchsrc")

    def test_commit_on_feature_is_allowed(self):
        r = git(self.work, *IDENT, "commit", "--allow-empty", "-m", "x", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_push_to_main_and_master_is_refused(self):
        for dest in ("HEAD:main", "HEAD:master", "HEAD:refs/heads/main", ":main"):
            with self.subTest(dest=dest):
                r = git(self.work, "push", "origin", dest, check=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("pre-push: refusing", r.stderr)

    def test_push_of_feature_branch_is_allowed(self):
        r = git(self.work, "push", "origin", "feature", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
