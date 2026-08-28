import os
import sys

# Admin task handler removed - handled by external tool


import os
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR) # 优先加载同目录模块（如 sounddevice.py）
import time
import re
import threading
import glob
import ctypes
from ctypes import wintypes
import json
import concurrent.futures
import subprocess
import queue
import random
import socket
from datetime import datetime
from tkinter import *
from tkinter import ttk, scrolledtext, Menu, messagebox
from tkinter import font as tkfont



from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk
HAS_PIL = True

from slashco_log_parser import (
    is_round_end_line,
    is_round_start_line,
    item_numeric_id,
    line_might_affect_state,
    normalize_item_id,
    parse_log_line,
)
from ecliptica_log_parser import (
    EclipticaState,
    is_ecliptica_room,
    line_might_affect_ecliptica_state,
    parse_ecliptica_line,
)
from ecliptica_history import EclipticaHistoryClient, EclipticaHistoryDialog
from ecliptica_sync import (
    DEFAULT_SYNC_URL,
    EclipticaSyncClient,
    is_boss_battle_active,
)
from slashco_updater import (
    APP_VERSION,
    download_update,
    fetch_latest_release,
    get_update_download_path,
    is_frozen_app,
    launch_updater_and_exit,
    parse_update_info,
)
from osc_output import (
    BossLockOscOutput,
    DEFAULT_OSC_HOST,
    DEFAULT_OSC_PORT,
    DEFAULT_OSC_PREFIX,
    normalize_osc_host,
    normalize_osc_port,
    normalize_osc_prefix,
)
from auto_jump import AutoJumpService

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False




# 赞助者名单覆盖 (mitmproxy 方案)
try:
    import sponsor_mitm as sponsor_caddy  # 保持变量名兼容
    HAS_SPONSOR_PROXY = True
except ImportError:
    HAS_SPONSOR_PROXY = False





def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(SCRIPT_DIR, relative_path)


# =====================
# 画廊窗口 (多图显示)
# =====================
class GalleryWindow:
    """显示多张图片的画廊窗口"""
    
    def __init__(self, parent, title, image_paths, cn_name=""):
        self.top = Toplevel(parent)
        self.top.title(f"图片预览 - {cn_name or title}")
        self.top.transient(parent)
        self.top.grab_set()
        
        # 计算窗口大小和布局
        num_images = len(image_paths)
        if num_images == 1:
            cols = 1
        elif num_images <= 4:
            cols = 2
        else:
            cols = 3
        rows = (num_images + cols - 1) // cols
        
        # 窗口尺寸
        cell_size = 350 # 每个图片单元格大小
        win_width = min(cols * cell_size + 40, 1200)
        win_height = min(rows * cell_size + 80, 800)
        
        # 居中显示
        screen_w = parent.winfo_screenwidth()
        screen_h = parent.winfo_screenheight()
        x = (screen_w - win_width) // 2
        y = (screen_h - win_height) // 2
        self.top.geometry(f"{win_width}x{win_height}+{x}+{y}")
        
        # 顶部信息栏
        top_bar = Frame(self.top)
        top_bar.pack(fill=X, padx=10, pady=10)
        Label(top_bar, text=f"📍 {cn_name or title}", font=("微软雅黑", 12, "bold")).pack(side=LEFT)
        ttk.Button(top_bar, text="✖ 关闭", command=self.top.destroy).pack(side=RIGHT)
        
        # 滚动画布
        canvas_frame = Frame(self.top)
        canvas_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        
        canvas = Canvas(canvas_frame, bg="#f0f0f0")
        scrollbar = ttk.Scrollbar(canvas_frame, orient=VERTICAL, command=canvas.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # 内部Frame放置图片
        inner_frame = Frame(canvas, bg="#f0f0f0")
        canvas.create_window((0, 0), window=inner_frame, anchor=NW)
        
        # 保持图片引用防止GC
        self.photo_refs = []
        
        # 加载并显示图片
        for i, path in enumerate(image_paths):
            row = i // cols
            col = i % cols
            
            try:
                img = Image.open(path)
                # 按比例缩放适应单元格
                max_size = cell_size - 20
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.photo_refs.append(photo)
                
                cell = Frame(inner_frame, bd=1, relief=SOLID, bg="white")
                cell.grid(row=row, column=col, padx=5, pady=5)
                
                lbl = Label(cell, image=photo, bg="white")
                lbl.pack(padx=5, pady=5)
                
                # 显示文件名
                fname = os.path.basename(path)
                Label(cell, text=fname[:20], font=("Consolas", 8), fg="#666", bg="white").pack()
            except Exception:
                pass
        
        # 更新滚动区域
        inner_frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))
        
        # 绑定鼠标滚轮
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))
        
        # ESC 关闭
        self.top.bind("<Escape>", lambda e: self.top.destroy())


HUD_MIN_SIZES = {
    "damage": (300, 210),
    "boss_lock": (320, 90),
    "party_damage": (320, 180),
}
HUD_DISPLAY_LABELS = {
    "damage": "伤害数据",
    "boss_lock": "Boss 锁定",
    "party_damage": "伤害统计",
}
HUD_DISPLAY_PANEL_KEYS = tuple(HUD_DISPLAY_LABELS)
HUD_LEGACY_DISPLAY_PANELS = {
    "both": HUD_DISPLAY_PANEL_KEYS,
    "damage": ("damage",),
    "boss_lock": ("boss_lock",),
    "party_damage": ("party_damage",),
}
HUD_DAMAGE_FIELDS = (
    ("class_name", "当前职业"),
    ("stage", "当前阶段"),
    ("current_boss", "当前 BOSS"),
    ("current_boss_phase", "BOSS 阶段"),
    ("current_phase_damage", "本局 BOSS 总伤害"),
    ("current_phase_damage_taken", "本局 BOSS 总受伤"),
    ("recent_5s_dps", "近 3 秒 DPS"),
    ("current_boss_elapsed", "当前 BOSS 耗时"),
    ("total_elapsed", "总耗时"),
)
HUD_DAMAGE_FIELD_KEYS = tuple(key for key, _label in HUD_DAMAGE_FIELDS)
HUD_DAMAGE_FIELD_LABELS = dict(HUD_DAMAGE_FIELDS)
HUD_DEFAULT_PRESET_VERSION = 2
HUD_DEFAULT_PRESET_DISPLAY_PANELS = HUD_DISPLAY_PANEL_KEYS
HUD_DEFAULT_PRESET_TRANSPARENCY = 100.0
HUD_DEFAULT_PRESET_FIELDS = (
    "total_elapsed",
    "current_boss",
    "current_boss_phase",
    "current_phase_damage",
    "current_phase_damage_taken",
    "recent_5s_dps",
    "current_boss_elapsed",
)
HUD_DEFAULT_PRESET_ORDER = (
    "total_elapsed",
    "stage",
    "current_boss",
    "current_boss_phase",
    "class_name",
    "current_phase_damage",
    "current_phase_damage_taken",
    "recent_5s_dps",
    "current_boss_elapsed",
)
HUD_DEFAULT_REFERENCE_SIZE = (2560, 1440)
HUD_DEFAULT_REFERENCE_LAYOUT = {
    "damage": {"x": -4, "y": 602, "width": 496, "height": 350},
    "boss_lock": {"x": 1050, "y": 1217, "width": 476, "height": 141},
    "party_damage": {"x": -1, "y": 110, "width": 516, "height": 379},
}
APP_USER_MODEL_ID = "SlashCoSense.Desktop"
TK_BASE_SCALING = 96.0 / 72.0


def configure_windows_process():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def configure_tk_display(root):
    try:
        root.tk.call("tk", "scaling", TK_BASE_SCALING)
    except Exception:
        pass
    for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(font_name, root=root).configure(
                family="Microsoft YaHei UI",
                size=9,
            )
        except Exception:
            pass


def main_window_geometry(screen_width, screen_height):
    available_width = max(640, int(screen_width) - 32)
    available_height = max(560, int(screen_height) - 72)
    return min(1300, available_width), min(900, available_height)


def normalize_hud_display_mode(value):
    mode = str(value or "both").lower()
    return mode if mode in HUD_LEGACY_DISPLAY_PANELS else "both"


def normalize_hud_display_panels(value):
    if value is None:
        return HUD_DISPLAY_PANEL_KEYS
    if isinstance(value, str):
        return HUD_LEGACY_DISPLAY_PANELS[normalize_hud_display_mode(value)]
    if not isinstance(value, (list, tuple, set)):
        return HUD_DISPLAY_PANEL_KEYS
    selected = set(value)
    return tuple(key for key in HUD_DISPLAY_PANEL_KEYS if key in selected)


def normalize_hud_damage_fields(value):
    if value is None:
        return list(HUD_DAMAGE_FIELD_KEYS)
    if not isinstance(value, (list, tuple, set)):
        return list(HUD_DAMAGE_FIELD_KEYS)
    if isinstance(value, set):
        value = [key for key in HUD_DAMAGE_FIELD_KEYS if key in value]
    normalized = []
    for key in value:
        if key in HUD_DAMAGE_FIELD_LABELS and key not in normalized:
            normalized.append(key)
    return normalized


def normalize_hud_field_order(value):
    ordered = normalize_hud_damage_fields(value) if value is not None else []
    ordered.extend(key for key in HUD_DAMAGE_FIELD_KEYS if key not in ordered)
    return ordered


def hud_default_preset_layout(screen_width, screen_height):
    reference_width, reference_height = HUD_DEFAULT_REFERENCE_SIZE
    width_scale = max(1, int(screen_width)) / reference_width
    height_scale = max(1, int(screen_height)) / reference_height
    scaled = {}
    for key, geometry in HUD_DEFAULT_REFERENCE_LAYOUT.items():
        scaled[key] = {
            "x": round(geometry["x"] * width_scale),
            "y": round(geometry["y"] * height_scale),
            "width": round(geometry["width"] * width_scale),
            "height": round(geometry["height"] * height_scale),
        }
    return normalize_hud_layout(scaled)


def pending_hud_default_preset(config, screen_width, screen_height):
    try:
        preset_version = int(config.get("hud_default_preset_version", 0))
    except (AttributeError, TypeError, ValueError):
        preset_version = 0
    if preset_version >= HUD_DEFAULT_PRESET_VERSION:
        return None
    return {
        "version": HUD_DEFAULT_PRESET_VERSION,
        "layout": hud_default_preset_layout(screen_width, screen_height),
        "display_panels": list(HUD_DEFAULT_PRESET_DISPLAY_PANELS),
        "transparency": HUD_DEFAULT_PRESET_TRANSPARENCY,
        "fields": list(HUD_DEFAULT_PRESET_FIELDS),
        "order": list(HUD_DEFAULT_PRESET_ORDER),
    }


