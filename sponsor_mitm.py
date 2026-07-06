# -*- coding: utf-8 -*-
"""
sponsor_mitm.py — 赞助者名单覆盖 (mitmproxy 方案)

使用 mitmproxy 作为本地系统代理，拦截 VRChat 对 Pastebin 的 HTTPS 请求。
优势:
  - 无需修改 DNS / hosts 文件
  - 不需要端口 53
  - VPN/加速器 兼容 (系统代理是本地回环)
  - mitmproxy 的 TLS 兼容性好

工作流程:
  1. 从 Pastebin 下载原始名单
  2. 在末尾用正确分隔符 ♴ (U+2674) 追加用户名
  3. 安装 mitmproxy CA (首次)
  4. 将 mitmdump 作为后台进程启动
  5. 设置 Windows 系统代理指向 mitmdump
  6. VRChat 通过系统代理访问 → 响应被替换
"""
import os
import sys
import subprocess
import threading
import time
import atexit
import shutil
import socket
import json
import random
from datetime import datetime, timedelta, timezone

# ==================== 路径管理 (OneFile 支持) ====================
DATA_DIR_ENV = "SLASHCO_SPONSOR_DATA_DIR"
PYI_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"


def _get_resource_dir():
    """获取资源文件目录 (PyInstaller _MEIPASS / 开发环境当前目录)"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def _get_work_dir():
    """获取工作目录 (Exe 所在目录 / 开发环境当前目录)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_default_data_dir():
    """获取默认数据目录，避免向单 exe 所在目录写入运行文件"""
    if os.name == "nt":
        base_dir = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.expanduser("~")
        )
        return os.path.join(base_dir, "SlashCoMonitor")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "SlashCoMonitor")


def _get_data_dir():
    """获取数据目录 (可通过环境变量覆盖，默认用户本地数据目录)"""
    custom_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    if custom_dir:
        return custom_dir
    return _get_default_data_dir()


def _get_data_tools_dir():
    return os.path.join(_get_data_dir(), "tools")


def _reset_env_for_child():
    env = os.environ.copy()
    env[PYI_RESET_ENV] = "1"
    return env


def _run_python_script(script_path):
    """运行脚本并确保 OneFile 子进程不复用父进程 _MEI 目录"""
    kwargs = {}
    if getattr(sys, 'frozen', False):
        kwargs["env"] = _reset_env_for_child()
    if os.name == 'nt':
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run([sys.executable, script_path], **kwargs)


def _run_powershell(script):
    if os.name != "nt":
        return 1, "", ""
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return int(proc.returncode), proc.stdout or "", proc.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def _runas_python_with_reset(params):
    """
    提权运行 python 脚本并确保 OneFile 子进程使用独立临时目录。
    返回 ShellExecuteW 的返回码。
    """
    import ctypes

    if getattr(sys, 'frozen', False) and os.name == 'nt':
        cmd_exe = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        cmd_params = f'/c set {PYI_RESET_ENV}=1&& "{sys.executable}" {params}'
        return ctypes.windll.shell32.ShellExecuteW(
            None, "runas", cmd_exe, cmd_params, None, 1
        )

    return ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )

# ==================== 配置 ====================
PROXY_PORT = 8080
SPONSOR_MODE_MITM = "mitm"
SPONSOR_MODE_CADDY = "caddy"
SPONSOR_MODE_LABELS = {
    SPONSOR_MODE_MITM: "mitmdump",
    SPONSOR_MODE_CADDY: "hosts + Caddy",
}
COMMON_ACCELERATOR_PORTS = (7890, 7891, 9090, 1080, 10808, 10080)
STATIC_PROXY_PORT_CANDIDATES = (PROXY_PORT, 18080, 28080, 38080, 48080, 58080)
DYNAMIC_PORT_MIN = 20000
DYNAMIC_PORT_MAX = 60999
DYNAMIC_PORT_SAMPLE_COUNT = 140
EPHEMERAL_PORT_ATTEMPTS = 24
PASTEBIN_URL = "https://pastebin.com/raw/2WVJpW1N"
SEPARATOR = "\u2674"  # ♴  (正确分隔符)
WRONG_SEPARATORS = ["\u2634", "\u2734"]  # 旧代码使用的错误分隔符

# 配置文件和临时文件放在数据目录 (可写)
MODIFIED_CONTENT_FILE = os.path.join(_get_data_dir(), "sponsors.dat")
ADDON_SCRIPT_FILE = os.path.join(_get_data_dir(), "tools", "mitm_addon.py")
HIT_LOG_FILE = os.path.join(_get_data_dir(), "tools", "sponsor_override_hits.log")
CA_INSTALLED_FLAG = os.path.join(_get_data_dir(), "tools", ".mitm_ca_trusted")
CA_CERT_FILE = os.path.join(os.path.expanduser("~"), ".mitmproxy", "mitmproxy-ca-cert.cer")
CA_PEM_FILE = os.path.join(os.path.expanduser("~"), ".mitmproxy", "mitmproxy-ca.pem")
CADDY_HOST_MARKER = "# SlashCoCaddy"
CADDY_DIR = os.path.join(_get_data_dir(), "tools", "caddy")
CADDYFILE = os.path.join(CADDY_DIR, "Caddyfile")
CADDY_CERT_FILE = os.path.join(CADDY_DIR, "pastebin.local.crt")
CADDY_KEY_FILE = os.path.join(CADDY_DIR, "pastebin.local.key")
CADDY_LOG_FILE = os.path.join(CADDY_DIR, "caddy.log")
CADDY_HOSTS_ADD_FLAG = os.path.join(CADDY_DIR, "_hosts_add_done")
CADDY_HOSTS_REMOVE_FLAG = os.path.join(CADDY_DIR, "_hosts_remove_done")

# ==================== 全局状态 ====================
_mitm_process = None
_caddy_process = None
_running = False
_lock = threading.Lock()
_original_proxy_settings = None
_active_proxy_port = None
_active_mode = None


def _log(msg, log_func=None):
    print(f"[SponsorMITM] {msg}")
    if log_func:
        try:
            log_func(f"[赞助覆盖] {msg}")
        except Exception:
            pass


