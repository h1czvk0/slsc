import time
import unittest
import uuid

from server.app.protocol import DamageUpdate, GameState, PlayerIdentity
from server.app.rooms import RoomFullError, RoomManager


class MemoryStore:
    def __init__(self):
        self.opened = {}
        self.persisted = []
        self.finished = []

    async def open_session(self, external_session_id):
        return self.opened.setdefault(external_session_id, str(uuid.uuid4()))

    async def persist_room(self, snapshot):
        self.persisted.append(snapshot)

    async def finish_room(self, snapshot):
        self.finished.append(snapshot)


class FakeWebSocket:
    def __init__(self):
        self.messages = []
        self.closed = []

    async def send_json(self, message):
        self.messages.append(message)

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))


def identity(index):
    return PlayerIdentity(vrc_user_id=f"usr_player-{index}", vrc_username=f"Player {index}")


def update(session_id, player, sequence=1, damage=100):
    return DamageUpdate(
        type="damage_update",
        protocol_version=1,
        session_id=session_id,
        sequence=sequence,
        client_timestamp_ms=int(time.time() * 1000),
        player=player,
        game=GameState(
            world="Ecliptica",
            stage="Bringer",
            class_name="Thaumaturge",
            boss_name="JimBringer",
            boss_phase=2,
            boss_damage=damage,
            session_total_damage=damage + 200,
        ),
    )


class RoomManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.manager = RoomManager(self.store, persist_interval=60, empty_room_ttl=300)

    async def asyncTearDown(self):
        await self.manager.stop()

    async def test_same_session_broadcasts_without_cross_room_interference(self):
        alice, bob, carol = identity(1), identity(2), identity(3)
        alice_socket, bob_socket, carol_socket = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
        alice_connection = await self.manager.join("session-a", alice, alice_socket)
        await self.manager.join("session-a", bob, bob_socket)
        await self.manager.join("session-b", carol, carol_socket)
        carol_message_count = len(carol_socket.messages)

        accepted = await self.manager.update(
            alice_connection,
            update("session-a", alice, damage=900),
        )

        self.assertTrue(accepted)
        self.assertEqual(alice_socket.messages[-1]["players"][0]["boss_damage"], 900)
        self.assertEqual(bob_socket.messages[-1]["players"][0]["vrc_username"], "Player 1")
        self.assertEqual(len(carol_socket.messages), carol_message_count)

    async def test_room_rejects_fifth_unique_player(self):
        for index in range(4):
            await self.manager.join("session-a", identity(index), FakeWebSocket())

        with self.assertRaises(RoomFullError):
            await self.manager.join("session-a", identity(5), FakeWebSocket())

    async def test_old_sequence_is_ignored(self):
        player = identity(1)
        connection = await self.manager.join("session-a", player, FakeWebSocket())

        self.assertTrue(await self.manager.update(connection, update("session-a", player, 5, 500)))
        self.assertFalse(await self.manager.update(connection, update("session-a", player, 4, 900)))

    async def test_reconnect_replaces_old_socket_without_marking_new_connection_offline(self):
        player = identity(1)
        old_socket, new_socket = FakeWebSocket(), FakeWebSocket()
        old_connection = await self.manager.join("session-a", player, old_socket)
        new_connection = await self.manager.join("session-a", player, new_socket)

        await self.manager.leave("session-a", player.vrc_user_id, old_connection)
        self.assertEqual(old_socket.closed[0][0], 4001)
        self.assertTrue(new_socket.messages[-1]["players"][0]["online"])

        await self.manager.leave("session-a", player.vrc_user_id, new_connection)
        await self.manager.flush_dirty()
        self.assertFalse(self.store.persisted[-1].players[0].online)

    async def test_dirty_room_is_persisted_as_per_game_player_result(self):
        player = identity(1)
        connection = await self.manager.join("session-a", player, FakeWebSocket())
        await self.manager.update(connection, update("session-a", player, damage=1234))

        await self.manager.flush_dirty()

        self.assertEqual(len(self.store.persisted), 1)
        snapshot = self.store.persisted[0]
        self.assertEqual(snapshot.session_id, "session-a")
        self.assertEqual(snapshot.players[0].game.boss_damage, 1234)


if __name__ == "__main__":
    unittest.main()