def is_hud_foreground():
    if os.name != "nt":
        return False
    process_handle = None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
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
            return False
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == os.getpid():
            return True
        process_handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not process_handle:
            return False
        path_buffer = ctypes.create_unicode_buffer(32768)
        path_length = wintypes.DWORD(len(path_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process_handle,
            0,
            path_buffer,
            ctypes.byref(path_length),
        ):
            return False
        return os.path.basename(path_buffer.value).casefold() == "vrchat.exe"
    except Exception:
        return False
    finally:
        if process_handle:
            ctypes.windll.kernel32.CloseHandle(process_handle)


def normalize_hud_opacity(value):
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        return 0.9
    return min(1.0, max(0.0, opacity))


def normalize_hud_transparency(value):
    try:
        transparency = float(value)
    except (TypeError, ValueError):
        return 10.0
    return min(100.0, max(0.0, transparency))


def normalize_hud_layout(layout):
    normalized = {}
    if not isinstance(layout, dict):
        return normalized
    for key, (min_width, min_height) in HUD_MIN_SIZES.items():
        raw = layout.get(key)
        if not isinstance(raw, dict):
            continue
        try:
            normalized[key] = {
                "x": int(raw["x"]),
                "y": int(raw["y"]),
                "width": max(min_width, int(raw["width"])),
                "height": max(min_height, int(raw["height"])),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return normalized


class _LayeredPoint(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class _LayeredSize(ctypes.Structure):
    _fields_ = (("cx", wintypes.LONG), ("cy", wintypes.LONG))


class _BlendFunction(ctypes.Structure):
    _fields_ = (
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    )


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _BitmapInfo(ctypes.Structure):
    _fields_ = (("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3))


_HUD_FONT_CACHE = {}
_HUD_FALLBACK_FONT_CACHE = {}
_HUD_GLYPH_SUPPORT_CACHE = {}


def _load_hud_font(size, bold=False):
    key = (int(size), bool(bold))
    cached = _HUD_FONT_CACHE.get(key)
    if cached:
        return cached
    font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = ("msyhbd.ttc", "msyh.ttc") if bold else ("msyh.ttc", "segoeui.ttf")
    for name in names:
        try:
            font = ImageFont.truetype(os.path.join(font_dir, name), key[0])
            _HUD_FONT_CACHE[key] = font
            return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _HUD_FONT_CACHE[key] = font
    return font


def _load_hud_fallback_fonts(size, bold=False):
    key = (int(size), bool(bold))
    cached = _HUD_FALLBACK_FONT_CACHE.get(key)
    if cached:
        return cached
    font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    candidates = (
        ("LeelaUIb.ttf" if bold else "LeelawUI.ttf", 0),
        ("Nirmala.ttc", 1 if bold else 0),
        ("seguisym.ttf", 0),
        ("cambria.ttc", 1),
        ("seguiemj.ttf", 0),
    )
    fonts = []
    for name, index in candidates:
        try:
            fonts.append(ImageFont.truetype(os.path.join(font_dir, name), key[0], index=index))
        except OSError:
            continue
    _HUD_FALLBACK_FONT_CACHE[key] = tuple(fonts)
    return _HUD_FALLBACK_FONT_CACHE[key]


def _hud_font_supports_character(font, character):
    if character.isspace():
        return True
    cache_key = (id(font), character)
    cached = _HUD_GLYPH_SUPPORT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        glyph = font.getmask(character, mode="L")
        missing = font.getmask("\U0010ffff", mode="L")
        supported = glyph.size != missing.size or bytes(glyph) != bytes(missing)
    except (AttributeError, OSError, ValueError):
        supported = True
    _HUD_GLYPH_SUPPORT_CACHE[cache_key] = supported
    return supported


def _hud_text_runs(text, base_font):
    value = str(text)
    if not value:
        return [(value, base_font)]
    try:
        size = int(base_font.size)
        bold = "bold" in str(base_font.getname()[1]).casefold()
    except (AttributeError, IndexError, TypeError):
        return [(value, base_font)]
    fonts = (base_font,) + _load_hud_fallback_fonts(size, bold)
    runs = []
    for character in value:
        selected = next(
            (font for font in fonts if _hud_font_supports_character(font, character)),
            base_font,
        )
        if runs and runs[-1][1] is selected:
            runs[-1] = (runs[-1][0] + character, selected)
        else:
            runs.append((character, selected))
    return runs


def _hud_text_width(draw, text, font):
    return sum(
        float(draw.textlength(run, font=run_font))
        for run, run_font in _hud_text_runs(text, font)
    )


def _fit_hud_font_to_width(draw, text, maximum_width, preferred_size, minimum_size, bold=True):
    preferred = max(1, int(preferred_size))
    minimum = max(1, min(preferred, int(minimum_size)))
    for size in range(preferred, minimum - 1, -1):
        font = _load_hud_font(size, bold=bold)
        if _hud_text_width(draw, text, font) <= maximum_width:
            return font
    return _load_hud_font(minimum, bold=bold)


def _premultiplied_bgra(image):
    red, green, blue, alpha = image.convert("RGBA").split()
    return Image.merge(
        "RGBA",
        (
            ImageChops.multiply(blue, alpha),
            ImageChops.multiply(green, alpha),
            ImageChops.multiply(red, alpha),
            alpha,
        ),
    ).tobytes()


def _native_toplevel_hwnd(window):
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = (wintypes.HWND,)
    user32.GetParent.restype = wintypes.HWND
    hwnd = wintypes.HWND(window.winfo_id())
    parent_hwnd = user32.GetParent(hwnd)
    return parent_hwnd or hwnd


def _native_window_rect(window):
    if os.name != "nt" or not hasattr(window, "winfo_id"):
        return (
            window.winfo_x(), window.winfo_y(),
            window.winfo_width(), window.winfo_height(),
        )
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    rect = wintypes.RECT()
    if user32.GetWindowRect(_native_toplevel_hwnd(window), ctypes.byref(rect)):
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    return window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height()


def _set_native_window_rect(window, x, y, width, height):
    geometry = f"{width}x{height}+{x}+{y}"
    window.geometry(geometry)
    if os.name != "nt" or not hasattr(window, "winfo_id"):
        return
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = (
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    )
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos(
        _native_toplevel_hwnd(window), None,
        int(x), int(y), int(width), int(height),
        0x0004 | 0x0010 | 0x0200,
    )


def _update_layered_window(window, image):
    if os.name != "nt":
        return False
    window.update_idletasks()
    width, height = image.size
    hwnd = _native_toplevel_hwnd(window)
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    user32.SetWindowLongW.restype = wintypes.LONG
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(_BitmapInfo),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    )
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    user32.UpdateLayeredWindow.argtypes = (
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(_LayeredPoint),
        ctypes.POINTER(_LayeredSize),
        wintypes.HDC,
        ctypes.POINTER(_LayeredPoint),
        wintypes.COLORREF,
        ctypes.POINTER(_BlendFunction),
        wintypes.DWORD,
    )
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    ex_style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, ex_style | 0x00080000 | 0x00000080)

    screen_dc = user32.GetDC(0)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = None
    old_bitmap = None
    try:
        bitmap_info = _BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(bitmap_info),
            0,
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits.value:
            return False
        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        pixel_data = _premultiplied_bgra(image)
        ctypes.memmove(bits.value, bytes(pixel_data), len(pixel_data))
        window_rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            destination = _LayeredPoint(window_rect.left, window_rect.top)
        else:
            destination = _LayeredPoint(window.winfo_x(), window.winfo_y())
        source = _LayeredPoint(0, 0)
        size = _LayeredSize(width, height)
        blend = _BlendFunction(0, 0, 255, 1)
        return bool(
            user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                memory_dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                2,
            )
        )
    finally:
        if old_bitmap:
            gdi32.SelectObject(memory_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(0, screen_dc)


class EclipticaDesktopHud:
    BG = "#090c14"
    TRANSPARENT = "#010203"
    FG = "#ffffff"
    ACCENT = "#aaa0ff"
    BORDER = "#343b4d"

    def __init__(self, root, layout=None, opacity=0.9):
        self.root = root
        self.layout = normalize_hud_layout(layout)
        self.opacity = normalize_hud_opacity(opacity)
        self.editing = False
        self.display_panels = HUD_DISPLAY_PANEL_KEYS
        self.damage_background_window = None
        self.lock_background_window = None
        self.party_background_window = None
        self.damage_window = None
        self.lock_window = None
        self.party_window = None
        self.damage_canvas = None
        self.lock_canvas = None
        self.party_canvas = None
        self.damage_preview_canvas = None
        self.lock_preview_canvas = None
        self.party_preview_canvas = None
        self.damage_title_text = ""
        self.damage_rows = []
        self.party_rows = []
        self.party_total_damage = 0
        self.lock_text = "Boss 当前锁定：-"
        self.lock_target = "-"
        self.boss_battle_active = False
        self.boss_lock_active = False
        self.resize_grips = {}
        self._pointer_operation = None

    def _set_click_through(self, window, enabled):
        if os.name != "nt":
            return
        try:
            window.update_idletasks()
            hwnd = window.winfo_id()
            parent_hwnd = ctypes.windll.user32.GetParent(hwnd)
            if parent_hwnd:
                hwnd = parent_hwnd
            get_style = ctypes.windll.user32.GetWindowLongW
            set_style = ctypes.windll.user32.SetWindowLongW
            ex_style = get_style(hwnd, -20)
            ex_style |= 0x00000080
            if enabled:
                ex_style |= 0x00000020
            else:
                ex_style &= ~0x00000020
            set_style(hwnd, -20, ex_style)
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0033)
        except Exception:
            pass

    def _create_background_window(self):
        window = Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", self.opacity)
        window.configure(bg=self.BG, highlightbackground=self.BORDER, highlightthickness=1)
        return window

    def _configure_content_window(self, window):
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=self.TRANSPARENT, highlightthickness=0)

    def _background_window(self, key):
        if key == "damage":
            return self.damage_background_window
        if key == "boss_lock":
            return self.lock_background_window
        return self.party_background_window

    def _set_window_pair_geometry(self, key, content_window, x, y, width, height):
        background = self._background_window(key)
        if background and background.winfo_exists():
            _set_native_window_rect(background, x, y, width, height)
        _set_native_window_rect(content_window, x, y, width, height)
        if background and background.winfo_exists():
            content_window.lift(background)
        self.layout[key] = {
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        }

    def _bind_drag(self, widget, key, window):
        widget.bind(
            "<ButtonPress-1>",
            lambda event: self._start_drag(event, key, window),
        )
        widget.bind("<B1-Motion>", self._drag_window)
        widget.configure(cursor="fleur")

    def _bind_edit_surface(self, widget, key):
        widget.bind(
            "<ButtonPress-1>",
            lambda event: self._start_edit_surface_action(event, key),
        )
        widget.bind("<B1-Motion>", self._continue_pointer_operation)
        widget.configure(cursor="fleur")

    def _start_edit_surface_action(self, event, key):
        if not self.editing:
            return
        window = self._background_window(key)
        if event.x >= event.widget.winfo_width() - 28 and event.y >= event.widget.winfo_height() - 28:
            self._start_resize(event, key, window, preview=True)
        else:
            self._start_drag(event, key, window, preview=True)

    def _continue_pointer_operation(self, event):
        operation = self._pointer_operation
        if operation and operation.get("kind") == "resize":
            self._resize_window(event)
        else:
            self._drag_window(event)

    def _draw_text(self, draw, x, y, text, fill, font, anchor="la"):
        runs = _hud_text_runs(text, font)
        if len(runs) == 1:
            draw.text((x, y), text, fill=fill, font=runs[0][1], anchor=anchor)
            return
        widths = [draw.textlength(run, font=run_font) for run, run_font in runs]
        horizontal_anchor = anchor[0] if anchor else "l"
        if horizontal_anchor == "m":
            cursor_x = x - sum(widths) / 2
        elif horizontal_anchor == "r":
            cursor_x = x - sum(widths)
        else:
            cursor_x = x
        vertical_anchor = anchor[1] if len(anchor) > 1 else "a"
        for (run, run_font), width in zip(runs, widths):
            draw.text((cursor_x, y), run, fill=fill, font=run_font, anchor=f"l{vertical_anchor}")
            cursor_x += width

    def _preview_canvas(self, key):
        if key == "damage":
            return self.damage_preview_canvas
        if key == "boss_lock":
            return self.lock_preview_canvas
        return self.party_preview_canvas

    def _hud_scale(self, key, width, height):
        base_width, base_height = HUD_MIN_SIZES[key]
        return max(1.0, min(float(width) / base_width, float(height) / base_height))

    def _scaled(self, value, scale):
        return max(1, int(round(value * scale)))

    def _render_edit_preview(self, key):
        canvas = self._preview_canvas(key)
        if not canvas or not canvas.winfo_exists():
            return
        canvas.delete("all")
        if not self.editing:
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        layout = self.layout.get(key, {})
        scale = self._hud_scale(
            key,
            layout.get("width", width),
            layout.get("height", height),
        )
        if key == "damage":
            for index, (label, value) in enumerate(self.damage_rows):
                y = self._scaled(12 + index * 19, scale)
                canvas.create_text(
                    self._scaled(14, scale), y, text=label, fill=self.FG,
                    font=("Microsoft YaHei UI", self._scaled(9, scale)), anchor=NW,
                )
                canvas.create_text(
                    self._scaled(160, scale), y - self._scaled(1, scale),
                    text=value, fill=self.FG,
                    font=("Microsoft YaHei UI", self._scaled(10, scale), "bold"), anchor=NW,
                )
        elif key == "boss_lock":
            canvas.create_text(
                width // 2, height // 2, text=self.lock_text, fill=self.FG,
                font=("Segoe UI", self._scaled(15, scale), "bold"), anchor=CENTER,
                width=max(1, width - self._scaled(20, scale)), justify=CENTER,
            )
        else:
            canvas.create_text(
                self._scaled(14, scale), self._scaled(12, scale),
                text="伤害统计", fill=self.ACCENT,
                font=("Microsoft YaHei UI", self._scaled(12, scale), "bold"), anchor=NW,
            )
            canvas.create_text(
                self._scaled(14, scale), self._scaled(38, scale),
                text="用户名", fill=self.FG,
                font=("Microsoft YaHei UI", self._scaled(9, scale), "bold"), anchor=NW,
            )
            canvas.create_text(
                width - self._scaled(14, scale), self._scaled(38, scale),
                text="本局 BOSS 伤害", fill=self.FG,
                font=("Microsoft YaHei UI", self._scaled(9, scale), "bold"), anchor=NE,
            )
            preview_rows = self.party_rows or [("玩家1", "12.3K"), ("玩家2", "9.8K")]
            for index, (username, damage) in enumerate(preview_rows):
                y = self._scaled(62 + index * 20, scale)
                canvas.create_text(
                    self._scaled(14, scale), y, text=username, fill=self.FG,
                    font=("Microsoft YaHei UI", self._scaled(9, scale)), anchor=NW,
                )
                canvas.create_text(
                    width - self._scaled(14, scale), y, text=damage, fill=self.FG,
                    font=("Microsoft YaHei UI", self._scaled(10, scale), "bold"), anchor=NE,
                )
            total_y = self._scaled(70 + len(preview_rows) * 20, scale)
            canvas.create_text(
                self._scaled(14, scale), total_y, text="总伤害", fill=self.ACCENT,
                font=("Microsoft YaHei UI", self._scaled(10, scale), "bold"), anchor=NW,
            )
            canvas.create_text(
                width - self._scaled(14, scale), total_y,
                text=format_ecliptica_number(
                    self.party_total_damage if self.party_rows else 22100
                ),
                fill=self.ACCENT,
                font=("Microsoft YaHei UI", self._scaled(10, scale), "bold"), anchor=NE,
            )
        for inset in (7, 12, 17):
            canvas.create_line(
                width - inset, height - 3, width - 3, height - inset,
                fill=self.ACCENT, width=2,
            )

    def _render_layered_image(self, window, image):
        if _update_layered_window(window, image):
            return
        window.configure(bg=self.BG)

    def _draw_edit_grip(self, draw, width, height):
        if not self.editing:
            return
        draw.rectangle((width - 28, height - 28, width, height), fill=(0, 0, 0, 1))
        color = (170, 160, 255, 255)
        for inset in (7, 12, 17):
            draw.line(
                (width - inset, height - 3, width - 3, height - inset),
                fill=color,
                width=2,
            )

    def _render_damage_text(self, _event=None):
        if not self.damage_canvas or not self.damage_canvas.winfo_exists():
            return
        geometry = self.layout.get("damage", {})
        width = max(1, int(geometry.get("width", self.damage_window.winfo_width())))
        height = max(1, int(geometry.get("height", self.damage_window.winfo_height())))
        scale = self._hud_scale("damage", width, height)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if self.damage_title_text:
            self._draw_text(
                draw,
                self._scaled(14, scale),
                self._scaled(12, scale),
                self.damage_title_text,
                self.FG,
                _load_hud_font(self._scaled(14, scale), bold=True),
            )
        for index, (label, value) in enumerate(self.damage_rows):
            y = self._scaled(12 + index * 19, scale)
            self._draw_text(
                draw,
                self._scaled(14, scale),
                y,
                label,
                self.FG,
                _load_hud_font(self._scaled(13, scale)),
            )
            self._draw_text(
                draw,
                self._scaled(160, scale),
                y - self._scaled(1, scale),
                value,
                self.FG,
                _load_hud_font(self._scaled(14, scale), bold=True),
            )
        self._draw_edit_grip(draw, width, height)
        self._render_layered_image(self.damage_window, image)

    def _render_lock_text(self, _event=None):
        if not self.lock_canvas or not self.lock_canvas.winfo_exists():
            return
        geometry = self.layout.get("boss_lock", {})
        width = max(1, int(geometry.get("width", self.lock_window.winfo_width())))
        height = max(1, int(geometry.get("height", self.lock_window.winfo_height())))
        scale = self._hud_scale("boss_lock", width, height)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        center_x = width // 2
        maximum_width = max(1, width - self._scaled(24, scale))
        preferred_size = self._scaled(21, scale)
        single_line_minimum = self._scaled(11, scale)
        lock_font = _fit_hud_font_to_width(
            draw,
            self.lock_text,
            maximum_width,
            preferred_size,
            single_line_minimum,
        )
        if _hud_text_width(draw, self.lock_text, lock_font) <= maximum_width:
            self._draw_text(
                draw, center_x, height // 2, self.lock_text,
                self.FG, lock_font, "mm",
            )
        else:
            prefix = "Boss 当前锁定："
            target = getattr(self, "lock_target", "-")
            prefix_font = _fit_hud_font_to_width(
                draw, prefix, maximum_width,
                self._scaled(15, scale), self._scaled(9, scale),
            )
            target_font = _fit_hud_font_to_width(
                draw, target, maximum_width,
                preferred_size, self._scaled(6, scale),
            )
            line_gap = self._scaled(6, scale)
            prefix_height = prefix_font.getbbox(prefix)[3] - prefix_font.getbbox(prefix)[1]
            target_height = target_font.getbbox(target)[3] - target_font.getbbox(target)[1]
            total_height = prefix_height + line_gap + target_height
            first_y = (height - total_height) // 2 + prefix_height // 2
            second_y = first_y + prefix_height // 2 + line_gap + target_height // 2
            self._draw_text(draw, center_x, first_y, prefix, self.FG, prefix_font, "mm")
            self._draw_text(draw, center_x, second_y, target, self.FG, target_font, "mm")
        self._draw_edit_grip(draw, width, height)
        self._render_layered_image(self.lock_window, image)

    def _render_party_text(self, _event=None):
        canvas = getattr(self, "party_canvas", None)
        window = getattr(self, "party_window", None)
        if not canvas or not canvas.winfo_exists() or not window:
            return
        geometry = self.layout.get("party_damage", {})
        width = max(1, int(geometry.get("width", window.winfo_width())))
        height = max(1, int(geometry.get("height", window.winfo_height())))
        scale = self._hud_scale("party_damage", width, height)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        left = self._scaled(14, scale)
        right = width - self._scaled(14, scale)
        self._draw_text(
            draw,
            left,
            self._scaled(12, scale),
            "伤害统计",
            self.ACCENT,
            _load_hud_font(self._scaled(15, scale), bold=True),
        )
        header_font = _load_hud_font(self._scaled(10, scale), bold=True)
        self._draw_text(draw, left, self._scaled(40, scale), "用户名", self.FG, header_font)
        self._draw_text(
            draw,
            right,
            self._scaled(40, scale),
            "本局 BOSS 伤害",
            self.FG,
            header_font,
            "ra",
        )
        damage_font = _load_hud_font(self._scaled(12, scale), bold=True)
        rows = self.party_rows
        for index, (username, damage) in enumerate(rows):
            y = self._scaled(65 + index * 22, scale)
            username_font = _fit_hud_font_to_width(
                draw,
                username,
                max(1, width - self._scaled(160, scale)),
                self._scaled(11, scale),
                self._scaled(7, scale),
            )
            self._draw_text(draw, left, y, username, self.FG, username_font)
            self._draw_text(draw, right, y, damage, self.FG, damage_font, "ra")
        total_y = self._scaled(76 + len(rows) * 22, scale)
        line_y = max(self._scaled(56, scale), total_y - self._scaled(8, scale))
        draw.line((left, line_y, right, line_y), fill=(170, 160, 255, 150), width=1)
        total_font = _load_hud_font(self._scaled(12, scale), bold=True)
        self._draw_text(draw, left, total_y, "总伤害", self.ACCENT, total_font)
        self._draw_text(
            draw,
            right,
            total_y,
            format_ecliptica_number(self.party_total_damage),
            self.ACCENT,
            total_font,
            "ra",
        )
        self._draw_edit_grip(draw, width, height)
        self._render_layered_image(window, image)

    def _create_resize_grip(self, window, key):
        grip = Label(
            window,
            text="//",
            bg=self.TRANSPARENT,
            fg=self.FG,
            font=("Consolas", 11, "bold"),
            cursor="size_nw_se",
            padx=3,
            pady=1,
        )
        grip.bind(
            "<ButtonPress-1>",
            lambda event: self._start_resize(event, key, window),
        )
        grip.bind("<B1-Motion>", self._resize_window)
        self.resize_grips[key] = grip

    def _start_drag(self, event, key, window, preview=False):
        if not self.editing:
            return
        current = self.layout.get(key, {})
        self._pointer_operation = {
            "kind": "drag",
            "key": key,
            "window": window,
            "pointer_x": event.x_root,
            "pointer_y": event.y_root,
            "x": int(current.get("x", window.winfo_x())),
            "y": int(current.get("y", window.winfo_y())),
            "width": int(current.get("width", window.winfo_width())),
            "height": int(current.get("height", window.winfo_height())),
            "preview": bool(preview),
        }

    def _drag_window(self, event):
        operation = self._pointer_operation
        if not self.editing or not operation or operation.get("kind") != "drag":
            return
        window = operation["window"]
        x = operation["x"] + event.x_root - operation["pointer_x"]
        y = operation["y"] + event.y_root - operation["pointer_y"]
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = min(max(-operation["width"] + 80, x), screen_w - 80)
        y = min(max(0, y), screen_h - 40)
        if operation.get("preview"):
            self._set_preview_geometry(
                operation["key"], x, y, operation["width"], operation["height"]
            )
        else:
            self._set_window_pair_geometry(
                operation["key"], window, x, y, operation["width"], operation["height"]
            )

    def _start_resize(self, event, key, window, preview=False):
        if not self.editing:
            return
        self._pointer_operation = {
            "kind": "resize",
            "key": key,
            "window": window,
            "pointer_x": event.x_root,
            "pointer_y": event.y_root,
            "width": window.winfo_width(),
            "height": window.winfo_height(),
            "preview": bool(preview),
        }

    def _resize_window(self, event):
        operation = self._pointer_operation
        if not self.editing or not operation or operation.get("kind") != "resize":
            return
        key = operation["key"]
        window = operation["window"]
        min_width, min_height = HUD_MIN_SIZES[key]
        width = max(min_width, operation["width"] + event.x_root - operation["pointer_x"])
        height = max(min_height, operation["height"] + event.y_root - operation["pointer_y"])
        width = min(width, self.root.winfo_screenwidth())
        height = min(height, self.root.winfo_screenheight())
        x = self.layout.get(key, {}).get("x", window.winfo_x())
        y = self.layout.get(key, {}).get("y", window.winfo_y())
        if operation.get("preview"):
            self._set_preview_geometry(key, x, y, width, height)
        else:
            self._set_window_pair_geometry(key, window, x, y, width, height)
            if key == "damage":
                self._render_damage_text()
            elif key == "boss_lock":
                self._render_lock_text()
            else:
                self._render_party_text()

    def _set_preview_geometry(self, key, x, y, width, height):
        window = self._background_window(key)
        _set_native_window_rect(window, x, y, width, height)
        self.layout[key] = {
            "x": int(x), "y": int(y), "width": int(width), "height": int(height),
        }
        window.update_idletasks()
        self._render_edit_preview(key)

    def _create_damage_window(self):
        background = self._create_background_window()
        window = Toplevel(self.root)
        self._configure_content_window(window)
        self.damage_canvas = Canvas(
            window,
            bg=self.TRANSPARENT,
            width=HUD_MIN_SIZES["damage"][0],
            height=HUD_MIN_SIZES["damage"][1],
            highlightthickness=0,
        )
        self.damage_canvas.pack(fill=BOTH, expand=True)
        self.damage_canvas.bind("<Configure>", self._render_damage_text)
        self.damage_background_window = background
        self.damage_window = window
        self.damage_preview_canvas = Canvas(background, bg=self.BG, highlightthickness=0)
        self.damage_preview_canvas.pack(fill=BOTH, expand=True)
        self.damage_preview_canvas.bind(
            "<Configure>", lambda _event: self._render_edit_preview("damage")
        )
        self._bind_edit_surface(background, "damage")
        self._bind_edit_surface(self.damage_preview_canvas, "damage")
        self._bind_drag(window, "damage", window)
        self._bind_drag(self.damage_canvas, "damage", window)
        self._create_resize_grip(window, "damage")
        self._render_damage_text()
        self._set_click_through(background, True)
        self._set_click_through(window, True)

    def _create_lock_window(self):
        background = self._create_background_window()
        window = Toplevel(self.root)
        self._configure_content_window(window)
        self.lock_canvas = Canvas(
            window,
            bg=self.TRANSPARENT,
            width=HUD_MIN_SIZES["boss_lock"][0],
            height=HUD_MIN_SIZES["boss_lock"][1],
            highlightthickness=0,
        )
        self.lock_canvas.pack(fill=BOTH, expand=True)
        self.lock_canvas.bind("<Configure>", self._render_lock_text)
        self.lock_background_window = background
        self.lock_window = window
        self.lock_preview_canvas = Canvas(background, bg=self.BG, highlightthickness=0)
        self.lock_preview_canvas.pack(fill=BOTH, expand=True)
        self.lock_preview_canvas.bind(
            "<Configure>", lambda _event: self._render_edit_preview("boss_lock")
        )
        self._bind_edit_surface(background, "boss_lock")
        self._bind_edit_surface(self.lock_preview_canvas, "boss_lock")
        self._bind_drag(window, "boss_lock", window)
        self._bind_drag(self.lock_canvas, "boss_lock", window)
        self._create_resize_grip(window, "boss_lock")
        self._render_lock_text()
        self._set_click_through(background, True)
        self._set_click_through(window, True)

    def _create_party_window(self):
        background = self._create_background_window()
        window = Toplevel(self.root)
        self._configure_content_window(window)
        self.party_canvas = Canvas(
            window,
            bg=self.TRANSPARENT,
            width=HUD_MIN_SIZES["party_damage"][0],
            height=HUD_MIN_SIZES["party_damage"][1],
            highlightthickness=0,
        )
        self.party_canvas.pack(fill=BOTH, expand=True)
        self.party_canvas.bind("<Configure>", self._render_party_text)
        self.party_background_window = background
        self.party_window = window
        self.party_preview_canvas = Canvas(background, bg=self.BG, highlightthickness=0)
        self.party_preview_canvas.pack(fill=BOTH, expand=True)
        self.party_preview_canvas.bind(
            "<Configure>", lambda _event: self._render_edit_preview("party_damage")
        )
        self._bind_edit_surface(background, "party_damage")
        self._bind_edit_surface(self.party_preview_canvas, "party_damage")
        self._bind_drag(window, "party_damage", window)
        self._bind_drag(self.party_canvas, "party_damage", window)
        self._create_resize_grip(window, "party_damage")
        self._render_party_text()
        self._set_click_through(background, True)
        self._set_click_through(window, True)

    def _ensure_windows(self):
        if not self.damage_window or not self.damage_window.winfo_exists():
            self._create_damage_window()
        if not self.lock_window or not self.lock_window.winfo_exists():
            self._create_lock_window()
        if not self.party_window or not self.party_window.winfo_exists():
            self._create_party_window()

    def _default_layout(self):
        self.damage_window.update_idletasks()
        self.lock_window.update_idletasks()
        self.party_window.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        return hud_default_preset_layout(screen_w, screen_h)

    def _capture_window_layout(self, key, window, use_background=False):
        if use_background:
            background = self._background_window(key)
            if background and background.winfo_exists():
                window = background
        x, y, width, height = _native_window_rect(window)
        self.layout[key] = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def _apply_window_layout(self, key, window, defaults):
        geometry = self.layout.get(key, defaults[key])
        min_width, min_height = HUD_MIN_SIZES[key]
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(screen_w, max(min_width, int(geometry["width"])))
        height = min(screen_h, max(min_height, int(geometry["height"])))
        x = min(max(-width + 80, int(geometry["x"])), screen_w - 80)
        y = min(max(0, int(geometry["y"])), screen_h - 40)
        self._set_window_pair_geometry(key, window, x, y, width, height)

    def _content_background_size(self, key, width, height):
        """Return the opaque background size needed by the current text."""
        width = max(1, int(width))
        height = max(1, int(height))
        if key not in ("damage", "party_damage"):
            return width, height

        scale = self._hud_scale(key, width, height)
        if key == "party_damage":
            rows = getattr(self, "party_rows", ())
            total_y = self._scaled(76 + len(rows) * 22, scale)
            fitted_height = total_y + self._scaled(28, scale)
            # The right-hand column is aligned to the HUD's right edge, so the
            # background must retain the full configured width.
            return width, min(height, max(self._scaled(104, scale), fitted_height))

        rows = getattr(self, "damage_rows", ())
        if not rows:
            return min(width, self._scaled(190, scale)), min(height, self._scaled(40, scale))

        image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        label_font = _load_hud_font(self._scaled(13, scale))
        value_font = _load_hud_font(self._scaled(14, scale), bold=True)
        right_edge = self._scaled(14, scale)
        value_x = self._scaled(160, scale)
        for label, value in rows:
            right_edge = max(
                right_edge,
                self._scaled(14, scale) + _hud_text_width(draw, label, label_font),
                value_x + _hud_text_width(draw, value, value_font),
            )
        fitted_width = int(round(right_edge)) + self._scaled(14, scale)
        last_y = self._scaled(12 + (len(rows) - 1) * 19, scale)
        fitted_height = last_y + self._scaled(25, scale)
        return (
            min(width, max(self._scaled(190, scale), fitted_width)),
            min(height, max(self._scaled(40, scale), fitted_height)),
        )

    def _fit_background_to_content(self, key, window):
        if self.editing:
            return
        background = self._background_window(key)
        if not background or not background.winfo_exists():
            return
        geometry = self.layout.get(key, {})
        x = int(geometry.get("x", window.winfo_x()))
        y = int(geometry.get("y", window.winfo_y()))
        width = int(geometry.get("width", window.winfo_width()))
        height = int(geometry.get("height", window.winfo_height()))
        fitted_width, fitted_height = self._content_background_size(key, width, height)
        _set_native_window_rect(background, x, y, fitted_width, fitted_height)
        window.lift(background)

    def _required_party_content_height(self, width):
        width_scale = max(1.0, float(width) / HUD_MIN_SIZES["party_damage"][0])
        natural_height = (104 + len(getattr(self, "party_rows", ())) * 22) * width_scale
        return max(HUD_MIN_SIZES["party_damage"][1], int(natural_height + 0.999))

    def _place_windows(self):
        defaults = self._default_layout()
        party_geometry = dict(self.layout.get("party_damage", defaults["party_damage"]))
        required_party_height = self._required_party_content_height(
            party_geometry.get("width", HUD_MIN_SIZES["party_damage"][0])
        )
        if int(party_geometry.get("height", 0)) < required_party_height:
            party_geometry["height"] = required_party_height
            self.layout["party_damage"] = party_geometry
        self._apply_window_layout("damage", self.damage_window, defaults)
        self._apply_window_layout("boss_lock", self.lock_window, defaults)
        self._apply_window_layout("party_damage", self.party_window, defaults)
        self._fit_background_to_content("damage", self.damage_window)
        self._fit_background_to_content("party_damage", self.party_window)

    def set_opacity(self, opacity):
        self.opacity = normalize_hud_opacity(opacity)
        visible_opacity = max(0.2, self.opacity) if getattr(self, "editing", False) else self.opacity
        for background, content in (
            (self.damage_background_window, self.damage_window),
            (self.lock_background_window, self.lock_window),
            (self.party_background_window, self.party_window),
        ):
            if background and background.winfo_exists():
                background.attributes("-alpha", visible_opacity)
                if content and content.winfo_exists():
                    content.lift(background)

    def reset_layout(self):
        self._ensure_windows()
        self._pointer_operation = None
        defaults = self._default_layout()
        self.layout = {key: dict(value) for key, value in defaults.items()}
        self._apply_window_layout("damage", self.damage_window, defaults)
        self._apply_window_layout("boss_lock", self.lock_window, defaults)
        self._apply_window_layout("party_damage", self.party_window, defaults)
        self.damage_window.update_idletasks()
        self.lock_window.update_idletasks()
        self.party_window.update_idletasks()
        self._render_damage_text()
        self._render_lock_text()
        self._render_party_text()
        if getattr(self, "editing", False):
            self._render_edit_preview("damage")
            self._render_edit_preview("boss_lock")
            self._render_edit_preview("party_damage")
        return {key: dict(value) for key, value in self.layout.items()}

    def _apply_edit_visuals(self):
        for key, window in (
            ("damage", self.damage_window),
            ("boss_lock", self.lock_window),
            ("party_damage", self.party_window),
        ):
            background = self._background_window(key)
            background.configure(highlightbackground=self.ACCENT, highlightthickness=2)
            background.attributes("-alpha", max(0.2, self.opacity))
            self._set_click_through(background, False)
            window.withdraw()
            self._render_edit_preview(key)

    def _apply_display_visibility(self):
        show_damage = "damage" in self.display_panels
        show_boss_lock = (
            "boss_lock" in self.display_panels
            and (self.editing or self.boss_lock_active)
        )
        show_party_damage = (
            "party_damage" in self.display_panels
            and (
                self.editing
                or (
                    self.boss_battle_active
                    and len(getattr(self, "party_rows", ())) >= 2
                )
            )
        )
        party_background = getattr(self, "party_background_window", None)
        party_window = getattr(self, "party_window", None)
        if show_damage:
            self.damage_background_window.deiconify()
            if self.editing:
                self.damage_window.withdraw()
            else:
                self.damage_window.deiconify()
                self.damage_window.lift(self.damage_background_window)
        else:
            self.damage_background_window.withdraw()
            self.damage_window.withdraw()
        if show_boss_lock:
            self.lock_background_window.deiconify()
            if self.editing:
                self.lock_window.withdraw()
            else:
                self.lock_window.deiconify()
                self.lock_window.lift(self.lock_background_window)
        else:
            self.lock_background_window.withdraw()
            self.lock_window.withdraw()
        if party_background and party_window and show_party_damage:
            party_background.deiconify()
            if self.editing:
                party_window.withdraw()
            else:
                party_window.deiconify()
                party_window.lift(party_background)
        elif party_background and party_window:
            party_background.withdraw()
            party_window.withdraw()

    def begin_edit(self, snapshot, display_panels=None, selected_fields=None, party_players=None):
        self.editing = True
        self.update(snapshot, display_panels, selected_fields, party_players)
        self._apply_edit_visuals()

    def end_edit(self):
        self._ensure_windows()
        self._capture_window_layout("damage", self.damage_window, use_background=True)
        self._capture_window_layout("boss_lock", self.lock_window, use_background=True)
        self._capture_window_layout("party_damage", self.party_window, use_background=True)
        self.editing = False
        self._pointer_operation = None
        for key, window in (
            ("damage", self.damage_window),
            ("boss_lock", self.lock_window),
            ("party_damage", self.party_window),
        ):
            self.resize_grips[key].place_forget()
            background = self._background_window(key)
            background.configure(highlightbackground=self.BORDER, highlightthickness=1)
            background.attributes("-alpha", self.opacity)
            self._set_click_through(background, True)
            self._set_click_through(window, True)
            self._render_edit_preview(key)
        defaults = self._default_layout()
        self._apply_window_layout("damage", self.damage_window, defaults)
        self._apply_window_layout("boss_lock", self.lock_window, defaults)
        self._apply_window_layout("party_damage", self.party_window, defaults)
        self._render_damage_text()
        self._render_lock_text()
        self._render_party_text()
        self._fit_background_to_content("damage", self.damage_window)
        self._fit_background_to_content("party_damage", self.party_window)
        self._apply_display_visibility()
        return self.get_layout()

    def get_layout(self):
        for key, window in (
            ("damage", self.damage_window),
            ("boss_lock", self.lock_window),
            ("party_damage", self.party_window),
        ):
            if window and window.winfo_exists():
                self._capture_window_layout(key, window)
        return {key: dict(value) for key, value in self.layout.items()}

    def _set_party_players(self, party_players):
        party_rows = []
        party_total = 0
        seen_players = set()
        for player in party_players if isinstance(party_players, list) else []:
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("vrc_user_id") or "").strip()
            username = str(player.get("vrc_username") or "").strip()
            identity = player_id or username.casefold()
            if not username or not identity or identity in seen_players:
                continue
            seen_players.add(identity)
            try:
                damage = max(0, int(player.get("boss_damage") or 0))
            except (TypeError, ValueError, OverflowError):
                damage = 0
            party_total += damage
            party_rows.append((username, format_ecliptica_number(damage)))
        self.party_rows = party_rows
        self.party_total_damage = party_total

    def update_party(self, party_players):
        self._ensure_windows()
        self._set_party_players(party_players)
        party_geometry = self.layout.get("party_damage")
        required_height = self._required_party_content_height(
            (party_geometry or {}).get("width", HUD_MIN_SIZES["party_damage"][0])
        )
        if not party_geometry or int(party_geometry.get("height", 0)) < required_height:
            defaults = self._default_layout()
            party_geometry = dict(party_geometry or defaults["party_damage"])
            party_geometry["height"] = max(
                int(party_geometry.get("height", 0)),
                required_height,
            )
            self.layout["party_damage"] = party_geometry
            self._apply_window_layout("party_damage", self.party_window, defaults)
        self._fit_background_to_content("party_damage", self.party_window)
        self._render_party_text()
        self._apply_display_visibility()
        if not self.editing and self.party_window.state() == "normal":
            self._set_click_through(self.party_window, True)

    def update(self, snapshot, display_panels=None, selected_fields=None, party_players=None):
        self._ensure_windows()
        if display_panels is not None:
            self.display_panels = normalize_hud_display_panels(display_panels)
        boss_phase = snapshot.get("current_boss_phase")
        phase_text = str(boss_phase) if boss_phase is not None else "-"
        rows = {
            "class_name": ("当前职业", str(snapshot.get("class_name", "-"))),
            "stage": ("当前阶段", str(snapshot.get("stage", "-"))),
            "current_boss": ("当前 BOSS", str(snapshot.get("current_boss", "-"))),
            "current_boss_phase": ("BOSS 阶段", phase_text),
            "current_phase_damage": (
                "本局 BOSS 总伤害",
                format_ecliptica_number(snapshot.get("current_phase_damage", 0)),
            ),
            "current_phase_damage_taken": (
                "本局 BOSS 总受伤",
                format_ecliptica_number(snapshot.get("current_phase_damage_taken", 0)),
            ),
            "recent_5s_dps": (
                "近 3 秒 DPS",
                f"{snapshot.get('recent_3s_dps', snapshot.get('recent_5s_dps', 0.0)):.1f}",
            ),
            "current_boss_elapsed": (
                "当前 BOSS 耗时",
                format_ecliptica_clock(snapshot.get("current_phase_elapsed", 0)),
            ),
            "total_elapsed": ("总耗时", format_ecliptica_clock_hms(snapshot.get("total_elapsed", 0))),
        }
        field_keys = normalize_hud_damage_fields(selected_fields)
        self.damage_rows = [rows[key] for key in field_keys]

        self._set_party_players(party_players)

        aggro = snapshot.get("aggro", {})
        target = aggro.get("target", "-")
        self.lock_target = str(target)
        self.lock_text = f"Boss 当前锁定：{target}"
        self.boss_battle_active = is_boss_battle_active(snapshot)
        self.boss_lock_active = (
            aggro.get("state") not in ("inactive", None)
            and str(target).strip() not in ("", "-", "某玩家")
        )
        self._render_damage_text()
        self._render_lock_text()
        self._render_party_text()
        self._place_windows()
        self._apply_display_visibility()
        if self.editing:
            self._apply_edit_visuals()
        else:
            for window in (
                self.damage_background_window,
                self.damage_window,
                self.lock_background_window,
                self.lock_window,
                getattr(self, "party_background_window", None),
                getattr(self, "party_window", None),
            ):
                if window and window.state() == "normal":
                    self._set_click_through(window, True)

    def hide(self, force=False):
        if self.editing and not force:
            return
        for window in (
            self.damage_background_window,
            self.damage_window,
            self.lock_background_window,
            self.lock_window,
            getattr(self, "party_background_window", None),
            getattr(self, "party_window", None),
        ):
            if window and window.winfo_exists():
                window.withdraw()

    def destroy(self):
        for window in (
            self.damage_background_window,
            self.damage_window,
            self.lock_background_window,
            self.lock_window,
            getattr(self, "party_background_window", None),
            getattr(self, "party_window", None),
        ):
            if window and window.winfo_exists():
                try:
                    window.destroy()
                except Exception:
                    pass
        self.damage_background_window = None
        self.damage_window = None
        self.lock_background_window = None
        self.lock_window = None
        self.party_background_window = None
        self.party_window = None


