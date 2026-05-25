import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slashco_log_parser import (  # noqa: E402
    is_round_end_line,
    is_round_start_line,
    item_numeric_id,
    line_might_affect_state,
    normalize_item_id,
    parse_log_line,
)


class LogParserTests(unittest.TestCase):
    def test_parse_item_assignment(self):
        event = parse_log_line("Assigning item SCItem12 as: Fuel")
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "item")
        self.assertEqual(event.groups, ("SCItem12", "Fuel"))
        self.assertEqual(normalize_item_id(event.groups[0]), "SC_Item12")

    def test_parse_item_collision(self):
        event = parse_log_line("(SC_Item4) collided with: Box_Medium (UnityEngine.GameObject)")
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "item_collision")
        self.assertEqual(event.groups, ("SC_Item4", "Box_Medium"))

    def test_parse_generator_and_stats_events(self):
        cases = [
            ("For a game of 4 players, 6 will be spawned", "fuel_base", ("6",)),
            ("2 extra fuel cans will appear in sealed rooms", "fuel_extra", ("2",)),
            ("9 items will spawn outside sealed rooms", "item_outside", ("9",)),
            ("3 items will spawn INside sealed rooms", "item_inside", ("3",)),
            ("Gas fueled to SC_generator1", "fuel", ("SC_generator1",)),
            (
                "SC_generator2 Progress check. updated HAS_BATTERY value: True",
                "battery_progress",
                ("SC_generator2", "True"),
            ),
            ("Battery for SC_generator1 improperly set. FIXING NOW.", "battery_fixing", ("SC_generator1",)),
            ("Generator Battery skillcheck failed", "battery_skillcheck_failed", ()),
            ("4 Rooms will be SEALED", "rooms_sealed", ("4",)),
        ]
        for line, kind, groups in cases:
            with self.subTest(kind=kind):
                event = parse_log_line(line)
                self.assertIsNotNone(event)
                self.assertEqual(event.kind, kind)
                self.assertEqual(event.groups, groups)

    def test_round_boundaries(self):
        self.assertTrue(is_round_start_line("Selected landing spot on map Farm"))
        self.assertTrue(is_round_start_line("SLASHCO Game setup"))
        self.assertTrue(is_round_start_line("Getting Map Spawnpoints"))
        self.assertTrue(is_round_end_line("Returning to Lobby"))
        self.assertTrue(is_round_end_line("Logging all doors for map Lobby"))
        self.assertFalse(is_round_end_line("Logging all doors for map Farm"))

    def test_state_filter(self):
        self.assertTrue(line_might_affect_state("Assigning item SC_Item1 as: Fuel"))
        self.assertFalse(line_might_affect_state("this is an unrelated VRChat log line"))

    def test_item_numeric_id(self):
        self.assertEqual(item_numeric_id("SC_Item29"), 29)
        self.assertEqual(item_numeric_id("bad"), -1)


if __name__ == "__main__":
    unittest.main()
