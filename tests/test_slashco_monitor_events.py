import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco import ROUND_TIMEOUT_SECONDS, SlashCoMonitorCN  # noqa: E402


class FakeRoot:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 1

    def after(self, _delay_ms, callback, *args):
        after_id = f"after-{self.next_id}"
        self.next_id += 1
        self.callbacks[after_id] = (callback, args)
        return after_id

    def after_cancel(self, after_id):
        self.callbacks.pop(after_id, None)

    def run_all(self):
        callbacks = list(self.callbacks.items())
        self.callbacks.clear()
        for _after_id, (callback, args) in callbacks:
            callback(*args)


class FakeLabel:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


class MonitorEventTests(unittest.TestCase):
    def make_monitor(self, active=True):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.round_active = active
        monitor.held_items = set()
        monitor.consumed_fuel_items = set()
        monitor.pending_fuel_after_ids = {}
        monitor.added_fuel = []
        monitor.add_fuel_from_consumed_item = monitor.added_fuel.append
        return monitor

    def test_fuel_hibernation_counts_during_active_round_without_local_hold(self):
        monitor = self.make_monitor(active=True)
        monitor.root = FakeRoot()

        SlashCoMonitorCN.process_line(
            monitor,
            "2026.05.26 00:38:39 Debug      -  Hibernating item SC_Item6, (Fuel)",
        )
        monitor.root.run_all()

        self.assertEqual(monitor.added_fuel, ["SC_Item6"])

    def test_fuel_hibernation_ignored_before_round_is_active(self):
        monitor = self.make_monitor(active=False)

        SlashCoMonitorCN.process_line(
            monitor,
            "2026.05.26 00:31:36 Debug      -  Hibernating item SC_Item6, (Fuel)",
        )

        self.assertEqual(monitor.added_fuel, [])

    def test_pending_fuel_hibernation_can_be_cancelled_by_round_reset(self):
        monitor = self.make_monitor(active=True)
        monitor.root = FakeRoot()

        SlashCoMonitorCN.process_line(
            monitor,
            "2026.05.26 00:45:00 Debug      -  Hibernating item SC_Item3, (Fuel)",
        )
        monitor.round_active = False
        SlashCoMonitorCN._cancel_pending_fuel_hibernations(monitor)
        monitor.root.run_all()

        self.assertEqual(monitor.added_fuel, [])

    def make_fuel_monitor(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.game_stats = {
            "fuel_base": 0,
            "fuel_extra": 0,
            "item_out": 0,
            "item_in": 0,
            "players": 0,
            "free_fuel": 0,
            "sealed_rooms": None,
        }
        monitor.fuel_added_count = 0
        monitor.free_fuel_explicit = False
        monitor.consumed_fuel_items = set()
        monitor.positions = []
        monitor.logs = []
        monitor.update_item_position = lambda iid, pos: monitor.positions.append((iid, pos))
        monitor.log = monitor.logs.append
        return monitor

    def test_fuel_headstart_is_inferred_from_player_count(self):
        monitor = self.make_fuel_monitor()

        SlashCoMonitorCN.set_player_fuel_headstart(monitor, 1)
        self.assertEqual(monitor.game_stats["free_fuel"], 4)
        self.assertEqual(SlashCoMonitorCN.get_fuel_count(monitor), 4)

        SlashCoMonitorCN.add_fuel(monitor)
        self.assertEqual(monitor.fuel_added_count, 1)
        self.assertEqual(SlashCoMonitorCN.get_fuel_count(monitor), 5)

    def test_fuel_count_defaults_to_zero_without_player_count(self):
        monitor = self.make_fuel_monitor()

        self.assertEqual(SlashCoMonitorCN.get_fuel_count(monitor), 0)
        SlashCoMonitorCN.add_fuel_from_consumed_item(monitor, "SC_Item6")

        self.assertEqual(SlashCoMonitorCN.get_fuel_count(monitor), 1)
        self.assertEqual(monitor.positions, [("SC_Item6", "已加油")])

    def test_logged_free_fuel_is_ignored_in_favor_of_player_rule(self):
        monitor = self.make_fuel_monitor()

        SlashCoMonitorCN.set_player_fuel_headstart(monitor, 7, -2, explicit=True)

        self.assertTrue(monitor.free_fuel_explicit)
        self.assertEqual(monitor.game_stats["players"], 7)
        self.assertEqual(monitor.game_stats["free_fuel"], 0)
        self.assertEqual(SlashCoMonitorCN.get_fuel_count(monitor), 0)

    def make_stats_monitor(self):
        monitor = self.make_fuel_monitor()
        monitor.lbl_stats_fuel = FakeLabel()
        monitor.lbl_stats_item = FakeLabel()
        monitor.lbl_stats_sealed = FakeLabel()
        monitor.lbl_stats_headstart = FakeLabel()
        return monitor

    def test_detected_zero_sealed_rooms_stays_visible(self):
        monitor = self.make_stats_monitor()
        monitor.game_stats["sealed_rooms"] = 0

        SlashCoMonitorCN.update_stats_ui(monitor)

        self.assertEqual(monitor.lbl_stats_sealed.config["text"], "有 0 个门被锁上")

    def test_undetected_sealed_rooms_stays_hidden(self):
        monitor = self.make_stats_monitor()

        SlashCoMonitorCN.update_stats_ui(monitor)

        self.assertEqual(monitor.lbl_stats_sealed.config["text"], "")

    def make_timer_monitor(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.root = FakeRoot()
        monitor.lbl_round_timer = FakeLabel()
        monitor._round_timer_after_id = None
        monitor._is_shutting_down = False
        monitor.round_active = False
        monitor.round_started_at = None
        monitor.round_timed_out = False
        monitor.logs = []
        monitor.log = monitor.logs.append
        return monitor

    def test_round_timer_starts_green(self):
        monitor = self.make_timer_monitor()

        SlashCoMonitorCN.start_round_timer(monitor)

        self.assertTrue(monitor.round_active)
        self.assertEqual(monitor.lbl_round_timer.config["bg"], "#d8f5d0")
        self.assertIn("对局计时：00:00", monitor.lbl_round_timer.config["text"])

    def test_round_timer_marks_timeout_once(self):
        monitor = self.make_timer_monitor()
        monitor.round_active = True
        monitor.round_started_at = __import__("time").monotonic() - ROUND_TIMEOUT_SECONDS - 1

        SlashCoMonitorCN._update_round_timer_ui(monitor)
        SlashCoMonitorCN._update_round_timer_ui(monitor)

        self.assertEqual(monitor.lbl_round_timer.config["bg"], "#d93025")
        self.assertIn("超时", monitor.lbl_round_timer.config["text"])
        self.assertEqual(monitor.logs, ["对局计时已超过 25 分钟。"])

    def test_round_timer_stops_to_waiting_state(self):
        monitor = self.make_timer_monitor()
        SlashCoMonitorCN.start_round_timer(monitor)

        SlashCoMonitorCN.stop_round_timer(monitor)

        self.assertFalse(monitor.round_active)
        self.assertIsNone(monitor.round_started_at)
        self.assertEqual(monitor.lbl_round_timer.config["bg"], "#eeeeee")
        self.assertIn("等待开始", monitor.lbl_round_timer.config["text"])

    def test_duplicate_round_start_does_not_reset_visible_stats(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.round_active = True
        monitor.logs = []
        monitor.log = monitor.logs.append
        monitor.reset_game = lambda *args, **kwargs: self.fail("duplicate start should not reset")
        monitor.start_round_timer = lambda: self.fail("duplicate start should not restart timer")

        SlashCoMonitorCN.process_line(monitor, "SLASHCO Game setup.")

        self.assertEqual(monitor.logs, ["忽略重复开始信号: 新回合开始"])

    def test_first_round_start_resets_and_starts_timer(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.round_active = False
        calls = []
        monitor.reset_game = lambda *args, **kwargs: calls.append(("reset", args, kwargs))
        monitor.start_round_timer = lambda: calls.append(("timer", (), {}))

        SlashCoMonitorCN.process_line(monitor, "SLASHCO Game setup.")

        self.assertEqual(calls[0][0], "reset")
        self.assertEqual(calls[0][2], {"force": True, "reason": "新回合开始"})
        self.assertEqual(calls[1][0], "timer")


if __name__ == "__main__":
    unittest.main()