def _is_port_available(port):
    """检查本地端口是否可用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False


def _parse_proxy_port(value):
    try:
        port = int(str(value).strip())
    except Exception:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _parse_port_from_endpoint(endpoint):
    text = str(endpoint or "").strip()
    if not text:
        return None

    if "://" in text:
        text = text.split("://", 1)[1]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if "/" in text:
        text = text.split("/", 1)[0]

    if text.startswith("[") and "]" in text:
        _, _, rest = text.partition("]")
        if rest.startswith(":"):
            return _parse_proxy_port(rest[1:])
        return None

    if ":" not in text:
        return None
    return _parse_proxy_port(text.rsplit(":", 1)[1])


def _extract_proxy_server_endpoints(proxy_server):
    text = str(proxy_server or "").strip()
    if not text:
        return []

    if "=" not in text:
        return [text]

    endpoints = []
    for part in text.split(";"):
        if "=" not in part:
            continue
        _, value = part.split("=", 1)
        value = value.strip()
        if value:
            endpoints.append(value)
    return endpoints


def _is_local_proxy_endpoint(endpoint):
    text = str(endpoint or "").strip()
    if not text:
        return False
    if "://" in text:
        text = text.split("://", 1)[1]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if "/" in text:
        text = text.split("/", 1)[0]
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")].lower() in ("::1", "0:0:0:0:0:0:0:1")
    host = text.rsplit(":", 1)[0].strip().lower() if ":" in text else text.lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _is_local_port_listening(port):
    parsed = _parse_proxy_port(port)
    if not parsed:
        return False
    try:
        with socket.create_connection(("127.0.0.1", parsed), timeout=0.25):
            return True
    except OSError:
        return False


def _looks_like_stale_local_proxy(settings):
    if not settings or settings.get("ProxyEnable") != 1:
        return False

    endpoints = _extract_proxy_server_endpoints(settings.get("ProxyServer"))
    if not endpoints:
        return False

    ports = []
    for endpoint in endpoints:
        if not _is_local_proxy_endpoint(endpoint):
            return False
        port = _parse_port_from_endpoint(endpoint)
        if not port:
            return False
        if port in COMMON_ACCELERATOR_PORTS:
            return False
        ports.append(port)

    return bool(ports) and all(not _is_local_port_listening(port) for port in ports)


def _normalize_upstream_proxy(endpoint):
    text = str(endpoint or "").strip()
    if not text:
        return None

    if "://" in text:
        scheme, rest = text.split("://", 1)
        scheme = scheme.lower().strip()
        if scheme not in ("http", "https"):
            return None
        return f"{scheme}://{rest.strip()}" if rest.strip() else None

    return f"http://{text}"


def _get_proxy_port_candidates(upstream_proxy=None):
    """生成代理端口候选列表"""
    upstream_port = _parse_port_from_endpoint(upstream_proxy)
    blocked_ports = set(COMMON_ACCELERATOR_PORTS)
    if upstream_port:
        blocked_ports.add(upstream_port)

    seen = set()
    ordered = []

    def append_port(port):
        parsed = _parse_proxy_port(port)
        if not parsed or parsed in seen or parsed in blocked_ports:
            return
        seen.add(parsed)
        ordered.append(parsed)

    for p in STATIC_PROXY_PORT_CANDIDATES:
        append_port(p)

    for p in range(DYNAMIC_PORT_MIN, DYNAMIC_PORT_MIN + 120):
        append_port(p)

    rng = random.Random((int(time.time() * 1000) ^ os.getpid()) & 0xFFFFFFFF)
    for _ in range(DYNAMIC_PORT_SAMPLE_COUNT):
        append_port(rng.randint(DYNAMIC_PORT_MIN, DYNAMIC_PORT_MAX))

    return ordered


def _pick_ephemeral_port(excluded_ports=None):
    excluded = set(COMMON_ACCELERATOR_PORTS)
    for item in excluded_ports or ():
        parsed = _parse_proxy_port(item)
        if parsed:
            excluded.add(parsed)

    for _ in range(EPHEMERAL_PORT_ATTEMPTS):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                candidate = int(sock.getsockname()[1])
        except OSError:
            continue

        if candidate in excluded:
            continue
        if _is_port_available(candidate):
            return candidate
    return None


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _write_ca_trusted_flag():
    os.makedirs(os.path.dirname(CA_INSTALLED_FLAG), exist_ok=True)
    with open(CA_INSTALLED_FLAG, "w", encoding="utf-8") as f:
        f.write("trusted")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def normalize_sponsor_mode(mode):
    text = str(mode or "").strip().lower()
    aliases = {
        "": SPONSOR_MODE_MITM,
        "mitm": SPONSOR_MODE_MITM,
        "mitmdump": SPONSOR_MODE_MITM,
        "mitmproxy": SPONSOR_MODE_MITM,
        "proxy": SPONSOR_MODE_MITM,
        "caddy": SPONSOR_MODE_CADDY,
        "hosts": SPONSOR_MODE_CADDY,
        "host": SPONSOR_MODE_CADDY,
        "hosts+caddy": SPONSOR_MODE_CADDY,
        "hosts + caddy": SPONSOR_MODE_CADDY,
    }
    return aliases.get(text, SPONSOR_MODE_MITM)


def get_sponsor_mode_label(mode):
    return SPONSOR_MODE_LABELS.get(normalize_sponsor_mode(mode), SPONSOR_MODE_LABELS[SPONSOR_MODE_MITM])


def _parse_sponsor_names(name):
    if isinstance(name, str):
        if not name.strip():
            return []
        return [n.strip() for n in name.replace("，", ",").split(",") if n.strip()]
    return [str(n).strip() for n in (name or ()) if str(n).strip()]


def _wait_for_flag(flag_file, timeout_seconds=30):
    deadline = time.time() + float(timeout_seconds)
    while time.time() < deadline:
        if os.path.exists(flag_file):
            _safe_remove(flag_file)
            return True
        time.sleep(0.5)
    return False


def _caddy_path_value(path):
    return str(path).replace("\\", "/")


# ==================== 名单处理 ====================
def fetch_original_sponsors(log_func=None):
    """从 Pastebin 下载原始名单"""
    # 优先尝试本地备份 (数据目录)
    local_backup = os.path.join(_get_data_dir(), "sponsors_original.txt")

    _log("正在下载原始赞助者名单...", log_func)
    try:
        import urllib.request
        req = urllib.request.Request(
            PASTEBIN_URL,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36'
            }
        )

        try:
            # 确保不通过代理下载 (代理可能还未启动或已被我们修改)
            import urllib.request as _ur
            proxy_handler = _ur.ProxyHandler({})
            opener = _ur.build_opener(proxy_handler)
            with opener.open(req, timeout=15) as resp:
                content = resp.read().decode('utf-8')
        except Exception:
            # 如果直连失败，尝试系统默认（可能用户没有全局代理，但有系统代理）
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode('utf-8')

        if content:
            try:
                with open(local_backup, 'w', encoding='utf-8') as f:
                    f.write(content)
            except Exception:
                pass
            _log(f"已下载名单 ({len(content)} 字符)", log_func)
            return content

    except Exception as e:
        _log(f"下载名单失败: {e}", log_func)
    
    # 尝试读取本地备份
    if os.path.exists(local_backup):
        try:
            with open(local_backup, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            pass
            
    return None


def build_modified_content(original, names):
    """在名单末尾追加用户名列表 (清理旧错误分隔符)"""
    if not names:
        return original

    # 兼容单个字符串输入
    if isinstance(names, str):
        names = [names]

    content = original.rstrip()

    for name in names:
        name = name.strip()
        if not name:
            continue

        # 清理旧的错误分隔符条目
        for wrong_sep in WRONG_SEPARATORS:
            wrong_entry = wrong_sep + name
            if wrong_entry in content:
                content = content.replace(wrong_entry, "")
                content = content.rstrip()

        # 检查是否已用正确分隔符添加
        check_str = SEPARATOR + name
        if check_str in content or content.endswith(name):
            continue

        # 追加 (使用正确的 ♴ U+2674)
        content = content + SEPARATOR + name
    
    return content


# ==================== mitmproxy Addon 生成 ====================
def _generate_addon_script(content_file_path, hit_log_file=None):
    """生成 mitmproxy addon 脚本"""
    # 使用 正斜杠 路径 避免转义问题
    safe_path = content_file_path.replace("\\", "/")
    safe_log_path = (hit_log_file or HIT_LOG_FILE).replace("\\", "/")

    addon_code = f'''# -*- coding: utf-8 -*-
# Auto-generated mitmproxy addon for sponsor list override
from mitmproxy import http
import os
import time

CONTENT_FILE = r"{safe_path}"
HIT_LOG_FILE = r"{safe_log_path}"
TARGETS = (
    ("pastebin.com", "/raw/2WVJpW1N"),
    ("www.pastebin.com", "/raw/2WVJpW1N"),
)

def _clean_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = path.rstrip("/")
    return path or "/"

def _is_target(host: str, path: str) -> bool:
    for target_host, target_path in TARGETS:
        if host == target_host and path == target_path:
            return True
    return False

def _append_hit_log(message: str):
    try:
        os.makedirs(os.path.dirname(HIT_LOG_FILE), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(HIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{{ts}}] {{message}}\\n")
    except Exception:
        pass

class SponsorOverrideAddon:
    def request(self, flow: http.HTTPFlow):
        host = flow.request.host.lower()
        path = _clean_path(flow.request.path)
        if not _is_target(host, path):
            return

        try:
            with open(CONTENT_FILE, "r", encoding="utf-8") as f:
                modified = f.read()
            flow.response = http.Response.make(
                200,
                modified.encode("utf-8"),
                {{
                    "Content-Type": "text/plain; charset=utf-8",
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }},
            )
            _append_hit_log(f"OVERRIDE {{host}}{{path}} len={{len(modified)}}")
        except Exception as e:
            _append_hit_log(f"OVERRIDE_ERROR {{host}}{{path}} {{e}}")

    def response(self, flow: http.HTTPFlow):
        host = flow.request.host.lower()
        path = _clean_path(flow.request.path)
        if _is_target(host, path):
            body_len = len(flow.response.content or b"")
            _append_hit_log(f"RESP {{host}}{{path}} status={{flow.response.status_code}} body={{body_len}}")

addons = [SponsorOverrideAddon()]
'''

    os.makedirs(os.path.dirname(ADDON_SCRIPT_FILE), exist_ok=True)
    with open(ADDON_SCRIPT_FILE, 'w', encoding='utf-8') as f:
        f.write(addon_code)
    return ADDON_SCRIPT_FILE


# ==================== CA 证书管理 ====================
def _ensure_ca_generated(log_func=None):
    """确保 mitmproxy CA 证书已生成"""
    if os.path.exists(CA_CERT_FILE) and os.path.exists(CA_PEM_FILE):
        return True

    _log("生成 mitmproxy CA 证书...", log_func)
    try:
        # 运行 mitmdump 一次，让它生成 CA
        mitmdump = _get_mitmdump_path()
        proc = subprocess.Popen(
            [mitmdump, "--listen-port", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        time.sleep(3)
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass

    return os.path.exists(CA_CERT_FILE) and os.path.exists(CA_PEM_FILE)


def _get_cert_thumbprint(cert_path):
    """读取证书指纹，用于校验真实系统信任状态。"""
    if not cert_path or not os.path.exists(cert_path) or os.name != "nt":
        return None

    script = (
        "$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("
        + _ps_quote(cert_path)
        + "); $cert.Thumbprint"
    )
    rc, out, _ = _run_powershell(script)
    if rc != 0:
        return None
    thumbprint = "".join((out or "").split()).upper()
    return thumbprint or None


def _is_cert_thumbprint_trusted(thumbprint):
    """检查证书指纹是否存在于当前用户或本机根证书信任存储。"""
    thumbprint = "".join(str(thumbprint or "").split()).upper()
    if not thumbprint or os.name != "nt":
        return False

    script = (
        "$thumb = " + _ps_quote(thumbprint) + "; "
        "$stores = @('Cert:\\CurrentUser\\Root', 'Cert:\\LocalMachine\\Root'); "
        "foreach ($store in $stores) { "
        "$found = Get-ChildItem -Path $store -ErrorAction SilentlyContinue "
        "| Where-Object { $_.Thumbprint -eq $thumb } "
        "| Select-Object -First 1; "
        "if ($found) { Write-Output 'trusted'; exit 0 } "
        "}; exit 1"
    )
    rc, out, _ = _run_powershell(script)
    return rc == 0 and "trusted" in (out or "").lower()


def _is_ca_trusted(log_func=None):
    """检查 mitmproxy CA 是否已真实安装到 Windows 信任存储。"""
    if not os.path.exists(CA_CERT_FILE):
        _safe_remove(CA_INSTALLED_FLAG)
        return False

    if os.name != "nt":
        return os.path.exists(CA_INSTALLED_FLAG)

    thumbprint = _get_cert_thumbprint(CA_CERT_FILE)
    if not thumbprint:
        _safe_remove(CA_INSTALLED_FLAG)
        return False

    if _is_cert_thumbprint_trusted(thumbprint):
        if not os.path.exists(CA_INSTALLED_FLAG):
            try:
                _write_ca_trusted_flag()
            except Exception:
                pass
        return True

    if os.path.exists(CA_INSTALLED_FLAG):
        _safe_remove(CA_INSTALLED_FLAG)
        _log("CA 信任标记已失效，准备重新安装证书", log_func)
    return False


def _trust_ca(log_func=None):
    """安装 mitmproxy CA 到 Windows 信任存储"""
    if _is_ca_trusted(log_func):
        _log("CA 已信任，跳过", log_func)
        return True

    if not os.path.exists(CA_CERT_FILE):
        if not _ensure_ca_generated(log_func):
            _log("CA 证书不存在", log_func)
            return False

    _log("安装 mitmproxy CA 到系统信任存储...", log_func)
    import ctypes

    resource_dir = _get_resource_dir()
    helper_path = os.path.join(resource_dir, "admin_helper.py")
    
    flag_file = CA_INSTALLED_FLAG

    _safe_remove(flag_file)

    # 使用 admin_helper 的 add 模式安装证书
    if os.path.exists(helper_path):
        # 直接使用 certutil 安装 CA (通过 admin_helper)
        params = f'"{helper_path}" mitm_ca_trust "{CA_CERT_FILE}" --flag "{flag_file}"'
    else:
        # Popen fallback (should not happen if packaged correctly)
        _log("错误: 找不到 admin_helper.py", log_func)
        return False

    try:
        ret = _runas_python_with_reset(params)
        if int(ret) <= 32:
            _log(f"UAC 提权失败 (code={ret})", log_func)
            return False

        for _ in range(30):
            if os.path.exists(flag_file):
                _safe_remove(flag_file)
                if _is_ca_trusted(log_func):
                    _log("CA 证书已安装", log_func)
                    return True
                _log("CA 安装完成但未在系统信任存储中验证到证书", log_func)
                return False
            time.sleep(1)

        _log("CA 安装超时", log_func)
        return False
    except Exception as e:
        _log(f"CA 安装失败: {e}", log_func)
        return False


# ==================== 系统代理管理 (Global Proxy) ====================
def _get_proxy_settings():
    """获取当前系统代理设置"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_READ
        )
        settings = {}
        for name in ["ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL", "AutoDetect"]:
            try:
                val, _ = winreg.QueryValueEx(key, name)
                settings[name] = val
            except FileNotFoundError:
                settings[name] = None
        winreg.CloseKey(key)
        return settings
    except Exception:
        return {}


