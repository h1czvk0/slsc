import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from slashco_portable import MANIFEST_ASSET, MANIFEST_MAX_BYTES, PACKAGE_ASSETS, Manifest, install_runtime


APP_VERSION = "3.9.38"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/h1czvk0/slsc/releases/latest"
GITHUB_PROXY_PREFIXES = (
    "https://gh-proxy.org/",
    "https://v4.gh-proxy.org/",
    "https://v6.gh-proxy.org/",
    "https://cdn.gh-proxy.org/",
)
UPDATE_ASSET_NAME_RE = re.compile(r"SlashCoSense.*\.exe$", re.IGNORECASE)
UPDATE_TEMP_DIR_NAME = "SlashCoSenseUpdate"
UPDATE_NEW_EXE_NAME = "SlashCoSense.new.exe"
UPDATE_BACKUP_EXE_NAME = "SlashCoSense.backup.exe"
UPDATE_FAILED_ENV = "SLASHCO_UPDATE_FAILED"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    asset_name: str
    download_url: str
    release_url: str = ""
    sha256: str = ""


def parse_version(value):
    text = str(value or "").strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    parts = []
    for part in re.split(r"[.\-+_]", text):
        if not part:
            continue
        match = re.match(r"(\d+)", part)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def is_newer_version(remote, current):
    remote_parts = parse_version(remote)
    current_parts = parse_version(current)
    if not remote_parts or not current_parts:
        return False
    max_len = max(len(remote_parts), len(current_parts))
    remote_parts = remote_parts + (0,) * (max_len - len(remote_parts))
    current_parts = current_parts + (0,) * (max_len - len(current_parts))
    return remote_parts > current_parts


def build_github_url_candidates(url):
    candidates = [prefix + url for prefix in GITHUB_PROXY_PREFIXES]
    candidates.append(url)
    return candidates


def select_release_asset(release_data):
    """返回 (文件名, 下载地址, sha256)；Release 未提供摘要时 sha256 为空串。"""
    for asset in release_data.get("assets", []) or []:
        name = str(asset.get("name", ""))
        download_url = str(asset.get("browser_download_url", ""))
        if UPDATE_ASSET_NAME_RE.search(name) and download_url:
            algorithm, _, digest = str(asset.get("digest") or "").partition(":")
            sha256 = digest.strip().lower() if algorithm.strip().lower() == "sha256" else ""
            return name, download_url, sha256
    return None, None, ""


def parse_update_info(release_data, current_version=APP_VERSION):
    tag_name = str(release_data.get("tag_name", "")).strip()
    if not tag_name or not is_newer_version(tag_name, current_version):
        return None

    asset_name, download_url, sha256 = select_release_asset(release_data)
    if not download_url:
        return None

    return UpdateInfo(
        version=".".join(str(p) for p in parse_version(tag_name)) or tag_name.lstrip("vV"),
        tag_name=tag_name,
        asset_name=asset_name,
        download_url=download_url,
        release_url=str(release_data.get("html_url", "")),
        sha256=sha256,
    )


def fetch_latest_release(request_get, timeout=8):
    last_error = None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SlashCoSense-Updater",
    }
    for url in build_github_url_candidates(GITHUB_LATEST_RELEASE_API):
        try:
            resp = request_get(url, timeout=timeout, headers=headers)
            if getattr(resp, "status_code", None) != 200:
                last_error = f"HTTP {getattr(resp, 'status_code', 'unknown')}"
                continue
            try:
                return resp.json()
            except Exception:
                return json.loads(resp.text)
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(last_error or "无法获取更新信息")


def get_update_temp_dir():
    return os.path.join(tempfile.gettempdir(), UPDATE_TEMP_DIR_NAME)


def get_update_download_path():
    return os.path.join(get_update_temp_dir(), UPDATE_NEW_EXE_NAME)


