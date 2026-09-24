import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from dashboard import detector_command
from fetch_upstream import extract_verified


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def bundle(self, extra=None):
        archive = self.root / "official.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name in ("launch.py", "start.sh", "LICENSE"):
                bundle.writestr("release/" + name, "fixture")
            if extra:
                bundle.writestr(extra, "fixture")
        return archive, "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_verified_package_layout(self):
        archive, digest = self.bundle()
        extract_verified(archive, digest, self.root / "install")
        self.assertTrue((self.root / "install/launch.py").is_file())

    def test_digest_mismatch_does_not_install(self):
        archive, _ = self.bundle()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            extract_verified(archive, "sha256:" + "0" * 64, self.root / "install")
        self.assertFalse((self.root / "install").exists())

    def test_refuse_existing_installation(self):
        archive, digest = self.bundle()
        destination = self.root / "install"
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            extract_verified(archive, digest, destination)

    def test_zip_traversal_rejected(self):
        archive, digest = self.bundle("../outside.txt")
        with self.assertRaisesRegex(ValueError, "Unsafe archive"):
            extract_verified(archive, digest, self.root / "install")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_windows_source_import_path_and_portable_isolation(self):
        with patch("dashboard.os.name", "nt"):
            self.assertNotIn("-I", detector_command(self.root))
            (self.root / "python").mkdir()
            (self.root / "python/python.exe").touch()
            self.assertIn("-I", detector_command(self.root))
