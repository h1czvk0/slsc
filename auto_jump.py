import ctypes
import os
import socket
import threading
import time
from ctypes import wintypes

from osc_output import (
    DEFAULT_OSC_HOST,
    DEFAULT_OSC_PORT,
    build_osc_message,
    normalize_osc_host,
    normalize_osc_port,
)


VRCHAT_JUMP_ADDRESS = "/input/Jump"
VK_SPACE = 0x20
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WH_KEYBOARD_LL = 13
AUTO_JUMP_PRESS_SECONDS = 0.05
AUTO_JUMP_RELEASE_SECONDS = 0.05
AUTO_JUMP_POLL_SECONDS = 0.005
FOREGROUND_REFRESH_SECONDS = 0.1


def is_space_down():
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_SPACE) & 0x8000)
    except Exception:
        return False


def foreground_process_name():
    if os.name != "nt":
        return ""
    process_handle = None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        process_handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not process_handle:
            return ""
        path_buffer = ctypes.create_unicode_buffer(32768)
        path_length = wintypes.DWORD(len(path_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process_handle,
            0,
            path_buffer,
            ctypes.byref(path_length),
        ):
            return ""
        return os.path.basename(path_buffer.value)
    except Exception:
        return ""
    finally:
        if process_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(process_handle)
            except Exception:
                pass


def is_vrchat_foreground():
    return foreground_process_name().casefold() == "vrchat.exe"


class KbdLlHookStruct(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class SpaceKeyHook:
    def __init__(self, physical_state_provider=is_space_down):
        self.physical_state_provider = physical_state_provider
        self._capture_enabled = False
        self._awaiting_release = False
        self._space_down = False
        self._thread = None
        self._thread_id = 0
        self._hook_handle = None
        self._callback = None
        self._ready_event = threading.Event()
        self._lock = threading.RLock()
        self.last_error = ""

    @property
    def running(self):
        thread = self._thread
        return bool(thread and thread.is_alive() and self._hook_handle)

    @property
    def awaiting_release(self):
        with self._lock:
            return self._awaiting_release

    def set_capture(self, enabled):
        capture = bool(enabled)
        with self._lock:
            if capture and not self._capture_enabled:
                self._awaiting_release = bool(self.physical_state_provider())
            elif not capture:
                self._awaiting_release = False
            self._capture_enabled = capture

    def is_down(self):
        with self._lock:
            return self._space_down and not self._awaiting_release

    def handle_space_event(self, message):
        is_down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = message in (WM_KEYUP, WM_SYSKEYUP)
        if not is_down and not is_up:
            return False
        with self._lock:
            self._space_down = is_down
            if not self._capture_enabled:
                return False
            if self._awaiting_release:
                if is_up:
                    self._awaiting_release = False
                return False
            return True

    def start(self, timeout=1.0):
        if os.name != "nt":
            self.last_error = "keyboard hook requires Windows"
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.running
            self.last_error = ""
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="osc-auto-jump-hook",
                daemon=True,
            )
            self._thread.start()
        self._ready_event.wait(max(0.0, float(timeout)))
        return self.running

    def stop(self, timeout=1.0):
        self.set_capture(False)
        thread_id = self._thread_id
        if os.name == "nt" and thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        with self._lock:
            self._thread = None
            self._thread_id = 0
            self._hook_handle = None
            self._callback = None
            self._space_down = False
            self._awaiting_release = False

    def _run(self):
        hook_handle = None
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hook_proc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.SetWindowsHookExW.argtypes = (
                ctypes.c_int,
                hook_proc_type,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            )
            user32.SetWindowsHookExW.restype = wintypes.HANDLE
            user32.CallNextHookEx.argtypes = (
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            def callback(code, wparam, lparam):
                if code >= 0:
                    event = ctypes.cast(lparam, ctypes.POINTER(KbdLlHookStruct)).contents
                    if event.vkCode == VK_SPACE and self.handle_space_event(int(wparam)):
                        return 1
                return user32.CallNextHookEx(hook_handle, code, wparam, lparam)

            self._callback = hook_proc_type(callback)
            self._thread_id = kernel32.GetCurrentThreadId()
            hook_handle = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL,
                self._callback,
                kernel32.GetModuleHandleW(None),
                0,
            )
            if not hook_handle:
                raise ctypes.WinError()
            self._hook_handle = hook_handle
            self._ready_event.set()

            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.last_error = str(exc)
            self._ready_event.set()
        finally:
            if hook_handle:
                try:
                    ctypes.windll.user32.UnhookWindowsHookEx(hook_handle)
                except Exception:
                    pass
            self._hook_handle = None
            self._ready_event.set()


class AutoJumpPulseController:
    def __init__(
        self,
        press_seconds=AUTO_JUMP_PRESS_SECONDS,
        release_seconds=AUTO_JUMP_RELEASE_SECONDS,
    ):
        self.press_seconds = max(0.001, float(press_seconds))
        self.release_seconds = max(0.001, float(release_seconds))
        self.jump_pressed = False
        self.next_transition_at = 0.0

    def reset(self, now=0.0):
        was_pressed = self.jump_pressed
        self.jump_pressed = False
        self.next_transition_at = float(now)
        return [False] if was_pressed else []

    def update(self, active, now):
        current_time = float(now)
        if not active:
            return self.reset(current_time)
        if current_time < self.next_transition_at:
            return []
        self.jump_pressed = not self.jump_pressed
        delay = self.press_seconds if self.jump_pressed else self.release_seconds
        self.next_transition_at = current_time + delay
        return [self.jump_pressed]


class AutoJumpOscOutput:
    def __init__(self, host=DEFAULT_OSC_HOST, port=DEFAULT_OSC_PORT, socket_factory=socket.socket):
        self.socket_factory = socket_factory
        self.host = normalize_osc_host(host)
        self.port = normalize_osc_port(port)
        self.last_value = None
        self._lock = threading.RLock()

    def configure(self, host, port):
        normalized = (normalize_osc_host(host), normalize_osc_port(port))
        with self._lock:
            if normalized != (self.host, self.port):
                self.release(force=True)
                self.host, self.port = normalized
                self.last_value = None
        return normalized

    def send(self, pressed, force=False):
        value = 1 if pressed else 0
        with self._lock:
            if not force and value == self.last_value:
                return False
            packet = build_osc_message(VRCHAT_JUMP_ADDRESS, value)
            with self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as osc_socket:
                osc_socket.sendto(packet, (self.host, self.port))
            self.last_value = value
        return True

    def release(self, force=False):
        return self.send(False, force=force)


class AutoJumpService:
    def __init__(
        self,
        host=DEFAULT_OSC_HOST,
        port=DEFAULT_OSC_PORT,
        output=None,
        foreground_provider=is_vrchat_foreground,
        space_down_provider=is_space_down,
        clock=time.monotonic,
        sleep=time.sleep,
        key_hook=None,
    ):
        self.output = output or AutoJumpOscOutput(host, port)
        self.foreground_provider = foreground_provider
        self.space_down_provider = space_down_provider
        self.clock = clock
        self.sleep = sleep
        self.key_hook = key_hook if key_hook is not None else SpaceKeyHook()
        self.pulse = AutoJumpPulseController()
        self._enabled = False
        self._context_allowed = False
        self._pause_reason = "仅在 Ecliptica 世界生效"
        self._testing = False
        self._vrchat_foreground = False
        self._space_down = False
        self._last_foreground_check = float("-inf")
        self._last_error = ""
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread = None

    def configure(self, host, port):
        with self._lock:
            normalized = self.output.configure(host, port)
            self.pulse.reset(self.clock())
            self._wake_event.set()
            return normalized

    def set_enabled(self, enabled):
        with self._lock:
            self._enabled = bool(enabled)
            self._wake_event.set()

    def set_context(self, allowed, pause_reason=""):
        with self._lock:
            normalized_allowed = bool(allowed)
            normalized_reason = (
                "" if normalized_allowed else str(pause_reason or "当前状态已暂停")
            )
            changed = (
                self._context_allowed != normalized_allowed
                or self._pause_reason != normalized_reason
            )
            self._context_allowed = normalized_allowed
            self._pause_reason = normalized_reason
            if changed:
                self._wake_event.set()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            if self.key_hook and not self.key_hook.start():
                self._last_error = self.key_hook.last_error or "keyboard hook failed to start"
            self._thread = threading.Thread(
                target=self._run,
                name="osc-auto-jump",
                daemon=True,
            )
            self._thread.start()
            return True

    def tick(self, now=None):
        current_time = float(self.clock() if now is None else now)
        with self._lock:
            enabled = self._enabled
            context_allowed = self._context_allowed
            testing = self._testing
            if (
                enabled
                and context_allowed
                and current_time - self._last_foreground_check >= FOREGROUND_REFRESH_SECONDS
            ):
                self._vrchat_foreground = bool(self.foreground_provider())
                self._last_foreground_check = current_time
            elif not enabled or not context_allowed:
                self._vrchat_foreground = False
            capture_space = enabled and context_allowed and not testing and self._vrchat_foreground
            if self.key_hook and self.key_hook.running:
                self.key_hook.set_capture(capture_space)
                self._space_down = bool(capture_space and self.key_hook.is_down())
            elif self.key_hook and enabled:
                self._space_down = False
                self._last_error = self.key_hook.last_error or "keyboard hook is not running"
            else:
                self._space_down = bool(capture_space and self.space_down_provider())
            active = (
                enabled
                and context_allowed
                and not testing
                and self._vrchat_foreground
                and self._space_down
            )
            actions = self.pulse.update(active, current_time)
            if not active and self.output.last_value == 1 and False not in actions:
                actions.append(False)

        try:
            for pressed in actions:
                self.output.send(pressed)
            with self._lock:
                if not self.key_hook or self.key_hook.running or not enabled:
                    self._last_error = ""
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                self.pulse.reset(current_time)
        return bool(actions)

    def test_jump(self, pulse_seconds=0.06):
        with self._lock:
            self._testing = True
            self.pulse.reset(self.clock())
        try:
            self.output.send(True, force=True)
            self.sleep(max(0.01, float(pulse_seconds)))
            self.output.release(force=True)
            with self._lock:
                self._last_error = ""
            return True
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            try:
                self.output.release(force=True)
            except Exception:
                pass
            return False
        finally:
            with self._lock:
                self._testing = False
                self._wake_event.set()

    def snapshot(self):
        with self._lock:
            return {
                "enabled": self._enabled,
                "context_allowed": self._context_allowed,
                "pause_reason": self._pause_reason,
                "testing": self._testing,
                "vrchat_foreground": self._vrchat_foreground,
                "space_down": self._space_down,
                "jumping": self.pulse.jump_pressed,
                "awaiting_release": bool(
                    self.key_hook and self.key_hook.awaiting_release
                ),
                "error": self._last_error,
            }

    def stop(self, timeout=1.0):
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        try:
            self.output.release(force=True)
        except Exception:
            pass
        if self.key_hook:
            self.key_hook.stop(timeout=timeout)
        with self._lock:
            self._thread = None
            self.pulse.reset(self.clock())

    def _run(self):
        try:
            while not self._stop_event.is_set():
                self.tick()
                self._wake_event.wait(AUTO_JUMP_POLL_SECONDS)
                self._wake_event.clear()
        finally:
            if self.key_hook:
                self.key_hook.set_capture(False)
            try:
                self.output.release(force=True)
            except Exception:
                pass
