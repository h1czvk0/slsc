import ctypes
import glob
import os
import re
import threading
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime

from ecliptica_log_parser import is_ecliptica_room, parse_ecliptica_line


LOG_FILENAME_PATTERN = re.compile(
    r"^output_log_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.txt$",
    re.IGNORECASE,
)
PROCESS_LOG_MATCH_SECONDS = 30.0
IDENTITY_SCAN_BYTES = 2 * 1024 * 1024
INITIAL_TAIL_SCAN_BYTES = 20 * 1024 * 1024
APPEND_SCAN_LIMIT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class VrchatProcess:
    pid: int
    started_at: float


@dataclass(frozen=True)
class VrchatLogCandidate:
    path: str
    booted_at: float
    modified_at: float
    size: int
    pid: int | None = None
    vrc_user_id: str = ""
    vrc_username: str = ""
    room_name: str = ""
    session_id: str = ""
    last_ecliptica_at: float = 0.0
    ecliptica_activity: bool = False

    @property
    def in_ecliptica(self):
        return is_ecliptica_room(self.room_name) or self.ecliptica_activity

    @property
    def label(self):
        username = self.vrc_username or "未识别用户"
        room = self.room_name or "未知世界"
        pid = str(self.pid) if self.pid is not None else "-"
        return f"{username}｜{room}｜PID {pid}"


def log_boot_timestamp(path: str) -> float:
    match = LOG_FILENAME_PATTERN.match(os.path.basename(str(path or "")))
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").timestamp()
        except ValueError:
            pass
    try:
        return os.path.getctime(path)
    except OSError:
        return 0.0


def foreground_process_id() -> int:
    if os.name != "nt":
        return 0
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return int(process_id.value)
    except Exception:
        return 0


def enumerate_vrchat_processes() -> list[VrchatProcess]:
    if os.name != "nt":
        return []

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    kernel32 = ctypes.windll.kernel32
    snapshot = wintypes.HANDLE(-1).value
    processes = []
    try:
        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return []
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while has_entry:
            if str(entry.szExeFile).casefold() == "vrchat.exe":
                handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        created = wintypes.FILETIME()
                        exited = wintypes.FILETIME()
                        kernel = wintypes.FILETIME()
                        user = wintypes.FILETIME()
                        if kernel32.GetProcessTimes(
                            handle,
                            ctypes.byref(created),
                            ctypes.byref(exited),
                            ctypes.byref(kernel),
                            ctypes.byref(user),
                        ):
                            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
                            started_at = ticks / 10_000_000 - 11_644_473_600
                            processes.append(
                                VrchatProcess(pid=int(entry.th32ProcessID), started_at=started_at)
                            )
                    finally:
                        kernel32.CloseHandle(handle)
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    except Exception:
        return []
    finally:
        if snapshot not in (None, wintypes.HANDLE(-1).value):
            try:
                kernel32.CloseHandle(snapshot)
            except Exception:
                pass
    return processes


def match_processes_to_logs(
    paths: list[str],
    processes: list[VrchatProcess],
    max_delta_seconds=PROCESS_LOG_MATCH_SECONDS,
) -> dict[str, int]:
    pairs = []
    for path in paths:
        booted_at = log_boot_timestamp(path)
        for process in processes:
            delta = abs(booted_at - process.started_at)
            if delta <= float(max_delta_seconds):
                pairs.append((delta, path, process.pid))
    pairs.sort(key=lambda item: item[0])

    assigned_paths = set()
    assigned_pids = set()
    result = {}
    for _delta, path, pid in pairs:
        if path in assigned_paths or pid in assigned_pids:
            continue
        assigned_paths.add(path)
        assigned_pids.add(pid)
        result[path] = pid
    return result


