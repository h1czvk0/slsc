# -*- coding: utf-8 -*-
"""
admin_helper.py - 独立管理员任务脚本 (零外部依赖)

用法:
  python admin_helper.py mitm_ca_trust <cert_path> [--flag <flag_file>]
  python admin_helper.py <sponsor_hosts_add|sponsor_hosts_remove> <target_host> <marker> [--flag <flag_file>]

此脚本仅使用 Python 内置模块，不依赖任何第三方库。
通过 ShellExecuteW runas 以管理员权限运行。
"""
import os
import sys
import subprocess

HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
APP_NAME = "SlashCoMonitor"
USAGE = """Usage:
  admin_helper.py mitm_ca_trust <cert_path> [--flag <flag_file>]
  admin_helper.py <sponsor_hosts_add|sponsor_hosts_remove> <target_host> <marker> [--flag <flag_file>]"""


def get_data_dir():
    """获取运行日志目录，避免向单 exe 所在目录写入文件"""
    if os.name == "nt":
        base_dir = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.expanduser("~")
        )
        return os.path.join(base_dir, APP_NAME)
    return os.path.join(os.path.expanduser("~"), ".local", "share", APP_NAME)


def get_log_path():
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "admin_helper_debug.log")

def log(msg):
    """写入调试日志"""
    try:
        log_path = get_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except:
        pass

def main():
    log(f"=== admin_helper.py started ===")
    log(f"sys.argv = {sys.argv}")
    log(f"cwd = {os.getcwd()}")

    if len(sys.argv) < 2:
        log("ERROR: Not enough arguments")
        print(USAGE)
        return

    mode = sys.argv[1]

    # --- mitmproxy CA 信任模式 ---
    if mode == "mitm_ca_trust":
        cert_path = sys.argv[2] if len(sys.argv) > 2 else None
        if not cert_path or not os.path.exists(cert_path):
            log(f"ERROR: cert not found: {cert_path}")
            return

        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break

        log(f"Installing mitmproxy CA: {cert_path}")
        success = False
        try:
            result = subprocess.run(
                ["certutil", "-addstore", "Root", cert_path],
                capture_output=True, text=True, timeout=30
            )
            log(f"certutil stdout: {result.stdout}")
            log(f"certutil stderr: {result.stderr}")
            log(f"certutil returncode: {result.returncode}")
            success = result.returncode == 0
        except Exception as e:
            log(f"certutil failed: {e}")
            success = False

        if not success:
            log("mitmproxy CA install failed; flag file will not be created")
            return

        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
                log(f"Flag file created: {flag_file}")
            except Exception as e:
                log(f"ERROR creating flag file: {e}")

        log("=== admin_helper.py (mitm_ca_trust) finished ===")
        return

    # --- hosts-only sponsor mode ---
    if mode in ("sponsor_hosts_add", "sponsor_hosts_remove"):
        target_host = sys.argv[2] if len(sys.argv) > 2 else "pastebin.com"
        marker = sys.argv[3] if len(sys.argv) > 3 else "# SlashCoCaddy"

        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break

        log(f"{mode}: host={target_host}, marker={marker}, flag={flag_file}")
        success = False
        try:
            os.system(f'attrib -r "{HOSTS_FILE}"')
            content = ""
            if os.path.exists(HOSTS_FILE):
                try:
                    with open(HOSTS_FILE, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    with open(HOSTS_FILE, "r", encoding="mbcs") as f:
                        content = f.read()

            lines = content.splitlines()
            new_lines = []
            for line in lines:
                lower = line.lower()
                if marker.lower() in lower:
                    continue
                if "slashcocaddy" in lower and target_host.lower() in lower:
                    continue
                new_lines.append(line)

            if mode == "sponsor_hosts_add":
                new_lines.append(f"127.0.0.1 {target_host} {marker}")

            new_content = "\n".join(new_lines)
            if new_content:
                new_content += "\n"
            with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                f.write(new_content)

            os.system("ipconfig /flushdns")
            success = True
            log(f"{mode} completed")
        except Exception as e:
            log(f"{mode} failed: {e}")
            import traceback
            log(traceback.format_exc())

        if success and flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
                log(f"Flag file created: {flag_file}")
            except Exception as e:
                log(f"ERROR creating flag file: {e}")

        log(f"=== admin_helper.py ({mode}) finished ===")
        return

    log(f"ERROR: Unknown mode: {mode}")
    print(USAGE)


if __name__ == "__main__":
    main()
