import unittest
import uuid

from fastapi.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app


class ApiMemoryStore:
    def __init__(self):
        self.session_id = str(uuid.uuid4())

    async def start(self):
        pass

    async def close(self):
        pass

    async def ping(self):
        return True

    async def open_session(self, _external_session_id):
        return self.session_id

    async def persist_room(self, _snapshot):
        pass

    async def finish_room(self, _snapshot):
        pass

    async def search_players(self, username, limit):
        return [
            {
                "vrc_user_id": "usr_alice",
                "current_vrc_username": username,
                "session_count": min(3, limit),
            }
        ]

    async def player_sessions(self, vrc_user_id, _limit, _offset):
        return [{"game_session_id": self.session_id, "vrc_user_id": vrc_user_id}]

    async def session_details(self, session_id):
        if session_id != self.session_id:
            return None
        return {"id": session_id, "players": []}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.store = ApiMemoryStore()
        settings = Settings(
            database_url="unused",
            sync_api_key="test-secret",
            persist_interval_seconds=60,
            empty_room_ttl_seconds=300,
            allowed_origins=("*",),
        )
        self.client = TestClient(create_app(settings=settings, store=self.store))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_health_and_public_history_queries(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        search = self.client.get("/api/players/search", params={"username": "Alice"})
        self.assertEqual(search.json()["players"][0]["current_vrc_username"], "Alice")
        history = self.client.get("/api/players/usr_alice/sessions")
        self.assertEqual(history.json()["sessions"][0]["vrc_user_id"], "usr_alice")

    def test_websocket_requires_token_and_returns_room_state(self):
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/ws"):
                pass

        with self.client.websocket_connect("/ws?token=test-secret") as websocket:
            websocket.send_json(
                {
                    "type": "join",
                    "protocol_version": 1,
                    "session_id": "12345",
                    "player": {
                        "vrc_user_id": "usr_alice",
                        "vrc_username": "Alice",
                    },
                }
            )
            joined = websocket.receive_json()
            self.assertEqual(joined["type"], "room_state")
            self.assertEqual(joined["session_id"], "12345")

            websocket.send_json(
                {
                    "type": "damage_update",
                    "protocol_version": 1,
                    "session_id": "12345",
                    "sequence": 1,
                    "client_timestamp_ms": 1000,
                    "player": {
                        "vrc_user_id": "usr_alice",
                        "vrc_username": "Alice",
                    },
                    "game": {
                        "world": "Ecliptica",
                        "stage": "Bringer",
                        "class_name": "Thaumaturge",
                        "boss_name": "JimBringer",
                        "boss_phase": 2,
                        "boss_damage": 4321,
                        "session_total_damage": 7654,
                        "damage_taken": 0,
                        "defeated_count": 1,
                        "intermission": False,
                    },
                }
            )
            state = websocket.receive_json()
            self.assertEqual(state["players"][0]["boss_damage"], 4321)


if __name__ == "__main__":
    unittest.main()
