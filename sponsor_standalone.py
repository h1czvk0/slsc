# -*- coding: utf-8 -*-
import os
import sys

# Support running bundled helper scripts from a frozen EXE.
if __name__ == "__main__" and getattr(sys, "frozen", False) and len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
    script_path = sys.argv[1]
    sys.argv = sys.argv[1:]
    try:
        import runpy

        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"Error running script {script_path}: {e}")
    sys.exit(0)

import ctypes
import json
import queue
import threading
import shutil
import subprocess
import random
import socket
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_system_data_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return os.path.join(base, "SlashCoSponsor")
    return os.path.join(get_base_dir(), "SlashCoSponsor")


SYSTEM_DATA_DIR = get_system_data_dir()
os.makedirs(SYSTEM_DATA_DIR, exist_ok=True)
os.environ["SLASHCO_SPONSOR_DATA_DIR"] = SYSTEM_DATA_DIR

import sponsor_mitm


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


DEFAULT_SPONSOR_PROXY_PORT = 8080
COMMON_ACCELERATOR_PORTS = (7890, 7891, 9090, 1080, 10808, 10080)


def _parse_port(value):
    try:
        port = int(str(value).strip())
    except Exception:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _parse_port_from_proxy_server(proxy_server):
    text = str(proxy_server or "").strip()
    if not text:
        return None

    # ProxyServer could be:
    # 1) 127.0.0.1:7890
    # 2) http=127.0.0.1:7890;https=127.0.0.1:7890
    if "=" in text:
        for part in text.split(";"):
            if "http=" in part or "https=" in part:
                _, value = part.split("=", 1)
                text = value.strip()
                break

    if "://" in text:
        text = text.split("://", 1)[1]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if "/" in text:
        text = text.split("/", 1)[0]

    if text.startswith("[") and "]" in text:
        _, _, rest = text.partition("]")
        if rest.startswith(":"):
            return _parse_port(rest[1:])
        return None

    if ":" not in text:
        return None
    return _parse_port(text.rsplit(":", 1)[1])


def _is_local_port_available(port):
    parsed = _parse_port(port)
    if not parsed:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", parsed))
        return True
    except OSError:
        return False


def _build_port_candidates():
    candidates = [DEFAULT_SPONSOR_PROXY_PORT, 18080, 28080, 38080, 48080, 58080]
    candidates.extend(range(20000, 20120))

    rng = random.Random((int(datetime.now().timestamp() * 1000) ^ os.getpid()) & 0xFFFFFFFF)
    for _ in range(120):
        candidates.append(rng.randint(20000, 60999))

    ordered = []
    seen = set()
    for item in candidates:
        port = _parse_port(item)
        if not port or port in seen:
            continue
        seen.add(port)
        ordered.append(port)
    return ordered


