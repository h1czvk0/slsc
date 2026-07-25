import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_log_parser import EclipticaState, parse_ecliptica_line  # noqa: E402
from slashco import SlashCoMonitorCN  # noqa: E402


class EclipticaMonitorIntegrationTests(unittest.TestCase):
    def make_monitor(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.current_game_mode = "slashco"
        monitor.ecliptica_state = EclipticaState()
        monitor.mode_changes = []
        monitor.ui_updates = 0

        def set_mode(mode, reason="", log_change=True):
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


if __name__ == "__main__":
    unittest.main()
