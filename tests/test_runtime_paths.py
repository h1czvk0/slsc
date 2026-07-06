import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_detect_upstream_proxy_normalizes_proxy_server(self):
        settings = {
            "ProxyEnable": 1,
            "ProxyServer": "http=http://127.0.0.1:7890;https=http://127.0.0.1:7890",
        }
        upstream, original = sponsor_mitm._detect_upstream_proxy(settings)
        self.assertEqual(upstream, "http://127.0.0.1:7890")
        self.assertIs(original, settings)

        settings["ProxyServer"] = "http=127.0.0.1:7891;https=127.0.0.1:7891"
        upstream, _ = sponsor_mitm._detect_upstream_proxy(settings)
        self.assertEqual(upstream, "http://127.0.0.1:7891")

    def test_socks_only_proxy_is_not_used_as_http_upstream(self):
        settings = {
            "ProxyEnable": 1,
            "ProxyServer": "socks=127.0.0.1:1080",
        }
        upstream, _ = sponsor_mitm._detect_upstream_proxy(settings)
        self.assertIsNone(upstream)

    def test_stale_local_proxy_detection_skips_accelerator_and_live_ports(self):
        accelerator_settings = {
            "ProxyEnable": 1,
            "ProxyServer": "http=127.0.0.1:7890;https=127.0.0.1:7890",
        }
        self.assertFalse(sponsor_mitm._looks_like_stale_local_proxy(accelerator_settings))

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            live_port = sock.getsockname()[1]
            live_settings = {
                "ProxyEnable": 1,
                "ProxyServer": f"http=127.0.0.1:{live_port};https=127.0.0.1:{live_port}",
            }
            self.assertFalse(sponsor_mitm._looks_like_stale_local_proxy(live_settings))

        stale_port = live_port
        stale_settings = {
            "ProxyEnable": 1,
            "ProxyServer": f"http=127.0.0.1:{stale_port};https=127.0.0.1:{stale_port}",
        }
        self.assertTrue(sponsor_mitm._looks_like_stale_local_proxy(stale_settings))

    def test_caddy_proxy_override_merge_preserves_existing_entries(self):
        merged = sponsor_mitm._merge_proxy_override("<local>;example.com;pastebin.com")
        self.assertEqual(merged, "<local>;example.com;pastebin.com;www.pastebin.com")

        merged_again = sponsor_mitm._merge_proxy_override(merged)
        self.assertEqual(merged_again, merged)

    def test_ca_trust_flag_is_removed_when_cert_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            flag_path = os.path.join(tmp_dir, ".mitm_ca_trusted")
            with open(flag_path, "w", encoding="utf-8") as f:
                f.write("trusted")

            with mock.patch.object(sponsor_mitm, "CA_CERT_FILE", os.path.join(tmp_dir, "missing.cer")), \
                    mock.patch.object(sponsor_mitm, "CA_INSTALLED_FLAG", flag_path):
                self.assertFalse(sponsor_mitm._is_ca_trusted())
                self.assertFalse(os.path.exists(flag_path))

    def test_force_cleanup_does_not_restore_proxy_when_idle(self):
        old_running = sponsor_mitm._running
        old_active_port = sponsor_mitm._active_proxy_port
        old_original = sponsor_mitm._original_proxy_settings
        try:
            sponsor_mitm._running = False
            sponsor_mitm._active_proxy_port = None
            sponsor_mitm._original_proxy_settings = None

            with mock.patch.object(sponsor_mitm, "_restore_proxy") as restore_proxy, \
                    mock.patch.object(sponsor_mitm, "_stop_mitmdump"), \
                    mock.patch.object(sponsor_mitm, "_kill_stale_mitmdump_processes"), \
                    mock.patch.object(sponsor_mitm, "_cleanup_old_caddy_residuals"):
                sponsor_mitm.force_cleanup()

            restore_proxy.assert_not_called()
        finally:
            sponsor_mitm._running = old_running
            sponsor_mitm._active_proxy_port = old_active_port
            sponsor_mitm._original_proxy_settings = old_original

    def test_admin_helper_mitm_ca_failure_does_not_write_flag(self):
        class FailedResult:
            stdout = ""
            stderr = "failed"
            returncode = 1

        with tempfile.TemporaryDirectory() as tmp_dir:
            cert_path = os.path.join(tmp_dir, "mitmproxy-ca-cert.cer")
            flag_path = os.path.join(tmp_dir, "flag")
            with open(cert_path, "w", encoding="utf-8") as f:
                f.write("not a real cert")

            argv = ["admin_helper.py", "mitm_ca_trust", cert_path, "--flag", flag_path]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(admin_helper.subprocess, "run", return_value=FailedResult()):
                admin_helper.main()

            self.assertFalse(os.path.exists(flag_path))

    def test_sponsor_mode_normalization(self):
        self.assertEqual(sponsor_mitm.normalize_sponsor_mode("mitmdump"), "mitm")
        self.assertEqual(sponsor_mitm.normalize_sponsor_mode("hosts + caddy"), "caddy")
        self.assertEqual(sponsor_mitm.normalize_sponsor_mode("unknown"), "mitm")

    def test_generate_caddyfile_uses_generated_cert_and_content_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            content_file = os.path.join(tmp_dir, "sponsors.dat")
            with open(content_file, "w", encoding="utf-8") as f:
                f.write("base")

            with mock.patch.object(sponsor_mitm, "CADDY_DIR", tmp_dir), \
                    mock.patch.object(sponsor_mitm, "CADDYFILE", os.path.join(tmp_dir, "Caddyfile")), \
                    mock.patch.object(sponsor_mitm, "CADDY_CERT_FILE", os.path.join(tmp_dir, "pastebin.crt")), \
                    mock.patch.object(sponsor_mitm, "CADDY_KEY_FILE", os.path.join(tmp_dir, "pastebin.key")), \
                    mock.patch.object(sponsor_mitm, "CADDY_LOG_FILE", os.path.join(tmp_dir, "caddy.log")):
                caddyfile = sponsor_mitm._generate_caddyfile(content_file)

            with open(caddyfile, "r", encoding="utf-8") as f:
                text = f.read()
            self.assertIn("https://pastebin.com:443", text)
            self.assertIn("tls", text)
            self.assertIn("rewrite * /sponsors.dat", text)

    def test_force_cleanup_does_not_cleanup_caddy_when_idle(self):
        old_running = sponsor_mitm._running
        old_active_mode = sponsor_mitm._active_mode
        old_caddy_process = sponsor_mitm._caddy_process
        try:
            sponsor_mitm._running = False
            sponsor_mitm._active_mode = None
            sponsor_mitm._caddy_process = None

            with mock.patch.object(sponsor_mitm, "_restore_proxy"), \
                    mock.patch.object(sponsor_mitm, "_stop_mitmdump"), \
                    mock.patch.object(sponsor_mitm, "_kill_stale_mitmdump_processes"), \
                    mock.patch.object(sponsor_mitm, "_remove_caddy_hosts") as remove_hosts, \
                    mock.patch.object(sponsor_mitm, "_stop_caddy") as stop_caddy, \
                    mock.patch.object(sponsor_mitm, "_cleanup_old_caddy_residuals") as cleanup_caddy:
                sponsor_mitm.force_cleanup()

            remove_hosts.assert_not_called()
            stop_caddy.assert_not_called()
            cleanup_caddy.assert_not_called()
        finally:
            sponsor_mitm._running = old_running
            sponsor_mitm._active_mode = old_active_mode
            sponsor_mitm._caddy_process = old_caddy_process


if __name__ == "__main__":
    unittest.main()
