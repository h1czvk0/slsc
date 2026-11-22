import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZipFile

from slashco_portable import APP_EXE, MANIFEST_ASSET, PACKAGE_ASSETS, Manifest, Package, install_runtime, runnable
from slashco_updater import download_portable_update


def archive_bytes(files):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def make_release(version, app=b"MZ first", extra_app_files=None):
    packages = {
        "app": archive_bytes({APP_EXE: app, **(extra_app_files or {})}),
        "runtime": archive_bytes({"_internal/base_library.zip": b"lib", "_internal/python312.dll": b"python"}),
        "mitmdump": archive_bytes({"_internal/tools/mitmdump.exe": b"mitm"}),
        "caddy": archive_bytes({"_internal/tools/caddy/caddy.exe": b"caddy"}),
    }
    return Manifest(version, {
        name: Package(len(data), hashlib.sha256(data).hexdigest()) for name, data in packages.items()
    }), packages


class PortableTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / "portable"
        self.original, packages = make_release("3.1.0", extra_app_files={"obsolete.txt": b"old"})
        install_runtime(self.cache, self.original, lambda name, _spec, path: path.write_bytes(packages[name]))

    def test_skips_unchanged_packages_when_updating_across_versions(self):
        updated, packages = make_release("3.4.1", app=b"MZ updated")
        files = {MANIFEST_ASSET: updated.to_bytes(), **{
            PACKAGE_ASSETS[name]: content for name, content in packages.items()
        }}
        release = {"assets": [{
            "name": name, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "browser_download_url": f"https://github.com/h1czvk0/slsc/releases/download/v{updated.version}/{name}",
        } for name, data in files.items()]}
        downloads = []

        def get(url, **_kwargs):
            downloads.append(url.rsplit("/", 1)[-1])
            content = files[downloads[-1]]
            response = MagicMock(status_code=200, headers={"Content-Length": str(len(content))})
            response.__enter__.return_value = response
            response.iter_content.return_value = [content]
            return response

        download_portable_update(get, release, updated.version, self.cache)
        self.assertEqual(downloads, [MANIFEST_ASSET, PACKAGE_ASSETS["app"]])
        # Opening the older offline EXE must still choose the cached newer release.
        install_runtime(self.cache, self.original, lambda *_args: self.fail("seed should be reused"))
        with runnable(self.cache, minimum=self.original) as (manifest, executable):
            self.assertEqual(manifest, updated)
            self.assertEqual(executable.read_bytes(), b"MZ updated")
            self.assertFalse(executable.with_name("obsolete.txt").exists())

    def test_corrupt_or_unsafe_package_leaves_previous_runtime_usable(self):
        updated, packages = make_release("3.2.0", extra_app_files={"../unexpected.txt": b"unsafe"})
        for content, error in ((b"corrupt", "SHA-256"), (packages["app"], "无效路径")):
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    install_runtime(self.cache, updated, lambda _name, _spec, path: path.write_bytes(content))
                self.assertEqual(list(self.cache.glob("staging-*")), [])
                with runnable(self.cache) as (manifest, executable):
                    self.assertEqual(manifest, self.original)
                    self.assertTrue(executable.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows runtime leases")
    def test_old_runtime_stays_intact_while_launcher_is_using_it(self):
        with runnable(self.cache) as (_, old_executable):
            for version in ("3.2.0", "3.3.0"):
                updated, packages = make_release(version)
                install_runtime(
                    self.cache, updated, lambda name, _spec, path: path.write_bytes(packages[name]),
                )
            with runnable(self.cache) as (manifest, _):
                self.assertEqual(manifest.version, "3.3.0")
                self.assertTrue(old_executable.is_file())
        with runnable(self.cache):
            self.assertFalse(old_executable.exists())
