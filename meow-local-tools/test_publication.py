import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from audit_publication import audit, git, contains_private, needles_for


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "core.hooksPath", ".unused-hooks")
        (self.root / "codex-providers.toml").write_text(
            '[[providers]]\nname="Private Route"\napi="https://private.example.invalid/v1"\nkey="private-fixture-value"', encoding="utf-8")

    def stage(self, content):
        (self.root / "README.md").write_text(content, encoding="utf-8")
        git(self.root, "add", "README.md")

    def test_safe_source_ignores_untracked_private_config(self):
        self.stage("Public documentation")
        with contextlib.redirect_stdout(io.StringIO()):
            audit(self.root, required=True)

    def test_private_name_host_and_key_blocked_without_echo(self):
        for value in ("Private Route", "private.example.invalid", "private-fixture-value"):
            self.stage("Public document " + value)
            with self.assertRaises(ValueError) as error:
                audit(self.root)
            self.assertNotIn(value, str(error.exception))

    def test_staged_content_checked_instead_of_working_file(self):
        self.stage("private-fixture-value")
        (self.root / "README.md").write_text("Safe now")
        with self.assertRaises(ValueError):
            audit(self.root)

    def test_private_file_cannot_be_force_added(self):
        self.stage("Public")
        git(self.root, "add", "codex-providers.toml")
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            audit(self.root)

    def test_old_history_is_checked(self):
        self.stage("private-fixture-value")
        git(self.root, "commit", "-qm", "Fixture")
        self.stage("Public now")
        with self.assertRaisesRegex(ValueError, "Git history"):
            audit(self.root, history=True)

    def test_missing_required_private_input_fails(self):
        self.stage("Public")
        (self.root / "codex-providers.toml").unlink()
        with self.assertRaisesRegex(ValueError, "Private configuration"):
            audit(self.root, required=True)

    def test_short_name_labels_checked_without_matching_code_words(self):
        needles = needles_for(set(), {"Example"})
        self.assertTrue(contains_private(b'{"name": "Example"}', needles))
        self.assertTrue(contains_private(b'<td>Example</td>', needles))
        self.assertFalse(contains_private(b'def example_function(): pass', needles))
