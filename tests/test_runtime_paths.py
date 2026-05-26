import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import admin_helper  # noqa: E402
import slashco  # noqa: E402
import sponsor_mitm  # noqa: E402


class RuntimePathTests(unittest.TestCase):
    def test_slashco_writable_paths_use_local_data_dir(self):
        self.assertEqual(os.path.basename(slashco.DATA_DIR), "SlashCoMonitor")
        self.assertEqual(
            slashco.LOCAL_JSON_FILENAME,
            os.path.join(slashco.DATA_DIR, "locations.json"),
        )

    def test_sponsor_data_dir_defaults_to_local_data_dir(self):
        old_value = os.environ.pop(sponsor_mitm.DATA_DIR_ENV, None)
        try:
            self.assertEqual(
                os.path.basename(sponsor_mitm._get_data_dir()),
                "SlashCoMonitor",
            )
        finally:
            if old_value is not None:
                os.environ[sponsor_mitm.DATA_DIR_ENV] = old_value

    def test_sponsor_data_dir_allows_env_override(self):
        old_value = os.environ.get(sponsor_mitm.DATA_DIR_ENV)
        custom_dir = os.path.join(os.path.abspath(os.sep), "tmp", "custom-slashco-data")
        try:
            os.environ[sponsor_mitm.DATA_DIR_ENV] = custom_dir
            self.assertEqual(sponsor_mitm._get_data_dir(), custom_dir)
        finally:
            if old_value is None:
                os.environ.pop(sponsor_mitm.DATA_DIR_ENV, None)
            else:
                os.environ[sponsor_mitm.DATA_DIR_ENV] = old_value

    def test_admin_helper_debug_log_uses_local_data_dir(self):
        log_path = admin_helper.get_log_path()
        self.assertEqual(os.path.basename(os.path.dirname(log_path)), "SlashCoMonitor")
        self.assertEqual(os.path.basename(log_path), "admin_helper_debug.log")


if __name__ == "__main__":
    unittest.main()
