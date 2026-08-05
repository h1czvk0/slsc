import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_history import (  # noqa: E402
    EclipticaHistoryClient,
    HistoryApiError,
    format_history_duration,
    format_history_number,
    history_api_base,
    order_history_settlements,
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
        if url.endswith("/api/sessions"):
            return FakeResponse(
                {
                    "sessions": [
                        {
                            "game_session_id": "game-1",
                            "external_session_id": "284719",
                            "player_names": ["Alice", "Bob"],
                        }
                    ]
                }
            )
        if url.endswith("/api/sessions/game-1"):
            return FakeResponse(
                {
                    "id": "game-1",
                    "settlements": [
                        {
                            "vrc_username": "Alice",
                            "boss": "JimBringer",
                            "phase": 2,
                            "total": 5200,
                        },
                        {
                            "vrc_username": "Bob",
                            "boss": "JimBringer",
                            "phase": 2,
                            "total": 4300,
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

    def test_session_list_and_details_use_expected_endpoints(self):
        session = FakeSession()
        client = EclipticaHistoryClient(
            "ws://zzu2.wch1.top:44976/ws",
            request_session=session,
        )

        sessions = client.sessions()
        details = client.session_details("game-1")

        self.assertEqual(sessions[0]["player_names"], ["Alice", "Bob"])
        self.assertEqual(len(details["settlements"]), 2)
        self.assertEqual(
            [call[0] for call in session.calls],
            [
                "http://zzu2.wch1.top:44976/api/sessions",
                "http://zzu2.wch1.top:44976/api/sessions/game-1",
            ],
        )

    def test_empty_session_id_is_rejected_locally(self):
        client = EclipticaHistoryClient(
            "ws://zzu2.wch1.top:44976/ws",
            request_session=FakeSession(),
        )

        with self.assertRaises(HistoryApiError):
            client.session_details(" ")

    def test_history_format_matches_live_boss_settlement_table(self):
        self.assertEqual(format_history_number(82_900), "82.9K")
        self.assertEqual(format_history_number(1_234_567), "1.23M")
        self.assertEqual(format_history_duration(898), "14分58秒")

    def test_rows_are_grouped_by_boss_phase_then_player(self):
        rows = [
            {"vrc_username": "Bob", "boss": "Jim", "phase": 1, "settled_at_ms": 100},
            {"vrc_username": "Alice", "boss": "Jim", "phase": 2, "settled_at_ms": 200},
            {"vrc_username": "Alice", "boss": "Jim", "phase": 1, "settled_at_ms": 100},
            {"vrc_username": "Bob", "boss": "Jim", "phase": 2, "settled_at_ms": 200},
        ]

        ordered = order_history_settlements(rows)

        self.assertEqual(
            [(row["vrc_username"], row["phase"]) for row in ordered],
            [("Alice", 2), ("Bob", 2), ("Alice", 1), ("Bob", 1)],
        )


if __name__ == "__main__":
    unittest.main()
