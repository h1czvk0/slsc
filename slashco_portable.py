"""Prepare and launch a complete, versioned runtime inside the user cache."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile


APP_EXE = "SlashCoSenseApp.exe"
MANIFEST_ASSET = "SlashCoSense-portable.json"
PACKAGE_ASSETS = {name: f"SlashCoSense-{name}.zip" for name in ("app", "runtime", "mitmdump", "caddy")}
RESTART_EXIT_CODE = 42
LAUNCHED_ENV = "SLASHCO_PORTABLE_LAUNCHED"
MANIFEST_MAX_BYTES = 32 * 1024


def get_data_dir():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SlashCoMonitor"
    return Path.home() / ".local" / "share" / "SlashCoMonitor"


def sha256_file(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


@dataclass(frozen=True)
class Package:
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    version: str
    packages: dict[str, Package]

    @classmethod
    def from_bytes(cls, data):
        if len(data) > MANIFEST_MAX_BYTES:
            raise ValueError("便携版清单过大")
        doc = json.loads(data)
        if not isinstance(doc, dict) or doc.get("format") != 1:
            raise ValueError("无法识别便携版清单格式")
        version = doc.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,9}){2}", version):
            raise ValueError("便携版版本号无效")
        entries = doc.get("packages")
        if not isinstance(entries, dict) or entries.keys() != PACKAGE_ASSETS.keys():
            raise ValueError("便携版组件列表不完整")
        packages = {}
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                raise ValueError(f"便携版组件 {name} 的清单格式无效")
            size, digest = entry.get("size"), entry.get("sha256")
            if (
                type(size) is not int or size <= 0
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise ValueError(f"便携版组件 {name} 的摘要或大小无效")
            packages[name] = Package(size, digest)
        return cls(version, packages)

    def to_bytes(self):
        entries = {
            name: {"size": package.size, "sha256": package.sha256}
            for name, package in self.packages.items()
        }
        return json.dumps(
            {"format": 1, "version": self.version, "packages": entries},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    @property
    def version_key(self):
        return tuple(map(int, self.version.split(".")))

    @property
    def directory_name(self):
        return f"{self.version}-{hashlib.sha256(self.to_bytes()).hexdigest()[:16]}"


@contextmanager
def _cache_lock(cache):
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".lock").open("a+b") as file:
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + 30
            while True:
                try:
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX)
        yield


def _runtimes(cache):
    builds = []
    for path in (cache / "versions").glob("*/release.json"):
        try:
            with path.open("rb") as file:
                manifest = Manifest.from_bytes(file.read(MANIFEST_MAX_BYTES + 1))
            if path.parent.name == manifest.directory_name and _runtime_ready(path.parent):
                builds.append((manifest, path.parent))
        except (OSError, ValueError):
            continue
    return sorted(builds, key=lambda build: (build[0].version_key, build[1].name), reverse=True)


def _runtime_ready(path):
    app = path / "app"
    return (
        any(dll.is_file() for dll in (app / "_internal").glob("python[0-9][0-9]*.dll"))
        and all((app / filename).is_file() for filename in (
            APP_EXE, "_internal/base_library.zip", "_internal/tools/mitmdump.exe",
            "_internal/tools/caddy/caddy.exe",
        ))
    )


def _package_matches(path, package):
    return path.stat().st_size == package.size and sha256_file(path) == package.sha256


def _extract(archive, destination):
    with ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            parts = entry.filename.split("/")
            if any(
                not part or part in {".", ".."} or part[-1] in ". "
                or re.search(r'[\\:<>|"?*\x00-\x1f]', part)
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])", part.split(".")[0])
                for part in parts
            ):
                raise ValueError(f"组件包含无效路径：{entry.filename}")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def install_runtime(cache, manifest, fetch_package):
    """Fetch only missing packages and make the finished runtime visible in one rename."""
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix="staging-") as temporary:
        build = Path(temporary) / "build"
        archives = build / "packages"
        archives.mkdir(parents=True)
        with _cache_lock(cache):
            existing = _runtimes(cache)
            if existing and existing[0][0].version_key >= manifest.version_key:
                return
            for name, package in manifest.packages.items():
                for old_manifest, old_path in existing:
                    source = old_path / "packages" / PACKAGE_ASSETS[name]
                    if old_manifest.packages[name] != package or not source.is_file():
                        continue
                    target = archives / source.name
                    try:
                        os.link(source, target)
                    except OSError:
                        shutil.copyfile(source, target)
                    break

        for name, package in manifest.packages.items():
            path = archives / PACKAGE_ASSETS[name]
            if path.exists() and not _package_matches(path, package):
                path.unlink()
            if not path.exists():
                fetch_package(name, package, path)
                if not _package_matches(path, package):
                    raise ValueError(f"便携版组件 {name} 的大小或 SHA-256 不一致")
            _extract(path, build / "app")

        if not _runtime_ready(build):
            raise ValueError("便携版程序文件不完整")
        (build / "release.json").write_bytes(manifest.to_bytes())
        destination = cache / "versions" / manifest.directory_name
        with _cache_lock(cache):
            existing = _runtimes(cache)
            if existing and existing[0][0].version_key >= manifest.version_key:
                return
            destination.parent.mkdir(exist_ok=True)
            if destination.exists():
                if _runtime_in_use(destination):
                    raise RuntimeError("便携版程序文件仍被占用，请关闭其他实例后重试")
                shutil.rmtree(destination)
            build.rename(destination)


def _runtime_in_use(path):
    for lease in path.glob(".running-*"):
        try:
            lease.unlink(missing_ok=True)
        except OSError:
            # Windows refuses to unlink a lease while a launcher holds it open.
            return True
    return False


@contextmanager
def runnable(cache, minimum=None):
    """Keep the chosen runtime available until the launched program exits."""
    lease = None
    selected = None
    with _cache_lock(cache):
        builds = _runtimes(cache)
        if builds and (minimum is None or builds[0][0].version_key >= minimum.version_key):
            manifest, path = builds[0]
            lease = tempfile.NamedTemporaryFile(dir=path, prefix=".running-", delete=False)
            selected = manifest, path / "app" / APP_EXE
            for _, old_path in builds[2:]:
                if not _runtime_in_use(old_path):
                    shutil.rmtree(old_path, ignore_errors=True)
    try:
        yield selected
    finally:
        if lease is not None:
            lease.close()
            try:
                Path(lease.name).unlink(missing_ok=True)
            except OSError:
                pass
