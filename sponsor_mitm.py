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


def _get_data_dir():
    """获取数据目录 (可通过环境变量覆盖，默认工作目录)"""
    custom_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    if custom_dir:
        return custom_dir
    return _get_work_dir()


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
PASTEBIN_URL = "https://pastebin.com/raw/2WVJpW1N"
SEPARATOR = "\u2674"  # ♴  (正确分隔符)
WRONG_SEPARATORS = ["\u2634", "\u2734"]  # 旧代码使用的错误分隔符

# 配置文件和临时文件放在数据目录 (可写)
MODIFIED_CONTENT_FILE = os.path.join(_get_data_dir(), "sponsors.dat")
ADDON_SCRIPT_FILE = os.path.join(_get_data_dir(), "tools", "mitm_addon.py")
HIT_LOG_FILE = os.path.join(_get_data_dir(), "tools", "sponsor_override_hits.log")
CA_INSTALLED_FLAG = os.path.join(_get_data_dir(), "tools", ".mitm_ca_trusted")
CA_CERT_FILE = os.path.join(os.path.expanduser("~"), ".mitmproxy", "mitmproxy-ca-cert.cer")

# ==================== 全局状态 ====================
_mitm_process = None
_running = False
_lock = threading.Lock()
_original_proxy_settings = None
_active_proxy_port = None


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


def _get_proxy_port_candidates():
    """生成代理端口候选列表"""
    candidates = [PROXY_PORT, 18080, 28080, 38080, 48080, 58080]
    candidates.extend(range(20000, 20050))
    seen = set()
    ordered = []
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    return ordered


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

    os.makedirs(os.path.dirname(addon_code_path := ADDON_SCRIPT_FILE), exist_ok=True)
    with open(ADDON_SCRIPT_FILE, 'w', encoding='utf-8') as f:
        f.write(addon_code)
    return ADDON_SCRIPT_FILE


# ==================== CA 证书管理 ====================
def _ensure_ca_generated(log_func=None):
    """确保 mitmproxy CA 证书已生成"""
    if os.path.exists(CA_CERT_FILE):
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

    return os.path.exists(CA_CERT_FILE)


def _is_ca_trusted():
    """检查 mitmproxy CA 是否已安装到 Windows 信任存储"""
    return os.path.exists(CA_INSTALLED_FLAG)


def _trust_ca(log_func=None):
    """安装 mitmproxy CA 到 Windows 信任存储"""
    if _is_ca_trusted():
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
    
    tools_dir = os.path.dirname(CA_INSTALLED_FLAG) # tools dir in WORK dir
    flag_file = CA_INSTALLED_FLAG

    if os.path.exists(flag_file):
        try:
            os.remove(flag_file)
        except Exception:
            pass

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
                try:
                    os.remove(flag_file)
                except Exception:
                    pass
                os.makedirs(tools_dir, exist_ok=True)
                with open(CA_INSTALLED_FLAG, 'w') as f:
                    f.write("trusted")
                _log("CA 证书已安装", log_func)
                return True
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
        for name in ["ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"]:
            try:
                val, _ = winreg.QueryValueEx(key, name)
                settings[name] = val
            except FileNotFoundError:
                settings[name] = None
        winreg.CloseKey(key)
        return settings
    except Exception:
        return {}