def format_ecliptica_number(value):
    number = float(value or 0)
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(int(round(number)))


def format_ecliptica_duration(value):
    total_seconds = max(0, int(round(float(value or 0))))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}分{seconds:02d}秒"
    return f"{seconds}秒"


def format_ecliptica_clock(value):
    total_seconds = max(0, int(float(value or 0)))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def format_ecliptica_clock_hms(value):
    total_seconds = max(0, int(float(value or 0)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

# =====================
# 配置
# =====================
PLAYER_ITEM_ID_THRESHOLD = 29
IMAGE_FILENAME = resource_path("cover.png")
ICON_FILENAME = resource_path("icon.ico")

APP_NAME = "SlashCoMonitor"
if os.name == 'nt': # Windows
    # 通常是 C:\Users\用户名\AppData\Local\SlashCoMonitor
    DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), APP_NAME)
else: # Mac/Linux 备用
    DATA_DIR = os.path.join(os.path.expanduser('~'), '.local', 'share', APP_NAME)

LOCAL_JSON_FILENAME = os.path.join(DATA_DIR, "locations.json")

REMOTE_TRANSLATION_URL = "https://gitee.com/hiczvko/translated_locations/raw/master/locations.json"

DEBUG_BATTERY_LOG = False
BATTERY_DEBOUNCE_SECONDS = 0.5
PENDING_TIMEOUT_SECONDS = 20.0
PENDING_ASSOCIATE_WINDOW_SECONDS = 20.0
DEFAULT_SPONSOR_PROXY_PORT = 8080
COMMON_ACCELERATOR_PORTS = (7890, 7891, 9090, 1080, 10808, 10080)
LOG_TAIL_SCAN_BYTES = 20 * 1024 * 1024
LOG_TAIL_READ_BLOCK_BYTES = 1024 * 1024
LOG_IDENTITY_SCAN_BYTES = 2 * 1024 * 1024
LOG_PROCESS_BATCH_SIZE = 200
LOG_PROCESS_BATCH_DELAY_MS = 1
LOG_RECOVERY_SYNC_TIMEOUT_SECONDS = 15.0
UI_CALLBACK_POLL_INTERVAL_MS = 25
UI_CALLBACK_BATCH_SIZE = 200
TREE_REBUILD_DELAY_MS = 50
IMAGE_SYNC_START_DELAY_MS = 2000
LOG_FILE_CHECK_INTERVAL_SECONDS = 3.0
FUEL_HIBERNATE_CONFIRM_DELAY_MS = 700
FUEL_REQUIRED_COUNT = 8
ROUND_TIMEOUT_SECONDS = 25 * 60
ECLIPTICA_CONFIG_FILENAME = os.path.join(DATA_DIR, "ecliptica_config.json")
ECLIPTICA_HUD_REFRESH_MS = 500
ECLIPTICA_SYNC_INTERVAL_MS = 100
PANEL_MODE_LABELS = {
    "auto": "自动",
    "slashco": "SlashCo",
    "ecliptica": "Ecliptica",
}
PANEL_MODE_KEYS = {label: key for key, label in PANEL_MODE_LABELS.items()}


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


def _build_sponsor_proxy_port_candidates():
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

ITEM_TRANSLATION = {
    "Proxy-Locator": "便携式探测设备",
    "Master Lock 607": "607型主锁",
    "MasterLock": "607型主锁",
    "Royal Burger": "皇家汉堡",
    "Cookie": "曲奇",
    "Beer Keg": "啤酒桶",
    "Mayonnaise": "蛋黄酱",
    "Orange Jello": "橙味果冻",
    "Costco Frozen Pizza": "Costco速冻披萨",
    "Airport Jungle Juice": "烈性酒",
    "Rhino Pill": "犀牛丸",
    "The Rock": "岩石",
    "LabMeat": "人造肉",
    "Pocket Sand": "沙袋",
    "The Baby": "巫毒娃娃",
    "Newport Menthols": "纽波特薄荷",
    "B-GONE Soda": "B-GONE苏打水",
    "Red 40 Vial": "40号红色染剂",
    "Red40": "40号红色染剂",
    "Milk Jug": "桶装牛奶",
    "Pot of Greed": "贪婪之壶",
    "Deathward": "不死图腾",
    "Evil Jonkler Cart": "邪恶的琼克尔·卡特",
    "25 Gram Benadryl": "25g苯海拉明",
    "FlintWater": "弗林特密歇根自来水",
    "Balkan Boost": "巴尔干激素",
    "Fuel": "燃料",
    "Battery": "电池",
    "Glass Bottle": "玻璃瓶",
}

CUSTOM_ITEM_COLORS = {
    # 607型主锁
    "Master Lock 607": "#9F883C", "MasterLock": "#9F883C", "607型主锁": "#9F883C",
    # 皇家汉堡
    "Royal Burger": "#9D4C1F", "皇家汉堡": "#9D4C1F",
    # 曲奇
    "Cookie": "#B08149", "曲奇": "#B08149",
    # 啤酒桶
    "Beer Keg": "#382413", "啤酒桶": "#382413",
    # 蛋黄酱
    "Mayonnaise": "#CDC938", "蛋黄酱": "#CDC938",
    # 橙味果冻
    "Orange Jello": "#C47044", "橙味果冻": "#C47044",
    # Costco速冻披萨
    "Costco Frozen Pizza": "#006AC4", "Costco速冻披萨": "#006AC4",
    # 烈性酒
    "Airport Jungle Juice": "#E05AC8", "烈性酒": "#E05AC8",
    # 犀牛丸
    "Rhino Pill": "#958B69", "犀牛丸": "#958B69",
    # 岩石
    "The Rock": "#786A4C", "岩石": "#786A4C",
    # 人造肉
    "LabMeat": "#82630D", "人造肉": "#82630D",
    # 沙袋
    "Pocket Sand": "#ACA38A", "沙袋": "#ACA38A",
    # 巫毒娃娃
    "The Baby": "#8B733D", "巫毒娃娃": "#8B733D",
    # 纽波特薄荷
    "Newport Menthols": "#00847E", "纽波特薄荷": "#00847E",
    # B-GONE苏打水
    "B-GONE Soda": "#A2B3BB", "B-GONE苏打水": "#A2B3BB",
    # 40号红色染剂
    "Red 40 Vial": "#FE3619", "Red40": "#FE3619", "40号红色染剂": "#FE3619",
    # 桶装牛奶
    "Milk Jug": "#9B9C9C", "桶装牛奶": "#9B9C9C",
    # 贪婪之壶
    "Pot of Greed": "#34432F", "贪婪之壶": "#34432F",
    # 不死图腾
    "Deathward": "#413938", "不死图腾": "#413938",
    # 弗林特密歇根自来水
    "FlintWater": "#A5A4A4", "弗林特密歇根自来水": "#A5A4A4",
    # 巴尔干激素
    "Balkan Boost": "#B90C1A", "巴尔干激素": "#B90C1A"
}


LOCATION_TRANSLATION = {}

def load_translations():
    global LOCATION_TRANSLATION
    try:
        # 1. 尝试从用户数据目录加载在线更新缓存
        external_json = LOCAL_JSON_FILENAME
        
        # 2. 尝试从 PyInstaller 临时目录加载 (内部默认)
        internal_json = None
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            internal_json = os.path.join(sys._MEIPASS, 'locations.json')
        
        target_path = None
        source_type = ""

        if os.path.exists(external_json):
            target_path = external_json
            source_type = "外部文件"
        elif internal_json and os.path.exists(internal_json):
            target_path = internal_json
            source_type = "内置资源"
        elif os.path.exists(os.path.join(SCRIPT_DIR, 'locations.json')): # 开发环境 fallback
            target_path = os.path.join(SCRIPT_DIR, 'locations.json')
            source_type = "开发文件"

        if target_path:
            with open(target_path, 'r', encoding='utf-8') as f:
                LOCATION_TRANSLATION = json.load(f)
            print(f"成功加载 locations.json ({source_type}): {len(LOCATION_TRANSLATION)} 条翻译")
        else:
            print("警告: 未找到任何 locations.json，位置翻译将不可用")
            
    except Exception as e:
        print(f"加载 locations.json 失败: {e}")

load_translations()


# === 图片系统配置 ===
REMOTE_IMG_ROOT = ""  # 请在此填入 GitHub Raw 加速地址，例如 https://raw.gh.fake/User/Repo/main
IMG_DIR = "img_assets"
IMG_JSON = "images.json"

