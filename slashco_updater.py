import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass


APP_VERSION = "3.9.5"
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


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    asset_name: str
    download_url: str
    release_url: str = ""


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
    for asset in release_data.get("assets", []) or []:
        name = str(asset.get("name", ""))
        download_url = str(asset.get("browser_download_url", ""))
        if UPDATE_ASSET_NAME_RE.search(name) and download_url:
            return name, download_url
    return None, None


def parse_update_info(release_data, current_version=APP_VERSION):
    tag_name = str(release_data.get("tag_name", "")).strip()
    if not tag_name or not is_newer_version(tag_name, current_version):
        return None

    asset_name, download_url = select_release_asset(release_data)
    if not download_url:
        return None

    return UpdateInfo(
        version=".".join(str(p) for p in parse_version(tag_name)) or tag_name.lstrip("vV"),
        tag_name=tag_name,
        asset_name=asset_name,
        download_url=download_url,
        release_url=str(release_data.get("html_url", "")),
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


def download_update(request_get, download_url, destination, progress_callback=None, timeout=15):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    tmp_path = destination + ".download"
    last_error = None
    headers = {"User-Agent": "SlashCoSense-Updater"}

    for url in build_github_url_candidates(download_url):
        try:
            with request_get(url, stream=True, timeout=timeout, headers=headers) as resp:
                if getattr(resp, "status_code", None) != 200:
                    last_error = f"HTTP {getattr(resp, 'status_code', 'unknown')}"
                    continue

                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total:
                            progress_callback(downloaded, total)

            os.replace(tmp_path, destination)
            if progress_callback:
                size = os.path.getsize(destination)
                progress_callback(size, size)
            return destination
        except Exception as exc:
            last_error = str(exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    raise RuntimeError(last_error or "下载更新失败")


def _quote_bat(value):
    return '"' + str(value).replace('"', '""') + '"'


def create_updater_bat(current_exe, new_exe, app_args=None):
    update_dir = get_update_temp_dir()
    os.makedirs(update_dir, exist_ok=True)
    bat_path = os.path.join(update_dir, "apply_update.bat")
    backup_exe = current_exe + ".old"
    app_args = app_args or []
    relaunch = " ".join([_quote_bat(current_exe)] + [_quote_bat(arg) for arg in app_args])

    lines = [
        "@echo off",
        "setlocal",
        "chcp 65001 >nul",
        f'set "OLD={current_exe}"',
        f'set "NEW={new_exe}"',
        f'set "BAK={backup_exe}"',
        "for %%I in (\"%OLD%\") do set \"APPDIR=%%~dpI\"",
        "for /l %%i in (1,1,60) do (",
        "  move /Y \"%OLD%\" \"%BAK%\" >nul 2>nul && goto replace",
        "  timeout /t 1 /nobreak >nul",
        ")",
        "exit /b 1",
        ":replace",
        "move /Y \"%NEW%\" \"%OLD%\" >nul 2>nul",
        "if errorlevel 1 (",
        "  move /Y \"%BAK%\" \"%OLD%\" >nul 2>nul",
        "  exit /b 1",
        ")",
        "timeout /t 1 /nobreak >nul",
        "set \"PYINSTALLER_RESET_ENVIRONMENT=1\"",
        f"start \"\" /D \"%APPDIR%\" {relaunch}",
        "timeout /t 2 /nobreak >nul",
        "del /f /q \"%BAK%\" >nul 2>nul",
        f"del /f /q { _quote_bat(bat_path) } >nul 2>nul",
        "endlocal",
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


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))
