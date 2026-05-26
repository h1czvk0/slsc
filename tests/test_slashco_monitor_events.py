import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco import SlashCoMonitorCN  # noqa: E402


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
            "sealed_rooms": 0,
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

    def test_explicit_free_fuel_overrides_inferred_value(self):
        monitor = self.make_fuel_monitor()

        SlashCoMonitorCN.set_player_fuel_headstart(monitor, 4, 0, explicit=True)

        self.assertTrue(monitor.free_fuel_explicit)
        self.assertEqual(monitor.game_stats["players"], 4)
        self.assertEqual(monitor.game_stats["free_fuel"], 0)
        self.assertEqual(SlashCoMonitorCN.get_fuel_count(monitor), 0)


if __name__ == "__main__":
    unittest.main()
