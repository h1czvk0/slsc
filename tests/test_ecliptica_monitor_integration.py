import pathlib
import sys
import unittest

from PIL import Image, ImageDraw


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_log_parser import EclipticaState, parse_ecliptica_line  # noqa: E402
from slashco import (  # noqa: E402
    EclipticaDesktopHud,
    PANEL_MODE_LABELS,
    SlashCoMonitorCN,
    _hud_text_runs,
    _hud_text_width,
    _fit_hud_font_to_width,
    _load_hud_font,
    _premultiplied_bgra,
    format_ecliptica_clock,
    format_ecliptica_duration,
    main_window_geometry,
    normalize_hud_display_mode,
    normalize_hud_damage_fields,
    normalize_hud_layout,
    normalize_hud_opacity,
    normalize_hud_transparency,
)


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self):
        self.packed = False
        self.config = {}
        self.hide_calls = 0

    def pack(self, **_kwargs):
        self.packed = True

    def pack_forget(self):
        self.packed = False

    def configure(self, **kwargs):
        self.config.update(kwargs)

    def hide(self):
        self.hide_calls += 1

    def winfo_manager(self):
        return "pack" if self.packed else ""


class FakeOscOutput:
    def __init__(self):
        self.published = []
        self.clear_calls = 0

    def publish_target(self, target, **kwargs):
        self.published.append((target, kwargs))
        return True

    def clear(self, **_kwargs):
        self.clear_calls += 1
        return True


class FakeAfterRoot:
    def __init__(self):
        self.callback = None

    def after(self, _delay, callback):
        self.callback = callback
        return "after-test"

    def after_cancel(self, _after_id):
        self.callback = None


class FakeHudWindow:
    def __init__(self):
        self.alpha = 1.0
        self.lifted_above = None
        self.window_state = "withdrawn"

    def winfo_exists(self):
        return True

    def attributes(self, name, value):
        if name == "-alpha":
            self.alpha = value

    def lift(self, window):
        self.lifted_above = window

    def deiconify(self):
        self.window_state = "normal"

    def withdraw(self):
        self.window_state = "withdrawn"

    def state(self):
        return self.window_state


class FakeGeometryWindow:
    def __init__(self, geometry="250x210+0+0"):
        self.geometry_value = geometry
        self.lifted_above = None

    def winfo_exists(self):
        return True

    def geometry(self, value=None):
        if value is not None:
            self.geometry_value = value
        return self.geometry_value

    def lift(self, window):
        self.lifted_above = window

    def update_idletasks(self):
        pass

    def _parts(self):
        width, height, x, y = self.geometry_value.replace("x", "+").split("+")
        return int(width), int(height), int(x), int(y)

    def winfo_width(self):
        return self._parts()[0]

    def winfo_height(self):
        return self._parts()[1]

    def winfo_x(self):
        return self._parts()[2]

    def winfo_y(self):
        return self._parts()[3]


class FakeScreenRoot:
    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080


class FakeDraw:
    def __init__(self):
        self.text_items = []

    def text(self, *args, **kwargs):
        self.text_items.append((args, kwargs))


