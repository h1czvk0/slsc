import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import WebSocket

from .protocol import DamageUpdate, GameState, MAX_ROOM_PLAYERS, PlayerIdentity


class RoomFullError(Exception):
    pass


class RoomNotJoinedError(Exception):
    pass


@dataclass
class PlayerState:
    identity: PlayerIdentity
    game: GameState = field(default_factory=GameState)
    online: bool = True
    joined_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    left_at: float | None = None
    last_sequence: int = 0

    def wire_dict(self):
        return {
            "vrc_user_id": self.identity.vrc_user_id,
            "vrc_username": self.identity.vrc_username,
            "boss_name": self.game.boss_name,
            "boss_phase": self.game.boss_phase,
            "boss_damage": self.game.boss_damage,
            "session_total_damage": self.game.session_total_damage,
            "online": self.online,
            "updated_at_ms": int(self.updated_at * 1000),
        }


@dataclass
class Connection:
    connection_id: str
    websocket: WebSocket
    player_id: str
    last_sequence: int = 0


@dataclass
class RoomSnapshot:
    database_id: str
    session_id: str
    players: list[PlayerState]
    started_at: float
    ended_at: float | None = None


@dataclass
class Room:
    database_id: str
    session_id: str
    started_at: float = field(default_factory=time.time)
    players: dict[str, PlayerState] = field(default_factory=dict)
    connections: dict[str, Connection] = field(default_factory=dict)
    server_sequence: int = 0
    dirty: bool = True
    empty_since: float | None = None

    def state_message(self):
        players = sorted(
            (player.wire_dict() for player in self.players.values()),
            key=lambda player: (-player["boss_damage"], player["vrc_username"].casefold()),
        )
        return {
            "type": "room_state",
            "protocol_version": 1,
            "session_id": self.session_id,
            "server_sequence": self.server_sequence,
            "server_timestamp_ms": int(time.time() * 1000),
            "players": players[:MAX_ROOM_PLAYERS],
        }

    def snapshot(self, ended_at=None):
        return RoomSnapshot(
            database_id=self.database_id,
            session_id=self.session_id,
            players=[
                PlayerState(
                    identity=player.identity.model_copy(deep=True),
                    game=player.game.model_copy(deep=True),
                    online=player.online,
                    joined_at=player.joined_at,
                    updated_at=player.updated_at,
                    left_at=player.left_at,
                    last_sequence=player.last_sequence,
                )
                for player in self.players.values()
            ],
            started_at=self.started_at,
            ended_at=ended_at,
        )


class RoomStore(Protocol):
    async def open_session(self, external_session_id: str) -> str: ...

    async def persist_room(self, snapshot: RoomSnapshot): ...

    async def finish_room(self, snapshot: RoomSnapshot): ...


