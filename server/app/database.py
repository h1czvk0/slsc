import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from .rooms import RoomSnapshot


MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"


def _timestamp(value):
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


class PostgresStore:
    def __init__(self, database_url: str, stale_session_seconds=600.0):
        self.database_url = database_url
        self.stale_session_seconds = max(60.0, float(stale_session_seconds))
        self.pool = None

    async def start(self):
        self.pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=10,
            command_timeout=10,
        )
        migration = MIGRATION_PATH.read_text(encoding="utf-8")
        async with self.pool.acquire() as connection:
            await connection.execute(migration)
            await connection.execute(
                """
                UPDATE game_sessions
                SET status = 'abandoned', ended_at = last_activity_at
                WHERE ended_at IS NULL
                  AND last_activity_at < NOW() - ($1::DOUBLE PRECISION * INTERVAL '1 second')
                """,
                self.stale_session_seconds,
            )

    async def close(self):
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def ping(self):
        if self.pool is None:
            return False
        async with self.pool.acquire() as connection:
            return await connection.fetchval("SELECT 1") == 1

    async def open_session(self, external_session_id: str) -> str:
        session_uuid = uuid.uuid4()
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO game_sessions (id, external_session_id, status)
                VALUES ($1, $2, 'running')
                ON CONFLICT (external_session_id) WHERE ended_at IS NULL
                DO UPDATE SET last_activity_at = NOW(), status = 'running'
                RETURNING id
                """,
                session_uuid,
                external_session_id,
            )
        return str(row["id"])

    async def persist_room(self, snapshot: RoomSnapshot):
        session_uuid = uuid.UUID(snapshot.database_id)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE game_sessions
                    SET last_activity_at = NOW()
                    WHERE id = $1
                    """,
                    session_uuid,
                )
                for player in snapshot.players:
                    player_uuid = uuid.uuid4()
                    player_row = await connection.fetchrow(
                        """
                        INSERT INTO players (
                            id, vrc_user_id, current_vrc_username, first_seen_at, last_seen_at
                        )
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (vrc_user_id) DO UPDATE
                        SET current_vrc_username = EXCLUDED.current_vrc_username,
                            last_seen_at = GREATEST(players.last_seen_at, EXCLUDED.last_seen_at)
                        RETURNING id
                        """,
                        player_uuid,
                        player.identity.vrc_user_id,
                        player.identity.vrc_username,
                        _timestamp(player.joined_at),
                        _timestamp(player.updated_at),
                    )
                    player_uuid = player_row["id"]
                    await connection.execute(
                        """
                        INSERT INTO player_names (player_id, vrc_username, first_seen_at, last_seen_at)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (player_id, vrc_username) DO UPDATE
                        SET last_seen_at = GREATEST(player_names.last_seen_at, EXCLUDED.last_seen_at)
                        """,
                        player_uuid,
                        player.identity.vrc_username,
                        _timestamp(player.joined_at),
                        _timestamp(player.updated_at),
                    )
                    await connection.execute(
                        """
                        INSERT INTO game_player_results (
                            game_session_id, player_id, vrc_username,
                            world, stage, class_name, boss_name, boss_phase,
                            boss_damage, session_total_damage, damage_taken,
                            defeated_count, intermission, last_sequence,
                            joined_at, last_seen_at, left_at
                        )
                        VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8,
                            $9, $10, $11, $12, $13, $14, $15, $16, $17
                        )
                        ON CONFLICT (game_session_id, player_id) DO UPDATE
                        SET vrc_username = EXCLUDED.vrc_username,
                            world = EXCLUDED.world,
                            stage = EXCLUDED.stage,
                            class_name = EXCLUDED.class_name,
                            boss_name = EXCLUDED.boss_name,
                            boss_phase = EXCLUDED.boss_phase,
                            boss_damage = EXCLUDED.boss_damage,
                            session_total_damage = EXCLUDED.session_total_damage,
                            damage_taken = EXCLUDED.damage_taken,
                            defeated_count = EXCLUDED.defeated_count,
                            intermission = EXCLUDED.intermission,
                            last_sequence = EXCLUDED.last_sequence,
                            last_seen_at = EXCLUDED.last_seen_at,
                            left_at = EXCLUDED.left_at
                        """,
                        session_uuid,
                        player_uuid,
                        player.identity.vrc_username,
                        player.game.world,
                        player.game.stage,
                        player.game.class_name,
                        player.game.boss_name,
                        player.game.boss_phase,
                        player.game.boss_damage,
                        player.game.session_total_damage,
                        player.game.damage_taken,
                        player.game.defeated_count,
                        player.game.intermission,
                        player.last_sequence,
                        _timestamp(player.joined_at),
                        _timestamp(player.updated_at),
                        _timestamp(player.left_at),
                    )

    async def finish_room(self, snapshot: RoomSnapshot):
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE game_sessions
                SET status = 'completed', ended_at = $2, last_activity_at = $2
                WHERE id = $1 AND ended_at IS NULL
                """,
                uuid.UUID(snapshot.database_id),
                _timestamp(snapshot.ended_at),
            )

    async def search_players(self, username: str, limit: int):
        search = username.strip()
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT p.id, p.vrc_user_id, p.current_vrc_username,
                       p.first_seen_at, p.last_seen_at,
                       COUNT(DISTINCT r.game_session_id)::INT AS session_count
                FROM players p
                LEFT JOIN game_player_results r ON r.player_id = p.id
                WHERE p.current_vrc_username ILIKE '%' || $1 || '%'
                   OR EXISTS (
                       SELECT 1 FROM player_names n
                       WHERE n.player_id = p.id AND n.vrc_username ILIKE '%' || $1 || '%'
                   )
                GROUP BY p.id
                ORDER BY
                    CASE WHEN LOWER(p.current_vrc_username) = LOWER($1) THEN 0 ELSE 1 END,
                    p.last_seen_at DESC
                LIMIT $2
                """,
                search,
                limit,
            )
        return [dict(row) for row in rows]

    async def player_sessions(self, vrc_user_id: str, limit: int, offset: int):
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT s.id AS game_session_id, s.external_session_id,
                       s.started_at, s.ended_at, s.status,
                       r.vrc_username, r.world, r.stage, r.class_name,
                       r.boss_name, r.boss_phase, r.boss_damage,
                       r.session_total_damage, r.damage_taken,
                       r.defeated_count, r.joined_at, r.left_at
                FROM players p
                JOIN game_player_results r ON r.player_id = p.id
                JOIN game_sessions s ON s.id = r.game_session_id
                WHERE p.vrc_user_id = $1
                ORDER BY s.started_at DESC
                LIMIT $2 OFFSET $3
                """,
                vrc_user_id,
                limit,
                offset,
            )
        return [dict(row) for row in rows]

    async def session_details(self, session_id: str):
        try:
            session_uuid = uuid.UUID(session_id)
        except ValueError:
            return None
        async with self.pool.acquire() as connection:
            session = await connection.fetchrow(
                """
                SELECT id, external_session_id, status, started_at, ended_at, last_activity_at
                FROM game_sessions WHERE id = $1
                """,
                session_uuid,
            )
            if session is None:
                return None
            players = await connection.fetch(
                """
                SELECT p.vrc_user_id, r.vrc_username, r.world, r.stage, r.class_name,
                       r.boss_name, r.boss_phase, r.boss_damage,
                       r.session_total_damage, r.damage_taken, r.defeated_count,
                       r.joined_at, r.left_at
                FROM game_player_results r
                JOIN players p ON p.id = r.player_id
                WHERE r.game_session_id = $1
                ORDER BY r.session_total_damage DESC
                """,
                session_uuid,
            )
        result = dict(session)
        result["players"] = [dict(player) for player in players]
        return result