class EclipticaMonitorIntegrationTests(unittest.TestCase):
    def make_monitor(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.detected_game_mode = "slashco"
        monitor.current_game_mode = "slashco"
        monitor.ecliptica_state = EclipticaState()
        monitor.mode_changes = []
        monitor.ui_updates = 0

        def set_mode(mode, reason="", log_change=True):
            monitor.detected_game_mode = mode
            monitor.current_game_mode = mode
            monitor.mode_changes.append((mode, reason, log_change))

        def update_ui():
            monitor.ui_updates += 1

        monitor._set_active_game_mode = set_mode
        monitor._update_ecliptica_ui = update_ui
        return monitor

    def test_entering_ecliptica_room_switches_panel(self):
        monitor = self.make_monitor()
        event = parse_ecliptica_line(
            "2026.07.26 01:44:01 Debug - [Behaviour] Entering Room: Ecliptica - Demo Playtest"
        )

        self.assertTrue(monitor._handle_ecliptica_event(event))
        self.assertEqual(monitor.current_game_mode, "ecliptica")
        self.assertEqual(monitor.ecliptica_state.world_name, "Ecliptica - Demo Playtest")
        self.assertEqual(monitor.ui_updates, 1)

    def test_entering_another_room_switches_back_and_resets_ecliptica(self):
        monitor = self.make_monitor()
        monitor.current_game_mode = "ecliptica"
        monitor.ecliptica_state.session_id = "24172"
        event = parse_ecliptica_line(
            "2026.07.26 02:00:00 Debug - [Behaviour] Entering Room: SlashCo"
        )

        self.assertTrue(monitor._handle_ecliptica_event(event))
        self.assertEqual(monitor.current_game_mode, "slashco")
        self.assertEqual(monitor.ecliptica_state.session_id, "")

    def test_strong_ecliptica_event_recovers_when_room_line_was_missed(self):
        monitor = self.make_monitor()
        event = parse_ecliptica_line("ECLIPTICA loaded SESSION ID 24172")

        self.assertTrue(monitor._handle_ecliptica_event(event))
        self.assertEqual(monitor.current_game_mode, "ecliptica")
        self.assertEqual(monitor.ecliptica_state.session_id, "24172")

    def test_damage_line_alone_does_not_force_ecliptica_mode(self):
        monitor = self.make_monitor()
        event = parse_ecliptica_line("damage has been taken: 43, from source: attack_chop")

        self.assertFalse(monitor._handle_ecliptica_event(event))
        self.assertEqual(monitor.current_game_mode, "slashco")
        self.assertEqual(monitor.ecliptica_state.session_damage_taken, 0)

    def test_authentication_updates_identity_without_switching_panel(self):
        monitor = self.make_monitor()
        event = parse_ecliptica_line(
            "2026.07.26 16:04:50 Debug - User Authenticated: TestPlayer "
            "(usr_00000000-0000-0000-0000-000000000001)"
        )

        self.assertFalse(monitor._handle_ecliptica_event(event))
        self.assertEqual(monitor.current_game_mode, "slashco")
        self.assertEqual(monitor.ecliptica_state.local_player_name, "TestPlayer")


class PanelModeSelectionTests(unittest.TestCase):
    def make_monitor(self, preference="auto", detected="slashco"):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.detected_game_mode = detected
        monitor.current_game_mode = "slashco"
        monitor.panel_mode_var = FakeVar(PANEL_MODE_LABELS[preference])
        monitor.mode_status_var = FakeVar("当前：SlashCo")
        monitor.lbl_mode_status = FakeWidget()
        monitor.slashco_left_frame = FakeWidget()
        monitor.ecliptica_left_frame = FakeWidget()
        monitor.slashco_right_frame = FakeWidget()
        monitor.ecliptica_right_frame = FakeWidget()
        monitor.ecliptica_hud = None
        return monitor

    def test_auto_mode_follows_detected_game(self):
        monitor = self.make_monitor(preference="auto")

        monitor._set_active_game_mode("ecliptica", log_change=False)

        self.assertEqual(monitor.detected_game_mode, "ecliptica")
        self.assertEqual(monitor.current_game_mode, "ecliptica")
        self.assertTrue(monitor.ecliptica_left_frame.packed)
        self.assertFalse(monitor.slashco_left_frame.packed)

    def test_manual_ecliptica_panel_ignores_slashco_detection(self):
        monitor = self.make_monitor(preference="ecliptica", detected="ecliptica")
        monitor.ecliptica_hud = FakeWidget()

        monitor._set_active_game_mode("slashco", log_change=False)

        self.assertEqual(monitor.detected_game_mode, "slashco")
        self.assertEqual(monitor.current_game_mode, "ecliptica")
        self.assertTrue(monitor.ecliptica_right_frame.packed)
        self.assertEqual(monitor.ecliptica_hud.hide_calls, 0)

    def test_manual_slashco_panel_ignores_ecliptica_detection(self):
        monitor = self.make_monitor(preference="slashco")

        monitor._set_active_game_mode("ecliptica", log_change=False)

        self.assertEqual(monitor.detected_game_mode, "ecliptica")
        self.assertEqual(monitor.current_game_mode, "slashco")
        self.assertTrue(monitor.slashco_right_frame.packed)

    def test_slasher_info_is_only_visible_in_slashco_mode(self):
        monitor = self.make_monitor(preference="auto")
        monitor.btn_toggle_img = FakeWidget()
        monitor.btn_toggle_img.packed = True
        monitor.img_container = FakeWidget()
        monitor.img_container.packed = True
        monitor.log_frame = FakeWidget()
        monitor.img_visible = True

        monitor._show_game_panel("ecliptica", log_change=False)
        self.assertFalse(monitor.btn_toggle_img.packed)
        self.assertFalse(monitor.img_container.packed)

        monitor._show_game_panel("slashco", log_change=False)
        self.assertTrue(monitor.btn_toggle_img.packed)
        self.assertTrue(monitor.img_container.packed)
        self.assertEqual(monitor.btn_toggle_img.config["text"], "隐藏 Slasher 信息 ▲")


class HudVisibilityTests(unittest.TestCase):
    def make_monitor(self, panel="ecliptica", foreground=True, enabled=True):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.current_game_mode = panel
        monitor.ecliptica_hud_enabled = FakeVar(enabled)
        monitor._is_hud_foreground = lambda: foreground
        return monitor

    def test_hud_requires_ecliptica_panel_and_allowed_foreground_app(self):
        self.assertTrue(self.make_monitor()._should_show_ecliptica_hud())
        self.assertFalse(self.make_monitor(panel="slashco")._should_show_ecliptica_hud())
        self.assertFalse(self.make_monitor(foreground=False)._should_show_ecliptica_hud())
        self.assertFalse(self.make_monitor(enabled=False)._should_show_ecliptica_hud())

    def test_main_window_geometry_fits_scaled_desktop(self):
        self.assertEqual(main_window_geometry(1920, 1080), (1300, 900))
        self.assertEqual(main_window_geometry(1280, 720), (1248, 648))


class OscPublishingTests(unittest.TestCase):
    def make_monitor(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.ecliptica_osc_enabled = FakeVar(True)
        monitor.ecliptica_osc_name_only = FakeVar(False)
        monitor.ecliptica_osc_prefix_var = FakeVar("自定义锁定：")
        monitor.ecliptica_osc_status_var = FakeVar("")
        monitor.ecliptica_osc = FakeOscOutput()
        return monitor

    def test_inactive_boss_state_clears_without_publishing_dash(self):
        monitor = self.make_monitor()

        monitor._publish_ecliptica_osc({"aggro": {"state": "inactive", "target": "-"}})

        self.assertEqual(monitor.ecliptica_osc.clear_calls, 1)
        self.assertEqual(monitor.ecliptica_osc.published, [])
        self.assertEqual(monitor.ecliptica_osc_status_var.get(), "等待 Boss 战")

    def test_active_boss_state_publishes_target(self):
        monitor = self.make_monitor()

        monitor._publish_ecliptica_osc({"aggro": {"state": "other", "target": "ಣಪರೀಕ್ಷೆ"}})

        self.assertEqual(monitor.ecliptica_osc.clear_calls, 0)
        self.assertEqual(monitor.ecliptica_osc.published[0][0], "ಣಪರೀಕ್ಷೆ")
        self.assertEqual(monitor.ecliptica_osc.published[0][1]["prefix"], "自定义锁定：")

    def test_unknown_boss_target_is_cleared_instead_of_published(self):
        monitor = self.make_monitor()

        monitor._publish_ecliptica_osc({"aggro": {"state": "unknown", "target": "-"}})

        self.assertEqual(monitor.ecliptica_osc.clear_calls, 1)
        self.assertEqual(monitor.ecliptica_osc.published, [])
        self.assertEqual(monitor.ecliptica_osc_status_var.get(), "等待锁定目标")

    def test_button_sends_visible_sample_while_output_is_disabled(self):
        monitor = self.make_monitor()
        monitor.ecliptica_osc_enabled.set(False)
        monitor.root = FakeAfterRoot()
        monitor._ecliptica_osc_test_after_id = None
        monitor._ecliptica_osc_test_active = False
        monitor._configure_ecliptica_osc = lambda: ("127.0.0.1", 9000)
        monitor._save_ecliptica_config = lambda: None
        monitor.log = lambda _message: None

        monitor._test_ecliptica_osc()

        self.assertEqual(monitor.ecliptica_osc.published[0][0], "测试玩家")
        self.assertEqual(monitor.ecliptica_osc.published[0][1]["prefix"], "自定义锁定：")
        self.assertTrue(monitor._ecliptica_osc_test_active)
        self.assertEqual(monitor.ecliptica_osc_status_var.get(), "测试已发送，3 秒后自动清除")
        monitor.root.callback()
        self.assertEqual(monitor.ecliptica_osc.clear_calls, 1)
        self.assertEqual(monitor.ecliptica_osc_status_var.get(), "测试结束，已清除")


class HudLayoutTests(unittest.TestCase):
    def test_ecliptica_duration_format(self):
        self.assertEqual(format_ecliptica_duration(45), "45秒")
        self.assertEqual(format_ecliptica_duration(126), "2分06秒")
        self.assertEqual(format_ecliptica_duration(None), "0秒")

    def test_ecliptica_clock_format(self):
        self.assertEqual(format_ecliptica_clock(None), "00:00")
        self.assertEqual(format_ecliptica_clock(5.9), "00:05")
        self.assertEqual(format_ecliptica_clock(126), "02:06")
        self.assertEqual(format_ecliptica_clock(3930), "65:30")

    def test_damage_hud_uses_current_boss_phase_metrics(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud._ensure_windows = lambda: None
        hud._render_damage_text = lambda: None
        hud._render_lock_text = lambda: None
        hud._place_windows = lambda: None
        hud._apply_display_visibility = lambda: None
        hud.editing = False
        hud.damage_background_window = FakeHudWindow()
        hud.damage_window = FakeHudWindow()
        hud.lock_background_window = FakeHudWindow()
        hud.lock_window = FakeHudWindow()

        hud.update(
            {
                "class_name": "Thaumaturge",
                "stage": "Bringer",
                "current_boss": "JimBringer",
                "current_boss_phase": 2,
                "current_phase_damage": 128200,
                "current_phase_damage_taken": 6900,
                "recent_5s_dps": 354.25,
                "current_boss_elapsed": 126,
                "total_elapsed": 3930,
                "aggro": {},
            }
        )

        self.assertEqual(
            hud.damage_rows,
            [
                ("当前职业", "Thaumaturge"),
                ("当前阶段", "Bringer"),
                ("当前 BOSS", "JimBringer"),
                ("BOSS 阶段", "2"),
                ("本局 BOSS 总伤害", "128.2K"),
                ("本局 BOSS 总受伤", "6.9K"),
                ("近 5 秒 DPS", "354.2"),
                ("当前 BOSS 耗时", "02:06"),
                ("总耗时", "65:30"),
            ],
        )

    def test_damage_hud_only_renders_selected_fields(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud._ensure_windows = lambda: None
        hud._render_damage_text = lambda: None
        hud._render_lock_text = lambda: None
        hud._place_windows = lambda: None
        hud._apply_display_visibility = lambda: None
        hud.editing = False
        hud.damage_background_window = FakeHudWindow()
        hud.damage_window = FakeHudWindow()
        hud.lock_background_window = FakeHudWindow()
        hud.lock_window = FakeHudWindow()

        hud.update(
            {"class_name": "Thaumaturge", "recent_5s_dps": 12.5, "aggro": {}},
            selected_fields=["class_name", "recent_5s_dps"],
        )

        self.assertEqual(hud.damage_rows, [("当前职业", "Thaumaturge"), ("近 5 秒 DPS", "12.5")])

    def test_hud_field_normalization_keeps_order_and_allows_empty_selection(self):
        self.assertEqual(
            normalize_hud_damage_fields(["total_elapsed", "class_name", "unknown"]),
            ["class_name", "total_elapsed"],
        )
        self.assertEqual(normalize_hud_damage_fields([]), [])

    def test_boss_lock_hud_is_hidden_outside_boss_battle(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud.display_mode = "both"
        hud.editing = False
        hud.boss_lock_active = False
        hud.damage_background_window = FakeHudWindow()
        hud.damage_window = FakeHudWindow()
        hud.lock_background_window = FakeHudWindow()
        hud.lock_window = FakeHudWindow()

        hud._apply_display_visibility()

        self.assertEqual(hud.damage_window.state(), "normal")
        self.assertEqual(hud.lock_background_window.state(), "withdrawn")
        self.assertEqual(hud.lock_window.state(), "withdrawn")

    def test_boss_lock_hud_only_keeps_the_target_line(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud._ensure_windows = lambda: None
        hud._render_damage_text = lambda: None
        hud._render_lock_text = lambda: None
        hud._place_windows = lambda: None
        hud._apply_display_visibility = lambda: None
        hud.editing = False
        hud.damage_background_window = FakeHudWindow()
        hud.damage_window = FakeHudWindow()
        hud.lock_background_window = FakeHudWindow()
        hud.lock_window = FakeHudWindow()

        hud.update({"aggro": {"state": "other", "target": "Player A", "status": "其他玩家"}})

        self.assertEqual(hud.lock_text, "Boss 当前锁定：Player A")
        self.assertTrue(hud.boss_lock_active)
        self.assertFalse(hasattr(hud, "lock_detail_text"))

        hud.update({"aggro": {"state": "unknown", "target": "-"}})
        self.assertFalse(hud.boss_lock_active)

    def test_hud_scale_follows_the_smaller_window_dimension(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)

        self.assertEqual(hud._hud_scale("damage", 300, 210), 1.0)
        self.assertEqual(hud._hud_scale("damage", 600, 420), 2.0)
        self.assertEqual(hud._hud_scale("damage", 600, 210), 1.0)
        self.assertEqual(hud._hud_scale("boss_lock", 640, 180), 2.0)

    def test_preview_geometry_updates_visible_background_without_text_window(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud.layout = {}
        hud.damage_background_window = FakeGeometryWindow()
        hud._render_edit_preview = lambda _key: None

        hud._set_preview_geometry("damage", 720, 480, 360, 280)

        self.assertEqual(hud.damage_background_window.geometry(), "360x280+720+480")
        self.assertEqual(
            hud.layout["damage"],
            {"x": 720, "y": 480, "width": 360, "height": 280},
        )

    def test_hud_pair_geometry_moves_background_and_text_together(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud.layout = {}
        hud.damage_background_window = FakeGeometryWindow()
        content = FakeGeometryWindow()

        hud._set_window_pair_geometry("damage", content, 640, 360, 300, 240)

        self.assertEqual(content.geometry(), "300x240+640+360")
        self.assertEqual(hud.damage_background_window.geometry(), content.geometry())
        self.assertEqual(
            hud.layout["damage"],
            {"x": 640, "y": 360, "width": 300, "height": 240},
        )

    def test_reset_layout_restores_default_positions_and_sizes(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud.root = FakeScreenRoot()
        hud.layout = {"damage": {"x": 900, "y": 700, "width": 500, "height": 400}}
        hud.damage_background_window = FakeGeometryWindow()
        hud.lock_background_window = FakeGeometryWindow("320x90+0+0")
        hud.damage_window = FakeGeometryWindow("500x400+900+700")
        hud.lock_window = FakeGeometryWindow("500x150+800+500")
        hud._pointer_operation = {"kind": "drag"}
        hud._ensure_windows = lambda: None
        hud._render_damage_text = lambda: None
        hud._render_lock_text = lambda: None

        layout = hud.reset_layout()

        self.assertEqual(layout["damage"], {"x": 24, "y": 435, "width": 300, "height": 210})
        self.assertEqual(layout["boss_lock"], {"x": 800, "y": 24, "width": 320, "height": 90})
        self.assertEqual(hud.damage_window.geometry(), hud.damage_background_window.geometry())
        self.assertEqual(hud.lock_window.geometry(), hud.lock_background_window.geometry())
        self.assertIsNone(hud._pointer_operation)

    def test_hud_text_color_is_pure_white(self):
        self.assertEqual(EclipticaDesktopHud.FG, "#ffffff")

    def test_hud_text_is_drawn_once_without_outline_or_shadow(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        draw = FakeDraw()

        hud._draw_text(draw, 10, 20, "HUD", "#ffffff", "font", "la")

        self.assertEqual(len(draw.text_items), 1)
        self.assertEqual(draw.text_items[0][0], ((10, 20), "HUD"))
        self.assertEqual(draw.text_items[0][1]["fill"], "#ffffff")

    def test_hud_font_falls_back_for_kannada_without_losing_chinese(self):
        runs = _hud_text_runs("Boss 当前锁定：ಣಪರೀಕ್ಷೆ", _load_hud_font(24, bold=True))

        self.assertEqual("".join(text for text, _font in runs), "Boss 当前锁定：ಣಪರೀಕ್ಷೆ")
        kannada_fonts = [font.getname()[0] for text, font in runs if "ಣ" in text]
        self.assertEqual(kannada_fonts, ["Nirmala UI"])

    def test_hud_font_falls_back_for_thai_and_mathematical_art_text(self):
        text = "ฅ小荷包蛋ฅ 𝓐𝕬𝔄"
        runs = _hud_text_runs(text, _load_hud_font(24, bold=True))

        self.assertEqual("".join(run for run, _font in runs), text)
        thai_fonts = [font.getname()[0] for run, font in runs if "ฅ" in run]
        art_fonts = [font.getname()[0] for run, font in runs if "𝓐" in run]
        self.assertEqual(thai_fonts, ["Leelawadee UI", "Leelawadee UI"])
        self.assertIn(art_fonts[0], ("Segoe UI Symbol", "Cambria Math"))

    def test_boss_lock_font_shrinks_to_fit_available_width(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (600, 100)))
        text = "Boss 当前锁定：没有坏心思的AliceAliceAlice"
        font = _fit_hud_font_to_width(draw, text, 360, 28, 8)

        self.assertLess(font.size, 28)
        self.assertLessEqual(_hud_text_width(draw, text, font), 360)

    def test_layered_window_pixels_use_premultiplied_bgra(self):
        image = Image.new("RGBA", (2, 1))
        image.putdata(((200, 100, 50, 128), (10, 20, 30, 255)))

        pixels = _premultiplied_bgra(image)

        self.assertEqual(list(pixels[:4]), [25, 50, 100, 128])
        self.assertEqual(list(pixels[4:]), [30, 20, 10, 255])

    def test_hud_opacity_only_changes_background_windows(self):
        hud = EclipticaDesktopHud.__new__(EclipticaDesktopHud)
        hud.opacity = 0.9
        hud.damage_background_window = FakeHudWindow()
        hud.lock_background_window = FakeHudWindow()
        hud.damage_window = FakeHudWindow()
        hud.lock_window = FakeHudWindow()

        hud.set_opacity(0.45)

        self.assertEqual(hud.damage_background_window.alpha, 0.45)
        self.assertEqual(hud.lock_background_window.alpha, 0.45)
        self.assertEqual(hud.damage_window.alpha, 1.0)
        self.assertEqual(hud.lock_window.alpha, 1.0)
        self.assertIs(hud.damage_window.lifted_above, hud.damage_background_window)
        self.assertIs(hud.lock_window.lifted_above, hud.lock_background_window)

    def test_hud_opacity_is_clamped_and_invalid_values_use_default(self):
        self.assertEqual(normalize_hud_opacity(0.65), 0.65)
        self.assertEqual(normalize_hud_opacity(-0.05), 0.0)
        self.assertEqual(normalize_hud_opacity(0.05), 0.05)
        self.assertEqual(normalize_hud_opacity(1.5), 1.0)
        self.assertEqual(normalize_hud_opacity("bad"), 0.9)

    def test_hud_transparency_supports_fully_transparent_background(self):
        self.assertEqual(normalize_hud_transparency(100), 100.0)
        self.assertEqual(normalize_hud_transparency(-1), 0.0)
        self.assertEqual(normalize_hud_transparency(101), 100.0)
        self.assertEqual(normalize_hud_transparency("bad"), 10.0)

    def test_hud_display_mode_defaults_to_both_for_invalid_values(self):
        self.assertEqual(normalize_hud_display_mode("damage"), "damage")
        self.assertEqual(normalize_hud_display_mode("boss_lock"), "boss_lock")
        self.assertEqual(normalize_hud_display_mode("both"), "both")
        self.assertEqual(normalize_hud_display_mode("invalid"), "both")

    def test_layout_values_are_normalized_and_minimum_size_is_enforced(self):
        layout = normalize_hud_layout(
            {
                "damage": {"x": "35", "y": 42, "width": 100, "height": 80},
                "boss_lock": {"x": 500, "y": 20, "width": 480, "height": 120},
            }
        )

        self.assertEqual(
            layout["damage"],
            {"x": 35, "y": 42, "width": 300, "height": 210},
        )
        self.assertEqual(
            layout["boss_lock"],
            {"x": 500, "y": 20, "width": 480, "height": 120},
        )

    def test_invalid_or_partial_layout_entries_are_ignored(self):
        layout = normalize_hud_layout(
            {
                "damage": {"x": 1, "y": 2, "width": "bad", "height": 220},
                "boss_lock": {"x": 1, "y": 2},
                "unknown": {"x": 1, "y": 2, "width": 3, "height": 4},
            }
        )

        self.assertEqual(layout, {})


if __name__ == "__main__":
    unittest.main()
