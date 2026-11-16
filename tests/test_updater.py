import hashlib
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco_updater import (  # noqa: E402
    GITHUB_LATEST_RELEASE_API,
    UPDATE_FAILED_ENV,
    build_github_url_candidates,
    cleanup_update_temp_dir,
    create_updater_bat,
    download_update,
    fetch_latest_release,
    get_update_temp_dir,
    is_newer_version,
    parse_update_info,
    parse_version,
    remove_legacy_backup,
)


class FakeDownload:
    """Streaming response stub; ``length`` is the advertised Content-Length (None to omit)."""

    status_code = 200

    def __init__(self, body, length):
        self.body = body
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_content(self, chunk_size=1):
        yield self.body[:1]
        yield self.body[1:]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class UpdaterTests(unittest.TestCase):
    def test_parse_and_compare_versions(self):
        self.assertEqual(parse_version("v3.2.10"), (3, 2, 10))
        self.assertTrue(is_newer_version("v3.2.10", "3.2.2"))
        self.assertFalse(is_newer_version("3.2.2", "3.2.2"))
        self.assertFalse(is_newer_version("3.2.1", "3.2.2"))

    def test_github_candidates_use_mirrors_before_source(self):
        candidates = build_github_url_candidates(GITHUB_LATEST_RELEASE_API)
        self.assertGreater(len(candidates), 1)
        self.assertTrue(candidates[0].startswith("https://gh-proxy.org/"))
        self.assertEqual(candidates[-1], GITHUB_LATEST_RELEASE_API)

    def test_parse_update_info_selects_slashcosense_exe(self):
        release = {
            "tag_name": "v3.3.0",
            "html_url": "https://github.com/h1czvk0/slsc/releases/tag/v3.3.0",
            "assets": [
                {"name": "notes.txt", "browser_download_url": "https://example.invalid/notes.txt"},
                {
                    "name": "SlashCoSense_v3.3.0.exe",
                    "browser_download_url": "https://github.com/h1czvk0/slsc/releases/download/v3.3.0/SlashCoSense.exe",
                },
            ],
        }
        info = parse_update_info(release, current_version="3.2.2")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "3.3.0")
        self.assertEqual(info.asset_name, "SlashCoSense_v3.3.0.exe")

    def test_parse_update_info_ignores_same_version(self):
        release = {
            "tag_name": "v3.2.2",
            "assets": [
                {
                    "name": "SlashCoSense.exe",
                    "browser_download_url": "https://example.invalid/SlashCoSense.exe",
                }
            ],
        }
        self.assertIsNone(parse_update_info(release, current_version="3.2.2"))

    def test_fetch_latest_release_tries_mirror_before_source(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if url == GITHUB_LATEST_RELEASE_API:
                return FakeResponse(200, {"tag_name": "v3.3.0", "assets": []})
            return FakeResponse(502, text="bad gateway")

        data = fetch_latest_release(fake_get)
        self.assertEqual(data["tag_name"], "v3.3.0")
        self.assertEqual(calls[-1], GITHUB_LATEST_RELEASE_API)
        self.assertTrue(calls[0].startswith("https://gh-proxy.org/"))

    def test_updater_bat_resets_pyinstaller_environment_before_relaunch(self):
        bat_path = create_updater_bat(
            r"C:\Apps\SLSC\SlashCoSense.exe",
            r"C:\Users\tester\AppData\Local\Temp\SlashCoSenseUpdate\SlashCoSense.new.exe",
        )
        try:
            content = pathlib.Path(bat_path).read_text(encoding="utf-8")
        finally:
            cleanup_update_temp_dir()

        self.assertIn('set "PYINSTALLER_RESET_ENVIRONMENT=1"', content)
        self.assertIn('start "" /D "%APPDIR%" "C:\\Apps\\SLSC\\SlashCoSense.exe"', content)

    def test_updater_bat_overwrites_in_place_and_keeps_backup_out_of_app_dir(self):
        bat_path = create_updater_bat(
            r"C:\Users\tester\Desktop\100%\SlashCoSense.exe",
            r"C:\Temp\SlashCoSenseUpdate\SlashCoSense.new.exe",
        )
        try:
            content = pathlib.Path(bat_path).read_text(encoding="utf-8")
        finally:
            cleanup_update_temp_dir()

        # 原文件只被覆盖，不改名、不移动，桌面图标位置才不会变。
        self.assertNotIn("move ", content)
        self.assertNotIn(".old", content)
        self.assertIn('copy /b /y "%NEW%" "%OLD%"', content)
        self.assertIn(r'set "OLD=C:\Users\tester\Desktop\100%%\SlashCoSense.exe"', content)
        backup_line = next(line for line in content.splitlines() if line.startswith('set "BAK='))
        self.assertIn(get_update_temp_dir(), backup_line)
        self.assertNotIn("Desktop", backup_line)
        # 覆盖失败时还原，并带着失败标记重启，避免反复下载。
        self.assertIn('copy /b /y "%BAK%" "%OLD%"', content)
        self.assertIn(f'set "{UPDATE_FAILED_ENV}=1"', content)
        self.assertLess(content.index('del /f /q "%BAK%"'), content.index('start ""'))

    def test_parse_update_info_reads_sha256_digest(self):
        release = {
            "tag_name": "v9.0.0",
            "assets": [{
                "name": "SlashCoSense.exe",
                "browser_download_url": "https://example.invalid/SlashCoSense.exe",
                "digest": "sha256:ABCDEF",
            }],
        }
        self.assertEqual(parse_update_info(release, current_version="3.2.2").sha256, "abcdef")
        release["assets"][0]["digest"] = None
        self.assertEqual(parse_update_info(release, current_version="3.2.2").sha256, "")

    def download(self, bodies, expected_sha256=""):
        """Serve one body per candidate URL; returns (result path or error, urls tried, bytes)."""
        calls = []

        def fake_get(url, **kwargs):
            body, length = bodies[len(calls)]
            calls.append(url)
            return FakeDownload(body, length)

        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = os.path.join(tmp_dir, "new.exe")
            try:
                download_update(
                    fake_get, "https://example.invalid/app.exe", destination,
                    expected_sha256=expected_sha256,
                )
            except RuntimeError as error:
                self.assertEqual(os.listdir(tmp_dir), [])
                return error, calls, None
            return destination, calls, pathlib.Path(destination).read_bytes()

    def test_download_rejects_truncated_and_non_exe_responses_then_tries_next_url(self):
        good = b"MZ" + b"\x00" * 64
        result, calls, data = self.download([
            (good[:10], len(good)),          # 镜像提前断开
            (b"<html>error</html>", None),   # 镜像返回错误页
            (good, len(good)),
        ])
        self.assertEqual(data, good)
        self.assertEqual(len(calls), 3)

    def test_download_verifies_sha256_when_release_provides_it(self):
        good = b"MZ" + b"\x01" * 64
        tampered = b"MZ" + b"\x02" * 64
        digest = hashlib.sha256(good).hexdigest()

        result, calls, data = self.download([(tampered, len(tampered)), (good, len(good))], digest)
        self.assertEqual(data, good)
        self.assertEqual(len(calls), 2)

        candidates = len(build_github_url_candidates("https://example.invalid/app.exe"))
        error, calls, data = self.download([(tampered, len(tampered))] * candidates, digest)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("SHA-256", str(error))

    def test_remove_legacy_backup_retries_while_file_is_locked(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            exe = os.path.join(tmp_dir, "SlashCoSense.exe")
            backup = exe + ".old"
            self.assertTrue(remove_legacy_backup(exe, sleep=lambda _seconds: None))

            pathlib.Path(backup).write_bytes(b"old")
            waits = []
            real_remove = os.remove

            def locked_twice(path):
                if len(waits) < 2:
                    raise PermissionError("in use")
                real_remove(path)

            with mock.patch("slashco_updater.os.remove", side_effect=locked_twice):
                self.assertTrue(remove_legacy_backup(exe, sleep=waits.append))
            self.assertEqual(len(waits), 2)
            self.assertFalse(os.path.exists(backup))

            pathlib.Path(backup).write_bytes(b"old")
            with mock.patch("slashco_updater.os.remove", side_effect=PermissionError("in use")):
                self.assertFalse(remove_legacy_backup(exe, attempts=3, sleep=lambda _seconds: None))


if __name__ == "__main__":
    unittest.main()
