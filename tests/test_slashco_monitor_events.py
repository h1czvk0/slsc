import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco import SlashCoMonitorCN  # noqa: E402


class MonitorEventTests(unittest.TestCase):
    def make_monitor(self, active=True):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.round_active = active
        monitor.held_items = set()
        monitor.added_fuel = []
        monitor.add_fuel_from_consumed_item = monitor.added_fuel.append
        return monitor

    def test_fuel_hibernation_counts_during_active_round_without_local_hold(self):
        monitor = self.make_monitor(active=True)

        SlashCoMonitorCN.process_line(
            monitor,
            "2026.05.26 00:38:39 Debug      -  Hibernating item SC_Item6, (Fuel)",
        )

        self.assertEqual(monitor.added_fuel, ["SC_Item6"])

    def test_fuel_hibernation_ignored_before_round_is_active(self):
        monitor = self.make_monitor(active=False)

        SlashCoMonitorCN.process_line(
            monitor,
            "2026.05.26 00:31:36 Debug      -  Hibernating item SC_Item6, (Fuel)",
        )

        self.assertEqual(monitor.added_fuel, [])


if __name__ == "__main__":
    unittest.main()
