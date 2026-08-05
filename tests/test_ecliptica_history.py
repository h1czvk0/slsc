import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_history import (  # noqa: E402
    EclipticaHistoryClient,
    HistoryApiError,
    format_history_number,
    history_api_base,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if url.endswith("/api/players/search"):
            return FakeResponse(
                {
                    "players": [
                        {
                            "vrc_user_id": "usr_alice",
                            "current_vrc_username": "Alice",
                            "session_count": 2,
                        }
                    ]
                }
            )
        if url.endswith("/api/players/usr_alice/sessions"):
            return FakeResponse(
                {
                    "sessions": [
                        {
                            "game_session_id": "game-1",
                            "session_total_damage": 5200,
                        }
                    ]
                }
            )
        if url.endswith("/api/sessions/game-1"):
            return FakeResponse(
                {
                    "id": "game-1",
                    "players": [
                        {
                            "vrc_user_id": "usr_alice",
                            "session_total_damage": 5200,
                        },
                        {
                            "vrc_user_id": "usr_bob",
                            "session_total_damage": 4300,
                        },
                    ],
                }
            )
        return FakeResponse({}, status_code=404)


class HistoryClientTests(unittest.TestCase):
    def test_websocket_url_maps_to_public_history_api(self):
        self.assertEqual(
            history_api_base("ws://zzu2.wch1.top:44976/ws"),
            "http://zzu2.wch1.top:44976",
        )
        self.assertEqual(
            history_api_base("wss://sync.example.com/ws?token=secret"),
            "https://sync.example.com",
        )

    def test_search_history_and_session_details_use_expected_endpoints(self):
        session = FakeSession()
        client = EclipticaHistoryClient(
            "ws://zzu2.wch1.top:44976/ws",
            request_session=session,
        )

        players = client.search_players("Alice")
        sessions = client.player_sessions("usr_alice")
        details = client.session_details("game-1")

        self.assertEqual(players[0]["vrc_user_id"], "usr_alice")
        self.assertEqual(sessions[0]["session_total_damage"], 5200)
        self.assertEqual(len(details["players"]), 2)
        self.assertEqual(
            [call[0] for call in session.calls],
            [
                "http://zzu2.wch1.top:44976/api/players/search",
                "http://zzu2.wch1.top:44976/api/players/usr_alice/sessions",
                "http://zzu2.wch1.top:44976/api/sessions/game-1",
            ],
        )

    def test_invalid_player_and_empty_search_are_rejected_locally(self):
        client = EclipticaHistoryClient(
            "ws://zzu2.wch1.top:44976/ws",
            request_session=FakeSession(),
        )

        with self.assertRaises(HistoryApiError):
            client.search_players(" ")
        with self.assertRaises(HistoryApiError):
            client.player_sessions("Alice")

    def test_history_damage_format_uses_settlement_total(self):
        self.assertEqual(format_history_number(1234567), "1,234,567")
        self.assertEqual(format_history_number(None), "0")


if __name__ == "__main__":
    unittest.main()