def download_update(
    request_get, download_url, destination, progress_callback=None, timeout=15, expected_sha256="",
):
    """下载新版本并校验完整性；某个地址返回的文件不完整或摘要不符时换下一个地址。"""
    return _download_release_file(
        request_get, download_url, destination, progress_callback, timeout,
        expected_sha256, header_bytes=b"MZ",
    )


def _download_release_file(
    request_get, download_url, destination, progress_callback=None, timeout=15,
    expected_sha256="", *, expected_size=None, header_bytes=None,
):
    destination = os.fspath(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    tmp_path = destination + ".download"
    expected_sha256 = str(expected_sha256 or "").strip().lower()
    last_error = None
    headers = {"User-Agent": "SlashCoSense-Updater", "Accept-Encoding": "identity"}

    for url in build_github_url_candidates(download_url):
        try:
            with request_get(url, stream=True, timeout=timeout, headers=headers) as resp:
                if getattr(resp, "status_code", None) != 200:
                    last_error = f"HTTP {getattr(resp, 'status_code', 'unknown')}"
                    continue

                total = int(resp.headers.get("Content-Length") or 0)
                if expected_size is not None and total and total != expected_size:
                    raise RuntimeError("下载文件大小与发布信息不一致")
                downloaded = 0
                digest = hashlib.sha256()
                header = b""
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if expected_size is not None and downloaded > expected_size:
                            raise RuntimeError("下载文件大小与发布信息不一致")
                        f.write(chunk)
                        digest.update(chunk)
                        if header_bytes and len(header) < len(header_bytes):
                            header = (header + chunk)[:len(header_bytes)]
                        if progress_callback and (total or expected_size):
                            progress_callback(downloaded, total or expected_size)

            if total and downloaded != total:
                raise RuntimeError(f"下载不完整（{downloaded}/{total} 字节）")
            if expected_size is not None and downloaded != expected_size:
                raise RuntimeError("下载文件大小与发布信息不一致")
            if header_bytes and header != header_bytes:
                raise RuntimeError("下载内容不是有效的 exe 文件")
            if expected_sha256 and digest.hexdigest() != expected_sha256:
                raise RuntimeError("下载文件的 SHA-256 与发布信息不一致")

            os.replace(tmp_path, destination)
            if progress_callback:
                progress_callback(downloaded, downloaded)
            return destination
        except Exception as exc:
            last_error = str(exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    raise RuntimeError(last_error or "下载更新失败")


def download_portable_update(request_get, release_data, version, cache, progress_callback=None):
    base_url = f"https://github.com/h1czvk0/slsc/releases/download/v{version}/"
    assets = {
        asset["name"]: asset for asset in release_data.get("assets", [])
        if asset.get("name") and asset.get("browser_download_url") == base_url + asset["name"]
    }
    asset = assets.get(MANIFEST_ASSET)
    if asset is None:
        raise RuntimeError("新版本缺少便携版更新清单")
    size = asset.get("size")
    digest = asset.get("digest") or ""
    if (
        type(size) is not int or not 0 < size <= MANIFEST_MAX_BYTES
        or not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
    ):
        raise RuntimeError("便携版更新清单缺少有效的大小或 SHA-256")

    with tempfile.TemporaryDirectory() as temporary:
        manifest_path = Path(temporary) / MANIFEST_ASSET
        _download_release_file(
            request_get, asset["browser_download_url"], manifest_path,
            expected_sha256=digest.removeprefix("sha256:"), expected_size=size,
        )
        manifest = Manifest.from_bytes(manifest_path.read_bytes())
    if manifest.version != version:
        raise RuntimeError("便携版清单版本与发布版本不一致")

    def fetch_package(name, package, path):
        asset = assets.get(PACKAGE_ASSETS[name])
        if asset is None or asset.get("size") != package.size:
            raise RuntimeError(f"发布附件与组件 {name} 的清单不一致")

        def show_progress(downloaded, total):
            if progress_callback:
                progress_callback(name, downloaded, total)

        _download_release_file(
            request_get, asset["browser_download_url"], path, show_progress,
            expected_sha256=package.sha256, expected_size=package.size,
        )

    install_runtime(cache, manifest, fetch_package)


def _bat_text(value):
    # 批处理里的 % 需要写成 %%，否则会被当作变量引用。
    return str(value).replace("%", "%%")


def _quote_bat(value):
    return '"' + _bat_text(value).replace('"', '""') + '"'


def create_updater_bat(current_exe, new_exe, app_args=None):
    """生成替换脚本：等旧进程退出后原地覆盖 exe，再重新启动。

    原地覆盖不会删除或重命名原文件，桌面图标位置和指向它的快捷方式保持不变。
    备份放在临时目录，覆盖失败时还原；exe 旁边不产生任何文件。
    """
    update_dir = get_update_temp_dir()
    os.makedirs(update_dir, exist_ok=True)
    bat_path = os.path.join(update_dir, "apply_update.bat")
    backup_exe = os.path.join(update_dir, UPDATE_BACKUP_EXE_NAME)
    relaunch = " ".join(_quote_bat(arg) for arg in [current_exe, *(app_args or [])])

    lines = [
        "@echo off",
        "setlocal",
        "chcp 65001 >nul",
        f'set "OLD={_bat_text(current_exe)}"',
        f'set "NEW={_bat_text(new_exe)}"',
        f'set "BAK={_bat_text(backup_exe)}"',
        'for %%I in ("%OLD%") do set "APPDIR=%%~dpI"',
        # 旧进程（含单文件引导进程）退出前 exe 无法以写方式打开，以此判断是否可以覆盖。
        "for /l %%i in (1,1,60) do (",
        '  2>nul (>>"%OLD%" (call )) && goto replace',
        "  ping -n 2 127.0.0.1 >nul",
        ")",
        # 旧进程一直未退出：放弃本次替换，保留现有程序，下次启动重新更新。
        'del /f /q "%NEW%" >nul 2>nul',
        "goto finish",
        ":replace",
        'copy /b /y "%OLD%" "%BAK%" >nul 2>nul || goto failed',
        'copy /b /y "%NEW%" "%OLD%" >nul 2>nul || goto restore',
        "goto launch",
        ":restore",
        # 还原也失败时保留备份文件供手动恢复，不启动可能已损坏的程序。
        'copy /b /y "%BAK%" "%OLD%" >nul 2>nul || exit /b 1',
        ":failed",
        f'set "{UPDATE_FAILED_ENV}=1"',
        ":launch",
        'del /f /q "%NEW%" >nul 2>nul',
        'del /f /q "%BAK%" >nul 2>nul',
        'set "PYINSTALLER_RESET_ENVIRONMENT=1"',
        f'start "" /D "%APPDIR%" {relaunch}',
        ":finish",
        # 脚本删除自身并移除空的临时目录；这一行之后不再读取脚本文件。
        '(goto) 2>nul & del /f /q "%~f0" & rd "%~dp0." 2>nul',
    ]
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return bat_path


def launch_updater_and_exit(current_exe, new_exe, app_args=None):
    bat_path = create_updater_bat(current_exe, new_exe, app_args=app_args)
    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        cwd=os.path.dirname(current_exe),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def cleanup_update_temp_dir():
    try:
        shutil.rmtree(get_update_temp_dir(), ignore_errors=True)
    except Exception:
        pass


def remove_legacy_backup(current_exe, attempts=10, delay_seconds=1.0, sleep=time.sleep):
    """删除旧版更新脚本留在 exe 旁边的 .old 备份，返回它是否已不存在。

    刚更新完时旧进程可能还没退出、文件仍被占用，因此短暂重试。
    """
    backup_exe = str(current_exe) + ".old"
    for attempt in range(max(1, int(attempts))):
        try:
            os.remove(backup_exe)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < attempts:
                sleep(delay_seconds)
    return False


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))