class SlashCoMonitorCN:
    def __init__(self, root: Tk):
        if getattr(sys, 'frozen', False):
            self.resource_dir = os.path.dirname(sys.executable)
        else:
            self.resource_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = DATA_DIR
        os.makedirs(self.base_dir, exist_ok=True)

        self.root = root
        self.root.title("SlashCoSense")
        window_width, window_height = main_window_geometry(
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.root.geometry(f"{window_width}x{window_height}")
        self.root.minsize(min(960, window_width), min(600, window_height))

        # 关闭流程状态
        self._is_shutting_down = False
        self._log_queue_after_id = None
        self._pending_tick_after_id = None
        self._round_timer_after_id = None
        self._ecliptica_hud_after_id = None
        self._ecliptica_sync_after_id = None
        self._ecliptica_osc_test_after_id = None
        self._ecliptica_auto_jump_after_id = None
        self._ui_callback_after_id = None
        self._ui_callback_queue = queue.Queue()
        self._recovering_log_state = False
        self._log_recovery_started_at = None
        self._ecliptica_osc_test_active = False
        self._sponsor_op_lock = threading.Lock()


        # 设置窗口图标
        try:
            icon_path = resource_path('icon.ico')
            if os.path.exists(icon_path):
                self.root.iconbitmap(default=icon_path)
                icon_image = Image.open(icon_path).convert("RGBA")
                self._window_icon = ImageTk.PhotoImage(icon_image, master=self.root)
                self.root.iconphoto(True, self._window_icon)
        except Exception:
            pass

        self.log_dir = os.path.expandvars(r"%USERPROFILE%\AppData\LocalLow\VRChat\VRChat")
        self.is_monitoring = True
        self.current_click_col = None

        self.gens = {
            "SC_generator1": {"battery": False, "battery_pending": False, "pending_since": 0.0},
            "SC_generator2": {"battery": False, "battery_pending": False, "pending_since": 0.0},
        }

        self.game_stats = {"fuel_base": 0, "fuel_extra": 0, "item_out": 0, "item_in": 0, "players": 0, "free_fuel": 0, "sealed_rooms": None}
        self.fuel_added_count = 0
        self.free_fuel_explicit = False
        self.item_records = {}
        self.group_order = {"地图": [], "玩家": [], "未知": []}
        self.groups = {"地图": {}, "玩家": {}, "未知": {}}
        self.ecliptica_state = EclipticaState()
        self.detected_game_mode = "slashco"
        self.current_game_mode = "slashco"
        self.ecliptica_hud = None
        self.ecliptica_hud_editing = False
        self.ecliptica_history_dialog = None

        self.last_reset_time = 0.0
        self.last_battery_event = {"SC_generator1": 0.0, "SC_generator2": 0.0}
        self.last_pending_gid = None
        self.last_pending_time = 0.0
        self.last_game_end_time = 0.0
        self.round_active = False
        self.round_started_at = None
        self.round_timed_out = False
        self.held_items = set()
        self.consumed_fuel_items = set()
        self.pending_fuel_after_ids = {}
        self.GAME_END_DEBOUNCE_SECONDS = 2.0

        # 房主检测和OCR触发标志
        self.is_host = False
        self.ocr_triggered_this_round = False

        self.wildcard_patterns = []
        self.compile_wildcard_patterns()
        self.untranslated_locations = set()

        # UI 拖动变量
        try:
            table_font = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=9)
            self.current_row_height = max(24, table_font.metrics("linespace") + 6)
        except Exception:
            self.current_row_height = 24
        self.drag_start_y = 0
        self.start_row_height_on_drag = 24


        # 加载赞助者配置（UI 需要这些变量）
        self._load_sponsor_config()
        self._load_ecliptica_config()
        self.ecliptica_osc = BossLockOscOutput(
            self.ecliptica_osc_host_var.get(),
            self.ecliptica_osc_port_var.get(),
        )
        self.ecliptica_auto_jump = AutoJumpService(
            self.ecliptica_osc_host_var.get(),
            self.ecliptica_osc_port_var.get(),
        )
        self._update_ecliptica_auto_jump_context()
        self.ecliptica_auto_jump.set_enabled(self.ecliptica_auto_jump_enabled.get())
        self.ecliptica_auto_jump.start()
        self.ecliptica_sync = EclipticaSyncClient(
            interval_seconds=ECLIPTICA_SYNC_INTERVAL_MS / 1000.0,
        )
        self.ecliptica_sync.configure(
            True,
            DEFAULT_SYNC_URL,
        )
        self.ecliptica_sync.start()

        self.setup_ui()
        self.ecliptica_hud = EclipticaDesktopHud(
            self.root,
            self.ecliptica_hud_layout,
            self._ecliptica_hud_opacity(),
        )
        if self._ecliptica_hud_preset_save_pending:
            self._save_ecliptica_config()
            self._ecliptica_hud_preset_save_pending = False
        self._ecliptica_hud_after_id = self.root.after(
            ECLIPTICA_HUD_REFRESH_MS,
            self._ecliptica_hud_tick,
        )
        self._ecliptica_sync_after_id = self.root.after(
            ECLIPTICA_SYNC_INTERVAL_MS,
            self._ecliptica_sync_tick,
        )
        self._ecliptica_auto_jump_after_id = self.root.after(
            200,
            self._ecliptica_auto_jump_status_tick,
        )

        # 日志队列 (线程安全) —— 必须在所有线程启动之前初始化!
        self._log_queue = queue.Queue()
        self._pending_log_lines = queue.Queue()
        self._log_lines_after_id = None
        self._tree_rebuild_after_id = None
        self._process_log_queue()
        self._ui_callback_after_id = self.root.after(
            UI_CALLBACK_POLL_INTERVAL_MS,
            self._process_ui_callback_queue,
        )
        self.log(f"当前版本: {APP_VERSION}")

        threading.Thread(target=self._check_app_update_worker, daemon=True).start()

        # 启动更新检查线程
        threading.Thread(target=self.check_and_update_translations, daemon=True).start()

        self._pending_tick_after_id = self.root.after(1000, self.pending_timeout_tick)
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()

        # 图片系统初始化
        self.img_mappings = {}
        self.img_notes = {}  # 图片备注
        self.img_dir = os.path.join(self.base_dir, IMG_DIR)
        self.img_json_path = os.path.join(self.base_dir, IMG_JSON)
        self.tooltip_window = None
        self.tooltip_label = None
        self.tooltip_img = None
        self.last_tooltip_row = None
        
        if not os.path.exists(self.img_dir):
            try: os.makedirs(self.img_dir)
            except: pass

        self.setup_image_system()
        self.root.after(IMAGE_SYNC_START_DELAY_MS, self._start_image_sync_thread)

        # 启动赞助者覆盖 (延迟到 mainloop 启动后)
        if HAS_SPONSOR_PROXY and self.sponsor_enabled.get():
            self.root.after(500, self._deferred_start_sponsor)



    def _ui_after(self, callback, *args):
        """安全投递到主线程，避免工作线程直接调用 Tk。"""
        if self._is_shutting_down:
            return None
        if threading.current_thread() is not threading.main_thread():
            callback_queue = getattr(self, "_ui_callback_queue", None)
            if callback_queue is None:
                return None
            callback_queue.put((callback, args))
            return "queued"
        try:
            if not self.root.winfo_exists():
                return None
            return self.root.after(0, callback, *args)
        except Exception:
            return None

    def _process_ui_callback_queue(self):
        """由 Tk 主线程轮询执行后台线程投递的回调。"""
        self._ui_callback_after_id = None
        if self._is_shutting_down:
            return

        processed = 0
        while processed < UI_CALLBACK_BATCH_SIZE:
            try:
                callback, args = self._ui_callback_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception as exc:
                self.log(f"主线程回调执行失败: {exc}")
            processed += 1

        if self._is_shutting_down:
            return
        try:
            self._ui_callback_after_id = self.root.after(
                UI_CALLBACK_POLL_INTERVAL_MS,
                self._process_ui_callback_queue,
            )
        except Exception:
            self._ui_callback_after_id = None

    def _start_image_sync_thread(self):
        if self._is_shutting_down:
            return
        threading.Thread(target=self.start_image_sync, daemon=True).start()

    def _show_update_frame(self):
        if self._is_shutting_down or not hasattr(self, "update_frame"):
            return
        try:
            if not self.update_frame.winfo_ismapped():
                self.update_frame.pack(fill=X, padx=5, pady=2, before=self.mode_left_container)
                self._refresh_left_scrollregion()
        except Exception:
            pass

    def _hide_update_frame(self):
        if self._is_shutting_down or not hasattr(self, "update_frame"):
            return
        try:
            self.update_status_var.set("")
            self.update_progress_var.set(0.0)
            if self.update_frame.winfo_ismapped():
                self.update_frame.pack_forget()
                self._refresh_left_scrollregion()
        except Exception:
            pass

    def _set_update_status(self, text, progress=None):
        if self._is_shutting_down or not hasattr(self, "update_status_var"):
            return
        try:
            self._show_update_frame()
            self.update_status_var.set(text)
            if progress is not None:
                self.update_progress_var.set(max(0, min(100, float(progress))))
        except Exception:
            pass

    def _download_update_progress(self, downloaded, total):
        if total <= 0:
            return
        pct = downloaded * 100.0 / total
        self._ui_after(self._set_update_status, f"正在下载新版本... {pct:.0f}%", pct)

    def _check_app_update_worker(self):
        if not HAS_REQUESTS:
            self._ui_after(self._hide_update_frame)
            self._ui_after(self.log, "未安装 requests 库，跳过软件更新检查")
            return

        try:
            release_data = fetch_latest_release(requests.get)
            update_info = parse_update_info(release_data, APP_VERSION)
            if not update_info:
                self._ui_after(self._hide_update_frame)
                return

            self._ui_after(
                self._set_update_status,
                f"发现新版本 {update_info.tag_name}，准备下载...",
                0,
            )
            self._ui_after(self.log, f"发现新版本: {update_info.tag_name}")

            if not is_frozen_app():
                self._ui_after(
                    self._set_update_status,
                    f"发现新版本 {update_info.tag_name}（开发模式不自动替换）",
                    0,
                )
                return

            download_path = get_update_download_path()
            download_update(
                requests.get,
                update_info.download_url,
                download_path,
                progress_callback=self._download_update_progress,
            )
            self._ui_after(self._set_update_status, "下载完成，准备重启更新...", 100)
            self._ui_after(self._schedule_apply_downloaded_update, download_path)
        except Exception as e:
            self._ui_after(self._hide_update_frame)
            self._ui_after(self.log, f"软件更新检查失败: {e}")

    def _schedule_apply_downloaded_update(self, download_path):
        if self._is_shutting_down:
            return
        try:
            self.root.after(800, self._apply_downloaded_update, download_path)
        except Exception:
            self._apply_downloaded_update(download_path)

    def _apply_downloaded_update(self, download_path):
        if self._is_shutting_down:
            return
        try:
            launch_updater_and_exit(sys.executable, download_path, app_args=sys.argv[1:])
            self.on_close()
        except Exception as e:
            self._set_update_status(f"应用更新失败: {e}", 100)
            self.log(f"应用更新失败: {e}")

    def check_and_update_translations(self):

        if os.path.exists(LOCAL_JSON_FILENAME):
            try:
                with open(LOCAL_JSON_FILENAME, 'r', encoding='utf-8') as f:
                    local_data = json.load(f)
                    LOCATION_TRANSLATION.update(local_data)
                    self.compile_wildcard_patterns()
                    self._ui_after(self.log, "已加载本地翻译缓存")
            except Exception as e:
                err_msg = str(e)
                self._ui_after(self.log, f"本地翻译缓存加载失败: {err_msg}")

        if HAS_REQUESTS and REMOTE_TRANSLATION_URL:
            try:
                self._ui_after(self.log, "正在检查在线翻译更新...")
                resp = requests.get(REMOTE_TRANSLATION_URL, timeout=5)
                if resp.status_code == 200:
                    online_data = resp.json()
                    LOCATION_TRANSLATION.update(online_data)
                    self.compile_wildcard_patterns()

                    if not os.path.exists(DATA_DIR):
                        os.makedirs(DATA_DIR, exist_ok=True)

                    with open(LOCAL_JSON_FILENAME, 'w', encoding='utf-8') as f:
                        json.dump(online_data, f, ensure_ascii=False, indent=2)

                    self._ui_after(self.log, "翻译库已在线更新")
                else:
                    status = resp.status_code
                    self._ui_after(self.log, f"在线更新失败: HTTP {status}")
            except Exception as e:
                err_msg = str(e)
                self._ui_after(self.log, f"在线更新失败: {err_msg}")
        elif not HAS_REQUESTS:
            self._ui_after(self.log, "未安装 requests 库，跳过在线更新")

    def compile_wildcard_patterns(self):
        self.wildcard_patterns = []
        for key, trans_value in LOCATION_TRANSLATION.items():
            if 'X' in key:
                escaped_key = re.escape(key)
                pattern_str = "^" + escaped_key.replace("X", r"([A-Z0-9]+)") + "$"
                try:
                    pattern = re.compile(pattern_str, re.IGNORECASE)
                    self.wildcard_patterns.append((pattern, trans_value, key))
                except re.error:
                    pass
        
        # 按键长度降序排序（确保更具体的规则如 "DeskX_WOOD" 优先于 "X_WOOD"）
        self.wildcard_patterns.sort(key=lambda x: len(x[2]), reverse=True)

    def setup_ui(self):
        self.style = ttk.Style()
        self.style.configure("Treeview", rowheight=self.current_row_height)

        self.paned = PanedWindow(self.root, orient=HORIZONTAL)
        self.paned.pack(fill=BOTH, expand=True)

        self.left_p = Frame(self.paned)
        self.right_p = Frame(self.paned)

        self.paned.add(self.left_p, width=600)
        self.paned.add(self.right_p)

        top_bar = Frame(self.left_p)
        top_bar.pack(fill=X, padx=5, pady=5)
        self.update_frame_after_widget = top_bar
        
        ttk.Label(top_bar, text="数据面板：").pack(side=LEFT)
        self.panel_mode_combo = ttk.Combobox(
            top_bar,
            textvariable=self.panel_mode_var,
            values=tuple(PANEL_MODE_LABELS.values()),
            state="readonly",
            width=10,
        )
        self.panel_mode_combo.pack(side=LEFT, padx=(0, 8))
        self.panel_mode_combo.bind("<<ComboboxSelected>>", self._on_panel_mode_changed)
        self.mode_status_var = StringVar(value="当前：SlashCo")
        self.lbl_mode_status = ttk.Label(
            top_bar,
            textvariable=self.mode_status_var,
            foreground="#6c4cff",
            font=("微软雅黑", 9, "bold"),
        )
        self.lbl_mode_status.pack(side=LEFT, padx=(0, 12))
        ttk.Button(top_bar, text="导出未翻译位置", command=self.export_untranslated).pack(side=LEFT)
        self.force_reset_button = ttk.Button(
            top_bar,
            text="强制重置数据",
            command=self.force_reset,
        )
        self.force_reset_button.pack(side=RIGHT)

        self.left_scroll_host = Frame(self.left_p)
        self.left_scroll_host.pack(fill=BOTH, expand=True)
        self.left_canvas = Canvas(self.left_scroll_host, highlightthickness=0, borderwidth=0)
        self.left_scrollbar = ttk.Scrollbar(
            self.left_scroll_host,
            orient=VERTICAL,
            command=self.left_canvas.yview,
        )
        self.left_canvas.configure(yscrollcommand=self.left_scrollbar.set)
        self.left_scrollbar.pack(side=RIGHT, fill=Y)
        self.left_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.left_content = ttk.Frame(self.left_canvas)
        self.left_content_window = self.left_canvas.create_window(
            (0, 0),
            window=self.left_content,
            anchor=NW,
        )
        self.left_canvas.bind("<Configure>", self._on_left_canvas_configure)
        self.left_content.bind("<Configure>", lambda _event: self._refresh_left_scrollregion())

        self.update_status_var = StringVar(value="")
        self.update_progress_var = DoubleVar(value=0.0)
        self.update_frame = ttk.LabelFrame(self.left_content, text="软件更新", padding=5)
        update_row = ttk.Frame(self.update_frame)
        update_row.pack(fill=X)
        ttk.Label(update_row, textvariable=self.update_status_var, font=("微软雅黑", 9)).pack(side=LEFT, fill=X, expand=True)
        self.update_progress = ttk.Progressbar(
            self.update_frame,
            maximum=100,
            variable=self.update_progress_var,
            mode="determinate",
        )
        self.update_progress.pack(fill=X, pady=(4, 0))

        self.mode_left_container = ttk.Frame(self.left_content)
        self.mode_left_container.pack(fill=X)
        self.slashco_left_frame = ttk.Frame(self.mode_left_container)
        self.ecliptica_left_frame = ttk.Frame(self.mode_left_container)
        self.slashco_left_frame.pack(fill=X)

        self.mode_right_container = ttk.Frame(self.right_p)
        self.mode_right_container.pack(fill=BOTH, expand=True)
        self.slashco_right_frame = ttk.Frame(self.mode_right_container)
        self.ecliptica_right_frame = ttk.Frame(self.mode_right_container)
        self.slashco_right_frame.pack(fill=BOTH, expand=True)

        fuel_frame = ttk.LabelFrame(self.slashco_left_frame, text="燃油进度", padding=5)
        fuel_frame.pack(fill=X, padx=5, pady=5)
        fuel_row = ttk.Frame(fuel_frame)
        fuel_row.pack(fill=X, pady=2)
        ttk.Label(fuel_row, text="油桶", width=12, font=("微软雅黑", 9)).pack(side=LEFT)
        self.fuel_progress = ttk.Progressbar(fuel_row, length=200, maximum=FUEL_REQUIRED_COUNT)
        self.fuel_progress.pack(side=LEFT, padx=5, fill=X, expand=True)
        self.lbl_fuel_progress = ttk.Label(fuel_row, text=f"{FUEL_REQUIRED_COUNT}/0", width=8, font=("Consolas", 9))
        self.lbl_fuel_progress.pack(side=LEFT)

        gen_frame = ttk.LabelFrame(self.slashco_left_frame, text="电池状态", padding=5)
        gen_frame.pack(fill=X, padx=5, pady=5)

        self.ui_gens = {}
        for gid in ["SC_generator1", "SC_generator2"]:
            row = ttk.Frame(gen_frame)
            row.pack(fill=X, pady=2)
            ttk.Label(row, text=gid.replace("SC_", "").capitalize(), width=12, font=("Consolas", 9)).pack(side=LEFT)
            bat = ttk.Label(row, text="[缺电池]", foreground="red", width=10, font=("微软雅黑", 9))
            bat.pack(side=LEFT)
            self.ui_gens[gid] = {"bat": bat}

        stats_frame = ttk.LabelFrame(self.slashco_left_frame, text="对局输出统计", padding=5)
        stats_frame.pack(fill=X, padx=5, pady=2)

        # 取消左右分栏，直接垂直排列

        # 0. 对局计时
        self.lbl_round_timer = Label(
            stats_frame,
            text="对局计时：等待开始",
            font=("微软雅黑", 12, "bold"),
            bg="#eeeeee",
            fg="#555555",
            anchor=W,
            padx=8,
            pady=3,
        )
        self.lbl_round_timer.pack(fill=X, pady=(0, 4))

        # 1. 油桶
        self.lbl_stats_fuel = ttk.Label(stats_frame, text="油桶：等待检测...", font=("微软雅黑", 12, "bold"), foreground="#d35400")
        self.lbl_stats_fuel.pack(anchor=W, pady=2)

        # 2. 物品
        self.lbl_stats_item = ttk.Label(stats_frame, text="物品：等待检测...", font=("微软雅黑", 12, "bold"), foreground="#2980b9")
        self.lbl_stats_item.pack(anchor=W, pady=2)

        # 3. 封锁房间
        self.lbl_stats_sealed = ttk.Label(stats_frame, text="", font=("微软雅黑", 12, "bold"), foreground="#8e44ad")
        self.lbl_stats_sealed.pack(anchor=W, pady=2)

        # 4. 玩家优惠（可少加油）
        self.lbl_stats_headstart = ttk.Label(stats_frame, text="", font=("微软雅黑", 12, "bold"), foreground="#27ae60")
        self.lbl_stats_headstart.pack(anchor=W, pady=2)

        self._build_ecliptica_left_panel()




        
        # 赞助者名单覆盖设置
        if HAS_SPONSOR_PROXY:
            sponsor_frame = ttk.LabelFrame(self.left_content, text="赞助者名单覆盖", padding=5)
            sponsor_frame.pack(fill=X, padx=5, pady=2, before=self.mode_left_container)
            
            row1 = ttk.Frame(sponsor_frame)
            row1.pack(fill=X, pady=2)
            self.chk_sponsor = ttk.Checkbutton(
                row1, text="启用赞助者名单覆盖",
                variable=self.sponsor_enabled,
                command=self._on_sponsor_toggle_new
            )
            self.chk_sponsor.pack(side=LEFT)
            self.lbl_sponsor_status = ttk.Label(row1, text="", foreground="gray", font=("\u5fae\u8f6f\u96c5\u9ed1", 8))
            self.lbl_sponsor_status.pack(side=RIGHT)
            
            row2 = ttk.Frame(sponsor_frame)
            row2.pack(fill=X, pady=2)
            ttk.Label(row2, text="\u663e\u793a\u540d\u79f0\uff1a").pack(side=LEFT)
            self.entry_sponsor_name = ttk.Entry(row2, textvariable=self.sponsor_name, width=20)
            self.entry_sponsor_name.pack(side=LEFT, padx=(0, 5), fill=X, expand=True)
            ttk.Button(row2, text="\u4fdd\u5b58", command=self._save_sponsor_config, width=6).pack(side=RIGHT)

            row3 = ttk.Frame(sponsor_frame)
            row3.pack(fill=X, pady=2)
            ttk.Label(row3, text="模式：").pack(side=LEFT)
            ttk.Radiobutton(
                row3,
                text="mitmdump",
                variable=self.sponsor_mode,
                value="mitm",
                command=self._save_sponsor_config,
            ).pack(side=LEFT, padx=(0, 8))
            ttk.Radiobutton(
                row3,
                text="hosts + Caddy",
                variable=self.sponsor_mode,
                value="caddy",
                command=self._save_sponsor_config,
            ).pack(side=LEFT)
            ttk.Label(
                sponsor_frame,
                text="如果当前模式不生效，请停止后切换另一个模式再试。",
                foreground="#c0392b",
                font=("\u5fae\u8f6f\u96c5\u9ed1", 8),
            ).pack(fill=X, pady=(2, 0))

        # 参考图折叠区域
        self.img_visible = False
        self.btn_toggle_img = ttk.Button(
            self.left_content,
            text="显示 Slasher 信息 ▼",
            command=self.toggle_reference_image,
        )
        self.btn_toggle_img.pack(fill=X, padx=5, pady=(5, 0))

        self.img_container = Frame(self.left_content, bg="black")
        # 默认不显示 img_container
        # self.img_container.pack(fill=X, padx=5, pady=2)

        self.lbl_image = Label(self.img_container, text="Loading...", bg="black", fg="white")
        self.lbl_image.pack(anchor=CENTER)

        self.load_fixed_height_image()

        self.log_frame = ttk.LabelFrame(self.left_content, text="系统日志", padding=5)
        self.log_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)
        self.txt_log = scrolledtext.ScrolledText(self.log_frame, height=5, font=("Consolas", 8))
        self.txt_log.pack(fill=BOTH, expand=True)

        item_frame = ttk.LabelFrame(self.slashco_right_frame, text="本局物品清单", padding=5)
        item_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)

        # 全屏画廊覆盖层 (初始隐藏)
        self.gallery_overlay = None
        self.gallery_photo_refs = []

        self.resize_bar = Frame(item_frame, height=3, cursor="sb_v_double_arrow", bg="#F0F0F0")
        self.resize_bar.pack(fill=X, side=TOP, pady=(0, 2))

        self.resize_bar.bind("<ButtonPress-1>", self.on_resize_press)
        self.resize_bar.bind("<B1-Motion>", self.on_resize_motion)
        self.resize_bar.bind("<ButtonRelease-1>", self.on_resize_release)
        self.resize_bar.bind("<Enter>", lambda e: self.resize_bar.configure(bg="#A0A0A0"))
        self.resize_bar.bind("<Leave>", lambda e: self.resize_bar.configure(bg="#F0F0F0"))

        # 恢复默认 Treeview 样式，保持整体 UI 协调
        self.style.configure("Treeview", rowheight=self.current_row_height, font=("微软雅黑", 9))
        # 修改：移除选中时的背景色映射，仅保留前景色的变化（如果需要），或者完全移除映射以保持原色
        # 这里我们将 map 置空，这样选中时就不会改变背景色
        self.style.map('Treeview', background=[], foreground=[])
        
        cols = ("ID", "NameCN", "Pos")
        self.tree = ttk.Treeview(item_frame, columns=cols, show="headings")

        self.tree.heading("ID", text="ID")
        self.tree.column("ID", width=120, minwidth=50, stretch=False)
        self.tree.heading("NameCN", text="中文名称")
        self.tree.column("NameCN", width=180, minwidth=100, stretch=True)
        self.tree.heading("Pos", text="位置")
        self.tree.column("Pos", width=250, minwidth=100, stretch=True)

        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Motion>", self.on_tree_motion)
        self.tree.bind("<Leave>", self.on_tree_leave)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select) # 新增：绑定选中事件
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="复制当前单元格", command=self.copy_selected_cell)
        self.context_menu.add_command(label="复制原名", command=self.copy_original_name)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="复制完整列 (去重)", command=self.copy_current_column)

        self.tree.tag_configure("section", background="#f0f0f0", foreground="black", font=("微软雅黑", 9, "bold"))
        self.tree.tag_configure("blank", background="#ffffff")
        
        # 注册自定义颜色标签 (智能背景)
        self.register_custom_tags()

        sc = ttk.Scrollbar(item_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=sc.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sc.pack(side=RIGHT, fill=Y)
        self._build_ecliptica_right_panel()
        self._bind_left_mousewheel(self.left_content)
        self._refresh_left_scrollregion()
        self._set_active_game_mode("slashco", log_change=False)

    def _on_left_canvas_configure(self, event):
        self.left_canvas.itemconfigure(self.left_content_window, width=max(1, event.width))
        self._refresh_left_scrollregion()

    def _refresh_left_scrollregion(self):
        if not hasattr(self, "left_canvas"):
            return
        try:
            self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
        except Exception:
            pass

    def _on_left_mousewheel(self, event):
        if not hasattr(self, "left_canvas"):
            return
        delta = int(-event.delta / 120) if event.delta else 0
        if delta:
            self.left_canvas.yview_scroll(delta, "units")
            return "break"

    def _bind_left_mousewheel(self, widget):
        try:
            widget.bind("<MouseWheel>", self._on_left_mousewheel, add="+")
            for child in widget.winfo_children():
                self._bind_left_mousewheel(child)
        except Exception:
            pass

    def _build_ecliptica_left_panel(self):
        self.ecliptica_vars = {
            "session": StringVar(value="-"),
            "class": StringVar(value="-"),
            "stage": StringVar(value="-"),
            "boss": StringVar(value="-"),
            "phase": StringVar(value="-"),
            "boss_damage": StringVar(value="0"),
            "total_damage": StringVar(value="0"),
            "dps": StringVar(value="0.0"),
            "damage_taken": StringVar(value="0"),
            "defeated": StringVar(value="0"),
            "aggro": StringVar(value="等待 Boss 战"),
        }

        hud_frame = ttk.LabelFrame(self.ecliptica_left_frame, text="Ecliptica 桌面 HUD", padding=7)
        hud_frame.pack(fill=X, padx=5, pady=5)
        hud_controls = ttk.Frame(hud_frame)
        hud_controls.pack(fill=X)
        ttk.Checkbutton(
            hud_controls,
            text="启用透明置顶 HUD",
            variable=self.ecliptica_hud_enabled,
            command=self._on_ecliptica_hud_toggle,
        ).pack(side=LEFT)
        ttk.Checkbutton(
            hud_frame,
            text="仅在 VRChat/本程序位于前台时显示",
            variable=self.ecliptica_hud_foreground_only,
            command=self._on_ecliptica_hud_toggle,
        ).pack(anchor=W, pady=(6, 0))
        hud_action_row = ttk.Frame(hud_frame)
        hud_action_row.pack(fill=X, pady=(7, 0))
        ttk.Button(
            hud_action_row,
            textvariable=self.ecliptica_hud_layout_button_var,
            command=self._on_ecliptica_hud_layout_action,
        ).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(
            hud_action_row,
            text="显示字段",
            command=lambda: self._toggle_ecliptica_hud_panel("fields"),
        ).pack(side=LEFT, fill=X, expand=True, padx=5)
        ttk.Button(
            hud_action_row,
            text="显示顺序",
            command=lambda: self._toggle_ecliptica_hud_panel("order"),
        ).pack(side=LEFT, fill=X, expand=True)
        hud_display_row = ttk.Frame(hud_frame)
        hud_display_row.pack(fill=X, pady=(7, 0))
        ttk.Label(hud_display_row, text="显示内容：").pack(side=LEFT)
        for key, label in HUD_DISPLAY_LABELS.items():
            ttk.Checkbutton(
                hud_display_row,
                text=label,
                variable=self.ecliptica_hud_display_vars[key],
                command=self._on_ecliptica_hud_display_changed,
            ).pack(side=LEFT, padx=(0, 8))
        ttk.Label(
            hud_display_row,
            text="拖动预览框，右下角缩放",
            foreground="#666666",
            font=("微软雅黑", 8),
        ).pack(side=RIGHT)
        hud_opacity_row = ttk.Frame(hud_frame)
        hud_opacity_row.pack(fill=X, pady=(7, 0))
        ttk.Label(hud_opacity_row, text="背景透明度：").pack(side=LEFT)
        self.ecliptica_hud_opacity_scale = ttk.Scale(
            hud_opacity_row,
            from_=0,
            to=100,
            variable=self.ecliptica_hud_opacity_var,
            command=self._on_ecliptica_hud_opacity_changed,
        )
        self.ecliptica_hud_opacity_scale.pack(side=LEFT, fill=X, expand=True)
        self.ecliptica_hud_opacity_scale.bind(
            "<ButtonRelease-1>",
            self._on_ecliptica_hud_opacity_saved,
        )
        self.ecliptica_hud_opacity_scale.bind(
            "<KeyRelease>",
            self._on_ecliptica_hud_opacity_saved,
        )
        ttk.Label(
            hud_opacity_row,
            textvariable=self.ecliptica_hud_opacity_text_var,
            width=5,
            anchor=E,
        ).pack(side=LEFT, padx=(6, 8))
        ttk.Button(
            hud_opacity_row,
            text="恢复默认",
            command=self._restore_default_ecliptica_hud,
        ).pack(side=RIGHT)

        self.ecliptica_hud_fields_panel = ttk.LabelFrame(
            hud_frame,
            text="伤害 HUD 显示字段",
            padding=5,
        )
        hud_fields = self.ecliptica_hud_fields_panel
        for column_index in range(3):
            hud_fields.grid_columnconfigure(column_index, weight=1)
        for index, (key, label) in enumerate(HUD_DAMAGE_FIELDS):
            ttk.Checkbutton(
                hud_fields,
                text=label,
                variable=self.ecliptica_hud_field_vars[key],
                command=self._on_ecliptica_hud_fields_changed,
            ).grid(row=index // 3, column=index % 3, sticky=W, padx=(0, 12), pady=1)

        self.ecliptica_hud_order_panel = ttk.LabelFrame(
            hud_frame,
            text="伤害 HUD 显示顺序",
            padding=8,
        )
        ttk.Label(
            self.ecliptica_hud_order_panel,
            text="按住条目并上下拖动调整顺序",
            foreground="#666666",
        ).pack(anchor=W, pady=(0, 4))
        hud_order_list = ttk.Frame(self.ecliptica_hud_order_panel)
        hud_order_list.pack(fill=X, expand=True, padx=3, pady=(2, 3))
        self.ecliptica_hud_field_list = Listbox(
            hud_order_list,
            height=len(HUD_DAMAGE_FIELD_KEYS),
            width=42,
            exportselection=False,
            activestyle="none",
            cursor="hand2",
            font=("Microsoft YaHei UI", 11),
            selectborderwidth=1,
            borderwidth=2,
            relief=GROOVE,
        )
        hud_order_scrollbar = ttk.Scrollbar(
            hud_order_list,
            orient=VERTICAL,
            command=self.ecliptica_hud_field_list.yview,
        )
        self.ecliptica_hud_field_list.configure(yscrollcommand=hud_order_scrollbar.set)
        hud_order_scrollbar.pack(side=RIGHT, fill=Y)
        self.ecliptica_hud_field_list.pack(side=LEFT, fill=X, expand=True)
        self.ecliptica_hud_field_list.bind(
            "<ButtonPress-1>",
            self._start_ecliptica_hud_field_drag,
        )
        self.ecliptica_hud_field_list.bind(
            "<B1-Motion>",
            self._drag_ecliptica_hud_field,
        )
        self.ecliptica_hud_field_list.bind(
            "<ButtonRelease-1>",
            self._finish_ecliptica_hud_field_drag,
        )
        self._refresh_ecliptica_hud_field_list()

        lock_frame = ttk.LabelFrame(self.ecliptica_left_frame, text="Boss 当前锁定", padding=8)
        lock_frame.pack(fill=X, padx=5, pady=5)
        self.lbl_ecliptica_aggro = ttk.Label(
            lock_frame,
            textvariable=self.ecliptica_vars["aggro"],
            foreground="#6c4cff",
            font=("微软雅黑", 13, "bold"),
        )
        self.lbl_ecliptica_aggro.pack(anchor=CENTER, pady=4)
        ttk.Label(
            lock_frame,
            text="目标来自当前 Boss 的所有权转移日志；长时间未更新时会标记状态可能已过时。",
            foreground="#777777",
            font=("微软雅黑", 8),
        ).pack(anchor=CENTER)

        auto_jump_frame = ttk.LabelFrame(self.ecliptica_left_frame, text="自动连跳", padding=7)
        auto_jump_frame.pack(fill=X, padx=5, pady=5)
        auto_jump_controls = ttk.Frame(auto_jump_frame)
        auto_jump_controls.pack(fill=X)
        ttk.Checkbutton(
            auto_jump_controls,
            text="启用自动连跳（按住空格）",
            variable=self.ecliptica_auto_jump_enabled,
            command=self._on_ecliptica_auto_jump_toggle,
        ).pack(side=LEFT)
        ttk.Button(
            auto_jump_controls,
            text="测试跳跃",
            command=self._test_ecliptica_auto_jump,
        ).pack(side=RIGHT)
        ttk.Label(
            auto_jump_frame,
            textvariable=self.ecliptica_auto_jump_status_var,
            foreground="#666666",
            font=("微软雅黑", 8),
        ).pack(anchor=W, pady=(5, 0))
        ttk.Label(
            auto_jump_frame,
            text="仅在 Ecliptica 世界且非幕间、VRChat 位于前台时生效。",
            foreground="#777777",
            font=("微软雅黑", 8),
        ).pack(anchor=W, pady=(2, 0))

        osc_frame = ttk.LabelFrame(self.ecliptica_left_frame, text="OSC 输出", padding=7)
        osc_frame.pack(fill=X, padx=5, pady=5)
        osc_controls = ttk.Frame(osc_frame)
        osc_controls.pack(fill=X)
        ttk.Checkbutton(
            osc_controls,
            text="输出当前 Boss 锁定",
            variable=self.ecliptica_osc_enabled,
            command=self._on_ecliptica_osc_toggle,
        ).pack(side=LEFT)
        ttk.Label(osc_controls, text="主机").pack(side=LEFT, padx=(12, 3))
        osc_host_entry = ttk.Entry(osc_controls, textvariable=self.ecliptica_osc_host_var, width=14)
        osc_host_entry.pack(side=LEFT)
        ttk.Label(osc_controls, text="端口").pack(side=LEFT, padx=(8, 3))
        osc_port_entry = ttk.Entry(osc_controls, textvariable=self.ecliptica_osc_port_var, width=7)
        osc_port_entry.pack(side=LEFT)
        ttk.Button(osc_controls, text="测试", command=self._test_ecliptica_osc).pack(side=RIGHT)
        for entry in (osc_host_entry, osc_port_entry):
            entry.bind("<FocusOut>", self._on_ecliptica_osc_settings_changed)
            entry.bind("<Return>", self._on_ecliptica_osc_settings_changed)
        osc_options = ttk.Frame(osc_frame)
        osc_options.pack(fill=X, pady=(5, 0))
        ttk.Checkbutton(
            osc_options,
            text="仅输出玩家名",
            variable=self.ecliptica_osc_name_only,
            command=self._on_ecliptica_osc_format_changed,
        ).pack(side=LEFT)
        ttk.Label(
            osc_options,
            textvariable=self.ecliptica_osc_status_var,
            foreground="#666666",
            font=("微软雅黑", 8),
        ).pack(side=RIGHT)
        osc_prefix_row = ttk.Frame(osc_frame)
        osc_prefix_row.pack(fill=X, pady=(5, 0))
        ttk.Label(osc_prefix_row, text="输出前缀：").pack(side=LEFT)
        self.ecliptica_osc_prefix_entry = ttk.Entry(
            osc_prefix_row,
            textvariable=self.ecliptica_osc_prefix_var,
        )
        self.ecliptica_osc_prefix_entry.pack(side=LEFT, fill=X, expand=True)
        self.ecliptica_osc_prefix_entry.bind("<FocusOut>", self._on_ecliptica_osc_format_changed)
        self.ecliptica_osc_prefix_entry.bind("<Return>", self._on_ecliptica_osc_format_changed)
        self._update_ecliptica_osc_prefix_state()

        sync_frame = ttk.LabelFrame(self.ecliptica_left_frame, text="同局伤害同步", padding=7)
        sync_frame.pack(fill=X, padx=5, pady=5)
        sync_status_row = ttk.Frame(sync_frame)
        sync_status_row.pack(fill=X)
        ttk.Label(sync_status_row, text="100ms 实时同步：").pack(side=LEFT)
        ttk.Label(
            sync_status_row,
            textvariable=self.ecliptica_sync_status_var,
            foreground="#666666",
            font=("微软雅黑", 8),
        ).pack(side=LEFT)
        self.ecliptica_sync_connect_button = ttk.Button(
            sync_status_row,
            text="手动连接",
            command=self._manual_ecliptica_sync_connect,
        )
        self.ecliptica_sync_connect_button.pack(side=RIGHT)
        sync_url_row = ttk.Frame(sync_frame)
        sync_url_row.pack(fill=X, pady=(5, 0))
        ttk.Label(sync_url_row, text="服务器：").pack(side=LEFT)
        ttk.Label(
            sync_url_row,
            text=DEFAULT_SYNC_URL,
            foreground="#444444",
        ).pack(side=LEFT, fill=X, expand=True)
        ttk.Label(
            sync_frame,
            text="会话 ID 与 VRC 身份将自动从日志获取；仅同步同一会话。",
            foreground="#666666",
            font=("微软雅黑", 8),
        ).pack(anchor=W, pady=(5, 0))

        summary = ttk.LabelFrame(self.ecliptica_left_frame, text="战斗概览", padding=8)
        summary.pack(fill=X, padx=5, pady=2)
        rows = (
            ("会话 ID", "session"),
            ("当前职业", "class"),
            ("当前阶段", "stage"),
            ("当前 BOSS", "boss"),
            ("BOSS 阶段", "phase"),
            ("当前 BOSS 累计伤害", "boss_damage"),
            ("本局总伤害", "total_damage"),
            ("最近结算 DPS", "dps"),
            ("本局受到伤害", "damage_taken"),
            ("已击败 BOSS", "defeated"),
        )
        for row_index, (label, key) in enumerate(rows):
            ttk.Label(summary, text=label, width=18, foreground="#666666").grid(
                row=row_index,
                column=0,
                sticky=W,
                pady=2,
            )
            ttk.Label(
                summary,
                textvariable=self.ecliptica_vars[key],
                font=("微软雅黑", 10, "bold"),
            ).grid(row=row_index, column=1, sticky=E, pady=2)
        summary.grid_columnconfigure(1, weight=1)

    def _build_ecliptica_right_panel(self):
        frame = ttk.LabelFrame(self.ecliptica_right_frame, text="Ecliptica 伤害数据", padding=7)
        frame.pack(fill=BOTH, expand=True, padx=5, pady=5)

        settlement_header = ttk.Frame(frame)
        settlement_header.pack(fill=X, pady=(0, 4))
        ttk.Label(
            settlement_header,
            text="BOSS 伤害结算",
            foreground="#6c4cff",
            font=("微软雅黑", 11, "bold"),
        ).pack(side=LEFT)
        self.ecliptica_history_button = ttk.Button(
            settlement_header,
            text="查看历史对局",
            command=self._show_ecliptica_history,
        )
        self.ecliptica_history_button.pack(side=RIGHT)
        settlement_columns = ("Boss", "Phase", "Strike", "NonStrike", "Total", "Duration", "DPS")
        self.ecliptica_settlement_tree = ttk.Treeview(
            frame,
            columns=settlement_columns,
            show="headings",
            height=10,
        )
        settlement_widths = {
            "Boss": 145,
            "Phase": 50,
            "Strike": 75,
            "NonStrike": 75,
            "Total": 75,
            "Duration": 80,
            "DPS": 60,
        }
        settlement_labels = {
            "Boss": "BOSS",
            "Phase": "阶段",
            "Strike": "直击",
            "NonStrike": "非直击",
            "Total": "总伤害",
            "Duration": "耗时",
            "DPS": "DPS",
        }
        for column in settlement_columns:
            self.ecliptica_settlement_tree.heading(column, text=settlement_labels[column])
            self.ecliptica_settlement_tree.column(
                column,
                width=settlement_widths[column],
                anchor=CENTER,
            )
        self.ecliptica_settlement_tree.pack(fill=BOTH, expand=True)

        ttk.Label(
            frame,
            text="同局玩家实时伤害",
            foreground="#1f6f3a",
            font=("微软雅黑", 11, "bold"),
        ).pack(anchor=W, pady=(12, 4))
        sync_columns = ("Player", "Boss", "Phase", "BossDamage", "TotalDamage", "Status")
        sync_tree_container = ttk.Frame(frame)
        sync_tree_container.pack(fill=BOTH, expand=True)
        self.ecliptica_sync_tree = ttk.Treeview(
            sync_tree_container,
            columns=sync_columns,
            show="headings",
            height=8,
        )
        sync_labels = {
            "Player": "VRC 用户名",
            "Boss": "当前 BOSS",
            "Phase": "阶段",
            "BossDamage": "本 BOSS 伤害",
            "TotalDamage": "BOSS 结算总伤害",
            "Status": "状态",
        }
        sync_widths = {
            "Player": 150,
            "Boss": 130,
            "Phase": 50,
            "BossDamage": 100,
            "TotalDamage": 120,
            "Status": 65,
        }
        for column in sync_columns:
            self.ecliptica_sync_tree.heading(column, text=sync_labels[column])
            self.ecliptica_sync_tree.column(
                column,
                width=sync_widths[column],
                anchor=W if column in ("Player", "Boss") else CENTER,
            )
        self.ecliptica_sync_tree.pack(side=LEFT, fill=BOTH, expand=True)
        sync_scrollbar = ttk.Scrollbar(
            sync_tree_container,
            orient=VERTICAL,
            command=self.ecliptica_sync_tree.yview,
        )
        sync_scrollbar.pack(side=RIGHT, fill=Y)
        self.ecliptica_sync_tree.configure(yscrollcommand=sync_scrollbar.set)


    def _panel_mode_preference(self):
        label = self.panel_mode_var.get() if hasattr(self, "panel_mode_var") else PANEL_MODE_LABELS["auto"]
        return PANEL_MODE_KEYS.get(label, "auto")

    def _resolved_panel_mode(self):
        preference = self._panel_mode_preference()
        if preference == "auto":
            return getattr(self, "detected_game_mode", "slashco")
        return preference

    def _show_game_panel(self, mode, reason="", log_change=True, automatic=False):
        selected = "ecliptica" if mode == "ecliptica" else "slashco"
        changed = selected != getattr(self, "current_game_mode", "slashco")
        self.current_game_mode = selected
        if not hasattr(self, "slashco_left_frame"):
            return

        self.slashco_left_frame.pack_forget()
        self.ecliptica_left_frame.pack_forget()
        self.slashco_right_frame.pack_forget()
        self.ecliptica_right_frame.pack_forget()
        if selected == "ecliptica":
            self.ecliptica_left_frame.pack(fill=X)
            self.ecliptica_right_frame.pack(fill=BOTH, expand=True)
            self.mode_status_var.set("当前：Ecliptica")
            self.lbl_mode_status.configure(foreground="#6c4cff")
        else:
            self.slashco_left_frame.pack(fill=X)
            self.slashco_right_frame.pack(fill=BOTH, expand=True)
            self.mode_status_var.set("当前：SlashCo")
            self.lbl_mode_status.configure(foreground="#1f6f3a")

        self._update_slasher_info_visibility()
        self._refresh_left_scrollregion()
        if selected != "ecliptica" and self.ecliptica_hud:
            self.ecliptica_hud.hide(force=True)

        if changed and log_change and hasattr(self, "txt_log"):
            suffix = f" ({reason})" if reason else ""
            action = "自动切换" if automatic else "切换"
            self.log(f"数据面板已{action}为 {self.mode_status_var.get().replace('当前：', '')}{suffix}")

    def _set_active_game_mode(self, mode, reason="", log_change=True):
        self.detected_game_mode = "ecliptica" if mode == "ecliptica" else "slashco"
        automatic = self._panel_mode_preference() == "auto"
        self._show_game_panel(
            self._resolved_panel_mode(),
            reason=reason if automatic else "",
            log_change=log_change and automatic,
            automatic=automatic,
        )

    def _on_panel_mode_changed(self, _event=None):
        self._save_ecliptica_config()
        preference = self._panel_mode_preference()
        self._show_game_panel(
            self._resolved_panel_mode(),
            reason="手动选择" if preference != "auto" else "跟随当前地图",
            log_change=True,
            automatic=preference == "auto",
        )

    def _update_ecliptica_ui(self):
        snapshot = self.ecliptica_state.snapshot()
        self._update_ecliptica_auto_jump_context(snapshot)
        if not hasattr(self, "ecliptica_vars"):
            return
        phase = snapshot.get("current_boss_phase")
        stage_progress = snapshot.get("stage_progress")
        stage_text = snapshot.get("stage", "-")
        if stage_progress is not None:
            stage_text = f"{stage_text} · 进度 {stage_progress}"
        self.ecliptica_vars["session"].set(snapshot.get("session_id", "-"))
        self.ecliptica_vars["class"].set(snapshot.get("class_name", "-"))
        self.ecliptica_vars["stage"].set(stage_text)
        self.ecliptica_vars["boss"].set(snapshot.get("current_boss", "-"))
        self.ecliptica_vars["phase"].set(str(phase) if phase is not None else "-")
        self.ecliptica_vars["boss_damage"].set(
            format_ecliptica_number(snapshot.get("live_current_boss_damage", 0))
        )
        self.ecliptica_vars["total_damage"].set(
            format_ecliptica_number(snapshot.get("live_session_total_damage", 0))
        )
        self.ecliptica_vars["dps"].set(f"{snapshot.get('last_settlement_dps', 0.0):.1f}")
        self.ecliptica_vars["damage_taken"].set(format_ecliptica_number(snapshot.get("session_damage_taken", 0)))
        self.ecliptica_vars["defeated"].set(str(snapshot.get("defeated_count", 0)))

        aggro = snapshot.get("aggro", {})
        target = aggro.get("target", "-")
        aggro_text = f"{target} · {aggro.get('status', '未确认')}"
        if aggro.get("locked_secs", 0) > 0:
            aggro_text += f" · {aggro['locked_secs']} 秒"
        if aggro.get("stale") and aggro.get("state") not in ("inactive", None):
            aggro_text += " · 状态可能已过时"
        self.ecliptica_vars["aggro"].set(aggro_text)
        self.lbl_ecliptica_aggro.configure(foreground="#c0392b" if aggro.get("is_local") else "#6c4cff")
        self._publish_ecliptica_osc(snapshot)

        for item in self.ecliptica_settlement_tree.get_children():
            self.ecliptica_settlement_tree.delete(item)
        for settlement in self.ecliptica_state.settlements:
            self.ecliptica_settlement_tree.insert(
                "",
                END,
                values=(
                    settlement["boss"],
                    settlement["phase"],
                    format_ecliptica_number(settlement["strike"]),
                    format_ecliptica_number(settlement["non_strike"]),
                    format_ecliptica_number(settlement["total"]),
                    format_ecliptica_duration(settlement.get("duration", 0)),
                    f"{settlement['dps']:.1f}",
                ),
            )

    def _load_ecliptica_config(self):
        self.ecliptica_hud_enabled = BooleanVar(value=False)
        self.ecliptica_hud_foreground_only = BooleanVar(value=True)
        self.panel_mode_var = StringVar(value=PANEL_MODE_LABELS["auto"])
        self.ecliptica_hud_layout_button_var = StringVar(value="HUD 布局")
        self.ecliptica_hud_display_vars = {
            key: BooleanVar(value=True) for key in HUD_DISPLAY_PANEL_KEYS
        }
        self.ecliptica_hud_opacity_var = DoubleVar(value=10.0)
        self.ecliptica_hud_opacity_text_var = StringVar(value="10%")
        self.ecliptica_hud_field_vars = {
            key: BooleanVar(value=True) for key in HUD_DAMAGE_FIELD_KEYS
        }
        self.ecliptica_hud_field_order = list(HUD_DAMAGE_FIELD_KEYS)
        self._ecliptica_hud_field_drag_index = None
        self._ecliptica_hud_drag_ghost = None
        self._ecliptica_hud_drag_ghost_y = 0.0
        self._ecliptica_hud_drag_target_y = 0.0
        self._ecliptica_hud_drag_settling = False
        self._ecliptica_hud_drag_animation_id = None
        self.ecliptica_osc_enabled = BooleanVar(value=False)
        self.ecliptica_auto_jump_enabled = BooleanVar(value=False)
        self.ecliptica_auto_jump_status_var = StringVar(value="未启用")
        self.ecliptica_osc_name_only = BooleanVar(value=False)
        self.ecliptica_osc_prefix_var = StringVar(value=DEFAULT_OSC_PREFIX)
        self.ecliptica_osc_host_var = StringVar(value=DEFAULT_OSC_HOST)
        self.ecliptica_osc_port_var = StringVar(value=str(DEFAULT_OSC_PORT))
        self.ecliptica_osc_status_var = StringVar(value="未启用")
        self.ecliptica_sync_status_var = StringVar(value="等待日志中的会话与 VRC 身份")
        self._ecliptica_sync_player_rows = ()
        self.ecliptica_hud_layout = {}
        self.ecliptica_hud_default_preset_version = 0
        self._ecliptica_hud_preset_save_pending = False
        config = {}
        try:
            if os.path.exists(ECLIPTICA_CONFIG_FILENAME):
                with open(ECLIPTICA_CONFIG_FILENAME, "r", encoding="utf-8") as config_file:
                    config = json.load(config_file)
                self.ecliptica_hud_enabled.set(bool(config.get("hud_enabled", False)))
                self.ecliptica_hud_foreground_only.set(
                    bool(config.get("hud_foreground_only", True))
                )
                panel_mode = str(config.get("panel_mode", "auto")).lower()
                self.panel_mode_var.set(PANEL_MODE_LABELS.get(panel_mode, PANEL_MODE_LABELS["auto"]))
                display_config = config.get("hud_display_panels")
                if display_config is None:
                    display_config = config.get("hud_display_mode", "both")
                selected_panels = set(normalize_hud_display_panels(display_config))
                for key, variable in self.ecliptica_hud_display_vars.items():
                    variable.set(key in selected_panels)
                if "hud_transparency" in config:
                    hud_transparency = normalize_hud_transparency(config["hud_transparency"])
                else:
                    legacy_opacity = normalize_hud_opacity(config.get("hud_opacity", 0.9))
                    hud_transparency = (1.0 - legacy_opacity) * 100.0
                self.ecliptica_hud_opacity_var.set(hud_transparency)
                self.ecliptica_hud_opacity_text_var.set(f"{round(hud_transparency)}%")
                self.ecliptica_hud_layout = normalize_hud_layout(config.get("hud_layout", {}))
                selected_fields = normalize_hud_damage_fields(config.get("hud_fields"))
                self.ecliptica_hud_field_order = normalize_hud_field_order(
                    config.get("hud_field_order", config.get("hud_fields"))
                )
                for key, variable in self.ecliptica_hud_field_vars.items():
                    variable.set(key in selected_fields)
                osc_enabled = bool(config.get("osc_enabled", False))
                self.ecliptica_osc_enabled.set(osc_enabled)
                auto_jump_enabled = bool(config.get("auto_jump_enabled", False))
                self.ecliptica_auto_jump_enabled.set(auto_jump_enabled)
                self.ecliptica_auto_jump_status_var.set(
                    "仅在 Ecliptica 世界生效" if auto_jump_enabled else "未启用"
                )
                self.ecliptica_osc_name_only.set(bool(config.get("osc_name_only", False)))
                self.ecliptica_osc_prefix_var.set(normalize_osc_prefix(config.get("osc_prefix")))
                self.ecliptica_osc_host_var.set(normalize_osc_host(config.get("osc_host")))
                self.ecliptica_osc_port_var.set(str(normalize_osc_port(config.get("osc_port"))))
                self.ecliptica_osc_status_var.set("等待锁定目标" if osc_enabled else "未启用")
                self.ecliptica_sync_status_var.set("等待日志中的会话与 VRC 身份")
        except Exception:
            config = {}

        preset = pending_hud_default_preset(
            config,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        if preset is not None:
            self.ecliptica_hud_layout = preset["layout"]
            selected_panels = set(preset["display_panels"])
            for key, variable in self.ecliptica_hud_display_vars.items():
                variable.set(key in selected_panels)
            hud_transparency = normalize_hud_transparency(preset["transparency"])
            self.ecliptica_hud_opacity_var.set(hud_transparency)
            self.ecliptica_hud_opacity_text_var.set(f"{round(hud_transparency)}%")
            self.ecliptica_hud_field_order = preset["order"]
            selected_fields = set(preset["fields"])
            for key, variable in self.ecliptica_hud_field_vars.items():
                variable.set(key in selected_fields)
            self.ecliptica_hud_default_preset_version = preset["version"]
            self._ecliptica_hud_preset_save_pending = True
        else:
            self.ecliptica_hud_default_preset_version = HUD_DEFAULT_PRESET_VERSION

    def _save_ecliptica_config(self):
        try:
            if self.ecliptica_hud and not self.ecliptica_hud.editing:
                self.ecliptica_hud_layout = self.ecliptica_hud.get_layout()
            os.makedirs(os.path.dirname(ECLIPTICA_CONFIG_FILENAME), exist_ok=True)
            with open(ECLIPTICA_CONFIG_FILENAME, "w", encoding="utf-8") as config_file:
                json.dump(
                    {
                        "hud_enabled": bool(self.ecliptica_hud_enabled.get()),
                        "hud_foreground_only": bool(self.ecliptica_hud_foreground_only.get()),
                        "panel_mode": self._panel_mode_preference(),
                        "hud_display_panels": list(self._ecliptica_hud_display_panels()),
                        "hud_transparency": self._ecliptica_hud_transparency(),
                        "hud_opacity": self._ecliptica_hud_opacity(),
                        "hud_layout": self.ecliptica_hud_layout,
                        "hud_fields": self._ecliptica_hud_fields(),
                        "hud_field_order": self.ecliptica_hud_field_order,
                        "hud_default_preset_version": self.ecliptica_hud_default_preset_version,
                        "osc_enabled": bool(self.ecliptica_osc_enabled.get()),
                        "auto_jump_enabled": bool(self.ecliptica_auto_jump_enabled.get()),
                        "osc_name_only": bool(self.ecliptica_osc_name_only.get()),
                        "osc_prefix": normalize_osc_prefix(self.ecliptica_osc_prefix_var.get()),
                        "osc_host": normalize_osc_host(self.ecliptica_osc_host_var.get()),
                        "osc_port": normalize_osc_port(self.ecliptica_osc_port_var.get()),
                    },
                    config_file,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            if hasattr(self, "txt_log"):
                self.log(f"保存 Ecliptica HUD 设置失败: {exc}")

    def _show_ecliptica_history(self):
        dialog = getattr(self, "ecliptica_history_dialog", None)
        if dialog is not None and dialog.is_open:
            dialog.show()
            return
        try:
            self.ecliptica_history_dialog = EclipticaHistoryDialog(
                self.root,
                EclipticaHistoryClient(DEFAULT_SYNC_URL),
            )
        except Exception as exc:
            self.log(f"打开历史对局失败: {exc}")
            messagebox.showerror("历史对局", f"无法打开历史对局：{exc}")

    def _refresh_ecliptica_sync_players(self, sync_snapshot):
        tree = getattr(self, "ecliptica_sync_tree", None)
        if tree is None:
            return
        local_player_id = self.ecliptica_state.local_player_id
        rows = []
        for player in sync_snapshot.get("players", []):
            player_name = player.get("vrc_username", "-")
            if player.get("vrc_user_id") == local_player_id:
                player_name = f"{player_name}（我）"
            phase = player.get("boss_phase")
            rows.append(
                (
                    player_name,
                    player.get("boss_name", "-"),
                    str(phase) if phase is not None else "-",
                    format_ecliptica_number(player.get("boss_damage", 0)),
                    format_ecliptica_number(player.get("session_total_damage", 0)),
                    "在线" if player.get("online", True) else "离线",
                )
            )
        rows = tuple(rows)
        if rows == self._ecliptica_sync_player_rows:
            return
        self._ecliptica_sync_player_rows = rows
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tree.insert("", END, values=row)

    def _ecliptica_sync_tick(self):
        self._ecliptica_sync_after_id = None
        if self._is_shutting_down:
            return
        try:
            self.ecliptica_sync.start()
            self.ecliptica_sync.update_local_state(self._ecliptica_sync_local_state())
            sync_snapshot = self.ecliptica_sync.snapshot()
            self.ecliptica_sync_status_var.set(sync_snapshot["status"])
            self._update_ecliptica_sync_connect_button(sync_snapshot)
            self._refresh_ecliptica_sync_players(sync_snapshot)
            if (
                self.ecliptica_hud
                and not self.ecliptica_hud_editing
                and self._should_show_ecliptica_hud()
            ):
                self.ecliptica_hud.update_party(sync_snapshot.get("players", []))
        except Exception as exc:
            self.ecliptica_sync_status_var.set("同步客户端异常")
            self.log(f"Ecliptica 实时同步更新失败: {exc}")
        try:
            self._ecliptica_sync_after_id = self.root.after(
                ECLIPTICA_SYNC_INTERVAL_MS,
                self._ecliptica_sync_tick,
            )
        except Exception:
            self._ecliptica_sync_after_id = None

    def _update_ecliptica_sync_connect_button(self, sync_snapshot):
        button = getattr(self, "ecliptica_sync_connect_button", None)
        if button is None:
            return
        if bool(sync_snapshot.get("connected")):
            if button.winfo_manager():
                button.pack_forget()
        elif not button.winfo_manager():
            button.pack(side=RIGHT)

    def _manual_ecliptica_sync_connect(self):
        identity_event = self._read_vrc_identity_event(self.get_latest_log_file())
        if identity_event is not None:
            self.ecliptica_state.apply(identity_event)
            self._update_ecliptica_ui()
            self.log(f"已重新读取 VRC 身份: {identity_event.groups[0]}")
        else:
            self.log("手动连接未在最新日志中找到 VRC 用户身份")
        pending_lines = getattr(self, "_pending_log_lines", None)
        if (
            getattr(self, "_recovering_log_state", False)
            and pending_lines is not None
            and pending_lines.empty()
        ):
            self._finish_log_recovery()
        self.ecliptica_sync.update_local_state(self.ecliptica_state.snapshot())
        self.ecliptica_sync.reconnect_now()
        self.ecliptica_sync_status_var.set("手动连接：正在重新读取状态…")

    def _ecliptica_sync_local_state(self):
        if getattr(self, "_recovering_log_state", False):
            started_at = getattr(self, "_log_recovery_started_at", None)
            if started_at is None:
                self._log_recovery_started_at = time.monotonic()
                return None
            if time.monotonic() - started_at < LOG_RECOVERY_SYNC_TIMEOUT_SECONDS:
                return None
            self.log("日志恢复超时，已自动解除同步等待")
            self._finish_log_recovery()
        return self.ecliptica_state.snapshot()

    def _configure_ecliptica_osc(self):
        host = normalize_osc_host(self.ecliptica_osc_host_var.get())
        port = normalize_osc_port(self.ecliptica_osc_port_var.get())
        self.ecliptica_osc_host_var.set(host)
        self.ecliptica_osc_port_var.set(str(port))
        self.ecliptica_osc.configure(host, port)
        if getattr(self, "ecliptica_auto_jump", None):
            self.ecliptica_auto_jump.configure(host, port)
        return host, port

    def _on_ecliptica_auto_jump_toggle(self):
        enabled = bool(self.ecliptica_auto_jump_enabled.get())
        self._configure_ecliptica_osc()
        self._update_ecliptica_auto_jump_context()
        self.ecliptica_auto_jump.set_enabled(enabled)
        status = self.ecliptica_auto_jump.snapshot()
        if not enabled:
            text = "未启用"
        elif not status.get("context_allowed"):
            text = status.get("pause_reason") or "当前状态已暂停"
        else:
            text = "等待 VRChat 前台"
        self.ecliptica_auto_jump_status_var.set(text)
        self._save_ecliptica_config()
        self.log(f"自动连跳已{'启用' if enabled else '关闭'}")

    def _update_ecliptica_auto_jump_context(self, snapshot=None):
        service = getattr(self, "ecliptica_auto_jump", None)
        if service is None:
            return False
        if snapshot is None:
            snapshot = self.ecliptica_state.snapshot()
        in_ecliptica = is_ecliptica_room(self.ecliptica_state.world_name)
        intermission = bool(snapshot.get("intermission"))
        if not in_ecliptica:
            service.set_context(False, "仅在 Ecliptica 世界生效")
            return False
        if intermission:
            service.set_context(False, "幕间已自动暂停")
            return False
        service.set_context(True)
        return True

    def _test_ecliptica_auto_jump(self):
        host, port = self._configure_ecliptica_osc()
        self._save_ecliptica_config()
        self.ecliptica_auto_jump_status_var.set("正在发送测试跳跃...")

        def worker():
            succeeded = self.ecliptica_auto_jump.test_jump()
            try:
                self.root.after(0, lambda: self._finish_ecliptica_auto_jump_test(succeeded))
            except Exception:
                pass

        threading.Thread(target=worker, name="osc-auto-jump-test", daemon=True).start()
        self.log(f"自动连跳测试已发送到 {host}:{port}")

    def _finish_ecliptica_auto_jump_test(self, succeeded):
        snapshot = self.ecliptica_auto_jump.snapshot()
        if succeeded:
            self.ecliptica_auto_jump_status_var.set("测试跳跃已发送")
        else:
            self.ecliptica_auto_jump_status_var.set(
                f"测试失败：{snapshot.get('error') or '未知错误'}"
            )

    def _ecliptica_auto_jump_status_tick(self):
        self._ecliptica_auto_jump_after_id = None
        if self._is_shutting_down:
            return
        try:
            status = self.ecliptica_auto_jump.snapshot()
            if status.get("error"):
                text = f"发送失败：{status['error']}"
            elif not status.get("enabled"):
                text = "未启用"
            elif status.get("testing"):
                text = "正在发送测试跳跃..."
            elif not status.get("context_allowed"):
                text = status.get("pause_reason") or "当前状态已暂停"
            elif not status.get("vrchat_foreground"):
                text = "等待 VRChat 前台"
            elif status.get("awaiting_release"):
                text = "请松开空格后重新按住"
            elif status.get("jumping"):
                text = "连跳中"
            else:
                text = "按住空格开始连跳"
            self.ecliptica_auto_jump_status_var.set(text)
        except Exception:
            pass
        try:
            self._ecliptica_auto_jump_after_id = self.root.after(
                200,
                self._ecliptica_auto_jump_status_tick,
            )
        except Exception:
            self._ecliptica_auto_jump_after_id = None

    def _publish_ecliptica_osc(self, snapshot=None, force=False):
        if not self.ecliptica_osc_enabled.get():
            self.ecliptica_osc_status_var.set("未启用")
            return False
        if getattr(self, "_ecliptica_osc_test_active", False):
            return False
        if snapshot is None:
            snapshot = self.ecliptica_state.snapshot()
        aggro = snapshot.get("aggro", {})
        target = str(aggro.get("target", "")).strip()
        if aggro.get("state") == "inactive" or target in ("", "-", "某玩家"):
            try:
                cleared = self.ecliptica_osc.clear()
                status = "等待 Boss 战" if aggro.get("state") == "inactive" else "等待锁定目标"
                self.ecliptica_osc_status_var.set(status)
                return cleared
            except Exception as exc:
                self.ecliptica_osc_status_var.set(f"清除失败：{exc}")
                return False
        try:
            sent = self.ecliptica_osc.publish_target(
                target,
                force=force,
                name_only=bool(self.ecliptica_osc_name_only.get()),
                prefix=normalize_osc_prefix(self.ecliptica_osc_prefix_var.get()),
            )
            if sent:
                self.ecliptica_osc_status_var.set(f"已发送：{target}")
            return sent
        except Exception as exc:
            self.ecliptica_osc_status_var.set(f"发送失败：{exc}")
            return False

    def _on_ecliptica_osc_toggle(self):
        self._cancel_ecliptica_osc_test()
        self._configure_ecliptica_osc()
        if self.ecliptica_osc_enabled.get():
            self.ecliptica_osc.reset()
            self._save_ecliptica_config()
            self._publish_ecliptica_osc(force=True)
        else:
            try:
                self.ecliptica_osc.clear(force=True)
                self.ecliptica_osc_status_var.set("已停止并清除显示")
            except Exception as exc:
                self.ecliptica_osc_status_var.set(f"已停止，清除失败：{exc}")
            self._save_ecliptica_config()

    def _on_ecliptica_osc_settings_changed(self, _event=None):
        self._cancel_ecliptica_osc_test(clear=True)
        self._configure_ecliptica_osc()
        self._save_ecliptica_config()
        if self.ecliptica_osc_enabled.get():
            self._publish_ecliptica_osc(force=True)

    def _update_ecliptica_osc_prefix_state(self):
        entry = getattr(self, "ecliptica_osc_prefix_entry", None)
        if entry:
            entry.configure(state="disabled" if self.ecliptica_osc_name_only.get() else "normal")

    def _on_ecliptica_osc_format_changed(self, _event=None):
        self._cancel_ecliptica_osc_test(clear=True)
        self._update_ecliptica_osc_prefix_state()
        self.ecliptica_osc.reset()
        self._save_ecliptica_config()
        if self.ecliptica_osc_enabled.get():
            self._publish_ecliptica_osc(force=True)

    def _test_ecliptica_osc(self):
        self._cancel_ecliptica_osc_test(clear=True)
        host, port = self._configure_ecliptica_osc()
        self._save_ecliptica_config()
        try:
            self.ecliptica_osc.publish_target(
                "测试玩家",
                force=True,
                name_only=bool(self.ecliptica_osc_name_only.get()),
                prefix=normalize_osc_prefix(self.ecliptica_osc_prefix_var.get()),
            )
            self._ecliptica_osc_test_active = True
            self.ecliptica_osc_status_var.set("测试已发送，3 秒后自动清除")
            self._ecliptica_osc_test_after_id = self.root.after(3000, self._finish_ecliptica_osc_test)
            self.log(f"OSC 测试已发送到 {host}:{port}")
        except Exception as exc:
            self._ecliptica_osc_test_active = False
            self.ecliptica_osc_status_var.set(f"测试发送失败：{exc}")

    def _cancel_ecliptica_osc_test(self, clear=False):
        after_id = getattr(self, "_ecliptica_osc_test_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        was_active = getattr(self, "_ecliptica_osc_test_active", False)
        self._ecliptica_osc_test_after_id = None
        self._ecliptica_osc_test_active = False
        if clear and was_active:
            try:
                self.ecliptica_osc.clear(force=True)
            except Exception:
                pass

    def _finish_ecliptica_osc_test(self):
        self._ecliptica_osc_test_after_id = None
        self._ecliptica_osc_test_active = False
        try:
            self.ecliptica_osc.clear(force=True)
        except Exception as exc:
            self.ecliptica_osc_status_var.set(f"测试清除失败：{exc}")
            return
        if self.ecliptica_osc_enabled.get():
            self._publish_ecliptica_osc(force=True)
        else:
            self.ecliptica_osc_status_var.set("测试结束，已清除")

    def _on_ecliptica_hud_toggle(self):
        self._save_ecliptica_config()
        if not self.ecliptica_hud:
            return
        if self._should_show_ecliptica_hud():
            self.ecliptica_hud.update(
                self.ecliptica_state.snapshot(),
                self._ecliptica_hud_display_panels(),
                self._ecliptica_hud_fields(),
                self._ecliptica_party_hud_players(),
            )
        else:
            self.ecliptica_hud.hide(force=True)

    def _ecliptica_hud_display_panels(self):
        variables = getattr(self, "ecliptica_hud_display_vars", {})
        return tuple(
            key for key in HUD_DISPLAY_PANEL_KEYS
            if key in variables and variables[key].get()
        )

    def _ecliptica_hud_fields(self):
        variables = getattr(self, "ecliptica_hud_field_vars", {})
        order = normalize_hud_field_order(getattr(self, "ecliptica_hud_field_order", None))
        return [key for key in order if key in variables and variables[key].get()]

    def _ecliptica_party_hud_players(self):
        sync_client = getattr(self, "ecliptica_sync", None)
        if sync_client is None:
            return []
        try:
            players = sync_client.snapshot().get("players", [])
        except Exception:
            return []
        return players if isinstance(players, list) else []

    def _toggle_ecliptica_hud_panel(self, panel_name):
        panels = {
            "fields": getattr(self, "ecliptica_hud_fields_panel", None),
            "order": getattr(self, "ecliptica_hud_order_panel", None),
        }
        target = panels.get(panel_name)
        if target is None:
            return
        was_open = bool(target.winfo_manager())
        for panel in panels.values():
            if panel is not None:
                panel.pack_forget()
        if not was_open:
            target.pack(fill=X, pady=(7, 0))
        if hasattr(self, "left_canvas"):
            self._refresh_left_scrollregion()

    def _refresh_ecliptica_hud_field_list(self, selected_index=None):
        field_list = getattr(self, "ecliptica_hud_field_list", None)
        if field_list is None:
            return
        if selected_index is None:
            selection = field_list.curselection()
            selected_index = selection[0] if selection else None
        self.ecliptica_hud_field_order = normalize_hud_field_order(
            getattr(self, "ecliptica_hud_field_order", None)
        )
        field_list.delete(0, END)
        for key in self.ecliptica_hud_field_order:
            enabled = self.ecliptica_hud_field_vars[key].get()
            marker = "✓" if enabled else "·"
            field_list.insert(END, f"{marker} {HUD_DAMAGE_FIELD_LABELS[key]}")
        if selected_index is not None and self.ecliptica_hud_field_order:
            selected_index = min(max(0, selected_index), len(self.ecliptica_hud_field_order) - 1)
            field_list.selection_set(selected_index)
            field_list.activate(selected_index)
            field_list.see(selected_index)

    def _reorder_ecliptica_hud_field(self, current_index, new_index):
        order = self.ecliptica_hud_field_order
        if not (0 <= current_index < len(order) and 0 <= new_index < len(order)):
            return False
        if current_index == new_index:
            return False
        key = order.pop(current_index)
        order.insert(new_index, key)
        self._refresh_ecliptica_hud_field_list(new_index)
        return True

    def _cleanup_ecliptica_hud_drag_animation(self):
        field_list = getattr(self, "ecliptica_hud_field_list", None)
        animation_id = getattr(self, "_ecliptica_hud_drag_animation_id", None)
        if field_list is not None and animation_id is not None:
            try:
                field_list.after_cancel(animation_id)
            except Exception:
                pass
        ghost = getattr(self, "_ecliptica_hud_drag_ghost", None)
        if ghost is not None:
            try:
                ghost.destroy()
            except Exception:
                pass
        self._ecliptica_hud_drag_ghost = None
        self._ecliptica_hud_drag_animation_id = None
        self._ecliptica_hud_drag_settling = False

    def _start_ecliptica_hud_drag_animation(self, field_list, index, pointer_y):
        required = ("get", "bbox", "after", "winfo_width", "cget")
        if not all(hasattr(field_list, name) for name in required):
            return
        self._cleanup_ecliptica_hud_drag_animation()
        try:
            row_box = field_list.bbox(index)
            row_height = row_box[3] if row_box else 24
            target_y = max(0, min(field_list.winfo_height() - row_height, pointer_y - row_height // 2))
            ghost = Label(
                field_list,
                text=field_list.get(index),
                anchor=W,
                padx=8,
                bg="#6c4cff",
                fg="#ffffff",
                font=field_list.cget("font"),
                relief=RAISED,
                borderwidth=1,
            )
            ghost.place(
                x=2,
                y=target_y,
                width=max(1, field_list.winfo_width() - 4),
                height=row_height,
            )
            ghost.lift()
            self._ecliptica_hud_drag_ghost = ghost
            self._ecliptica_hud_drag_ghost_y = float(target_y)
            self._ecliptica_hud_drag_target_y = float(target_y)
            self._ecliptica_hud_drag_settling = False
            self._animate_ecliptica_hud_drag_ghost()
        except Exception:
            self._cleanup_ecliptica_hud_drag_animation()

    def _animate_ecliptica_hud_drag_ghost(self):
        self._ecliptica_hud_drag_animation_id = None
        ghost = getattr(self, "_ecliptica_hud_drag_ghost", None)
        field_list = getattr(self, "ecliptica_hud_field_list", None)
        if ghost is None or field_list is None:
            return
        current_y = float(getattr(self, "_ecliptica_hud_drag_ghost_y", 0.0))
        target_y = float(getattr(self, "_ecliptica_hud_drag_target_y", current_y))
        distance = target_y - current_y
        if abs(distance) < 0.75:
            current_y = target_y
        else:
            current_y += distance * 0.42
        self._ecliptica_hud_drag_ghost_y = current_y
        try:
            ghost.place_configure(y=int(round(current_y)))
            ghost.lift()
        except Exception:
            self._cleanup_ecliptica_hud_drag_animation()
            return
        if self._ecliptica_hud_drag_settling and current_y == target_y:
            self._cleanup_ecliptica_hud_drag_animation()
            return
        try:
            self._ecliptica_hud_drag_animation_id = field_list.after(
                16,
                self._animate_ecliptica_hud_drag_ghost,
            )
        except Exception:
            self._cleanup_ecliptica_hud_drag_animation()

    def _start_ecliptica_hud_field_drag(self, event):
        field_list = getattr(self, "ecliptica_hud_field_list", None)
        if field_list is None:
            return
        index = field_list.nearest(event.y)
        if not 0 <= index < len(self.ecliptica_hud_field_order):
            return
        self._ecliptica_hud_field_drag_index = index
        field_list.selection_clear(0, END)
        field_list.selection_set(index)
        field_list.activate(index)
        self._start_ecliptica_hud_drag_animation(field_list, index, event.y)

    def _drag_ecliptica_hud_field(self, event):
        field_list = getattr(self, "ecliptica_hud_field_list", None)
        current_index = getattr(self, "_ecliptica_hud_field_drag_index", None)
        if field_list is None or current_index is None:
            return
        if event.y < 12:
            field_list.yview_scroll(-1, "units")
        elif event.y > field_list.winfo_height() - 12:
            field_list.yview_scroll(1, "units")
        ghost = getattr(self, "_ecliptica_hud_drag_ghost", None)
        if ghost is not None:
            try:
                ghost_height = ghost.winfo_height()
                self._ecliptica_hud_drag_target_y = float(
                    max(0, min(field_list.winfo_height() - ghost_height, event.y - ghost_height // 2))
                )
            except Exception:
                pass
        new_index = field_list.nearest(event.y)
        if self._reorder_ecliptica_hud_field(current_index, new_index):
            self._ecliptica_hud_field_drag_index = new_index
            if ghost is not None:
                try:
                    ghost.lift()
                except Exception:
                    pass
        return "break"

    def _finish_ecliptica_hud_field_drag(self, _event=None):
        if getattr(self, "_ecliptica_hud_field_drag_index", None) is None:
            return
        final_index = self._ecliptica_hud_field_drag_index
        self._ecliptica_hud_field_drag_index = None
        ghost = getattr(self, "_ecliptica_hud_drag_ghost", None)
        field_list = getattr(self, "ecliptica_hud_field_list", None)
        if ghost is not None and field_list is not None:
            try:
                row_box = field_list.bbox(final_index)
                if row_box:
                    self._ecliptica_hud_drag_target_y = float(row_box[1])
                    self._ecliptica_hud_drag_settling = True
                else:
                    self._cleanup_ecliptica_hud_drag_animation()
            except Exception:
                self._cleanup_ecliptica_hud_drag_animation()
        self._on_ecliptica_hud_fields_changed(refresh_list=False)

    def _is_hud_foreground(self):
        return is_hud_foreground()

    def _should_show_ecliptica_hud(self):
        return (
            bool(self.ecliptica_hud_enabled.get())
            and getattr(self, "current_game_mode", "slashco") == "ecliptica"
            and (
                not bool(self.ecliptica_hud_foreground_only.get())
                or self._is_hud_foreground()
            )
        )

    def _ecliptica_hud_opacity(self):
        return normalize_hud_opacity(1.0 - self._ecliptica_hud_transparency() / 100.0)

    def _ecliptica_hud_transparency(self):
        return normalize_hud_transparency(self.ecliptica_hud_opacity_var.get())

    def _on_ecliptica_hud_opacity_changed(self, value):
        percent = round(normalize_hud_transparency(value))
        self.ecliptica_hud_opacity_text_var.set(f"{percent}%")
        if self.ecliptica_hud:
            self.ecliptica_hud.set_opacity(1.0 - percent / 100.0)

    def _on_ecliptica_hud_opacity_saved(self, _event=None):
        self._save_ecliptica_config()

    def _restore_default_ecliptica_hud(self):
        for variable in self.ecliptica_hud_display_vars.values():
            variable.set(True)
        self.ecliptica_hud_foreground_only.set(True)
        self.ecliptica_hud_opacity_var.set(HUD_DEFAULT_PRESET_TRANSPARENCY)
        self.ecliptica_hud_opacity_text_var.set(
            f"{round(HUD_DEFAULT_PRESET_TRANSPARENCY)}%"
        )
        self.ecliptica_hud_field_order = list(HUD_DEFAULT_PRESET_ORDER)
        for variable in self.ecliptica_hud_field_vars.values():
            variable.set(False)
        for key in HUD_DEFAULT_PRESET_FIELDS:
            self.ecliptica_hud_field_vars[key].set(True)
        self._refresh_ecliptica_hud_field_list(0)
        if self.ecliptica_hud:
            self.ecliptica_hud.set_opacity(0.9)
            self.ecliptica_hud_layout = self.ecliptica_hud.reset_layout()
            if self._should_show_ecliptica_hud() or self.ecliptica_hud_editing:
                self.ecliptica_hud.update(
                    self.ecliptica_state.snapshot(),
                    HUD_DISPLAY_PANEL_KEYS,
                    self._ecliptica_hud_fields(),
                    self._ecliptica_party_hud_players(),
                )
            else:
                self.ecliptica_hud.hide(force=True)
        else:
            self.ecliptica_hud_layout = {}
        self._save_ecliptica_config()
        self.log("HUD 位置、尺寸、背景透明度、显示内容和排序已恢复默认")

    def _on_ecliptica_hud_display_changed(self, _event=None):
        self._save_ecliptica_config()
        if self.ecliptica_hud and (self._should_show_ecliptica_hud() or self.ecliptica_hud_editing):
            self.ecliptica_hud.update(
                self.ecliptica_state.snapshot(),
                self._ecliptica_hud_display_panels(),
                self._ecliptica_hud_fields(),
                self._ecliptica_party_hud_players(),
            )

    def _on_ecliptica_hud_fields_changed(self, refresh_list=True):
        if refresh_list:
            self._refresh_ecliptica_hud_field_list()
        self._save_ecliptica_config()
        if self.ecliptica_hud and (self._should_show_ecliptica_hud() or self.ecliptica_hud_editing):
            self.ecliptica_hud.update(
                self.ecliptica_state.snapshot(),
                self._ecliptica_hud_display_panels(),
                self._ecliptica_hud_fields(),
                self._ecliptica_party_hud_players(),
            )

    def _on_ecliptica_hud_layout_action(self):
        if not self.ecliptica_hud:
            return
        if not self.ecliptica_hud_editing:
            self.ecliptica_hud_editing = True
            self.ecliptica_hud_layout_button_var.set("保存 HUD 布局")
            self.ecliptica_hud.begin_edit(
                self.ecliptica_state.snapshot(),
                self._ecliptica_hud_display_panels(),
                self._ecliptica_hud_fields(),
                self._ecliptica_party_hud_players(),
            )
            self.log("HUD 布局预览已显示：拖动框体调整位置，拖动右下角调整大小")
            return

        self.ecliptica_hud_layout = self.ecliptica_hud.end_edit()
        self.ecliptica_hud_editing = False
        self.ecliptica_hud_layout_button_var.set("HUD 布局")
        self._save_ecliptica_config()
        if self._should_show_ecliptica_hud():
            self.ecliptica_hud.update(
                self.ecliptica_state.snapshot(),
                self._ecliptica_hud_display_panels(),
                self._ecliptica_hud_fields(),
                self._ecliptica_party_hud_players(),
            )
        else:
            self.ecliptica_hud.hide(force=True)
        self.log("HUD 布局已保存")

    def _ecliptica_hud_tick(self):
        self._ecliptica_hud_after_id = None
        if self._is_shutting_down:
            return
        try:
            snapshot = None
            if self.ecliptica_hud and self._should_show_ecliptica_hud():
                snapshot = self.ecliptica_state.snapshot()
                self.ecliptica_hud.update(
                    snapshot,
                    self._ecliptica_hud_display_panels(),
                    self._ecliptica_hud_fields(),
                    self._ecliptica_party_hud_players(),
                )
                self._update_ecliptica_ui()
            elif self.ecliptica_hud:
                self.ecliptica_hud.hide()
            if self.ecliptica_osc_enabled.get():
                if snapshot is None:
                    snapshot = self.ecliptica_state.snapshot()
                self._publish_ecliptica_osc(snapshot)
        except Exception as exc:
            self.log(f"Ecliptica HUD 更新失败: {exc}")
        try:
            self._ecliptica_hud_after_id = self.root.after(
                ECLIPTICA_HUD_REFRESH_MS,
                self._ecliptica_hud_tick,
            )
        except Exception:
            self._ecliptica_hud_after_id = None

    def is_light_color(self, hex_color):
        """判断颜色是否为浅色"""
        hex_color = hex_color.replace('#', '')
        try:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            # 计算亮度 (Luma)
            yiq = ((r * 299) + (g * 587) + (b * 114)) / 1000
            return yiq >= 128
        except:
            return True

    def register_custom_tags(self):
        seen_colors = set()
        for hex_color in CUSTOM_ITEM_COLORS.values():
            if hex_color not in seen_colors:
                tag_name = f"color_{hex_color.replace('#', '')}"
                
                # 新思路：修改背景颜色 (background)
                # 根据背景色的深浅，自动选择文字颜色 (foreground) 以保证可读性
                if self.is_light_color(hex_color):
                    # 浅色背景 -> 黑色文字
                    text_color = "black"
                else:
                    # 深色背景 -> 白色文字
                    text_color = "white"
                
                self.tree.tag_configure(tag_name, background=hex_color, foreground=text_color, font=("微软雅黑", 9, "bold"))
                
                seen_colors.add(hex_color)

    def on_resize_press(self, event):
        self.drag_start_y = event.y_root
        try:
            current = self.style.lookup("Treeview", "rowheight")
            self.start_row_height_on_drag = int(current)
        except:
            self.start_row_height_on_drag = 25

    def on_resize_motion(self, event):
        delta = event.y_root - self.drag_start_y
        new_height = self.start_row_height_on_drag + delta
        if new_height < 15: new_height = 15
        if new_height > 120: new_height = 120
        self.style.configure("Treeview", rowheight=new_height)

    def on_resize_release(self, event):
        try:
            self.current_row_height = self.style.lookup("Treeview", "rowheight")
        except Exception:
            pass

    def show_context_menu(self, event):
        self.current_click_col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
        if self.current_click_col:
            self.context_menu.post(event.x_root, event.y_root)

    def copy_selected_cell(self):
        selection = self.tree.selection()
        if not selection or not self.current_click_col: return
        try:
            col_idx = int(self.current_click_col.replace("#", "")) - 1
            item = selection[0]
            vals = self.tree.item(item, "values")
            if vals and len(vals) > col_idx:
                text = str(vals[col_idx])
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.log(f"已复制: {text}")
        except Exception:
            pass

    def copy_original_name(self):
        selection = self.tree.selection()
        if not selection: return
        item_id = selection[0]
        # 检查是否是对应的 item_record
        if item_id in self.item_records:
            rec = self.item_records[item_id]
            target_text = rec["en"]  # 默认物品英文名
            
            # 如果点击的是位置列（第3列）
            if self.current_click_col == "#3":
                target_text = rec.get("pos_raw", "") or rec.get("pos", "")
                
            if target_text:
                self.root.clipboard_clear()
                self.root.clipboard_append(target_text)
                self.log(f"已复制原名: {target_text}")

    def toggle_reference_image(self):
        self.img_visible = not self.img_visible
        self._update_slasher_info_visibility()
        self._refresh_left_scrollregion()

    def _update_slasher_info_visibility(self):
        if not hasattr(self, "btn_toggle_img"):
            return
        if getattr(self, "current_game_mode", "slashco") != "slashco":
            self.btn_toggle_img.pack_forget()
            self.img_container.pack_forget()
            return

        if not self.btn_toggle_img.winfo_manager():
            self.btn_toggle_img.pack(fill=X, padx=5, pady=(5, 0), before=self.log_frame)
        if self.img_visible:
            self.img_container.pack(fill=X, padx=5, pady=2, after=self.btn_toggle_img)
            self.btn_toggle_img.configure(text="隐藏 Slasher 信息 ▲")
        else:
            self.img_container.pack_forget()
            self.btn_toggle_img.configure(text="显示 Slasher 信息 ▼")

    def copy_current_column(self):
        if not self.current_click_col: return
        try:
            col_idx = int(self.current_click_col.replace("#", "")) - 1
        except Exception:
            return
        data_list = []
        seen = set()
        for item in self.tree.get_children():
            tags = self.tree.item(item, "tags")
            if "section" in tags or "blank" in tags:
                continue
            vals = self.tree.item(item, "values")
            if vals and len(vals) > col_idx:
                val = str(vals[col_idx]).strip()
                if val and val not in seen:
                    data_list.append(val)
                    seen.add(val)
        if data_list:
            text = "\n".join(data_list)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.log(f"已复制列数据 ({len(data_list)} 条)")


    def export_untranslated(self):
        if not self.untranslated_locations:
            self.log("没有检测到新的未翻译位置")
            return

        filename = "untranslated_locations.txt"
        filepath = os.path.join(self.base_dir, filename)

        try:
            existing = set()
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        existing.add(line.strip())

            to_write = sorted(list(existing.union(self.untranslated_locations)))

            with open(filepath, "w", encoding="utf-8") as f:
                for loc in to_write:
                    if loc.strip():
                        f.write(f"{loc}\n")

            self.log(f"已导出 {len(self.untranslated_locations)} 个新位置到: {filename}")
            self.untranslated_locations.clear()
            messagebox.showinfo("导出成功", f"未翻译位置已保存至:\n{filepath}")

        except Exception as e:
            self.log(f"导出失败: {e}")
            messagebox.showerror("错误", f"导出文件失败: {e}")

    # =========================================================
    # 赞助者名单覆盖 (Caddy 方案)
    # =========================================================
    def _load_sponsor_config(self):
        """加载赞助者配置"""
        self.sponsor_enabled = BooleanVar(value=False)
        self.sponsor_name = StringVar(value="")
        self.sponsor_mode = StringVar(value="mitm")

        config_path = os.path.join(self.base_dir, "sponsor_config.json")
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                self.sponsor_enabled.set(cfg.get("enabled", False))
                self.sponsor_name.set(cfg.get("name", ""))
                mode = cfg.get("mode", "mitm")
                if HAS_SPONSOR_PROXY and hasattr(sponsor_caddy, "normalize_sponsor_mode"):
                    mode = sponsor_caddy.normalize_sponsor_mode(mode)
                self.sponsor_mode.set(mode if mode in ("mitm", "caddy") else "mitm")
        except Exception:
            pass

    def _save_sponsor_config(self):
        """保存赞助者配置"""
        config_path = os.path.join(self.base_dir, "sponsor_config.json")
        mode = self.sponsor_mode.get() if hasattr(self, "sponsor_mode") else "mitm"
        if HAS_SPONSOR_PROXY and hasattr(sponsor_caddy, "normalize_sponsor_mode"):
            mode = sponsor_caddy.normalize_sponsor_mode(mode)
        cfg = {
            "enabled": self.sponsor_enabled.get(),
            "name": self.sponsor_name.get().strip(),
            "mode": mode,
        }
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            mode_label = "hosts + Caddy" if cfg["mode"] == "caddy" else "mitmdump"
            self.log(f"赞助者设置已保存 (名称: {cfg['name'] or '未设置'}, 模式: {mode_label})")
        except Exception as e:
            self.log(f"保存赞助者设置失败: {e}")

    def _select_sponsor_proxy_port(self):
        blocked = set(COMMON_ACCELERATOR_PORTS)
        try:
            proxy_settings = sponsor_caddy._get_proxy_settings()
        except Exception:
            proxy_settings = {}

        if proxy_settings and proxy_settings.get("ProxyEnable") == 1:
            upstream_port = _parse_port_from_proxy_server(proxy_settings.get("ProxyServer"))
            if upstream_port:
                blocked.add(upstream_port)

        for port in _build_sponsor_proxy_port_candidates():
            if port in blocked:
                continue
            if _is_local_port_available(port):
                return port

        current_default = _parse_port(getattr(sponsor_caddy, "PROXY_PORT", DEFAULT_SPONSOR_PROXY_PORT))
        return current_default or DEFAULT_SPONSOR_PROXY_PORT

    def _prepare_sponsor_proxy_port(self):
        selected_port = self._select_sponsor_proxy_port()
        current_port = _parse_port(getattr(sponsor_caddy, "PROXY_PORT", DEFAULT_SPONSOR_PROXY_PORT))
        sponsor_caddy.PROXY_PORT = selected_port
        if selected_port != current_port:
            self.log(f"代理端口预设为: {selected_port}")
        return selected_port

    def _force_cleanup_sponsor_proxy(self):
        if not HAS_SPONSOR_PROXY:
            return

        try:
            force_cleanup = getattr(sponsor_caddy, "force_cleanup", None)
            if callable(force_cleanup):
                force_cleanup(log_func=None)
                return
        except Exception:
            pass

        for fn_name in ("_restore_proxy", "_stop_mitmdump", "_cleanup_old_caddy_residuals"):
            try:
                fn = getattr(sponsor_caddy, fn_name, None)
                if callable(fn):
                    fn(log_func=None)
            except Exception:
                pass

    def _on_sponsor_toggle_new(self):
        """开关切换事件"""
        try:
            is_enabled = self.sponsor_enabled.get()
            self._save_sponsor_config()
            if is_enabled:
                threading.Thread(target=self._start_sponsor_server, daemon=True).start()
            else:
                # 放入线程防止卡死 UI
                threading.Thread(target=self._stop_sponsor_server, daemon=True).start()
        except Exception as e:
            self.log(f"Toggle error: {e}")

    # --- 赞助者名单覆盖: Caddy 集成 ---

    def _start_sponsor_server(self):
        """使用 Caddy 启动赞助者名单覆盖"""
        if not self._sponsor_op_lock.acquire(blocking=False):
            self.log("赞助覆盖操作进行中，已忽略本次启动请求")
            return
        name = self.sponsor_name.get().strip()
        try:
            if not name:
                self._ui_after(messagebox.showwarning, "提示", "请先输入显示名称")
                self._ui_after(self.sponsor_enabled.set, False)
                return

            self._ui_after(self._update_sponsor_status, "正在启动...", "#f39c12")
            mode = self.sponsor_mode.get() if hasattr(self, "sponsor_mode") else "mitm"
            if HAS_SPONSOR_PROXY and hasattr(sponsor_caddy, "normalize_sponsor_mode"):
                mode = sponsor_caddy.normalize_sponsor_mode(mode)
            if mode == "mitm":
                self._prepare_sponsor_proxy_port()

            def _log_to_ui(msg):
                self.log(msg)

            success, message = sponsor_caddy.start_sponsor_override(name, log_func=_log_to_ui, mode=mode)

            if success:
                mode_label = "hosts + Caddy" if mode == "caddy" else "mitmdump"
                self._ui_after(self._update_sponsor_status, f"运行中 ✓ ({mode_label})", "#27ae60")
            else:
                self._ui_after(self._update_sponsor_status, f"失败: {message}；可切换另一个模式再试", "red")
                self._ui_after(self.sponsor_enabled.set, False)
        finally:
            self._sponsor_op_lock.release()

    def _stop_sponsor_server(self):
        """停止赞助者名单覆盖"""
        if not self._sponsor_op_lock.acquire(blocking=False):
            self.log("赞助覆盖操作进行中，已忽略本次停止请求")
            return
        try:
            self._ui_after(self.log, "正在停止赞助者名单覆盖...")
            log_func = None if self._is_shutting_down else self.log
            sponsor_caddy.stop_sponsor_override(log_func=log_func)
            self._ui_after(self._update_sponsor_status, "已停止", "gray")
        except Exception as e:
            self._ui_after(self.log, f"停止失败: {e}")
        finally:
            self._sponsor_op_lock.release()

    def _force_stop_sponsor_process(self):
        """关闭时兜底终止 mitmdump 进程，避免阻塞主线程。"""
        try:
            try:
                self._force_cleanup_sponsor_proxy()
            except Exception:
                pass

            proc = getattr(sponsor_caddy, "_mitm_process", None)
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

    def _stop_sponsor_server_with_timeout(self, timeout_seconds=1.5):
        """限时停止赞助者覆盖，超时后执行兜底终止，保证关窗快速返回。"""
        done = threading.Event()

        def _worker():
            try:
                with self._sponsor_op_lock:
                    sponsor_caddy.stop_sponsor_override(log_func=None)
            finally:
                done.set()

        threading.Thread(target=_worker, daemon=True).start()
        done.wait(timeout_seconds)
        if not done.is_set():
            self._force_stop_sponsor_process()
        try:
            self._force_cleanup_sponsor_proxy()
        except Exception:
            pass

    def _update_sponsor_status(self, text, color):
        """更新状态标签"""
        if hasattr(self, 'lbl_sponsor_status'):
            self.lbl_sponsor_status.configure(text=text, foreground=color)


    def load_fixed_height_image(self):
        if not os.path.exists(IMAGE_FILENAME):
            self.lbl_image.configure(text=f"图片文件 {IMAGE_FILENAME} 不存在")
            return

        try:
            self.tk_img = PhotoImage(file=IMAGE_FILENAME)
            self.lbl_image.configure(image=self.tk_img, text="")
        except Exception as e:
            self.lbl_image.configure(text=f"图片加载失败: {e}\n(请确保图片已手动调整为高度550像素)")



    def log(self, msg: str):
        if self._is_shutting_down:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{ts}] {msg}\n"
        if threading.current_thread() is threading.main_thread():
            try:
                self.txt_log.insert(END, formatted)
                self.txt_log.see(END)
            except Exception:
                pass
        else:
            # 从非主线程: 放入队列，由主线程轮询处理
            self._log_queue.put(formatted)

    def _process_log_queue(self):
        """主线程定时轮询日志队列"""
        if self._is_shutting_down:
            self._log_queue_after_id = None
            return
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.txt_log.insert(END, msg)
                self.txt_log.see(END)
        except queue.Empty:
            pass
        if self._is_shutting_down:
            self._log_queue_after_id = None
            return
        try:
            self._log_queue_after_id = self.root.after(100, self._process_log_queue)
        except Exception:
            self._log_queue_after_id = None

    def _deferred_start_sponsor(self):
        """在 mainloop 启动后异步启动覆盖"""
        if self._is_shutting_down:
            return
        threading.Thread(target=self._start_sponsor_server, daemon=True).start()

    def force_reset(self):
        if getattr(self, "current_game_mode", "slashco") == "ecliptica":
            world_name = self.ecliptica_state.world_name
            self.ecliptica_state.reset(preserve_world=False)
            self.ecliptica_state.world_name = world_name
            self._update_ecliptica_ui()
            self.log("=== 已手动重置 Ecliptica 战斗数据 ===")
        else:
            self.reset_game(force=True, reason="手动强制重置")

    def _format_round_elapsed(self, elapsed_seconds):
        total = max(0, int(elapsed_seconds))
        minutes, seconds = divmod(total, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _cancel_round_timer_tick(self):
        after_id = getattr(self, "_round_timer_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self._round_timer_after_id = None

    def _set_round_timer_ui(self, text, bg="#eeeeee", fg="#555555"):
        label = getattr(self, "lbl_round_timer", None)
        if label:
            label.configure(text=text, bg=bg, fg=fg)

    def _update_round_timer_ui(self):
        if not getattr(self, "round_active", False) or self.round_started_at is None:
            self._set_round_timer_ui("对局计时：等待开始", "#eeeeee", "#555555")
            return

        elapsed = time.monotonic() - self.round_started_at
        formatted = self._format_round_elapsed(elapsed)
        if elapsed >= ROUND_TIMEOUT_SECONDS:
            self._set_round_timer_ui(f"对局计时：{formatted}  超时", "#d93025", "white")
            if not self.round_timed_out:
                self.round_timed_out = True
                self.log("对局计时已超过 25 分钟。")
        else:
            self._set_round_timer_ui(f"对局计时：{formatted}", "#d8f5d0", "#1f6f3a")

    def _round_timer_tick(self):
        self._round_timer_after_id = None
        if self._is_shutting_down:
            return
        self._update_round_timer_ui()
        if not getattr(self, "round_active", False):
            return
        try:
            self._round_timer_after_id = self.root.after(1000, self._round_timer_tick)
        except Exception:
            self._round_timer_after_id = None

    def start_round_timer(self):
        self.round_active = True
        self.round_started_at = time.monotonic()
        self.round_timed_out = False
        self._cancel_round_timer_tick()
        self._update_round_timer_ui()
        try:
            self._round_timer_after_id = self.root.after(1000, self._round_timer_tick)
        except Exception:
            self._round_timer_after_id = None

    def stop_round_timer(self):
        self.round_active = False
        self.round_started_at = None
        self.round_timed_out = False
        self._cancel_round_timer_tick()
        self._update_round_timer_ui()

    def begin_round(self, reason):
        if getattr(self, "round_active", False):
            self.log(f"忽略重复开始信号: {reason}")
            return
        self.reset_game(force=True, reason=reason)
        self.start_round_timer()

    def reset_game(self, force=False, reason=""):
        now = time.time()
        if not force and (now - self.last_reset_time < 5):
            return
        self.last_reset_time = now
        self.log(f"=== {reason if reason else '正在重置对局状态'} ===")
        if self._tree_rebuild_after_id:
            try:
                self.root.after_cancel(self._tree_rebuild_after_id)
            except Exception:
                pass
            self._tree_rebuild_after_id = None
        self.item_records.clear()
        self.group_order = {"地图": [], "玩家": [], "未知": []}
        self.groups = {"地图": {}, "玩家": {}, "未知": {}}
        self.rebuild_item_tree()
        self.game_stats = {"fuel_base": 0, "fuel_extra": 0, "item_out": 0, "item_in": 0, "players": 0, "free_fuel": 0, "sealed_rooms": None}
        self.fuel_added_count = 0
        self.free_fuel_explicit = False
        self.update_fuel_ui()
        self.update_stats_ui()
        self.stop_round_timer()
        self.held_items.clear()
        self.consumed_fuel_items.clear()
        self._cancel_pending_fuel_hibernations()
        for gid in self.gens:
            self.gens[gid] = {"battery": False, "battery_pending": False, "pending_since": 0.0}
            self.last_battery_event[gid] = 0.0
            self.update_gen_ui(gid)
        self.last_pending_gid = None
        self.last_pending_time = 0.0

    def update_gen_ui(self, gid: str):
        data = self.gens[gid]
        ui = self.ui_gens[gid]
        if data.get("battery_pending", False) and not data.get("battery", False):
            ui["bat"].configure(text="[安装中]", foreground="orange")
            return
        if data["battery"]:
            ui["bat"].configure(text="[有电池]", foreground="green")
        else:
            ui["bat"].configure(text="[缺电池]", foreground="red")

    def infer_free_fuel_from_players(self, players: int) -> int:
        if players <= 0:
            return 0
        return max(0, min(FUEL_REQUIRED_COUNT, 5 - players))

    def get_fuel_count(self) -> int:
        free_fuel = int(self.game_stats.get("free_fuel", 0) or 0)
        added = int(getattr(self, "fuel_added_count", 0) or 0)
        return max(0, min(FUEL_REQUIRED_COUNT, free_fuel + added))

    def update_fuel_ui(self):
        current = self.get_fuel_count()
        if hasattr(self, "fuel_progress"):
            try:
                self.fuel_progress["value"] = current
            except Exception:
                pass
        if hasattr(self, "lbl_fuel_progress"):
            try:
                self.lbl_fuel_progress.configure(text=f"{FUEL_REQUIRED_COUNT}/{current}")
            except Exception:
                pass

    def set_player_fuel_headstart(self, players: int, free_fuel=None, explicit=False):
        players = max(0, int(players or 0))
        self.game_stats["players"] = players
        # 地图日志中的 free fuel 可能出现负数，显示和进度固定使用人数规则。
        free_fuel = self.infer_free_fuel_from_players(players)
        self.game_stats["free_fuel"] = max(0, min(FUEL_REQUIRED_COUNT, int(free_fuel or 0)))
        if explicit:
            self.free_fuel_explicit = True
        self.update_fuel_ui()

    def add_fuel(self, _gid: str = ""):
        if self.get_fuel_count() >= FUEL_REQUIRED_COUNT:
            return
        self.fuel_added_count = min(FUEL_REQUIRED_COUNT, int(getattr(self, "fuel_added_count", 0) or 0) + 1)
        self.update_fuel_ui()
        self.log(f"加油! 当前: {FUEL_REQUIRED_COUNT}/{self.get_fuel_count()}")

    def add_fuel_from_consumed_item(self, iid_norm: str):
        if iid_norm in self.consumed_fuel_items:
            return
        if self.get_fuel_count() >= FUEL_REQUIRED_COUNT:
            return
        self.consumed_fuel_items.add(iid_norm)
        self.update_item_position(iid_norm, "已加油")
        self.add_fuel()

    def _cancel_pending_fuel_hibernations(self):
        pending = getattr(self, "pending_fuel_after_ids", None)
        if not pending:
            return
        for after_id in list(pending.values()):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        pending.clear()

    def _confirm_fuel_hibernation(self, iid_norm: str):
        pending = getattr(self, "pending_fuel_after_ids", None)
        if pending is not None:
            pending.pop(iid_norm, None)
        if not getattr(self, "round_active", False):
            return
        self.add_fuel_from_consumed_item(iid_norm)

    def _schedule_fuel_hibernation(self, iid_norm: str):
        if iid_norm in getattr(self, "consumed_fuel_items", set()):
            return
        pending = getattr(self, "pending_fuel_after_ids", None)
        if pending is None:
            self.pending_fuel_after_ids = {}
            pending = self.pending_fuel_after_ids
        if iid_norm in pending:
            return
        try:
            pending[iid_norm] = self.root.after(
                FUEL_HIBERNATE_CONFIRM_DELAY_MS,
                self._confirm_fuel_hibernation,
                iid_norm,
            )
        except Exception:
            self._confirm_fuel_hibernation(iid_norm)

    def set_battery_pending(self, gid: str, reason: str = ""):
        if gid not in self.gens: return
        if self.gens[gid].get("battery", False): return
        self.gens[gid]["battery_pending"] = True
        self.gens[gid]["pending_since"] = time.time()
        self.last_pending_gid = gid
        self.last_pending_time = time.time()
        self.update_gen_ui(gid)
        self.log(f"{gid} 电池安装中 {reason}".strip())

    def set_battery_state(self, gid: str, state: bool, reason: str = ""):
        if gid not in self.gens: return
        now = time.time()
        old_state = self.gens[gid]["battery"]
        if (old_state == state) and (now - self.last_battery_event.get(gid, 0.0) < BATTERY_DEBOUNCE_SECONDS):
            return
        self.last_battery_event[gid] = now
        self.gens[gid]["battery"] = state
        self.gens[gid]["battery_pending"] = False
        self.gens[gid]["pending_since"] = 0.0
        self.update_gen_ui(gid)
        self.log(f"{gid} 电池状态 => {state} {reason}".strip())

    def fail_pending_battery_if_any(self, reason: str = ""):
        now = time.time()
        if self.last_pending_gid and (now - self.last_pending_time <= PENDING_ASSOCIATE_WINDOW_SECONDS):
            gid = self.last_pending_gid
            if self.gens.get(gid, {}).get("battery_pending", False):
                self.set_battery_state(gid, False, reason=reason)
            return
        for gid in self.gens:
            if self.gens[gid].get("battery_pending", False):
                self.set_battery_state(gid, False, reason=reason)
                return

    def pending_timeout_tick(self):
        if self._is_shutting_down:
            self._pending_tick_after_id = None
            return
        now = time.time()
        for gid in self.gens:
            if self.gens[gid].get("battery_pending", False):
                if now - self.gens[gid].get("pending_since", 0.0) > PENDING_TIMEOUT_SECONDS:
                    self.gens[gid]["battery_pending"] = False
                    self.gens[gid]["pending_since"] = 0.0
                    self.update_gen_ui(gid)
                    self.log(f"{gid} 电池安装中超时，已自动清理。")
        if self._is_shutting_down:
            self._pending_tick_after_id = None
            return
        try:
            self._pending_tick_after_id = self.root.after(1000, self.pending_timeout_tick)
        except Exception:
            self._pending_tick_after_id = None

    def update_stats_ui(self):
        base = self.game_stats["fuel_base"]
        extra = self.game_stats["fuel_extra"]
        if base > 0 or extra > 0:
            total = base + extra
            self.lbl_stats_fuel.configure(text=f"油桶：共有 {total} 桶，其中 {extra} 桶在上锁的房间")
        else:
            self.lbl_stats_fuel.configure(text="油桶：等待检测...")
        out_n = self.game_stats["item_out"]
        in_n = self.game_stats["item_in"]
        if out_n > 0 or in_n > 0:
            total = out_n + in_n
            self.lbl_stats_item.configure(text=f"物品：共有 {total} 个，其中 {in_n} 个在上锁的房间")
        else:
            self.lbl_stats_item.configure(text="物品：等待检测...")
        
        # 显示封锁房间数
        sealed = self.game_stats["sealed_rooms"]
        if sealed is not None:
            self.lbl_stats_sealed.configure(text=f"有 {sealed} 个门被锁上")
        else:
            self.lbl_stats_sealed.configure(text="")
        
        # 显示玩家优惠（只有检测到可少加油时才显示）
        players = self.game_stats["players"]
        free_fuel = self.game_stats["free_fuel"]
        if free_fuel > 0:
            self.lbl_stats_headstart.configure(text=f"局内有 {players} 名玩家，可少加 {free_fuel} 桶油")
        else:
            self.lbl_stats_headstart.configure(text="")

    def classify_source(self, iid_norm: str) -> str:
        num = item_numeric_id(iid_norm)
        if num < 0: return "未知"
        return "玩家" if num >= PLAYER_ITEM_ID_THRESHOLD else "地图"

    def add_item_record(self, iid_norm: str, cn: str, en: str):
        if iid_norm in self.item_records: return
        source = self.classify_source(iid_norm)
        group_key = en
        self.item_records[iid_norm] = {"cn": cn, "en": en, "source": source, "group": group_key, "pos": "", "pos_raw": ""}
        if group_key not in self.groups[source]:
            self.groups[source][group_key] = []
            self.group_order[source].append(group_key)
        self.groups[source][group_key].append(iid_norm)

    def get_sort_key(self, iid):
        rec = self.item_records.get(iid)
        if not rec: return (100, 0)
        
        # 优先级：油桶 > 电池 > 607型主锁 > 玻璃瓶 > 其他
        name = rec["en"]
        cn_name = rec["cn"]
        
        prio = 10  # 默认其他
        if "Fuel" in name or "燃料" in cn_name: prio = 0
        elif "Battery" in name or "电池" in cn_name: prio = 1
        elif "Master" in name and "Lock" in name: prio = 2 # Master Lock 607 / MasterLock
        elif name == "MasterLock": prio = 2
        elif "Glass" in name and "Bottle" in name: prio = 3
        
        # 提取ID数字 SC_Item123 -> 123
        num_id = item_numeric_id(iid)
        
        return (prio, num_id)

    def update_item_position(self, iid_norm: str, pos_name: str):
        clean_pos = re.sub(r'\s*\(\d+\)$', '', pos_name)
        clean_pos = re.sub(r'(?i)_?collider_?', '', clean_pos)
        clean_pos = clean_pos.strip(" _")
        clean_pos = clean_pos.replace("__", "_")
        final_pos = clean_pos

        is_translated = False
        gen_match = re.match(r"(?i)^SC_generator(\d+)$", clean_pos)
        if clean_pos == "已加油":
            is_translated = True
        elif gen_match:
            final_pos = f"已安装到发电机 {gen_match.group(1)}"
            is_translated = True
        elif clean_pos in LOCATION_TRANSLATION:
            final_pos = LOCATION_TRANSLATION[clean_pos]
            is_translated = True
        else:
            for pattern, trans_template, _ in self.wildcard_patterns:
                match = pattern.match(clean_pos)
                if match:
                    groups = match.groups()
                    result = trans_template
                    for g in groups:
                        result = result.replace("X", g, 1)
                    final_pos = result
                    is_translated = True
                    break

        if not is_translated and clean_pos.strip():
            if clean_pos not in self.untranslated_locations:
                self.untranslated_locations.add(clean_pos)
                self.log(f"[未翻译] 发现新位置: {clean_pos}")

        if iid_norm in self.item_records:
            self.item_records[iid_norm]["pos"] = final_pos
            self.item_records[iid_norm]["pos_raw"] = clean_pos  # 保存原始位置名
            for child in self.tree.get_children():
                vals = self.tree.item(child, "values")
                if vals and vals[0] == iid_norm:
                    new_vals = (vals[0], vals[1], final_pos)
                    self.tree.item(child, values=new_vals)
                    break

    def rebuild_item_tree(self):
        for it in self.tree.get_children():
            self.tree.delete(it)
        def insert_section(title: str):
            self.tree.insert("", END, values=("", title, ""), tags=("section",))
        def insert_blank():
            self.tree.insert("", END, values=("", "", ""), tags=("blank",))
            
        insert_section("【地图物品】")
        
        # 获取并排序地图物品
        map_items = []
        if "地图" in self.groups:
            for g in self.groups["地图"].values():
                map_items.extend(g)
        
        map_items.sort(key=self.get_sort_key)
        
        for iid in map_items:
            rec = self.item_records.get(iid)
            if not rec: continue
            
            tags = []
            # 应用颜色（仅限地图物品）
            color_hex = CUSTOM_ITEM_COLORS.get(rec["en"])
            if not color_hex:
                color_hex = CUSTOM_ITEM_COLORS.get(rec["cn"])
            
            if color_hex:
                tag_name = f"color_{color_hex.replace('#', '')}"
                tags.append(tag_name)
                
            self.tree.insert("", END, iid=iid, values=(iid, rec["cn"], rec["pos"]), tags=tuple(tags))
            
        insert_blank()
        insert_section("【玩家物品】")
        
        # 获取并排序玩家物品（按ID排序）
        player_items = []
        if "玩家" in self.groups:
            for g in self.groups["玩家"].values():
                player_items.extend(g)
        
        player_items.sort(key=lambda x: item_numeric_id(x))
        
        for iid in player_items:
            rec = self.item_records.get(iid)
            if not rec: continue
            # 玩家物品保持默认颜色，不添加特别的 tags
            self.tree.insert("", END, iid=iid, values=(iid, rec["cn"], rec["pos"]), tags=())

    def _flush_rebuild_item_tree(self):
        self._tree_rebuild_after_id = None
        if self._is_shutting_down:
            return
        self.rebuild_item_tree()

    def _schedule_rebuild_item_tree(self):
        if self._is_shutting_down or self._tree_rebuild_after_id:
            return
        try:
            self._tree_rebuild_after_id = self.root.after(TREE_REBUILD_DELAY_MS, self._flush_rebuild_item_tree)
        except Exception:
            self._tree_rebuild_after_id = None
            self.rebuild_item_tree()

    def _handle_ecliptica_event(self, event):
        if event.kind == "authenticated":
            self.ecliptica_state.apply(event)
            return False
        if event.kind == "room_entered":
            room_name = event.groups[0]
            if is_ecliptica_room(room_name):
                self.ecliptica_state.apply(event)
                self._set_active_game_mode("ecliptica", reason=room_name)
                self._update_ecliptica_ui()
            else:
                self.ecliptica_state.reset(preserve_world=False)
                self._set_active_game_mode("slashco", reason=room_name)
                self._update_ecliptica_ui()
            return True

        strong_events = {"session", "session_blank", "stage", "boss", "intermission", "lobby"}
        if self.detected_game_mode != "ecliptica" and event.kind not in strong_events:
            return False
        self._set_active_game_mode("ecliptica", reason="检测到 Ecliptica 日志")
        self.ecliptica_state.apply(event)
        self._update_ecliptica_ui()
        return True

    def _reset_for_new_log(self):
        self.reset_game(force=True, reason="新日志文件加载")
        self.ecliptica_state.reset(preserve_world=False)
        self._update_ecliptica_ui()
        self._set_active_game_mode("slashco", log_change=False)

    def process_line(self, line: str):
        ecliptica_event = parse_ecliptica_line(line)
        if ecliptica_event and self._handle_ecliptica_event(ecliptica_event):
            return

        event = parse_log_line(line)
        if not event:
            return
        self._set_active_game_mode("slashco", reason="检测到 SlashCo 日志")

        if event.kind == "item":
            iid_raw, raw_name = event.groups
            iid = normalize_item_id(iid_raw)
            cn_name = ITEM_TRANSLATION.get(raw_name.strip(), raw_name.strip())
            if iid not in self.item_records:
                self.add_item_record(iid, cn_name, raw_name.strip())
                self._schedule_rebuild_item_tree()
                self.log(f"发现: {cn_name} ({iid})")
            return

        if event.kind == "item_collision":
            iid = normalize_item_id(event.groups[0])
            pos_name = event.groups[1].strip()
            self.update_item_position(iid, pos_name)
            return

        if event.kind == "fuel_base":
            players = int(event.groups[0])
            self.game_stats["fuel_base"] = int(event.groups[1])
            if not self.free_fuel_explicit:
                self.set_player_fuel_headstart(players, free_fuel=None, explicit=False)
            self.update_stats_ui()
            return

        if event.kind == "fuel_extra":
            self.game_stats["fuel_extra"] = int(event.groups[0])
            self.update_stats_ui()
            return

        if event.kind == "item_outside":
            self.game_stats["item_out"] = int(event.groups[0])
            self.update_stats_ui()
            return

        if event.kind == "item_inside":
            self.game_stats["item_in"] = int(event.groups[0])
            self.update_stats_ui()
            return

        if event.kind == "map_landing":
            map_name = event.groups[0].strip()
            self.begin_round(f"新地图加载: {map_name}")
            # OCR 已改为通过 game_end 后的轮询机制触发，此处不再单独启动
            return

        if event.kind == "map_slashco":
            map_name = event.groups[0].strip()
            if "Lobby" in map_name:
                self.reset_game(force=True, reason="返回大厅")
            else:
                self.log(f"检测到地图数据加载: {map_name}")
            return

        if event.kind == "game_setup":
            self.begin_round("新回合开始")
            return

        if event.kind == "fuel":
            self.add_fuel(event.groups[0])
            return

        if event.kind == "fuel_inserted":
            self.add_fuel_from_consumed_item(normalize_item_id(event.groups[0]))
            return

        if event.kind == "pickup_item":
            self.held_items.add(normalize_item_id(event.groups[0]))
            return

        if event.kind in ("drop_item", "holster_item"):
            self.held_items.discard(normalize_item_id(event.groups[0]))
            return

        if event.kind == "item_hibernated":
            iid = normalize_item_id(event.groups[0])
            item_type = event.groups[1].strip().lower()
            if self.round_active and item_type == "fuel":
                self._schedule_fuel_hibernation(iid)
            self.held_items.discard(iid)
            return

        if event.kind == "battery_progress":
            self.set_battery_state(event.groups[0], event.groups[1].lower() == "true", reason="(ProgressCheck)")
            return

        if event.kind == "battery_skillcheck_failed":
            self.fail_pending_battery_if_any(reason="(SkillcheckFailed)")
            return

        if event.kind == "battery_fixing":
            self.set_battery_state(event.groups[0], True, reason="(FixingNow)")
            return

        if event.kind == "game_end":
            now = time.time()
            if now - self.last_game_end_time >= self.GAME_END_DEBOUNCE_SECONDS:
                self.last_game_end_time = now
                self.reset_game(force=True, reason=f"检测到结束信号: {event.groups[0]}")
            return

        if event.kind == "player_headstart":
            self.set_player_fuel_headstart(int(event.groups[0]), free_fuel=None, explicit=True)
            self.update_stats_ui()
            self.log(f"局内 {self.game_stats['players']} 名玩家，可少加 {self.game_stats['free_fuel']} 桶油")
            return

        if event.kind == "rooms_sealed":
            self.game_stats["sealed_rooms"] = max(0, int(event.groups[0]))
            self.update_stats_ui()
            self.log(f"检测到 {self.game_stats['sealed_rooms']} 个门被锁上")
            return

        if event.kind == "slashco_loading":
            self.log("检测到数据加载 (SLASHCO now loading data)")
            # OCR现在由音频匹配触发，不再使用日志触发
            return

    def get_latest_log_file(self):
        try:
            files = glob.glob(os.path.join(self.log_dir, "output_log_*.txt"))
            if not files: return None
            return max(files, key=os.path.getctime)
        except Exception:
            return None

    def _read_vrc_identity_event(self, path: str | None):
        """从日志开头独立读取 VRC 认证身份，不依赖 Ecliptica 对局恢复。"""
        if not path:
            return None
        try:
            with open(path, "rb") as log_file:
                data = log_file.read(LOG_IDENTITY_SCAN_BYTES)
        except (OSError, TypeError):
            return None

        latest_identity = None
        for line in data.decode("utf-8", errors="ignore").splitlines():
            event = parse_ecliptica_line(line)
            if event is not None and event.kind == "authenticated":
                latest_identity = event
        return latest_identity

    def _is_round_start_line(self, line: str) -> bool:
        return is_round_start_line(line)

    def _is_round_end_line(self, line: str) -> bool:
        return is_round_end_line(line)

    def _line_might_affect_state(self, line: str) -> bool:
        return line_might_affect_state(line) or line_might_affect_ecliptica_state(line)

    def _read_log_tail_text(self, path: str):
        try:
            file_size = os.path.getsize(path)
            if file_size <= 0:
                return "", 0, file_size

            read_size = min(file_size, LOG_TAIL_SCAN_BYTES)
            start_offset = file_size - read_size
            chunks = []
            remaining = read_size
            with open(path, "rb") as bf:
                bf.seek(start_offset)
                while remaining > 0:
                    chunk = bf.read(min(LOG_TAIL_READ_BLOCK_BYTES, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)

            data = b"".join(chunks)
            if start_offset > 0:
                first_newline = data.find(b"\n")
                if first_newline >= 0:
                    data = data[first_newline + 1:]
            return data.decode("utf-8", errors="ignore"), start_offset, file_size
        except Exception as e:
            self.log(f"读取日志尾部失败: {e}")
            return "", 0, 0

    def _get_active_round_recovery_lines(self, path: str):
        text, start_offset, file_size = self._read_log_tail_text(path)
        if not text:
            return []

        last_start_pos = None
        last_end_pos = None
        last_room_pos = None
        last_room_name = ""
        last_authenticated_line = ""
        first_session_positions = {}
        last_session_id = ""
        pos = 0
        for raw_line in text.splitlines(True):
            line = raw_line.strip()
            if line:
                ecliptica_event = parse_ecliptica_line(line)
                if ecliptica_event and ecliptica_event.kind == "authenticated":
                    last_authenticated_line = line
                elif ecliptica_event and ecliptica_event.kind == "room_entered":
                    last_room_pos = pos
                    last_room_name = ecliptica_event.groups[0]
                elif ecliptica_event and ecliptica_event.kind == "session":
                    last_session_id = ecliptica_event.groups[0]
                    first_session_positions.setdefault(last_session_id, pos)
                if self._is_round_start_line(line):
                    last_start_pos = pos
                if self._is_round_end_line(line):
                    last_end_pos = pos
            pos += len(raw_line)

        if last_room_pos is not None and is_ecliptica_room(last_room_name):
            recovery_pos = first_session_positions.get(last_session_id, last_room_pos)
            active_text = text[recovery_pos:]
            lines = [line for line in active_text.splitlines() if self._line_might_affect_state(line)]
            if last_authenticated_line and last_authenticated_line not in lines:
                lines.insert(0, last_authenticated_line)
            self.log(f"已从日志尾部恢复 Ecliptica 当前房间状态，共 {len(lines)} 行")
            return lines

        if last_start_pos is None:
            self.log("尾部扫描未找到当前回合开始点，已从日志末尾开始监听")
            return []
        if last_end_pos is not None and last_end_pos > last_start_pos:
            self.log("尾部扫描显示最近回合已结束，已从日志末尾开始监听")
            return []

        active_text = text[last_start_pos:]
        lines = [line for line in active_text.splitlines() if self._line_might_affect_state(line)]
        scanned_mb = min(file_size, LOG_TAIL_SCAN_BYTES) / (1024 * 1024)
        if start_offset > 0:
            self.log(f"已从日志尾部 {scanned_mb:.1f}MB 内定位当前回合，恢复 {len(lines)} 行")
        else:
            self.log(f"已从日志尾部定位当前回合，恢复 {len(lines)} 行")
        return lines

    def _enqueue_log_lines(self, lines):
        if self._is_shutting_down or not lines:
            return
        if not self._recovering_log_state:
            self._begin_log_recovery()
        for line in lines:
            self._pending_log_lines.put(line)
        if self._log_lines_after_id is None:
            self._log_lines_after_id = self._ui_after(self._process_pending_log_lines)

    def _process_live_log_lines(self, lines):
        if self._is_shutting_down:
            return
        for line in lines:
            try:
                self.process_line(line)
            except Exception as e:
                self.log(f"日志解析异常，已跳过该行: {e}")

    def _enqueue_live_log_lines(self, lines):
        if self._is_shutting_down or not lines:
            return
        self._ui_after(self._process_live_log_lines, list(lines))

    def _clear_pending_log_lines(self):
        try:
            while True:
                self._pending_log_lines.get_nowait()
        except queue.Empty:
            pass

    def _begin_log_recovery(self):
        self._recovering_log_state = True
        self._log_recovery_started_at = time.monotonic()

    def _finish_log_recovery(self):
        self._recovering_log_state = False
        self._log_recovery_started_at = None

    def _process_pending_log_lines(self):
        if self._is_shutting_down:
            self._log_lines_after_id = None
            return
        self._log_lines_after_id = None

        processed = 0
        start = time.perf_counter()
        while processed < LOG_PROCESS_BATCH_SIZE:
            try:
                line = self._pending_log_lines.get_nowait()
            except queue.Empty:
                break
            try:
                self.process_line(line)
            except Exception as e:
                self.log(f"日志解析异常，已跳过该行: {e}")
            processed += 1
            if time.perf_counter() - start > 0.01:
                break

        if self._is_shutting_down:
            self._log_lines_after_id = None
            return
        if not self._pending_log_lines.empty():
            try:
                self._log_lines_after_id = self.root.after(
                    LOG_PROCESS_BATCH_DELAY_MS,
                    self._process_pending_log_lines,
                )
            except Exception:
                self._log_lines_after_id = None
        else:
            self._finish_log_recovery()

    def monitor_loop(self):
        current_file_path = None
        current_offset = 0
        partial_line = ""
        last_file_check_time = 0.0
        self.log("正在扫描 VRChat 日志文件...")
        while self.is_monitoring:
            try:
                latest = None
                now = time.time()
                if current_file_path is None or now - last_file_check_time >= LOG_FILE_CHECK_INTERVAL_SECONDS:
                    latest = self.get_latest_log_file()
                    last_file_check_time = now
                if latest and latest != current_file_path:
                    if os.path.exists(latest) and os.path.getsize(latest) > 0:
                        current_file_path = latest
                        current_offset = 0
                        partial_line = ""
                        self.log(f"锁定日志: {os.path.basename(current_file_path)}")
                        try:
                            self._begin_log_recovery()
                            self._clear_pending_log_lines()
                            identity_event = self._read_vrc_identity_event(current_file_path)
                            recovery_lines = self._get_active_round_recovery_lines(current_file_path)
                            self._ui_after(self._reset_for_new_log)
                            if identity_event is not None:
                                recovery_lines = [
                                    f"User Authenticated: {identity_event.groups[0]} "
                                    f"({identity_event.groups[1]})"
                                ] + [
                                    line
                                    for line in recovery_lines
                                    if not (
                                        (event := parse_ecliptica_line(line)) is not None
                                        and event.kind == "authenticated"
                                    )
                                ]
                            current_offset = os.path.getsize(current_file_path)
                            if recovery_lines:
                                self._enqueue_log_lines(recovery_lines)
                            else:
                                self._ui_after(self._finish_log_recovery)
                        except Exception:
                            self._finish_log_recovery()
                            current_file_path = None
                            current_offset = 0
                            partial_line = ""
                            time.sleep(1)
                            continue
                if current_file_path:
                    if not os.path.exists(current_file_path):
                        current_file_path = None
                        current_offset = 0
                        partial_line = ""
                        time.sleep(1)
                        continue

                    file_size = os.path.getsize(current_file_path)
                    if file_size < current_offset:
                        current_offset = file_size
                        partial_line = ""

                    pending_lines = []
                    complete_lines = []
                    if file_size > current_offset:
                        with open(current_file_path, "rb") as bf:
                            bf.seek(current_offset)
                            data = bf.read(file_size - current_offset)
                        current_offset = file_size
                        text = partial_line + data.decode("utf-8", errors="ignore")
                        raw_lines = text.splitlines(True)
                        if raw_lines and not raw_lines[-1].endswith(("\n", "\r")):
                            partial_line = raw_lines.pop()
                        else:
                            partial_line = ""
                        complete_lines = raw_lines

                    for line in complete_lines:
                        line = line.rstrip("\r\n")
                        if self._line_might_affect_state(line):
                            pending_lines.append(line)
                            if len(pending_lines) >= LOG_PROCESS_BATCH_SIZE:
                                self._enqueue_live_log_lines(pending_lines)
                                pending_lines = []
                    self._enqueue_live_log_lines(pending_lines)
                    time.sleep(0.1)
                else:
                    time.sleep(1)
            except Exception as e:
                self.log(f"监控异常: {e}")
                time.sleep(2)


    # === 图片系统实现 ===
    GITHUB_BASE_URL = "https://github.com/h1czvk0/slsc/raw/refs/heads/main"
    GH_PROXIES = [
        "https://gh-proxy.org/",
        "https://hk.gh-proxy.org/",
        "https://cdn.gh-proxy.org/",
        "https://edgeone.gh-proxy.org/",
        "" # 最后尝试直连
    ]

    def setup_image_system(self):
        try:
            # 2. 加载 images.json (优先加载 base_dir 下的，因为刚可能被释放了)
            target_json = self.img_json_path
            if os.path.exists(target_json):
                with open(target_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.img_mappings = data.get("mappings", {})
                    self.img_notes = data.get("notes", {})
            else:
                self.log(f"未找到图片映射文件: {target_json}")
        except Exception as e:
            self.log(f"本地图片映射加载失败: {e}")

    def _download_file_with_proxy(self, relative_path, best_proxy=None):
        """尝试使用代理下载文件，返回 (content, worked_proxy)"""
        url_suffix = f"{self.GITHUB_BASE_URL}/{relative_path}"
        
        # 如果有确定的最佳代理，优先尝试
        proxies_to_try = self.GH_PROXIES.copy()
        if best_proxy in proxies_to_try:
            proxies_to_try.remove(best_proxy)
            proxies_to_try.insert(0, best_proxy)
            
        for proxy in proxies_to_try:
            try:
                full_url = f"{proxy}{url_suffix}"
                # self.log(f"尝试下载: {full_url}") # 调试用
                resp = requests.get(full_url, timeout=5) # 5秒超时测速
                if resp.status_code == 200:
                    return resp.content, proxy
            except:
                continue
        return None, None

    def start_image_sync(self):
        if not HAS_REQUESTS:
            self.log("未安装 requests 库，跳过图片同步")
            return

        try:
            self.log("正在检查图片更新...")
            
            # 1. 下载 images.json 并确定最佳代理
            content, best_proxy = self._download_file_with_proxy(IMG_JSON)
            if not content:
                self.log("无法连接到图片服务器 (images.json 下载失败)")
                return
            
            # self.log(f"连接成功，使用代理: {best_proxy if best_proxy else '直连'}")
            
            remote_data = json.loads(content.decode('utf-8'))
            remote_mappings = remote_data.get("mappings", {})
            remote_notes = remote_data.get("notes", {})
            
            # 2. 更新本地映射
            self.img_mappings.update(remote_mappings)
            if isinstance(remote_notes, dict):
                self.img_notes.update(remote_notes)
            with open(self.img_json_path, 'w', encoding='utf-8') as f:
                json.dump({"mappings": self.img_mappings, "notes": self.img_notes}, f, ensure_ascii=False, indent=4)
            
            # 3. 计算差异 (支持 list 类型的值)
            needed_files = set()
            for val in self.img_mappings.values():
                if isinstance(val, list):
                    for v in val:
                        if v and isinstance(v, str): needed_files.add(v)
                elif isinstance(val, str) and val:
                    needed_files.add(val)

            local_files = set()
            if os.path.exists(self.img_dir):
                local_files = set(os.listdir(self.img_dir))
                
            # 清理废弃图片
            for f in local_files:
                if f not in needed_files:
                    try: os.remove(os.path.join(self.img_dir, f))
                    except: pass
            
            files_to_download = [f for f in needed_files if f not in local_files]
            
            if not files_to_download:
                self.log("图片已是最新")
                return

            self.log(f"发现 {len(files_to_download)} 张新图片，开始同步...")
            
            # 4. 并行下载
            self.download_completed_count = 0
            self.download_total_count = len(files_to_download)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = []
                for fname in files_to_download:
                    local_path = os.path.join(self.img_dir, fname)
                    futures.append(executor.submit(self._download_image_worker, fname, local_path, best_proxy))
                
                # 监控进度
                completed = 0
                for f in concurrent.futures.as_completed(futures):
                    completed += 1
                    if completed % 5 == 0 or completed == self.download_total_count:
                        self._ui_after(self.log, f"图片同步进度: {completed}/{self.download_total_count}")
            
            self.log("图片同步完成")
                
        except Exception as e:
            self._ui_after(self.log, f"同步流程错误: {e}")

    def _download_image_worker(self, fname, local_path, best_proxy):
        try:
            content, _ = self._download_file_with_proxy(f"{IMG_DIR}/{fname}", best_proxy)
            if content:
                with open(local_path, 'wb') as f:
                    f.write(content)
        except:
            pass

    def _get_image_path_for_item(self, item_id):
        """公共方法：根据 Item ID 和当前地图获取第一张图片路径"""
        paths = self._get_all_image_paths_for_item(item_id)
        return paths[0] if paths else None

    def _get_all_image_paths_for_item(self, item_id):
        """获取某个 Item 的所有关联图片路径列表"""
        if item_id not in self.item_records:
            return []

        rec = self.item_records[item_id]
        pos_raw = rec.get("pos_raw", "")
        
        img_names = self.img_mappings.get(pos_raw)

        # 如果没有精确位置图片，尝试通用 Wildcard 映射。
        if not img_names:
            for pattern, _, w_key in self.wildcard_patterns:
                if pattern.match(pos_raw):
                    img_names = self.img_mappings.get(w_key)
                    if img_names:
                        break
        
        if not img_names:
            return []
        
        # 兼容: 字符串转列表
        if isinstance(img_names, str):
            img_names = [img_names]
        
        # 构建完整路径并过滤不存在的文件
        result = []
        for name in img_names:
            path = os.path.join(self.img_dir, name)
            if os.path.exists(path):
                result.append(path)
        
        return result

    def on_tree_motion(self, event):
        try:
            item_id = self.tree.identify_row(event.y)
            col = self.tree.identify_column(event.x)
            
            # 判断是否应该显示：
            # 1. 悬停在第3列 ("位置"列)
            # 2. 或者悬停的行就是当前选中的行 (无论哪一列)
            selection = self.tree.selection()
            is_selected_row = (selection and selection[0] == item_id)
            
            if not item_id or (col != "#3" and not is_selected_row):
                self.hide_img_tooltip()
                self.last_tooltip_row = None
                return

            if item_id == self.last_tooltip_row:
                return 
            self.last_tooltip_row = item_id

            # 使用提取的公共逻辑查找图片
            img_path = self._get_image_path_for_item(item_id)
            
            if img_path:
                self.show_img_tooltip(img_path, event.x_root, event.y_root)
                return
            
            self.hide_img_tooltip()
        except:
            pass


    def on_tree_leave(self, event):
        self.hide_img_tooltip()
        self.last_tooltip_row = None

    def on_tree_select(self, event):
        """处理列表选中逻辑：有图片时显示全屏画廊覆盖层"""
        selection = self.tree.selection()
        if not selection: 
            return

        item_id = selection[0]
        
        # 更新记录，让 hover 逻辑知道这是选中行
        if hasattr(self, 'last_tooltip_row') and self.last_tooltip_row != item_id:
             self.last_tooltip_row = None
        
        # 获取所有图片路径
        img_paths = self._get_all_image_paths_for_item(item_id)
        
        if not img_paths:
            self.hide_img_tooltip()
            return
        
        # 隐藏 tooltip
        self.hide_img_tooltip()
        
        # 显示全屏画廊覆盖层
        rec = self.item_records.get(item_id, {})
        cn_name = rec.get("cn", "")
        pos_raw = rec.get("pos_raw", "")
        self.show_gallery_overlay(img_paths, pos_raw, cn_name)

    def show_gallery_overlay(self, img_paths, pos_raw, cn_name):
        """显示全屏画廊覆盖层"""
        # 先销毁旧的覆盖层
        self.hide_gallery_overlay()
        
        # 创建覆盖层 Frame，覆盖整个 paned 区域
        self.gallery_overlay = Frame(self.root, bg="#f5f5f5")
        self.gallery_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        
        # 顶部栏：标题和返回按钮
        top_bar = Frame(self.gallery_overlay, bg="#ffffff", height=50)
        top_bar.pack(fill=X, side=TOP)
        top_bar.pack_propagate(False)
        
        title = cn_name if cn_name else pos_raw
        Label(top_bar, text=f"📍 {title} ({len(img_paths)}张图片)", 
              font=("微软雅黑", 14, "bold"), bg="#ffffff").pack(side=LEFT, padx=20, pady=10)
        
        ttk.Button(top_bar, text="← 返回", command=self.hide_gallery_overlay).pack(side=RIGHT, padx=20, pady=10)
        
        # 图片展示区域 (可滚动)
        canvas_frame = Frame(self.gallery_overlay, bg="#f5f5f5")
        canvas_frame.pack(fill=BOTH, expand=True, padx=20, pady=20)
        
        canvas = Canvas(canvas_frame, bg="#f5f5f5", highlightthickness=0)
        v_scroll = ttk.Scrollbar(canvas_frame, orient=VERTICAL, command=canvas.yview)
        h_scroll = ttk.Scrollbar(canvas_frame, orient=HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        
        v_scroll.pack(side=RIGHT, fill=Y)
        h_scroll.pack(side=BOTTOM, fill=X)
        canvas.pack(fill=BOTH, expand=True)
        
        inner = Frame(canvas, bg="#f5f5f5")
        canvas.create_window((0, 0), window=inner, anchor=NW)
        
        # 计算网格布局
        num = len(img_paths)
        if num == 1:
            cols = 1
        elif num <= 4:
            cols = 2
        else:
            cols = 3
        
        # 加载并显示图片
        self.gallery_photo_refs = []
        max_size = 350
        
        for i, path in enumerate(img_paths):
            row = i // cols
            col = i % cols
            try:
                img = Image.open(path)
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.gallery_photo_refs.append(photo)
                
                cell = Frame(inner, bd=2, relief=SOLID, bg="white")
                cell.grid(row=row, column=col, padx=10, pady=10)
                
                lbl = Label(cell, image=photo, bg="white")
                lbl.pack(padx=5, pady=5)
                
                # 显示文件名
                fname = os.path.basename(path)
                Label(cell, text=fname[:25], font=("Consolas", 8), fg="#666", bg="white").pack()
                
                # 显示备注（如果有）
                note = getattr(self, 'img_notes', {}).get(fname, "")
                if note:
                    Label(cell, text=f"📝 {note}", font=("微软雅黑", 9), fg="#333", bg="#fffacd", 
                          wraplength=max_size-20).pack(fill=X, padx=3, pady=3)
            except Exception:
                pass
        
        # 更新滚动区域
        inner.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))
        
        # 绑定鼠标滚轮
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))
        
        # ESC 返回
        self.gallery_overlay.bind("<Escape>", lambda e: self.hide_gallery_overlay())
        self.gallery_overlay.focus_set()

    def hide_gallery_overlay(self):
        """隐藏全屏画廊覆盖层"""
        if self.gallery_overlay:
            self.gallery_overlay.destroy()
            self.gallery_overlay = None
        self.gallery_photo_refs.clear()


    def show_img_tooltip(self, img_path, x, y):
        # 销毁旧窗口
        self.hide_img_tooltip()
        
        self.tooltip_window = Toplevel(self.root)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x+20}+{y+20}")
        self.tooltip_window.attributes("-topmost", True)
        
        try:
            pil_img = Image.open(img_path)
            # 限制大小，例如最大高度300
            max_h = 300
            if pil_img.height > max_h:
                ratio = max_h / pil_img.height
                new_w = int(pil_img.width * ratio)
                pil_img = pil_img.resize((new_w, max_h), Image.Resampling.LANCZOS)
            
            self.tooltip_img = ImageTk.PhotoImage(pil_img)
            lbl = Label(self.tooltip_window, image=self.tooltip_img, bg="#333333", bd=1, relief="solid")
            lbl.pack()
        except Exception:
            self.hide_img_tooltip()

    def hide_img_tooltip(self):
        if self.tooltip_window:
            try: self.tooltip_window.destroy()
            except: pass
        self.tooltip_window = None



    def on_close(self):
        """窗口关闭处理 - 优雅退出并释放资源"""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True

        # 先隐藏窗口，给用户即时关闭反馈
        try:
            self.root.withdraw()
            self.root.update_idletasks()
        except Exception:
            pass

        # 停止监控循环
        self.is_monitoring = False

        try:
            if getattr(self, "ecliptica_auto_jump", None):
                self.ecliptica_auto_jump.stop()
        except Exception:
            pass
        try:
            if getattr(self, "ecliptica_sync", None):
                self.ecliptica_sync.stop(timeout=1.5)
        except Exception:
            pass

        if HAS_SPONSOR_PROXY:
            try:
                self._stop_sponsor_server_with_timeout(timeout_seconds=1.8)
            except Exception:
                pass
            try:
                self._force_cleanup_sponsor_proxy()
            except Exception:
                pass

        # 取消周期性 after 任务
        for attr in (
            "_ui_callback_after_id",
            "_log_queue_after_id",
            "_log_lines_after_id",
            "_pending_tick_after_id",
            "_tree_rebuild_after_id",
            "_round_timer_after_id",
            "_ecliptica_hud_after_id",
            "_ecliptica_sync_after_id",
            "_ecliptica_osc_test_after_id",
            "_ecliptica_auto_jump_after_id",
        ):
            after_id = getattr(self, attr, None)
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr, None)

        # 清理悬浮层/tooltip
        try:
            self.hide_gallery_overlay()
        except Exception:
            pass
        try:
            self.hide_img_tooltip()
        except Exception:
            pass
        try:
            if self.ecliptica_hud:
                self.ecliptica_hud.destroy()
        except Exception:
            pass
        # 正常退出 Tk 循环
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

if __name__ == "__main__":
    # 支持在打包后运行脚本 (用于 admin_helper.py 等子进程调用)
    # 当作为 frozen exe 运行时，sys.executable 是 exe 本身。
    # 如果 argv[1] 是 .py 文件，则尝试执行它。
    if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
        script_path = sys.argv[1]
        # 调整 argv，让脚本看到的 argv[0] 是脚本路径
        sys.argv = sys.argv[1:]
        try:
            import runpy
            # 使用 run_path 执行脚本
            runpy.run_path(script_path, run_name="__main__")
        except Exception as e:
            print(f"Error running script {script_path}: {e}")
        sys.exit(0)

    configure_windows_process()
    root = Tk()
    root.withdraw()
    configure_tk_display(root)
    app = SlashCoMonitorCN(root)
    root.deiconify()
    # 接管窗口关闭事件
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    try:
        app._force_cleanup_sponsor_proxy()
    except Exception:
        pass
