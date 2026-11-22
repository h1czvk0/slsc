"""Split the PyInstaller runtime and append an offline seed to the launcher."""

import shutil
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco_portable import APP_EXE, MANIFEST_ASSET, PACKAGE_ASSETS, Manifest, Package, sha256_file
from slashco_updater import APP_VERSION


APP_FILES = {
    APP_EXE, "_internal/icon.ico", "_internal/locations.json", "_internal/cover.png",
    "_internal/admin_helper.py", "_internal/sponsors.txt", "_internal/tools/cleanup_hosts.py",
    # PyInstaller generates this archive during analysis, including the member order.
    "_internal/base_library.zip",
}
TOOL_FILES = {
    "_internal/tools/mitmdump.exe": "mitmdump",
    "_internal/tools/caddy/caddy.exe": "caddy",
}


def write_entry(bundle, source, relative):
    # Fixed ZIP metadata keeps the asset hash unchanged when the files are unchanged.
    entry = ZipInfo(relative)
    entry.compress_type = bundle.compression
    with source.open("rb") as file, bundle.open(entry, "w") as output:
        shutil.copyfileobj(file, output)


def build(dist):
    runtime = dist / Path(APP_EXE).stem
    output = dist / "release"
    output.mkdir(exist_ok=True)
    files = {path.relative_to(runtime).as_posix(): path for path in runtime.rglob("*") if path.is_file()}
    missing = (APP_FILES | TOOL_FILES.keys()) - files.keys()
    if missing:
        raise ValueError(f"缺少打包文件：{', '.join(sorted(missing))}")

    grouped = {name: [] for name in PACKAGE_ASSETS}
    for relative in sorted(files):
        name = "app" if relative in APP_FILES else TOOL_FILES.get(relative, "runtime")
        grouped[name].append(relative)

    packages = {}
    for name, asset in PACKAGE_ASSETS.items():
        path = output / asset
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for relative in grouped[name]:
                write_entry(archive, files[relative], relative)
        packages[name] = Package(path.stat().st_size, sha256_file(path))

    manifest = output / MANIFEST_ASSET
    manifest.write_bytes(Manifest(APP_VERSION, packages).to_bytes())
    executable = output / "SlashCoSense.exe"
    shutil.copyfile(dist / "SlashCoSenseLauncher.exe", executable)
    # The PyInstaller bootloader finds its own archive before this appended ZIP.
    with ZipFile(executable, "a", compression=ZIP_STORED) as seed:
        for asset in (MANIFEST_ASSET, *PACKAGE_ASSETS.values()):
            write_entry(seed, output / asset, asset)
    for path in (executable, manifest, *(output / asset for asset in PACKAGE_ASSETS.values())):
        print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    build(ROOT / "dist")
