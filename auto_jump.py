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
AUTO_JUMP_PRESS_SECONDS = 0.02
AUTO_JUMP_RELEASE_SECONDS = 0.03
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
    ):
        self.output = output or AutoJumpOscOutput(host, port)
        self.foreground_provider = foreground_provider
        self.space_down_provider = space_down_provider
        self.clock = clock
        self.sleep = sleep
        self.pulse = AutoJumpPulseController()
        self._enabled = False
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

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
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
            testing = self._testing
            if enabled and current_time - self._last_foreground_check >= FOREGROUND_REFRESH_SECONDS:
                self._vrchat_foreground = bool(self.foreground_provider())
                self._last_foreground_check = current_time
            elif not enabled:
                self._vrchat_foreground = False
            self._space_down = bool(
                enabled and self._vrchat_foreground and self.space_down_provider()
            )
            active = enabled and not testing and self._vrchat_foreground and self._space_down
            actions = self.pulse.update(active, current_time)
            if not active and self.output.last_value == 1 and False not in actions:
                actions.append(False)

        try:
            for pressed in actions:
                self.output.send(pressed)
            with self._lock:
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
                "testing": self._testing,
                "vrchat_foreground": self._vrchat_foreground,
                "space_down": self._space_down,
                "jumping": self.pulse.jump_pressed,
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
            try:
                self.output.release(force=True)
            except Exception:
                pass
