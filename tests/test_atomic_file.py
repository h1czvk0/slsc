import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from atomic_file import atomic_open


class AtomicFileTests(unittest.TestCase):
    def test_successful_write_replaces_file_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "settings.json"
            path.write_text("old", encoding="utf-8")
            with atomic_open(path) as file:
                file.write("新配置")
                self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual(path.read_text(encoding="utf-8"), "新配置")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_write_preserves_previous_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "settings.json"
            path.write_bytes(b"old")
            with self.assertRaises(ValueError):
                with atomic_open(path) as file:
                    file.write("partial")
                    raise ValueError("serialization failed")
            self.assertEqual(path.read_bytes(), b"old")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_replace_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "image.webp"
            path.write_bytes(b"old")
            with patch("atomic_file.os.replace", side_effect=PermissionError), self.assertRaises(PermissionError):
                with atomic_open(path, "wb") as file:
                    file.write(b"new")
            self.assertEqual(path.read_bytes(), b"old")
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
