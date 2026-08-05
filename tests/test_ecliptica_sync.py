import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_sync import (  # noqa: E402
    DEFAULT_SYNC_URL,
    EclipticaSyncClient,
    build_damage_update,
    build_join_message,
    normalize_room_players,
    normalize_sync_url,
    sync_identity,
)


def local_snapshot(**overrides):
    snapshot = {
        "world": "Ecliptica",
        "session_id": "284719",
        "local_player_id": "usr_11111111-1111-1111-1111-111111111111",
        "local_player_name": "Alice",
        "stage": "Bringer",
        "class_name": "Thaumaturge",
        "current_boss": "JimBringer",
        "current_boss_phase": 2,
        "current_boss_damage": 120000,
        "current_phase_damage": 128200,
        "session_total_damage": 302500,
        "live_session_total_damage": 310700,
        "session_damage_taken": 6900,
        "defeated_count": 3,
        "intermission": False,
    }
    snapshot.update(overrides)
    return snapshot


class SyncProtocolTests(unittest.TestCase):
    def test_default_server_uses_nat_public_port(self):
        self.assertEqual(DEFAULT_SYNC_URL, "ws://zzu2.wch1.top:44976/ws")

    def test_server_url_is_normalized_for_websocket_transport(self):
        self.assertEqual(normalize_sync_url("sync.example.com"), "wss://sync.example.com/ws")
        self.assertEqual(
            normalize_sync_url("https://sync.example.com/realtime?token=x"),
            "wss://sync.example.com/realtime?token=x",
        )
        self.assertEqual(normalize_sync_url("ftp://sync.example.com"), "")

    def test_identity_requires_log_session_user_id_and_name(self):
        self.assertEqual(
            sync_identity(local_snapshot()),
            (
                "284719",
                "usr_11111111-1111-1111-1111-111111111111",
                "Alice",
            ),
        )
        self.assertIsNone(sync_identity(local_snapshot(session_id="-")))
        self.assertIsNone(sync_identity(local_snapshot(local_player_id="Alice")))
        self.assertIsNone(sync_identity(local_snapshot(local_player_name="")))

    def test_realtime_damage_and_historical_settlement_damage_are_distinct(self):
        snapshot = local_snapshot()

        join = build_join_message(snapshot)
        update = build_damage_update(snapshot, 7)

        self.assertEqual(join["session_id"], "284719")
        self.assertEqual(join["player"]["vrc_username"], "Alice")
        self.assertEqual(update["sequence"], 7)
        self.assertEqual(update["game"]["boss_damage"], 128200)
        self.assertEqual(update["game"]["session_total_damage"], 302500)
        self.assertNotIn("delta_damage", update["game"])

    def test_room_state_keeps_all_unique_players_and_sorts_them(self):
        players = []
        for index in range(6):
            players.append(
                {
                    "vrc_user_id": f"usr_{index}",
                    "vrc_username": f"Player {index}",
                    "boss_damage": index * 100,
                }
            )
        players.append(dict(players[5]))

        normalized = normalize_room_players(
            {"type": "room_state", "session_id": "284719", "players": players},
            "284719",
        )

        self.assertEqual(len(normalized), 6)
        self.assertEqual(
            [item["boss_damage"] for item in normalized],
            [500, 400, 300, 200, 100, 0],
        )

    def test_other_session_and_older_room_state_are_ignored(self):
        client = EclipticaSyncClient()
        client.update_local_state(local_snapshot())
        player = {
            "vrc_user_id": "usr_22222222-2222-2222-2222-222222222222",
            "vrc_username": "Bob",
            "boss_damage": 800,
        }

        self.assertFalse(
            client.handle_server_message(
                {"type": "room_state", "session_id": "999999", "players": [player]}
            )
        )
        self.assertTrue(
            client.handle_server_message(
                {
                    "type": "room_state",
                    "session_id": "284719",
                    "server_sequence": 5,
                    "players": [player],
                }
            )
        )
        self.assertFalse(
            client.handle_server_message(
                {
                    "type": "room_state",
                    "session_id": "284719",
                    "server_sequence": 4,
                    "players": [],
                }
            )
        )
        self.assertEqual(client.snapshot()["players"][0]["vrc_username"], "Bob")


if __name__ == "__main__":
    unittest.main()