class RoomManager:
    def __init__(self, store: RoomStore, persist_interval=2.0, empty_room_ttl=300.0):
        self.store = store
        self.persist_interval = max(0.2, float(persist_interval))
        self.empty_room_ttl = max(1.0, float(empty_room_ttl))
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._worker = None

    async def start(self):
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        self._worker = asyncio.create_task(self._persistence_loop(), name="room-persistence")

    async def stop(self):
        self._stop_event.set()
        worker = self._worker
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        await self.flush_dirty()
        self._worker = None

    async def join(self, session_id: str, player: PlayerIdentity, websocket: WebSocket):
        old_connection = None
        async with self._lock:
            room = self._rooms.get(session_id)
            if room is None:
                database_id = await self.store.open_session(session_id)
                room = Room(database_id=database_id, session_id=session_id)
                self._rooms[session_id] = room
            if player.vrc_user_id not in room.players and len(room.players) >= MAX_ROOM_PLAYERS:
                raise RoomFullError(f"会话最多允许 {MAX_ROOM_PLAYERS} 名玩家")

            old_connection = room.connections.get(player.vrc_user_id)
            now = time.time()
            existing = room.players.get(player.vrc_user_id)
            if existing is None:
                existing = PlayerState(identity=player, joined_at=now, updated_at=now)
                room.players[player.vrc_user_id] = existing
            else:
                existing.identity = player
                existing.online = True
                existing.left_at = None
                existing.updated_at = now

            connection = Connection(
                connection_id=uuid.uuid4().hex,
                websocket=websocket,
                player_id=player.vrc_user_id,
            )
            room.connections[player.vrc_user_id] = connection
            room.empty_since = None
            room.server_sequence += 1
            room.dirty = True
            message, targets = room.state_message(), self._targets(room)

        if old_connection is not None:
            try:
                await old_connection.websocket.close(code=4001, reason="new connection replaced old one")
            except Exception:
                pass
        await self._broadcast(message, targets)
        return connection.connection_id

    async def update(self, connection_id: str, update: DamageUpdate) -> bool:
        async with self._lock:
            room = self._rooms.get(update.session_id)
            if room is None:
                raise RoomNotJoinedError("会话不存在")
            connection = room.connections.get(update.player.vrc_user_id)
            if connection is None or connection.connection_id != connection_id:
                raise RoomNotJoinedError("连接已失效")
            if update.sequence <= connection.last_sequence:
                return False

            connection.last_sequence = update.sequence
            player = room.players[update.player.vrc_user_id]
            player.identity = update.player
            player.game = update.game
            player.online = True
            player.updated_at = time.time()
            player.left_at = None
            player.last_sequence = update.sequence
            room.server_sequence += 1
            room.dirty = True
            message, targets = room.state_message(), self._targets(room)

        await self._broadcast(message, targets)
        return True

    async def leave(self, session_id: str, player_id: str, connection_id: str):
        async with self._lock:
            room = self._rooms.get(session_id)
            if room is None:
                return
            connection = room.connections.get(player_id)
            if connection is None or connection.connection_id != connection_id:
                return
            room.connections.pop(player_id, None)
            now = time.time()
            player = room.players.get(player_id)
            if player:
                player.online = False
                player.left_at = now
                player.updated_at = now
            if not room.connections:
                room.empty_since = now
            room.server_sequence += 1
            room.dirty = True
            message, targets = room.state_message(), self._targets(room)

        await self._broadcast(message, targets)

    async def room_count(self):
        async with self._lock:
            return len(self._rooms)

    async def flush_dirty(self):
        async with self._lock:
            dirty_rooms = [room for room in self._rooms.values() if room.dirty]
            for room in dirty_rooms:
                room.dirty = False
            snapshots = [room.snapshot() for room in dirty_rooms]

        failures = []
        for room, snapshot in zip(dirty_rooms, snapshots):
            try:
                await self.store.persist_room(snapshot)
            except Exception as exc:
                async with self._lock:
                    current = self._rooms.get(room.session_id)
                    if current is room:
                        current.dirty = True
                failures.append(exc)
        if failures:
            raise failures[0]

    async def expire_empty_rooms(self):
        now = time.time()
        async with self._lock:
            expired = [
                room
                for room in self._rooms.values()
                if room.empty_since is not None and now - room.empty_since >= self.empty_room_ttl
            ]
            for room in expired:
                snapshot = room.snapshot(ended_at=now)
                await self.store.persist_room(snapshot)
                await self.store.finish_room(snapshot)
                self._rooms.pop(room.session_id, None)

    async def _persistence_loop(self):
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.persist_interval)
            except asyncio.TimeoutError:
                try:
                    await self.flush_dirty()
                    await self.expire_empty_rooms()
                except Exception:
                    # A transient database failure keeps rooms dirty; the next pass retries.
                    continue

    @staticmethod
    def _targets(room: Room):
        return [connection.websocket for connection in room.connections.values()]

    @staticmethod
    async def _broadcast(message, targets):
        if not targets:
            return

        async def send(target):
            try:
                await target.send_json(message)
            except Exception:
                pass

        await asyncio.gather(*(send(target) for target in targets))
