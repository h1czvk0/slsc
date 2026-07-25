import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_log_parser import EclipticaState, parse_ecliptica_line  # noqa: E402
from slashco import PANEL_MODE_LABELS, SlashCoMonitorCN, normalize_hud_layout  # noqa: E402


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
        self.assertEqual(monitor.ecliptica_hud.hide_calls, 1)

    def test_manual_slashco_panel_ignores_ecliptica_detection(self):
        monitor = self.make_monitor(preference="slashco")

        monitor._set_active_game_mode("ecliptica", log_change=False)

        self.assertEqual(monitor.detected_game_mode, "ecliptica")
        self.assertEqual(monitor.current_game_mode, "slashco")
        self.assertTrue(monitor.slashco_right_frame.packed)


class HudLayoutTests(unittest.TestCase):
    def test_layout_values_are_normalized_and_minimum_size_is_enforced(self):
        layout = normalize_hud_layout(
            {
                "damage": {"x": "35", "y": 42, "width": 100, "height": 80},
                "boss_lock": {"x": 500, "y": 20, "width": 480, "height": 120},
            }
        )

        self.assertEqual(
            layout["damage"],
            {"x": 35, "y": 42, "width": 250, "height": 210},
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