class VrchatLogSelector:
    def __init__(
        self,
        log_dir: str,
        process_provider=enumerate_vrchat_processes,
        foreground_pid_provider=foreground_process_id,
    ):
        self.log_dir = str(log_dir)
        self.process_provider = process_provider
        self.foreground_pid_provider = foreground_pid_provider
        self._cache: dict[str, VrchatLogCandidate] = {}
        self._partials: dict[str, str] = {}
        self._manual_path = ""
        self._candidates: tuple[VrchatLogCandidate, ...] = ()
        self._ambiguous = False
        self._lock = threading.RLock()

    def set_manual_path(self, path: str | None):
        with self._lock:
            self._manual_path = os.path.abspath(path) if path else ""

    def snapshot(self):
        with self._lock:
            return {
                "manual_path": self._manual_path,
                "ambiguous": self._ambiguous,
                "candidates": list(self._candidates),
            }

    def select(self, current_path: str | None = None):
        candidates = self._refresh_candidates()
        by_path = {candidate.path: candidate for candidate in candidates}
        live = [candidate for candidate in candidates if candidate.pid is not None]
        foreground_pid = int(self.foreground_pid_provider() or 0)

        with self._lock:
            manual_path = self._manual_path
        manual = by_path.get(manual_path)
        if manual is not None and manual.pid is not None:
            return self._finish_selection(manual, False)
        if manual_path:
            with self._lock:
                self._manual_path = ""

        normalized_current = os.path.abspath(current_path) if current_path else ""
        current = by_path.get(normalized_current)
        if current is not None and current.pid is not None and current.in_ecliptica:
            return self._finish_selection(current, False)

        foreground = next(
            (candidate for candidate in live if candidate.pid == foreground_pid),
            None,
        )
        if foreground is not None and foreground.in_ecliptica:
            return self._finish_selection(foreground, False)

        ecliptica_candidates = [candidate for candidate in live if candidate.in_ecliptica]
        if len(ecliptica_candidates) == 1:
            return self._finish_selection(ecliptica_candidates[0], False)
        if len(ecliptica_candidates) > 1:
            return self._finish_selection(None, True)

        if current is not None and current.pid is not None:
            return self._finish_selection(current, False)
        if foreground is not None:
            return self._finish_selection(foreground, False)
        if len(live) == 1:
            return self._finish_selection(live[0], False)
        return self._finish_selection(None, len(live) > 1)

    def _finish_selection(self, selected, ambiguous):
        with self._lock:
            self._ambiguous = bool(ambiguous)
        return selected

    def _refresh_candidates(self):
        try:
            paths = [
                os.path.abspath(path)
                for path in glob.glob(os.path.join(self.log_dir, "output_log_*.txt"))
            ]
        except Exception:
            paths = []
        try:
            processes = list(self.process_provider() or [])
        except Exception:
            processes = []
        assignments = match_processes_to_logs(paths, processes)
        active_paths = set(assignments)
        with self._lock:
            if self._manual_path:
                active_paths.add(self._manual_path)
        candidates = []
        for path in active_paths:
            if not os.path.exists(path):
                continue
            candidate = self._read_candidate(path)
            candidates.append(replace(candidate, pid=assignments.get(path)))
        candidates.sort(key=lambda item: item.booted_at, reverse=True)
        with self._lock:
            self._candidates = tuple(candidates)
            stale_paths = set(self._cache) - set(paths)
            for path in stale_paths:
                self._cache.pop(path, None)
                self._partials.pop(path, None)
        return candidates

    def _read_candidate(self, path: str):
        stat = os.stat(path)
        cached = self._cache.get(path)
        if cached is None or stat.st_size < cached.size:
            candidate = self._initial_candidate(path, stat)
        elif stat.st_size > cached.size:
            candidate = self._append_candidate(path, stat, cached)
        else:
            candidate = replace(cached, modified_at=stat.st_mtime)
        self._cache[path] = candidate
        return candidate

    def _initial_candidate(self, path, stat):
        candidate = VrchatLogCandidate(
            path=path,
            booted_at=log_boot_timestamp(path),
            modified_at=stat.st_mtime,
            size=stat.st_size,
        )
        try:
            with open(path, "rb") as log_file:
                head = log_file.read(IDENTITY_SCAN_BYTES)
                tail_start = max(0, stat.st_size - INITIAL_TAIL_SCAN_BYTES)
                log_file.seek(tail_start)
                tail = log_file.read()
        except OSError:
            return candidate
        candidate = self._apply_text(candidate, head.decode("utf-8", errors="ignore"))
        candidate = self._apply_text(candidate, tail.decode("utf-8", errors="ignore"))
        self._partials[path] = ""
        return replace(candidate, size=stat.st_size, modified_at=stat.st_mtime)

    def _append_candidate(self, path, stat, cached):
        read_start = cached.size
        if stat.st_size - read_start > APPEND_SCAN_LIMIT_BYTES:
            read_start = stat.st_size - APPEND_SCAN_LIMIT_BYTES
            partial = ""
        else:
            partial = self._partials.get(path, "")
        try:
            with open(path, "rb") as log_file:
                log_file.seek(read_start)
                data = log_file.read(stat.st_size - read_start)
        except OSError:
            return cached
        text = partial + data.decode("utf-8", errors="ignore")
        lines = text.splitlines(True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._partials[path] = lines.pop()
        else:
            self._partials[path] = ""
        candidate = self._apply_text(cached, "".join(lines))
        return replace(candidate, size=stat.st_size, modified_at=stat.st_mtime)

    @staticmethod
    def _apply_text(candidate, text):
        updated = candidate
        for line in str(text or "").splitlines():
            event = parse_ecliptica_line(line)
            if event is None:
                continue
            if event.kind == "authenticated":
                updated = replace(
                    updated,
                    vrc_username=event.groups[0].strip(),
                    vrc_user_id=event.groups[1].strip(),
                )
            elif event.kind == "room_entered":
                updated = replace(
                    updated,
                    room_name=event.groups[0].strip(),
                    ecliptica_activity=is_ecliptica_room(event.groups[0]),
                )
            elif event.kind in ("session", "session_blank"):
                session_id = event.groups[0].strip() if event.kind == "session" else ""
                updated = replace(updated, session_id=session_id)
            if event.kind in ("session", "stage", "boss", "intermission", "lobby"):
                event_time = float(event.timestamp or updated.modified_at or 0.0)
                updated = replace(
                    updated,
                    last_ecliptica_at=max(updated.last_ecliptica_at, event_time),
                    ecliptica_activity=True,
                )
        return updated
