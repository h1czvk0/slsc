"""Ecliptica 桌面悬浮 HUD：布局归一化、逐像素 Alpha 渲染与窗口管理。"""

import ctypes
import os
import re
from ctypes import wintypes
from tkinter import BOTH, CENTER, NE, NW, Canvas, Label, Toplevel

from PIL import Image, ImageChops, ImageDraw, ImageFont

from ecliptica_sync import is_boss_battle_active


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


def normalize_hud_opacity(value):
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        return 0.9
    return min(1.0, max(0.0, opacity))


def normalize_hud_color(value, default="#ffffff"):
    """Return a Tk/Pillow-compatible RGB hex color, falling back on invalid input."""
    candidate = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", candidate):
        return candidate.lower()
    return default


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

    def __init__(self, root, layout=None, opacity=0.9, font_color="#ffffff"):
        self.root = root
        self.layout = normalize_hud_layout(layout)
        self.opacity = normalize_hud_opacity(opacity)
        self.font_color = normalize_hud_color(font_color)
        self.FG = self.font_color
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
        # Rendering the layered HUD images is relatively expensive.  Keep a
        # small signature for each surface so timer/sync ticks that carry the
        # same data do not recreate identical PIL images.
        self._damage_render_signature = None
        self._lock_render_signature = None
        self._party_render_signature = None
        self._window_placement_signature = None
        self._visibility_signature = None

    def _render_geometry(self, key, window):
        geometry = self.layout.get(key, {})
        width = max(1, int(geometry.get("width", window.winfo_width())))
        height = max(1, int(geometry.get("height", window.winfo_height())))
        return width, height

    def _placement_signature(self):
        return (
            tuple(
                (key, tuple(sorted(self.layout.get(key, {}).items())))
                for key in ("damage", "boss_lock", "party_damage")
            ),
            len(getattr(self, "party_rows", ())),
            bool(self.editing),
        )

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
                text="伤害统计", fill=self.font_color,
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
                self._scaled(14, scale), total_y, text="总伤害", fill=self.font_color,
                font=("Microsoft YaHei UI", self._scaled(10, scale), "bold"), anchor=NW,
            )
            canvas.create_text(
                width - self._scaled(14, scale), total_y,
                text=format_ecliptica_number(
                    self.party_total_damage if self.party_rows else 22100
                ),
                fill=self.font_color,
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
        width, height = self._render_geometry("damage", self.damage_window)
        signature = (
            width, height, self.damage_title_text, tuple(self.damage_rows),
            self.font_color, bool(self.editing),
        )
        if signature == getattr(self, "_damage_render_signature", None):
            return
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
        self._damage_render_signature = signature

    def _render_lock_text(self, _event=None):
        if not self.lock_canvas or not self.lock_canvas.winfo_exists():
            return
        width, height = self._render_geometry("boss_lock", self.lock_window)
        signature = (
            width, height, self.lock_text, self.font_color, bool(self.editing),
        )
        if signature == getattr(self, "_lock_render_signature", None):
            return
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
        self._lock_render_signature = signature

    def _render_party_text(self, _event=None):
        canvas = getattr(self, "party_canvas", None)
        window = getattr(self, "party_window", None)
        if not canvas or not canvas.winfo_exists() or not window:
            return
        width, height = self._render_geometry("party_damage", window)
        signature = (
            width, height, tuple(self.party_rows), self.party_total_damage,
            self.font_color, bool(self.editing),
        )
        if signature == getattr(self, "_party_render_signature", None):
            return
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
            self.font_color,
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
        self._draw_text(draw, left, total_y, "总伤害", self.font_color, total_font)
        self._draw_text(
            draw,
            right,
            total_y,
            format_ecliptica_number(self.party_total_damage),
            self.font_color,
            total_font,
            "ra",
        )
        self._draw_edit_grip(draw, width, height)
        self._render_layered_image(window, image)
        self._party_render_signature = signature

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
        self._damage_render_signature = None
        self._window_placement_signature = None
        self._visibility_signature = None
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
        self._lock_render_signature = None
        self._window_placement_signature = None
        self._visibility_signature = None
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
        self._party_render_signature = None
        self._window_placement_signature = None
        self._visibility_signature = None
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
        placement_signature = self._placement_signature()
        if placement_signature == getattr(self, "_window_placement_signature", None):
            return
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
        self._window_placement_signature = self._placement_signature()

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

    def set_font_color(self, color):
        self.font_color = normalize_hud_color(color)
        self.FG = self.font_color
        for grip in self.resize_grips.values():
            try:
                grip.configure(fg=self.font_color)
            except Exception:
                pass
        self._render_damage_text()
        self._render_lock_text()
        self._render_party_text()
        if getattr(self, "editing", False):
            self._render_edit_preview("damage")
            self._render_edit_preview("boss_lock")
            self._render_edit_preview("party_damage")

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
        visibility_signature = (
            show_damage, show_boss_lock, show_party_damage, bool(self.editing),
        )
        if visibility_signature == getattr(self, "_visibility_signature", None):
            return False
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
        self._visibility_signature = visibility_signature
        return True

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
        changed = (
            party_rows != getattr(self, "party_rows", None)
            or party_total != getattr(self, "party_total_damage", None)
        )
        self.party_rows = party_rows
        self.party_total_damage = party_total
        return changed

    def update_party(self, party_players):
        self._ensure_windows()
        party_changed = self._set_party_players(party_players)
        if (
            not party_changed
            and getattr(self, "_party_render_signature", None) is not None
            and getattr(self, "_visibility_signature", None) not in (None, "hidden")
        ):
            return
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
        visibility_changed = self._apply_display_visibility()
        if visibility_changed and not self.editing and self.party_window.state() == "normal":
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
        self._place_windows()
        previous_damage_signature = getattr(self, "_damage_render_signature", None)
        self._render_damage_text()
        if getattr(self, "_damage_render_signature", None) != previous_damage_signature:
            self._fit_background_to_content("damage", self.damage_window)
        self._render_lock_text()
        self._render_party_text()
        visibility_changed = self._apply_display_visibility()
        if self.editing:
            self._apply_edit_visuals()
        elif visibility_changed:
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
        if getattr(self, "_visibility_signature", None) == "hidden":
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
        self._visibility_signature = "hidden"

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
