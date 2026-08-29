import pathlib
import sys
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_sync import (  # noqa: E402
    DEFAULT_SYNC_URL,
    EclipticaSyncClient,
    build_damage_update,
    build_join_message,
    is_boss_battle_active,
    normalize_room_players,
    normalize_sync_url,
    sync_identity,
    sync_wait_status,
)


def local_snapshot(**overrides):
    snapshot = {
        "world": "Ecliptica",
        "session_id": "284719",
        "run_active": True,
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
        "settlements": [
            {
                "boss": "JimBringer",
                "phase": 2,
                "strike": 82000,
                "non_strike": 6100,
                "total": 88100,
                "duration": 898,
                "dps": 98.1,
                "timestamp": 1_700_000_000.25,
            }
        ],
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
        self.assertIsNone(sync_identity(local_snapshot(run_active=False)))

    def test_wait_status_reports_the_exact_missing_log_field(self):
        self.assertEqual(
            sync_wait_status(local_snapshot(local_player_name="", local_player_id="")),
            "等待日志中的 VRC 用户名与 ID",
        )
        self.assertEqual(
            sync_wait_status(local_snapshot(session_id="-")),
            "已识别 VRC 用户 Alice，等待会话 ID",
        )
        self.assertEqual(
            sync_wait_status(local_snapshot(run_active=False)),
            "已识别 VRC 用户 Alice，等待本局开始",
        )

    def test_boss_battle_requires_an_active_run_and_current_boss(self):
        self.assertTrue(is_boss_battle_active(local_snapshot()))
        self.assertFalse(is_boss_battle_active(local_snapshot(current_boss="-")))
        self.assertFalse(is_boss_battle_active(local_snapshot(intermission=True)))
        self.assertFalse(is_boss_battle_active(local_snapshot(run_active=False)))

    def test_realtime_damage_and_historical_settlement_damage_are_distinct(self):
        snapshot = local_snapshot()

        join = build_join_message(snapshot)
        update = build_damage_update(snapshot, 7)

        self.assertEqual(join["session_id"], "284719")
        self.assertEqual(join["player"]["vrc_username"], "Alice")
        self.assertEqual(update["sequence"], 7)
        self.assertEqual(update["game"]["boss_damage"], 128200)
        self.assertEqual(update["game"]["session_total_damage"], 302500)
        self.assertEqual(update["game"]["settlements"][0]["boss"], "JimBringer")
        self.assertEqual(update["game"]["settlements"][0]["total"], 88100)
        self.assertEqual(update["game"]["settlements"][0]["settled_at_ms"], 1_700_000_000_250)
        self.assertEqual(len(update["game"]["settlements"][0]["settlement_id"]), 64)
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
        players.append(
            {
                "vrc_user_id": "usr_offline",
                "vrc_username": "Offline Player",
                "boss_damage": 999999,
                "online": False,
            }
        )

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

    def test_initial_cached_room_state_is_hidden_until_current_state_arrives(self):
        client = EclipticaSyncClient()
        client.update_local_state(local_snapshot())
        client._room_state_messages_to_skip = 1
        previous_player = {
            "vrc_user_id": "usr_11111111-1111-1111-1111-111111111111",
            "vrc_username": "Alice",
            "boss_name": "PreviousBoss",
            "boss_phase": 3,
            "boss_damage": 0,
            "session_total_damage": 49_400,
        }
        current_player = dict(
            previous_player,
            boss_name="-",
            boss_phase=None,
            session_total_damage=0,
        )

        self.assertTrue(
            client.handle_server_message(
                {
                    "type": "room_state",
                    "session_id": "284719",
                    "server_sequence": 10,
                    "players": [previous_player],
                }
            )
        )
        self.assertEqual(client.snapshot()["players"], [])

        self.assertTrue(
            client.handle_server_message(
                {
                    "type": "room_state",
                    "session_id": "284719",
                    "server_sequence": 11,
                    "players": [current_player],
                }
            )
        )
        self.assertEqual(client.snapshot()["players"][0]["session_total_damage"], 0)

    def test_ending_run_immediately_clears_realtime_room_players(self):
        client = EclipticaSyncClient()
        client.update_local_state(local_snapshot())
        client.handle_server_message(
            {
                "type": "room_state",
                "session_id": "284719",
                "server_sequence": 1,
                "players": [
                    {
                        "vrc_user_id": "usr_11111111-1111-1111-1111-111111111111",
                        "vrc_username": "Alice",
                        "session_total_damage": 49_400,
                    }
                ],
            }
        )
        self.assertEqual(len(client.snapshot()["players"]), 1)

        client.update_local_state(local_snapshot(run_active=False))

        self.assertEqual(client.snapshot()["players"], [])
        self.assertIsNone(sync_identity(client._local_state))

    def test_manual_reconnect_clears_state_and_closes_current_connection(self):
        class FakeConnection:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        client = EclipticaSyncClient()
        client.update_local_state(local_snapshot())
        connection = FakeConnection()
        client._connection = connection
        client._connected = True
        initial_version = client._configuration_version

        client.reconnect_now()

        self.assertEqual(connection.close_calls, 1)
        self.assertFalse(client.snapshot()["connected"])
        self.assertEqual(client._configuration_version, initial_version + 1)
        self.assertIn("手动连接", client.snapshot()["status"])

    def test_entering_each_boss_battle_retries_once_when_not_connected(self):
        class FakeConnection:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        client = EclipticaSyncClient()
        outside_boss = local_snapshot(current_boss="-", current_boss_phase=None)
        client.update_local_state(outside_boss)
        initial_version = client._configuration_version

        first_connection = FakeConnection()
        client._connection = first_connection
        client.update_local_state(local_snapshot(current_boss="JimBringer", current_boss_phase=1))
        self.assertEqual(first_connection.close_calls, 1)
        self.assertEqual(client._configuration_version, initial_version + 1)

        client.update_local_state(local_snapshot(current_boss="JimBringer", current_boss_phase=2))
        self.assertEqual(client._configuration_version, initial_version + 1)

        client.update_local_state(outside_boss)
        second_connection = FakeConnection()
        client._connection = second_connection
        client.update_local_state(local_snapshot(current_boss="QueenBug", current_boss_phase=1))
        self.assertEqual(second_connection.close_calls, 1)
        self.assertEqual(client._configuration_version, initial_version + 2)

    def test_entering_boss_battle_preserves_a_healthy_connection(self):
        class FakeConnection:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        client = EclipticaSyncClient()
        client.update_local_state(local_snapshot(current_boss="-", current_boss_phase=None))
        initial_version = client._configuration_version
        connection = FakeConnection()
        client._connection = connection
        client._connected = True

        client.update_local_state(local_snapshot(current_boss="JimBringer", current_boss_phase=1))

        self.assertEqual(connection.close_calls, 0)
        self.assertTrue(client.snapshot()["connected"])
        self.assertEqual(client._configuration_version, initial_version)

    def test_unresponsive_connection_is_closed_for_automatic_retry(self):
        class WebSocketTimeoutException(Exception):
            pass

        class UnresponsiveConnection:
            def send(self, _payload):
                pass

            def recv(self):
                raise WebSocketTimeoutException()

        client = EclipticaSyncClient()
        client.configure(True, "ws://sync.example.test/ws")
        client.update_local_state(local_snapshot())
        connection = UnresponsiveConnection()

        with patch("ecliptica_sync.time.monotonic", side_effect=(0.0, 31.0)):
            with self.assertRaisesRegex(ConnectionError, "长时间未响应"):
                client._connection_loop(
                    connection,
                    client._configuration_version,
                    sync_identity(client._local_state),
                )


if __name__ == "__main__":
    unittest.main()
