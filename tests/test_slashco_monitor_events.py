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


if __name__ == "__main__":
    unittest.main()