def _set_global_proxy(proxy_port, log_func=None):
    """设置系统全局代理指向 mitmproxy"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )
        
        proxy_server = f"127.0.0.1:{int(proxy_port)}"
        
        # 启用代理
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        # 设置代理服务器
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
        
        # 清除 PAC (防止冲突/优先使用 PAC)
        try:
            winreg.DeleteValue(key, "AutoConfigURL")
        except FileNotFoundError:
            pass
            
        # 清除 ProxyOverride (设为 <local> 排除本地)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")

        winreg.CloseKey(key)

        # 通知 WinINET
        import ctypes
        ctypes.windll.Wininet.InternetSetOptionW(0, 39, 0, 0)
        ctypes.windll.Wininet.InternetSetOptionW(0, 37, 0, 0)

        _log(f"系统全局代理已设置: {proxy_server}", log_func)
        return True
    except Exception as e:
        _log(f"设置全局代理失败: {e}", log_func)
        return False


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
        # --mode upstream:http://hostname:port
        cmd.extend(["--mode", f"upstream:http://{upstream_proxy}"])
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
        pid = None
        try:
            pid = _mitm_process.pid
        except Exception:
            pass
        try:
            _mitm_process.terminate()  # 尝试优雅退出
            try:
                _mitm_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _mitm_process.kill()   # 强制退出
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
        _mitm_process = None
        _log("mitmdump 已停止", log_func)


# ==================== 旧方案清理 ====================
def _cleanup_old_caddy_residuals(log_func=None):
    """清理旧 Caddy 方案的残留 (hosts 条目 + 旧 Caddy 进程)"""
    import ctypes

    # 1. 停止残留旧进程
    # 仅处理 443(旧 Caddy) + 当前运行端口，避免误杀用户自己的 8080 代理软件
    ports_to_check = ["443"]
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

    # 2. 清理 hosts 文件 (需要管理员权限)
    # 通过 admin_helper 清理
    resource_dir = _get_resource_dir()
    helper_path = os.path.join(resource_dir, "admin_helper.py")
    flag_file = os.path.join(_get_data_dir(), "tools", "_hosts_cleanup_done")

    if os.path.exists(flag_file):
        try:
            os.remove(flag_file)
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
        try:
            params = f'"{helper_path}" remove "pastebin.com" "# SlashCoCaddy" --flag "{flag_file}"'
            ret = _runas_python_with_reset(params)
            if int(ret) > 32:
                for _ in range(15):
                    if os.path.exists(flag_file):
                        try:
                            os.remove(flag_file)
                        except Exception:
                            pass
                        _log("旧 hosts 条目已清理", log_func)
                        subprocess.run(
                            ["ipconfig", "/flushdns"],
                            capture_output=True, timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        )
                        break
                    time.sleep(1)
        except Exception:
            pass


# ==================== 主要 API ====================
def start_sponsor_override(name, log_func=None):
    """
    启动赞助者名单覆盖 (mitmproxy + Global Proxy)
    """
    global _running, _original_proxy_settings, _active_proxy_port

    with _lock:
        if _running:
            return True, "已在运行中"

    if not name:
        return False, "请先输入显示名称"

    # 支持列表或字符串
    if isinstance(name, str):
        if not name.strip():
             return False, "请先输入显示名称"
        name_list = [n.strip() for n in name.replace("，", ",").split(",") if n.strip()]
        if not name_list:
             return False, "请先输入显示名称"
    else:
        name_list = [n.strip() for n in name if n.strip()]

    # log 显示
    name_display = ", ".join(name_list)
    _log(f"启动赞助者覆盖: {name_display}", log_func)

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

    _restore_proxy(log_func)  # 确保清理上次可能残留的代理设置

    # 1. 下载原始名单 (在设置代理之前, 绕过代理直连)
    _log("步骤 1/5: 下载原始名单...", log_func)
    original = fetch_original_sponsors(log_func)
    if not original:
        # 优先读取数据目录下的 sponsors.txt，其次读取 bundled 的 sponsors.txt
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

    # 2. 构建修改后的内容
    _log("步骤 2/5: 构建修改后的名单...", log_func)
    modified = build_modified_content(original, name_list)
    
    os.makedirs(os.path.dirname(MODIFIED_CONTENT_FILE), exist_ok=True)
    with open(MODIFIED_CONTENT_FILE, 'w', encoding='utf-8') as f:
        f.write(modified)
    _log(f"名单已保存 ({len(modified)} 字符)", log_func)

    # 3. 安装 CA 证书 (首次)
    _log("步骤 3/5: 检查 CA 证书...", log_func)
    if not _is_ca_trusted():
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
    
    _original_proxy_settings = _get_proxy_settings()
    upstream = None
    if _original_proxy_settings and _original_proxy_settings.get("ProxyEnable") == 1:
        srv = _original_proxy_settings.get("ProxyServer")
        if srv:
            # 简单解析: 如果是 "127.0.0.1:7890" 类型
            if "=" not in srv:
                upstream = srv
            else:
                # 复杂类型 "http=...;https=..."，尝试提取 http/https
                for part in srv.split(";"):
                    if "http=" in part or "https=" in part:
                        upstream = part.split("=", 1)[1]
                        break

    selected_port = None
    for candidate_port in _get_proxy_port_candidates():
        # 跳过被占用端口
        if not _is_port_available(candidate_port):
            continue

        # 防止死循环: 若上游代理和候选端口一致，则跳过
        if upstream and f":{candidate_port}" in upstream:
            continue

        if _start_mitmdump(addon_path, candidate_port, upstream, log_func):
            selected_port = candidate_port
            break

    if not selected_port:
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

    # 注册退出清理
    atexit.register(lambda: stop_sponsor_override(log_func))

    _log("✓ 赞助者名单覆盖已启动! (系统代理模式)", log_func)
    return True, "运行中"


def stop_sponsor_override(log_func=None):
    """停止赞助者名单覆盖并清理"""
    global _running, _active_proxy_port

    with _lock:
        if not _running:
            return

    _log("正在停止...", log_func)

    # 1. 恢复系统代理
    _restore_proxy(log_func)

    # 2. 停止 mitmdump
    _stop_mitmdump(log_func)

    with _lock:
        _running = False
        _active_proxy_port = None

    _log("✓ 已停止，系统代理已恢复", log_func)


def is_running():
    """检查是否正在运行"""
    return _running
