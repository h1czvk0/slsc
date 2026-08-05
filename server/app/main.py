import asyncio
import hmac
import json
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .config import Settings
from .database import PostgresStore
from .protocol import DamageUpdate, JoinMessage, MAX_MESSAGE_BYTES, PingMessage
from .rooms import RoomFullError, RoomManager, RoomNotJoinedError


class UpdateRateLimiter:
    def __init__(self, limit=30, window_seconds=1.0):
        self.limit = int(limit)
        self.window_seconds = float(window_seconds)
        self.events = deque()

    def allow(self):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        while self.events and self.events[0] < cutoff:
            self.events.popleft()
        if len(self.events) >= self.limit:
            return False
        self.events.append(now)
        return True


def create_app(settings=None, store=None):
    settings = settings or Settings.from_env()
    store = store or PostgresStore(
        settings.database_url,
        stale_session_seconds=settings.empty_room_ttl_seconds * 2,
    )
    manager = RoomManager(
        store,
        persist_interval=settings.persist_interval_seconds,
        empty_room_ttl=settings.empty_room_ttl_seconds,
    )

    @asynccontextmanager
    async def lifespan(application):
        if hasattr(store, "start"):
            await store.start()
        await manager.start()
        application.state.store = store
        application.state.room_manager = manager
        try:
            yield
        finally:
            await manager.stop()
            if hasattr(store, "close"):
                await store.close()

    application = FastAPI(
        title="SlashCoSense Ecliptica Sync",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=settings.allowed_origins != ("*",),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @application.get("/health")
    async def health():
        try:
            database_ok = await store.ping()
        except Exception:
            database_ok = False
        if not database_ok:
            raise HTTPException(status_code=503, detail="database unavailable")
        return {"status": "ok", "active_rooms": await manager.room_count()}

    @application.get("/api/players/search")
    async def search_players(
        username: str = Query(min_length=1, max_length=128),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return {"players": await store.search_players(username, limit)}

    @application.get("/api/players/{vrc_user_id}/sessions")
    async def player_sessions(
        vrc_user_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
    ):
        if not vrc_user_id.startswith("usr_") or len(vrc_user_id) > 64:
            raise HTTPException(status_code=400, detail="invalid VRC user ID")
        return {
            "vrc_user_id": vrc_user_id,
            "sessions": await store.player_sessions(vrc_user_id, limit, offset),
        }

    @application.get("/api/sessions/{session_id}")
    async def session_details(session_id: str):
        result = await store.session_details(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="session not found")
        return result

    @application.websocket("/ws")
    async def websocket_sync(websocket: WebSocket):
        if settings.sync_api_key:
            supplied_token = websocket.query_params.get("token", "")
            if not hmac.compare_digest(supplied_token, settings.sync_api_key):
                await websocket.close(code=4401, reason="invalid sync token")
                return

        await websocket.accept()
        session_id = ""
        player_id = ""
        connection_id = ""
        limiter = UpdateRateLimiter()
        try:
            raw_join = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            if len(raw_join.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await websocket.close(code=4409, reason="message too large")
                return
            join = JoinMessage.model_validate_json(raw_join)
            session_id = join.session_id
            player_id = join.player.vrc_user_id
            connection_id = await manager.join(session_id, join.player, websocket)

            while True:
                raw_message = await websocket.receive_text()
                if len(raw_message.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await websocket.close(code=4409, reason="message too large")
                    return
                try:
                    envelope = json.loads(raw_message)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "code": "invalid_json", "message": "消息不是有效 JSON"}
                    )
                    continue

                message_type = envelope.get("type") if isinstance(envelope, dict) else None
                if message_type == "ping":
                    ping = PingMessage.model_validate(envelope)
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "protocol_version": 1,
                            "client_timestamp_ms": ping.client_timestamp_ms,
                            "server_timestamp_ms": int(time.time() * 1000),
                        }
                    )
                    continue
                if message_type != "damage_update":
                    await websocket.send_json(
                        {"type": "error", "code": "unknown_type", "message": "未知消息类型"}
                    )
                    continue
                if not limiter.allow():
                    await websocket.close(code=4429, reason="update rate exceeded")
                    return

                update = DamageUpdate.model_validate(envelope)
                if update.session_id != session_id or update.player.vrc_user_id != player_id:
                    await websocket.close(code=4403, reason="session or player identity changed")
                    return
                await manager.update(connection_id, update)
        except asyncio.TimeoutError:
            await websocket.close(code=4408, reason="join timeout")
        except ValidationError as exc:
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "validation_error",
                        "message": exc.errors(include_url=False)[0]["msg"],
                    }
                )
                await websocket.close(code=4400, reason="invalid protocol message")
            except Exception:
                pass
        except RoomFullError as exc:
            await websocket.send_json({"type": "error", "code": "room_full", "message": str(exc)})
            await websocket.close(code=4403, reason="room full")
        except RoomNotJoinedError:
            await websocket.close(code=4401, reason="connection replaced or room closed")
        except WebSocketDisconnect:
            pass
        finally:
            if connection_id:
                await manager.leave(session_id, player_id, connection_id)

    return application


app = create_app()
