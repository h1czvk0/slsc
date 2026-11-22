"""Launch the portable app from its cache and restore the bundled copy offline."""

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from threading import Thread
from zipfile import ZipFile

from slashco_portable import (
    LAUNCHED_ENV,
    MANIFEST_ASSET,
    MANIFEST_MAX_BYTES,
    PACKAGE_ASSETS,
    RESTART_EXIT_CODE,
    Manifest,
    get_data_dir,
    install_runtime,
    runnable,
)
from slashco_updater import remove_legacy_backup


def main():
    if Path(sys.executable + ".old").is_file():
        Thread(target=remove_legacy_backup, args=(sys.executable,), daemon=True).start()

    cache = get_data_dir() / "portable"
    with ZipFile(sys.executable) as seed:
        with seed.open(MANIFEST_ASSET) as file:
            bundled = Manifest.from_bytes(file.read(MANIFEST_MAX_BYTES + 1))

        def copy_package(name, _package, path):
            with seed.open(PACKAGE_ASSETS[name]) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)

        install_runtime(cache, bundled, copy_package)

    env = os.environ.copy()
    env[LAUNCHED_ENV] = "1"
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # The frozen launcher adds its temporary directory to the DLL search path.
    # The onedir child must load its own Python DLL and dependencies.
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    previous = None
    while True:
        with runnable(cache, minimum=bundled) as selected:
            if selected is None:
                raise RuntimeError("找不到完整的便携版运行文件")
            manifest, executable = selected
            if previous is not None and manifest.version_key <= previous.version_key:
                raise RuntimeError("未找到已下载的新版本，请重新打开程序")
            result = subprocess.run([str(executable), *sys.argv[1:]], env=env, cwd=Path(sys.executable).parent)
        if result.returncode != RESTART_EXIT_CODE:
            return result.returncode
        previous = manifest


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(None, str(error), "SlashCoSense 启动失败", 0x10)
        sys.exit(1)