class SponsorStandaloneApp:
    def __init__(self, root):
        self.root = root
        self.base_dir = get_base_dir()
        self.data_dir = SYSTEM_DATA_DIR
        self.config_path = os.path.join(self.data_dir, "sponsor_config.json")
        self.sponsors_txt_path = os.path.join(self.data_dir, "sponsors.txt")

        self._is_shutting_down = False
        self._log_queue = queue.Queue()
        self._log_queue_after_id = None
        self._operation_lock = threading.Lock()

        self._ensure_local_sponsors_txt()
        self.sponsor_enabled = tk.BooleanVar(value=False)
        self.sponsor_name = tk.StringVar(value="")
        self.sponsor_mode = tk.StringVar(value="mitm")
        self._load_config()

        self._setup_window()
        self._setup_ui()
        self._process_log_queue()

        self._update_sponsor_status("已停止", "gray")
        self.log("独立赞助替换工具已启动")
        self.log(f"数据目录: {self.data_dir}")

        if self.sponsor_enabled.get():
            if self.sponsor_name.get().strip():
                self._update_sponsor_status("等待自动启动...", "#f39c12")
                self.root.after(500, self._deferred_auto_start)
            else:
                self.log("配置为启用，但游戏ID为空，已跳过自动启动")
                self._set_enabled_and_save(False)

    def _ensure_local_sponsors_txt(self):
        if os.path.exists(self.sponsors_txt_path):
            return
        bundled = resource_path("sponsors.txt")
        if not os.path.exists(bundled):
            return
        try:
            shutil.copy2(bundled, self.sponsors_txt_path)
        except Exception:
            pass

    def _setup_window(self):
        self.root.title("SlashCoSponsor")
        self.root.geometry("620x430")
        self.root.minsize(520, 360)

        try:
            icon_path = resource_path("icon.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception:
            pass

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        sponsor_frame = ttk.LabelFrame(main, text="赞助者名单覆盖", padding=8)
        sponsor_frame.pack(fill=tk.X, padx=2, pady=(0, 8))

        row1 = ttk.Frame(sponsor_frame)
        row1.pack(fill=tk.X, pady=2)

        self.chk_sponsor = ttk.Checkbutton(
            row1,
            text="启用赞助者名单覆盖",
            variable=self.sponsor_enabled,
            command=self._on_sponsor_toggle,
        )
        self.chk_sponsor.pack(side=tk.LEFT)

        self.lbl_sponsor_status = ttk.Label(row1, text="", foreground="gray")
        self.lbl_sponsor_status.pack(side=tk.RIGHT)

        row2 = ttk.Frame(sponsor_frame)
        row2.pack(fill=tk.X, pady=4)

        ttk.Label(row2, text="你的游戏ID:").pack(side=tk.LEFT, padx=(0, 6))
        self.entry_sponsor_name = ttk.Entry(row2, textvariable=self.sponsor_name)
        self.entry_sponsor_name.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_save = ttk.Button(row2, text="保存", width=8, command=self._on_save_clicked)
        self.btn_save.pack(side=tk.LEFT, padx=(8, 0))

        row3 = ttk.Frame(sponsor_frame)
        row3.pack(fill=tk.X, pady=4)
        ttk.Label(row3, text="模式:").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(
            row3,
            text="mitmdump",
            variable=self.sponsor_mode,
            value="mitm",
            command=self._on_save_clicked,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(
            row3,
            text="hosts + Caddy",
            variable=self.sponsor_mode,
            value="caddy",
            command=self._on_save_clicked,
        ).pack(side=tk.LEFT)

        tutorial = (
            "在进入SlashCo前打开此功能\n"
            "进入地图后即可关闭\n"
            "注意：不关闭会导致无法下载模型"
        )
        ttk.Label(
            sponsor_frame,
            text=tutorial,
            foreground="#c0392b",
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(6, 0))

        log_frame = ttk.LabelFrame(main, text="日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.txt_log = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log.configure(state=tk.DISABLED)

    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.sponsor_enabled.set(bool(cfg.get("enabled", False)))
                self.sponsor_name.set(str(cfg.get("name", "")))
                mode = cfg.get("mode", "mitm")
                if hasattr(sponsor_mitm, "normalize_sponsor_mode"):
                    mode = sponsor_mitm.normalize_sponsor_mode(mode)
                self.sponsor_mode.set(mode if mode in ("mitm", "caddy") else "mitm")
        except Exception:
            self.sponsor_enabled.set(False)
            self.sponsor_name.set("")
            self.sponsor_mode.set("mitm")

    def _save_config(self, with_log=True):
        cfg = {
            "enabled": bool(self.sponsor_enabled.get()),
            "name": self.sponsor_name.get().strip(),
            "mode": (
                sponsor_mitm.normalize_sponsor_mode(self.sponsor_mode.get())
                if hasattr(sponsor_mitm, "normalize_sponsor_mode")
                else self.sponsor_mode.get()
            ),
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if with_log:
                mode_label = "hosts + Caddy" if cfg["mode"] == "caddy" else "mitmdump"
                self.log(f"配置已保存 (启用={cfg['enabled']}, 名称={cfg['name'] or '未设置'}, 模式={mode_label})")
        except Exception as e:
            if with_log:
                self.log(f"保存配置失败: {e}")

    def _set_enabled_and_save(self, enabled):
        self.sponsor_enabled.set(bool(enabled))
        self._save_config(with_log=False)

    def _append_log(self, line):
        if self._is_shutting_down:
            return
        try:
            self.txt_log.configure(state=tk.NORMAL)
            self.txt_log.insert(tk.END, line)
            self.txt_log.see(tk.END)
            self.txt_log.configure(state=tk.DISABLED)
        except Exception:
            pass

    def log(self, msg):
        if self._is_shutting_down:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        if threading.current_thread() is threading.main_thread():
            self._append_log(line)
        else:
            self._log_queue.put(line)

    def _process_log_queue(self):
        if self._is_shutting_down:
            self._log_queue_after_id = None
            return
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        if self._is_shutting_down:
            self._log_queue_after_id = None
            return
        self._log_queue_after_id = self.root.after(100, self._process_log_queue)

    def _ui_after(self, callback, *args):
        if self._is_shutting_down:
            return None
        try:
            if not self.root.winfo_exists():
                return None
            return self.root.after(0, callback, *args)
        except Exception:
            return None

    def _run_background(self, target):
        if self._is_shutting_down:
            return
        threading.Thread(target=target, daemon=True).start()

    def _update_sponsor_status(self, text, color):
        if hasattr(self, "lbl_sponsor_status"):
            self.lbl_sponsor_status.configure(text=text, foreground=color)

    def _safe_is_running(self):
        try:
            return bool(sponsor_mitm.is_running())
        except Exception:
            return False

    def _select_sponsor_proxy_port(self):
        blocked = set(COMMON_ACCELERATOR_PORTS)

        try:
            proxy_settings = sponsor_mitm._get_proxy_settings()
        except Exception:
            proxy_settings = {}

        if proxy_settings and proxy_settings.get("ProxyEnable") == 1:
            upstream_port = _parse_port_from_proxy_server(proxy_settings.get("ProxyServer"))
            if upstream_port:
                blocked.add(upstream_port)

        for port in _build_port_candidates():
            if port in blocked:
                continue
            if _is_local_port_available(port):
                return port

        current_default = _parse_port(getattr(sponsor_mitm, "PROXY_PORT", DEFAULT_SPONSOR_PROXY_PORT))
        return current_default or DEFAULT_SPONSOR_PROXY_PORT

    def _prepare_sponsor_proxy_port(self):
        selected_port = self._select_sponsor_proxy_port()
        current_port = _parse_port(getattr(sponsor_mitm, "PROXY_PORT", DEFAULT_SPONSOR_PROXY_PORT))
        sponsor_mitm.PROXY_PORT = selected_port
        if selected_port != current_port:
            self.log(f"代理端口预设为: {selected_port}")
        return selected_port

    def _force_cleanup_sponsor_proxy(self):
        try:
            force_cleanup = getattr(sponsor_mitm, "force_cleanup", None)
            if callable(force_cleanup):
                force_cleanup(log_func=None)
                return
        except Exception:
            pass

        for fn_name in ("_restore_proxy", "_stop_mitmdump", "_cleanup_old_caddy_residuals"):
            try:
                fn = getattr(sponsor_mitm, fn_name, None)
                if callable(fn):
                    fn(log_func=None)
            except Exception:
                pass

    def _on_save_clicked(self):
        self._save_config(with_log=True)

    def _on_sponsor_toggle(self):
        is_enabled = self.sponsor_enabled.get()
        self._save_config(with_log=False)
        if is_enabled:
            self._run_background(self._start_sponsor_worker)
        else:
            self._run_background(self._stop_sponsor_worker)

    def _deferred_auto_start(self):
        if self._is_shutting_down:
            return
        self._run_background(self._start_sponsor_worker)

    def _start_sponsor_worker(self):
        if not self._operation_lock.acquire(blocking=False):
            self.log("当前有操作进行中，已忽略启动请求")
            return
        try:
            name = self.sponsor_name.get().strip()
            if not name:
                self._ui_after(messagebox.showwarning, "提示", "请先输入你的游戏ID")
                self._ui_after(self._set_enabled_and_save, False)
                self._ui_after(self._update_sponsor_status, "已停止", "gray")
                return

            self._ui_after(self._update_sponsor_status, "正在启动...", "#f39c12")
            mode = self.sponsor_mode.get()
            if hasattr(sponsor_mitm, "normalize_sponsor_mode"):
                mode = sponsor_mitm.normalize_sponsor_mode(mode)
            if mode == "mitm":
                self._prepare_sponsor_proxy_port()
            success, message = sponsor_mitm.start_sponsor_override(name, log_func=self.log, mode=mode)
            if success:
                mode_label = "hosts + Caddy" if mode == "caddy" else "mitmdump"
                self._ui_after(self._update_sponsor_status, f"运行中 ✓ ({mode_label})", "#27ae60")
            else:
                self._ui_after(self._update_sponsor_status, f"失败: {message}", "red")
                self._ui_after(self._set_enabled_and_save, False)
        except Exception as e:
            self._ui_after(self.log, f"启动失败: {e}")
            self._ui_after(self._update_sponsor_status, "失败", "red")
            self._ui_after(self._set_enabled_and_save, False)
        finally:
            self._operation_lock.release()

    def _stop_sponsor_worker(self):
        if not self._operation_lock.acquire(blocking=False):
            self.log("当前有操作进行中，已忽略停止请求")
            return
        try:
            self._ui_after(self._update_sponsor_status, "正在停止...", "#f39c12")
            log_func = None if self._is_shutting_down else self.log
            sponsor_mitm.stop_sponsor_override(log_func=log_func)
            self._ui_after(self._update_sponsor_status, "已停止", "gray")
        except Exception as e:
            self._ui_after(self.log, f"停止失败: {e}")
            self._ui_after(self._update_sponsor_status, "停止失败", "red")
        finally:
            self._operation_lock.release()

    def _force_stop_sponsor_process(self):
        try:
            try:
                self._force_cleanup_sponsor_proxy()
            except Exception:
                pass

            proc = getattr(sponsor_mitm, "_mitm_process", None)
            if not proc:
                return
            pid = None
            try:
                pid = proc.pid
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=0.8)
                except Exception:
                    try:
                        proc.kill()
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
        except Exception:
            pass

    def _stop_sponsor_with_timeout(self, timeout_seconds=1.5):
        done = threading.Event()

        def worker():
            try:
                with self._operation_lock:
                    sponsor_mitm.stop_sponsor_override(log_func=None)
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True).start()
        done.wait(timeout_seconds)
        if not done.is_set():
            self._force_stop_sponsor_process()
        try:
            self._force_cleanup_sponsor_proxy()
        except Exception:
            pass

    def on_close(self):
        if self._is_shutting_down:
            return
        self._is_shutting_down = True

        try:
            self.root.withdraw()
            self.root.update_idletasks()
        except Exception:
            pass

        try:
            self._stop_sponsor_with_timeout(timeout_seconds=1.8)
        except Exception:
            pass
        try:
            self._force_cleanup_sponsor_proxy()
        except Exception:
            pass

        if self._log_queue_after_id:
            try:
                self.root.after_cancel(self._log_queue_after_id)
            except Exception:
                pass
            self._log_queue_after_id = None

        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    try:
        if os.name == "nt":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    root = tk.Tk()
    app = SponsorStandaloneApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    try:
        app._force_cleanup_sponsor_proxy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
