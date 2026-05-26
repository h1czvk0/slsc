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

    def test_proxy_port_candidates_skip_upstream_and_accelerator_ports(self):
        ports = sponsor_mitm._get_proxy_port_candidates(upstream_proxy="127.0.0.1:8080")
        self.assertNotIn(8080, ports)
        self.assertNotIn(7890, ports)
        self.assertIn(18080, ports)

    def test_parse_port_from_proxy_endpoint_formats(self):
        self.assertEqual(sponsor_mitm._parse_port_from_endpoint("127.0.0.1:7890"), 7890)
        self.assertEqual(sponsor_mitm._parse_port_from_endpoint("http://127.0.0.1:7891"), 7891)
        self.assertEqual(sponsor_mitm._parse_port_from_endpoint("user:pass@127.0.0.1:9090"), 9090)
        self.assertIsNone(sponsor_mitm._parse_port_from_endpoint("127.0.0.1"))

    def test_proxy_settings_match_requires_manual_proxy_without_auto_config(self):
        settings = {
            "ProxyEnable": 1,
            "ProxyServer": "http=127.0.0.1:18080;https=127.0.0.1:18080",
            "AutoConfigURL": None,
            "AutoDetect": 0,
        }
        self.assertTrue(sponsor_mitm._proxy_settings_match(settings, 18080))

        settings_with_pac = dict(settings)
        settings_with_pac["AutoConfigURL"] = "http://example.invalid/proxy.pac"
        self.assertFalse(sponsor_mitm._proxy_settings_match(settings_with_pac, 18080))

        settings_with_auto_detect = dict(settings)
        settings_with_auto_detect["AutoDetect"] = 1
        self.assertFalse(sponsor_mitm._proxy_settings_match(settings_with_auto_detect, 18080))


if __name__ == "__main__":
    unittest.main()
