import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco_updater import (  # noqa: E402
    GITHUB_LATEST_RELEASE_API,
    build_github_url_candidates,
    cleanup_update_temp_dir,
    create_updater_bat,
    fetch_latest_release,
    is_newer_version,
    parse_update_info,
    parse_version,
)


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


if __name__ == "__main__":
    unittest.main()