def _build_proxy_server_value(proxy_port):
    return (
        f"http=127.0.0.1:{int(proxy_port)};"
        f"https=127.0.0.1:{int(proxy_port)}"
    )


def _proxy_settings_match(settings, proxy_port):
    expected = _build_proxy_server_value(proxy_port).lower()
    return (
        settings.get("ProxyEnable") == 1
        and str(settings.get("ProxyServer") or "").strip().lower() == expected
        and not settings.get("AutoConfigURL")
        and settings.get("AutoDetect") in (None, 0)
    )


def _set_global_proxy(proxy_port, log_func=None):
    """设置系统全局代理指向 mitmproxy"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )

        proxy_server = _build_proxy_server_value(proxy_port)

        # 启用代理
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        # 设置代理服务器
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
        
        # 清除 PAC (防止冲突/优先使用 PAC)
        try:
            winreg.DeleteValue(key, "AutoConfigURL")
        except FileNotFoundError:
            pass

        # 关闭自动检测，避免 WPAD/PAC 抢占手动代理优先级
        winreg.SetValueEx(key, "AutoDetect", 0, winreg.REG_DWORD, 0)

        # 清除 ProxyOverride (设为 <local> 排除本地)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")

        winreg.CloseKey(key)

        # 通知 WinINET
        import ctypes
        ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)
        ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)

        actual = _get_proxy_settings()
        if not _proxy_settings_match(actual, proxy_port):
            _log(f"系统代理写入后校验失败: {actual}", log_func)
            return False

        _log(f"系统全局代理已设置: {proxy_server}", log_func)
        return True
    except Exception as e:
        _log(f"设置全局代理失败: {e}", log_func)
        return False


def _set_direct_network_for_caddy(log_func=None):
    """hosts+Caddy 模式需要目标域名直连本机 hosts，不能继续走系统代理/PAC。"""
    global _original_proxy_settings

    settings = _get_proxy_settings()
    if _looks_like_stale_local_proxy(settings):
        _original_proxy_settings = None
        _log(f"检测到失效的本地代理残留，已清理而不再恢复: {settings.get('ProxyServer')}", log_func)
    else:
        _original_proxy_settings = settings

    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )

        try:
            winreg.DeleteValue(key, "AutoConfigURL")
        except FileNotFoundError:
            pass
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(key, "AutoDetect", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)

        import ctypes
        ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)
        ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)

        _log("hosts+Caddy 模式已临时关闭系统代理/PAC，停止覆盖时会恢复原设置", log_func)
        return True
    except Exception as e:
        _log(f"设置 hosts+Caddy 直连网络失败: {e}", log_func)
        return False


def _detect_upstream_proxy(settings=None):
    settings = _get_proxy_settings() if settings is None else settings
    if not settings or settings.get("ProxyEnable") != 1:
        return None, settings

    server = settings.get("ProxyServer")
    if not server:
        return None, settings

    if "=" not in server:
        return _normalize_upstream_proxy(server), settings

    for part in server.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() in ("http", "https"):
            value = value.strip()
            if value:
                return _normalize_upstream_proxy(value), settings
    return None, settings


def _self_test_override(proxy_port, expected_names, log_func=None):
    """通过本地代理访问目标 URL，验证是否返回替换后的名单"""
    expected_markers = []
    for n in expected_names or ():
        name = str(n or "").strip()
        if name:
            expected_markers.append(SEPARATOR + name)
    if not expected_markers:
        return True

    try:
        import ssl
        import urllib.request
    except Exception as e:
        _log(f"代理自检失败: 无法导入 urllib ({e})", log_func)
        return False

    proxy = f"http://127.0.0.1:{int(proxy_port)}"
    insecure_ctx = ssl._create_unverified_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
        urllib.request.HTTPSHandler(context=insecure_ctx),
    )

    for idx in range(1, 5):
        try:
            req = urllib.request.Request(
                f"{PASTEBIN_URL}?_ts={int(time.time() * 1000)}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with opener.open(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            if any(marker in body for marker in expected_markers):
                _log(f"代理自检通过 (第 {idx} 次尝试)", log_func)
                return True
            _log(f"代理自检未命中替换内容 (第 {idx} 次)", log_func)
        except Exception as e:
            _log(f"代理自检请求失败 (第 {idx} 次): {e}", log_func)
        time.sleep(0.6)

    if os.path.exists(HIT_LOG_FILE):
        _log(f"可查看命中日志: {HIT_LOG_FILE}", log_func)
    return False


def _restore_proxy(log_func=None):
    """恢复原始代理设置"""
    global _original_proxy_settings
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )
        if _original_proxy_settings:
            # 恢复原始设置
            for name, val in _original_proxy_settings.items():
                if val is not None:
                    reg_type = winreg.REG_DWORD if isinstance(val, int) else winreg.REG_SZ
                    winreg.SetValueEx(key, name, 0, reg_type, val)
                else:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        else:
            # 安全恢复: 删除 PAC URL, 禁用手动代理
            try:
                winreg.DeleteValue(key, "AutoConfigURL")
            except FileNotFoundError:
                pass
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)

        # 通知 WinINET
        import ctypes
        ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)
        ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)

        _original_proxy_settings = None
        _log("系统代理已恢复", log_func)
    except Exception as e:
        _log(f"恢复代理失败: {e}", log_func)


# ==================== mitmdump 进程管理 ====================
def _get_mitmdump_path():
    """获取 mitmdump 可执行文件路径"""
    # OneFile: 先复制到数据目录运行，避免退出时 _MEI 占用导致清理失败
    resource_tools = os.path.join(_get_resource_dir(), "tools", "mitmdump.exe")
    data_tools = _get_data_tools_dir()
    data_mitmdump = os.path.join(data_tools, "mitmdump.exe")
    if os.path.exists(resource_tools):
        try:
            os.makedirs(data_tools, exist_ok=True)
            need_copy = (
                not os.path.exists(data_mitmdump)
                or os.path.getsize(data_mitmdump) != os.path.getsize(resource_tools)
            )
            if need_copy:
                shutil.copy2(resource_tools, data_mitmdump)
            return data_mitmdump
        except Exception:
            # 复制失败则回退到资源目录
            return resource_tools

    # 其次检查数据目录(例如历史残留但当前资源缺失)
    if os.path.exists(data_mitmdump):
        return data_mitmdump

    # 再检查资源目录下的 tools
    if os.path.exists(resource_tools):
        return resource_tools

    # 兼容开发环境
    venv_dir = os.path.dirname(sys.executable)
    # 尝试 venv/mitmdump.exe
    mitmdump_path = os.path.join(venv_dir, "mitmdump.exe")
    if os.path.exists(mitmdump_path):
        return mitmdump_path
    
    scripts_path = os.path.join(venv_dir, "Scripts", "mitmdump.exe")
    if os.path.exists(scripts_path):
        return scripts_path

    # 回退到 Path
    return "mitmdump"


def _start_mitmdump(addon_path, listen_port, upstream_proxy=None, log_func=None):
    """启动 mitmdump 进程"""
    global _mitm_process

    mitmdump = _get_mitmdump_path()
    _log(f"mitmdump 路径: {mitmdump}", log_func)

    cmd = [
        mitmdump,
        "--listen-host", "127.0.0.1",
        "--listen-port", str(int(listen_port)),
        "--ssl-insecure",                 # 不验证上游证书
        "--set", "flow_detail=0",         # 减少日志
        # 仅允许 pastebin.com 流量进入 mitm 处理，行为与 fish 版本保持一致。
        "--allow-hosts", r"^(?:www\.)?pastebin\.com(?::\d+)?$",
        "-s", addon_path,                 # addon 脚本
        "--quiet",                        # 安静模式
    ]

    if upstream_proxy:
        _log(f"检测到上游代理: {upstream_proxy}，启用 Upstream 模式", log_func)
        cmd.extend(["--mode", f"upstream:{upstream_proxy}"])
    else:
        _log("未检测到上游代理，启用常规代理模式", log_func)

    try:
        _mitm_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        time.sleep(2)

        if _mitm_process.poll() is not None:
            stdout = _mitm_process.stdout.read().decode('utf-8', errors='replace')
            stderr = _mitm_process.stderr.read().decode('utf-8', errors='replace')
            _log(f"mitmdump 启动失败 (code={_mitm_process.returncode})", log_func)
            if stdout: _log(f"STDOUT: {stdout[:500]}", log_func)
            if stderr: _log(f"STDERR: {stderr[:500]}", log_func)
            _mitm_process = None
            return False

        _log(f"mitmdump 已启动 (PID={_mitm_process.pid}, 端口={listen_port})", log_func)
        return True
    except FileNotFoundError:
        _log("mitmdump 未找到，请安装: pip install mitmproxy", log_func)
        return False
    except Exception as e:
        _log(f"启动 mitmdump 失败: {e}", log_func)
        return False


def _stop_mitmdump(log_func=None):
    """停止 mitmdump"""
    global _mitm_process
    if _mitm_process:
        proc = _mitm_process
        pid = None
        try:
            pid = proc.pid
        except Exception:
            pass
        try:
            if proc.poll() is None:
                try:
                    proc.terminate()  # 尝试优雅退出
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()   # 强制退出
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        pass
        except Exception:
            pass
        # 补充清理进程树，防止残留句柄占用 _MEI
        if os.name == 'nt' and pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        _close_process_pipes(proc)
        _mitm_process = None
        _log("mitmdump 已停止", log_func)


def _close_process_pipes(proc):
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None)
        if stream:
            try:
                stream.close()
            except Exception:
                pass


def _kill_stale_mitmdump_processes(log_func=None, skip_current=True):
    """清理本工具残留的 mitmdump，避免旧代理进程占端口或继续拦截。"""
    if os.name != "nt":
        return

    current_pid = None
    if skip_current and _mitm_process:
        try:
            current_pid = int(_mitm_process.pid)
        except Exception:
            current_pid = None

    rc, out, err = _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"name='mitmdump.exe'\" "
        "| Select-Object ProcessId,ExecutablePath,CommandLine "
        "| ConvertTo-Json -Compress"
    )
    if rc != 0 or not out.strip():
        return

    try:
        items = json.loads(out)
    except Exception:
        _log(f"读取 mitmdump 进程列表失败: {err or out[:120]}", log_func)
        return

    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return

    tags = (
        "\\_mei",
        "slashcomonitor",
        "slashcosense",
        "sponsor_override_hits.log",
        "mitm_addon.py",
    )
    cleaned = 0
    for proc in items:
        try:
            pid = int(proc.get("ProcessId") or 0)
        except Exception:
            pid = 0
        if pid <= 0:
            continue
        if current_pid and pid == current_pid:
            continue

        exe = str(proc.get("ExecutablePath") or "").lower()
        cmd = str(proc.get("CommandLine") or "").lower()
        if "\\_mei" in exe or any(tag in cmd for tag in tags):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=6,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                cleaned += 1
                _log(f"清理残留 mitmdump 进程: PID={pid}", log_func)
            except Exception:
                pass

    if cleaned:
        time.sleep(0.4)


# ==================== Caddy / hosts 模式 ====================
def _get_caddy_path():
    resource_caddy = os.path.join(_get_resource_dir(), "tools", "caddy", "caddy.exe")
    data_caddy = os.path.join(CADDY_DIR, "caddy.exe")
    if os.path.exists(resource_caddy):
        try:
            os.makedirs(CADDY_DIR, exist_ok=True)
            need_copy = (
                not os.path.exists(data_caddy)
                or os.path.getsize(data_caddy) != os.path.getsize(resource_caddy)
            )
            if need_copy:
                shutil.copy2(resource_caddy, data_caddy)
            return data_caddy
        except Exception:
            return resource_caddy

    if os.path.exists(data_caddy):
        return data_caddy
    return resource_caddy


def _load_ca_cert_and_key():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except Exception as e:
        raise RuntimeError(f"无法导入 cryptography: {e}")

    if not os.path.exists(CA_PEM_FILE):
        raise RuntimeError(f"找不到 mitmproxy CA 私钥: {CA_PEM_FILE}")

    with open(CA_PEM_FILE, "rb") as f:
        ca_pem = f.read()

    ca_key = serialization.load_pem_private_key(ca_pem, password=None)

    cert_blocks = []
    marker_begin = b"-----BEGIN CERTIFICATE-----"
    marker_end = b"-----END CERTIFICATE-----"
    start = 0
    while True:
        begin = ca_pem.find(marker_begin, start)
        if begin < 0:
            break
        end = ca_pem.find(marker_end, begin)
        if end < 0:
            break
        end += len(marker_end)
        cert_blocks.append(ca_pem[begin:end] + b"\n")
        start = end

    if not cert_blocks:
        raise RuntimeError("mitmproxy CA PEM 中没有证书")

    ca_cert = x509.load_pem_x509_certificate(cert_blocks[0])
    return ca_cert, ca_key


def _generate_caddy_leaf_cert(log_func=None):
    """为 hosts+Caddy 模式生成 pastebin.com 证书，复用已信任的 mitmproxy CA。"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except Exception as e:
        _log(f"生成 Caddy 证书失败: 无法导入 cryptography ({e})", log_func)
        return False

    try:
        ca_cert, ca_key = _load_ca_cert_and_key()
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "pastebin.com"),
        ])
        san = x509.SubjectAlternativeName([
            x509.DNSName("pastebin.com"),
            x509.DNSName("www.pastebin.com"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(san, critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(private_key=ca_key, algorithm=hashes.SHA256())
        )

        os.makedirs(CADDY_DIR, exist_ok=True)
        with open(CADDY_CERT_FILE, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(CADDY_KEY_FILE, "wb") as f:
            f.write(
                leaf_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
        return True
    except Exception as e:
        _log(f"生成 Caddy 证书失败: {e}", log_func)
        return False


def _generate_caddyfile(content_file_path):
    os.makedirs(CADDY_DIR, exist_ok=True)
    caddyfile = f'''{{
    auto_https off
    admin off
    log {{
        output file "{_caddy_path_value(CADDY_LOG_FILE)}"
        level WARN
    }}
}}

https://pastebin.com:443, https://www.pastebin.com:443 {{
    tls "{_caddy_path_value(CADDY_CERT_FILE)}" "{_caddy_path_value(CADDY_KEY_FILE)}"
    @sponsor path /raw/2WVJpW1N /raw/2WVJpW1N/
    handle @sponsor {{
        root * "{_caddy_path_value(os.path.dirname(content_file_path))}"
        rewrite * /{os.path.basename(content_file_path)}
        file_server
        header Content-Type "text/plain; charset=utf-8"
        header Cache-Control "no-store, no-cache, must-revalidate, max-age=0"
        header Pragma "no-cache"
        header Expires "0"
    }}
    respond "Not Found" 404
}}
'''
    with open(CADDYFILE, "w", encoding="utf-8") as f:
        f.write(caddyfile)
    return CADDYFILE


def _start_caddy(caddyfile_path, log_func=None):
    global _caddy_process

    caddy = _get_caddy_path()
    if not os.path.exists(caddy):
        _log(f"caddy.exe 未找到: {caddy}", log_func)
        return False

    cmd = [caddy, "run", "--config", caddyfile_path, "--adapter", "caddyfile"]
    try:
        _caddy_process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(caddy),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        time.sleep(1.5)
        if _caddy_process.poll() is not None:
            stdout = _caddy_process.stdout.read().decode("utf-8", errors="replace")
            stderr = _caddy_process.stderr.read().decode("utf-8", errors="replace")
            _log(f"Caddy 启动失败 (code={_caddy_process.returncode})", log_func)
            if stdout:
                _log(f"Caddy STDOUT: {stdout[:500]}", log_func)
            if stderr:
                _log(f"Caddy STDERR: {stderr[:500]}", log_func)
            _caddy_process = None
            return False

        _log(f"Caddy 已启动 (PID={_caddy_process.pid}, 端口=443)", log_func)
        return True
    except Exception as e:
        _log(f"启动 Caddy 失败: {e}", log_func)
        _caddy_process = None
        return False


def _stop_caddy(log_func=None):
    global _caddy_process
    if not _caddy_process:
        return

    proc = _caddy_process
    pid = None
    try:
        pid = proc.pid
    except Exception:
        pass

    try:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
    except Exception:
        pass

    if os.name == "nt" and pid:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass
    _close_process_pipes(proc)
    _caddy_process = None
    _log("Caddy 已停止", log_func)


def _run_admin_hosts(mode, marker, flag_file, log_func=None):
    resource_dir = _get_resource_dir()
    helper_path = os.path.join(resource_dir, "admin_helper.py")
    if not os.path.exists(helper_path):
        _log("错误: 找不到 admin_helper.py", log_func)
        return False

    _safe_remove(flag_file)
    params = f'"{helper_path}" {mode} "pastebin.com" "{marker}" --flag "{flag_file}"'
    try:
        ret = _runas_python_with_reset(params)
        if int(ret) <= 32:
            _log(f"UAC 提权失败 (code={ret})", log_func)
            return False
        if _wait_for_flag(flag_file, timeout_seconds=25):
            return True
        _log("hosts 修改等待超时", log_func)
        return False
    except Exception as e:
        _log(f"hosts 修改失败: {e}", log_func)
        return False


def _install_caddy_hosts(log_func=None):
    return _run_admin_hosts("sponsor_hosts_add", CADDY_HOST_MARKER, CADDY_HOSTS_ADD_FLAG, log_func)


def _remove_caddy_hosts(log_func=None):
    return _run_admin_hosts("sponsor_hosts_remove", CADDY_HOST_MARKER, CADDY_HOSTS_REMOVE_FLAG, log_func)


def _self_test_caddy_override(expected_names, log_func=None):
    expected_markers = []
    for n in expected_names or ():
        name = str(n or "").strip()
        if name:
            expected_markers.append(SEPARATOR + name)
    if not expected_markers:
        return True

    try:
        import ssl
        import urllib.request
    except Exception as e:
        _log(f"Caddy 自检失败: 无法导入 urllib ({e})", log_func)
        return False

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
    )
    for idx in range(1, 5):
        try:
            req = urllib.request.Request(
                f"{PASTEBIN_URL}?_ts={int(time.time() * 1000)}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with opener.open(req, timeout=8) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            if any(marker in body for marker in expected_markers):
                _log(f"Caddy 自检通过 (第 {idx} 次尝试)", log_func)
                return True
            _log(f"Caddy 自检未命中替换内容 (第 {idx} 次)", log_func)
        except Exception as e:
            _log(f"Caddy 自检请求失败 (第 {idx} 次): {e}", log_func)
        time.sleep(0.6)
    return False


# ==================== 旧方案清理 ====================
def _cleanup_old_caddy_residuals(log_func=None, stop_https_listener=False):
    """清理旧 Caddy 方案的残留 (hosts 条目 + 旧 Caddy 进程)"""
    import ctypes

    # 1. 停止残留旧进程
    # 仅在 hosts+Caddy 路径处理 443，避免 mitmdump 模式误杀用户自己的 HTTPS 服务。
    ports_to_check = ["443"] if stop_https_listener else []
    if _active_proxy_port:
        ports_to_check.append(str(_active_proxy_port))

    for port in ports_to_check:
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            for line in result.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                            )
                            _log(f"已停止占用端口 {port} 的残留进程 (PID={pid})", log_func)
                        except Exception:
                            pass
        except Exception:
            pass

    # 简单检测是否需要清理
    hosts_file = r"C:\Windows\System32\drivers\etc\hosts"
    markers = ["# SlashCoCaddy", "# SlashCoSponsorProxy"]
    needs_cleanup = False
    try:
        with open(hosts_file, 'r', encoding='utf-8') as f:
            content = f.read()
        needs_cleanup = any(m in content for m in markers)
    except Exception:
        pass
    
    if needs_cleanup:
        _log("发现旧 hosts 配置，准备清理...", log_func)
        for idx, marker in enumerate(markers):
            flag_file = os.path.join(_get_data_dir(), "tools", f"_hosts_cleanup_{idx}_done")
            try:
                _run_admin_hosts("sponsor_hosts_remove", marker, flag_file, log_func)
            except Exception:
                pass
        _log("旧 hosts 条目清理完成", log_func)


# ==================== 主要 API ====================
def _prepare_modified_sponsor_content(name_list, log_func=None):
    _log("步骤 1/5: 下载原始名单...", log_func)
    original = fetch_original_sponsors(log_func)
    if not original:
        sponsors_candidates = [
            os.path.join(_get_data_dir(), "sponsors.txt"),
            os.path.join(_get_resource_dir(), "sponsors.txt"),
        ]
        for sponsors_path in sponsors_candidates:
            if not os.path.exists(sponsors_path):
                continue
            try:
                with open(sponsors_path, 'r', encoding='utf-8') as f:
                    original = f.read()
                _log(f"使用本地 sponsors.txt 作为备用: {sponsors_path}", log_func)
                break
            except Exception:
                pass
        if not original:
            return False, "无法获取赞助者名单"

    _log("步骤 2/5: 构建修改后的名单...", log_func)
    modified = build_modified_content(original, name_list)

    os.makedirs(os.path.dirname(MODIFIED_CONTENT_FILE), exist_ok=True)
    with open(MODIFIED_CONTENT_FILE, 'w', encoding='utf-8') as f:
        f.write(modified)
    _log(f"名单已保存 ({len(modified)} 字符)", log_func)
    return True, ""


def start_sponsor_override(name, log_func=None, mode=SPONSOR_MODE_MITM):
    selected_mode = normalize_sponsor_mode(mode)
    if selected_mode == SPONSOR_MODE_CADDY:
        return _start_sponsor_override_caddy(name, log_func=log_func)
    return _start_sponsor_override_mitm(name, log_func=log_func)


def _start_sponsor_override_mitm(name, log_func=None):
    """
    启动赞助者名单覆盖 (mitmproxy + Global Proxy)
    """
    global _running, _original_proxy_settings, _active_proxy_port, _active_mode

    with _lock:
        if _running:
            return True, "已在运行中"

    if not name:
        return False, "请先输入显示名称"

    name_list = _parse_sponsor_names(name)
    if not name_list:
        return False, "请先输入显示名称"

    # log 显示
    name_display = ", ".join(name_list)
    _log(f"启动赞助者覆盖: {name_display}", log_func)

    startup_proxy_settings = _get_proxy_settings()

    # 0. 强制清理残留 (旧进程 + 旧代理设置 + Hosts)
    _cleanup_old_caddy_residuals(log_func)
    _active_proxy_port = None
    
    # 尝试运行 hosts 清理工具
    try:
        # cleanup_hosts.py 在资源目录的 tools 中
        cleanup_script = os.path.join(_get_resource_dir(), "tools", "cleanup_hosts.py")
        if os.path.exists(cleanup_script):
             _run_python_script(cleanup_script)
    except Exception:
        pass

    if _original_proxy_settings is not None:
        _restore_proxy(log_func)  # 恢复本进程上次启动时保存的代理设置
        startup_proxy_settings = _get_proxy_settings()
    else:
        _log("保留当前系统代理设置，稍后用于停止恢复和上游代理", log_func)

    ok, message = _prepare_modified_sponsor_content(name_list, log_func)
    if not ok:
        return False, message

    # 3. 安装 CA 证书 (首次)
    _log("步骤 3/5: 检查 CA 证书...", log_func)
    if not _is_ca_trusted(log_func):
        _ensure_ca_generated(log_func)
        if not _trust_ca(log_func):
            return False, "CA 证书安装失败 (需要管理员权限)"

    # 4. 启动 mitmproxy (自动检测上游代理)
    _log("步骤 4/5: 启动代理服务...", log_func)
    
    # 重新生成最新的 addon 脚本
    try:
        if os.path.exists(HIT_LOG_FILE):
            os.remove(HIT_LOG_FILE)
    except Exception:
        pass
    addon_path = _generate_addon_script(MODIFIED_CONTENT_FILE, HIT_LOG_FILE)

    _kill_stale_mitmdump_processes(log_func)
    upstream, _original_proxy_settings = _detect_upstream_proxy(startup_proxy_settings)

    selected_port = None
    attempted_ports = set()
    occupied_samples = []
    for candidate_port in _get_proxy_port_candidates(upstream_proxy=upstream):
        attempted_ports.add(candidate_port)
        # 跳过被占用端口
        if not _is_port_available(candidate_port):
            if len(occupied_samples) < 6:
                occupied_samples.append(candidate_port)
            continue

        if _start_mitmdump(addon_path, candidate_port, upstream, log_func):
            selected_port = candidate_port
            break

    if not selected_port:
        _log("固定候选端口不可用，尝试系统随机端口...", log_func)
        for _ in range(EPHEMERAL_PORT_ATTEMPTS):
            random_port = _pick_ephemeral_port(excluded_ports=attempted_ports)
            if not random_port:
                break
            attempted_ports.add(random_port)
            if _start_mitmdump(addon_path, random_port, upstream, log_func):
                selected_port = random_port
                break

    if not selected_port:
        if occupied_samples:
            _log(
                f"端口占用示例: {', '.join(str(x) for x in occupied_samples)}",
                log_func,
            )
        return False, "mitmproxy 启动失败（本地端口不可用）"

    if selected_port != PROXY_PORT:
        _log(f"默认端口 {PROXY_PORT} 不可用，已切换到 {selected_port}", log_func)

    # 6. 设置全局代理
    if not _set_global_proxy(selected_port, log_func):
        _stop_mitmdump(log_func)
        _active_proxy_port = None
        return False, "设置系统代理失败"

    # 双重保险: 等待 1 秒后再次静默刷新
    time.sleep(1.0)
    _set_global_proxy(selected_port, None)

    # 5. 自检代理链路
    _log("步骤 5/5: 验证拦截链路...", log_func)
    if not _self_test_override(selected_port, name_list, log_func):
        _restore_proxy(log_func)
        _stop_mitmdump(log_func)
        _active_proxy_port = None
        return False, "代理已启动但拦截未生效（请检查加速器代理模式/浏览器直连设置）"

    with _lock:
        _running = True
        _active_proxy_port = selected_port
        _active_mode = SPONSOR_MODE_MITM

    # 注册退出清理
    atexit.register(lambda: stop_sponsor_override(log_func))

    _log("✓ 赞助者名单覆盖已启动! (系统代理模式)", log_func)
    return True, "运行中"


def _start_sponsor_override_caddy(name, log_func=None):
    """
    启动赞助者名单覆盖 (hosts + Caddy)
    """
    global _running, _active_mode, _active_proxy_port

    with _lock:
        if _running:
            return True, "已在运行中"

    name_list = _parse_sponsor_names(name)
    if not name_list:
        return False, "请先输入显示名称"

    name_display = ", ".join(name_list)
    _log(f"启动赞助者覆盖: {name_display} (hosts + Caddy)", log_func)

    _cleanup_old_caddy_residuals(log_func, stop_https_listener=True)
    _active_proxy_port = None

    ok, message = _prepare_modified_sponsor_content(name_list, log_func)
    if not ok:
        return False, message

    _log("步骤 3/5: 检查 CA 证书...", log_func)
    if not _ensure_ca_generated(log_func):
        return False, "CA 证书生成失败"
    if not _is_ca_trusted(log_func):
        if not _trust_ca(log_func):
            return False, "CA 证书安装失败 (需要管理员权限)"

    if not _generate_caddy_leaf_cert(log_func):
        return False, "Caddy 证书生成失败"

    _kill_stale_mitmdump_processes(log_func)
    if not _set_direct_network_for_caddy(log_func):
        return False, "设置直连网络失败"

    _log("步骤 4/5: 启动 Caddy HTTPS 服务...", log_func)
    caddyfile = _generate_caddyfile(MODIFIED_CONTENT_FILE)
    if not _start_caddy(caddyfile, log_func):
        _restore_proxy(log_func)
        return False, "Caddy 启动失败（可能是 443 端口被占用）"

    if not _install_caddy_hosts(log_func):
        _stop_caddy(log_func)
        _restore_proxy(log_func)
        return False, "写入 hosts 失败 (需要管理员权限)"

    _log("步骤 5/5: 验证 hosts+Caddy 拦截链路...", log_func)
    if not _self_test_caddy_override(name_list, log_func):
        _remove_caddy_hosts(log_func)
        _stop_caddy(log_func)
        _restore_proxy(log_func)
        return False, "hosts+Caddy 已启动但拦截未生效"

    with _lock:
        _running = True
        _active_mode = SPONSOR_MODE_CADDY

    atexit.register(lambda: stop_sponsor_override(log_func))

    _log("✓ 赞助者名单覆盖已启动! (hosts + Caddy 模式)", log_func)
    return True, "运行中"


def stop_sponsor_override(log_func=None):
    """停止赞助者名单覆盖并清理"""
    global _running, _active_proxy_port, _active_mode

    with _lock:
        if not _running:
            return
        active_mode = _active_mode

    _log("正在停止...", log_func)

    if active_mode == SPONSOR_MODE_CADDY:
        _remove_caddy_hosts(log_func)
        _stop_caddy(log_func)
        _restore_proxy(log_func)
        with _lock:
            _running = False
            _active_proxy_port = None
            _active_mode = None
        _log("✓ 已停止，hosts 和网络设置已恢复", log_func)
        return

    # 1. 恢复系统代理
    _restore_proxy(log_func)

    # 2. 停止 mitmdump
    _stop_mitmdump(log_func)
    _kill_stale_mitmdump_processes(log_func, skip_current=False)

    with _lock:
        _running = False
        _active_proxy_port = None
        _active_mode = None

    _log("✓ 已停止，系统代理已恢复", log_func)


def force_cleanup(log_func=None):
    """强制清理代理、进程和旧方案残留，用于异常退出兜底。"""
    global _running, _active_proxy_port, _active_mode

    should_restore_proxy = (
        _running
        or _active_proxy_port is not None
        or _original_proxy_settings is not None
    )
    if should_restore_proxy:
        try:
            _restore_proxy(log_func)
        except Exception:
            pass

    try:
        _stop_mitmdump(log_func)
    except Exception:
        pass

    try:
        _kill_stale_mitmdump_processes(log_func, skip_current=False)
    except Exception:
        pass

    should_cleanup_caddy = _active_mode == SPONSOR_MODE_CADDY or _caddy_process is not None
    if should_cleanup_caddy:
        try:
            _remove_caddy_hosts(log_func)
        except Exception:
            pass
        try:
            _stop_caddy(log_func)
        except Exception:
            pass
        try:
            _cleanup_old_caddy_residuals(log_func, stop_https_listener=True)
        except Exception:
            pass

    with _lock:
        _running = False
        _active_proxy_port = None
        _active_mode = None


def is_running():
    """检查是否正在运行"""
    return _running
