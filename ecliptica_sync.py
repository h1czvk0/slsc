import hashlib
import json
import math
import socket
import threading
import time
from copy import deepcopy
from urllib.parse import urlparse, urlunparse


PROTOCOL_VERSION = 1
DEFAULT_SYNC_INTERVAL_SECONDS = 0.1
DEFAULT_SYNC_URL = "ws://zzu2.wch1.top:44976/ws"
HEARTBEAT_INTERVAL_SECONDS = 10.0
CONNECTION_STALE_TIMEOUT_SECONDS = 30.0


try:
    import websocket
except ImportError:  # pragma: no cover - exercised by the application status path
    websocket = None


def normalize_sync_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"wss://{text}"
    parsed = urlparse(text)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme.lower(), parsed.scheme.lower())
    if scheme not in ("ws", "wss") or not parsed.netloc:
        return ""
    path = parsed.path or "/ws"
    return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))


def _as_non_negative_int(value) -> int:
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_non_negative_float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _build_settlements(snapshot: dict) -> list[dict]:
    rows = snapshot.get("settlements")
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        boss = str(row.get("boss") or "-").strip()[:128] or "-"
        phase = row.get("phase")
        try:
            phase = float(phase) if phase is not None else None
        except (TypeError, ValueError, OverflowError):
            phase = None
        if phase is not None and (not math.isfinite(phase) or phase < 0 or phase > 1000):
            phase = None
        strike = _as_non_negative_int(row.get("strike"))
        non_strike = _as_non_negative_int(row.get("non_strike"))
        total = _as_non_negative_int(row.get("total"))
        duration = min(86_400.0, _as_non_negative_float(row.get("duration")))
        dps = _as_non_negative_float(row.get("dps"))
        settled_at_ms = _as_non_negative_int(_as_non_negative_float(row.get("timestamp")) * 1000)
        identity = "\x1f".join(
            (
                boss,
                "" if phase is None else repr(phase),
                str(strike),
                str(non_strike),
                str(total),
                str(settled_at_ms),
            )
        )
        result.append(
            {
                "settlement_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "boss": boss,
                "phase": phase,
                "strike": strike,
                "non_strike": non_strike,
                "total": total,
                "duration": duration,
                "dps": dps,
                "settled_at_ms": settled_at_ms,
            }
        )
    return result


def sync_identity(snapshot: dict | None):
    state = snapshot if isinstance(snapshot, dict) else {}
    session_id = str(state.get("session_id") or "").strip()
    player_id = str(state.get("local_player_id") or "").strip()
    player_name = str(state.get("local_player_name") or "").strip()
    if (
        not bool(state.get("run_active"))
        or session_id in ("", "-")
        or not player_id.startswith("usr_")
        or not player_name
    ):
        return None
    return session_id, player_id, player_name


def is_boss_battle_active(snapshot: dict | None) -> bool:
    state = snapshot if isinstance(snapshot, dict) else {}
    boss_name = str(state.get("current_boss") or "").strip()
    return (
        bool(state.get("run_active"))
        and not bool(state.get("intermission"))
        and boss_name not in ("", "-")
    )


def build_damage_update(snapshot: dict, sequence: int) -> dict:
    identity = sync_identity(snapshot)
    if identity is None:
        raise ValueError("missing session or VRChat identity")
    session_id, player_id, player_name = identity
    return {
        "type": "damage_update",
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
        "sequence": max(1, int(sequence)),
        "client_timestamp_ms": int(time.time() * 1000),
        "player": {
            "vrc_user_id": player_id,
            "vrc_username": player_name,
        },
        "game": {
            "world": str(snapshot.get("world") or "Ecliptica"),
            "stage": str(snapshot.get("stage") or "-"),
            "class_name": str(snapshot.get("class_name") or "-"),
            "boss_name": str(snapshot.get("current_boss") or "-"),
            "boss_phase": snapshot.get("current_boss_phase"),
            # 实时显示使用战斗期间的全部伤害事件，包含 BOSS 召唤的小怪。
            "boss_damage": _as_non_negative_int(snapshot.get("current_phase_damage")),
            # 历史对局只保存日志中 BOSS 结算条目的累计伤害。
            "session_total_damage": _as_non_negative_int(snapshot.get("session_total_damage")),
            "damage_taken": _as_non_negative_int(snapshot.get("session_damage_taken")),
            "defeated_count": _as_non_negative_int(snapshot.get("defeated_count")),
            "intermission": bool(snapshot.get("intermission", False)),
            "settlements": _build_settlements(snapshot),
        },
    }


def build_join_message(snapshot: dict) -> dict:
    identity = sync_identity(snapshot)
    if identity is None:
        raise ValueError("missing session or VRChat identity")
    session_id, player_id, player_name = identity
    return {
        "type": "join",
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
        "player": {
            "vrc_user_id": player_id,
            "vrc_username": player_name,
        },
    }


def normalize_room_players(message: dict, expected_session_id: str) -> list[dict]:
    if not isinstance(message, dict) or message.get("type") != "room_state":
        return []
    if str(message.get("session_id") or "") != str(expected_session_id or ""):
        return []
    raw_players = message.get("players")
    if not isinstance(raw_players, list):
        return []

    players = []
    seen = set()
    for raw in raw_players:
        if not isinstance(raw, dict):
            continue
        player_id = str(raw.get("vrc_user_id") or "").strip()
        player_name = str(raw.get("vrc_username") or "").strip()
        if not player_id.startswith("usr_") or not player_name or player_id in seen:
            continue
        seen.add(player_id)
        players.append(
            {
                "vrc_user_id": player_id,
                "vrc_username": player_name,
                "boss_name": str(raw.get("boss_name") or "-"),
                "boss_phase": raw.get("boss_phase"),
                "boss_damage": _as_non_negative_int(raw.get("boss_damage")),
                "session_total_damage": _as_non_negative_int(raw.get("session_total_damage")),
                "online": bool(raw.get("online", True)),
                "updated_at_ms": _as_non_negative_int(raw.get("updated_at_ms")),
            }
        )
    players.sort(key=lambda item: (-item["boss_damage"], item["vrc_username"].casefold()))
    return players


class EclipticaSyncClient:
    """Threaded WebSocket client for a single Ecliptica room.

    Tk owns configuration and local snapshots. Network I/O stays on a daemon
    thread, while ``snapshot`` gives Tk an immutable view of remote state.
    """

    def __init__(self, interval_seconds=DEFAULT_SYNC_INTERVAL_SECONDS, websocket_factory=None):
        self.interval_seconds = max(0.05, float(interval_seconds))
        self._websocket_factory = websocket_factory
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread = None
        self._connection = None
        self._enabled = False
        self._url = ""
        self._configuration_version = 0
        self._local_state = {}
        self._players = []
        self._status = "未启用"
        self._connected = False
        self._last_error = ""
        self._server_sequence = 0
        self._client_sequence = 0
        self._last_received_at = None
        self._room_state_messages_to_skip = 0

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="ecliptica-sync",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout=2.0):
        self._stop_event.set()
        self._wake_event.set()
        self._close_connection()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lock:
            self._connected = False
            self._thread = None

    def configure(self, enabled: bool, url: str):
        normalized_url = normalize_sync_url(url)
        with self._lock:
            changed = self._enabled != bool(enabled) or self._url != normalized_url
            self._enabled = bool(enabled)
            self._url = normalized_url
            if changed:
                self._configuration_version += 1
                self._players = []
                self._server_sequence = 0
                self._room_state_messages_to_skip = 0
            if not self._enabled:
                self._set_status_locked("未启用", connected=False)
            elif not self._url:
                self._set_status_locked("服务器地址无效", connected=False)
        if changed:
            self._close_connection()
            self._wake_event.set()

    def update_local_state(self, snapshot: dict | None):
        state = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
        with self._lock:
            previous_identity = sync_identity(self._local_state)
            current_identity = sync_identity(state)
            entered_boss_battle = (
                not is_boss_battle_active(self._local_state)
                and is_boss_battle_active(state)
            )
            self._local_state = state
            reconnect_required = previous_identity != current_identity or entered_boss_battle
            if reconnect_required:
                self._configuration_version += 1
                self._players = []
                self._server_sequence = 0
                self._client_sequence = 0
                self._last_received_at = None
                self._room_state_messages_to_skip = 0
                if entered_boss_battle and current_identity is not None:
                    self._set_status_locked("Boss 战开始，正在重新连接…", connected=False)
        if reconnect_required:
            self._close_connection()
        self._wake_event.set()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "url": self._url,
                "connected": self._connected,
                "status": self._status,
                "last_error": self._last_error,
                "players": deepcopy(self._players),
                "server_sequence": self._server_sequence,
                "last_received_at": self._last_received_at,
                "session_id": str(self._local_state.get("session_id") or "-"),
            }

    def handle_server_message(self, raw_message) -> bool:
        try:
            message = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(message, dict):
            return False

        with self._lock:
            identity = sync_identity(self._local_state)
            expected_session_id = identity[0] if identity else ""
            if message.get("type") == "room_state":
                if str(message.get("session_id") or "") != expected_session_id:
                    return False
                if self._room_state_messages_to_skip > 0:
                    self._room_state_messages_to_skip -= 1
                    return True
                server_sequence = _as_non_negative_int(message.get("server_sequence"))
                if server_sequence and server_sequence < self._server_sequence:
                    return False
                self._players = normalize_room_players(message, expected_session_id)
                self._server_sequence = max(self._server_sequence, server_sequence)
                self._last_received_at = time.time()
                return True
            if message.get("type") == "error":
                detail = str(message.get("message") or "服务器拒绝了请求")
                self._set_status_locked(f"同步失败：{detail}", connected=self._connected, error=detail)
                return True
            return message.get("type") == "pong"

    def _set_status_locked(self, status, connected=False, error=""):
        self._status = str(status)
        self._connected = bool(connected)
        self._last_error = str(error or "")

    def _set_status(self, status, connected=False, error=""):
        with self._lock:
            self._set_status_locked(status, connected=connected, error=error)

    def _close_connection(self):
        with self._lock:
            connection = self._connection
            self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _factory(self):
        if self._websocket_factory is not None:
            return self._websocket_factory
        if websocket is None:
            return None
        return websocket.create_connection

    def _run(self):
        retry_delay = 1.0
        while not self._stop_event.is_set():
            with self._lock:
                enabled = self._enabled
                url = self._url
                state = deepcopy(self._local_state)
                configuration_version = self._configuration_version

            identity = sync_identity(state)
            if not enabled:
                self._set_status("未启用")
                self._wait(0.5)
                continue
            if not url:
                self._set_status("服务器地址无效")
                self._wait(0.5)
                continue
            if identity is None:
                self._set_status("等待日志中的会话与 VRC 身份")
                self._wait(0.2)
                continue
            factory = self._factory()
            if factory is None:
                self._set_status("缺少 websocket-client 依赖", error="websocket-client 未安装")
                self._wait(2.0)
                continue

            self._set_status("正在连接同步服务器…")
            connection = None
            try:
                connection = factory(url, timeout=3.0, enable_multithread=True)
                try:
                    connection.settimeout(0.02)
                except Exception:
                    pass
                with self._lock:
                    self._connection = connection
                    self._set_status_locked("已连接", connected=True)
                retry_delay = 1.0
                self._connection_loop(connection, configuration_version, identity)
            except Exception as exc:
                if not self._stop_event.is_set():
                    detail = str(exc).strip() or exc.__class__.__name__
                    self._set_status(
                        f"连接失败，{int(retry_delay)} 秒后重试",
                        error=detail,
                    )
            finally:
                with self._lock:
                    if self._connection is connection:
                        self._connection = None
                    self._connected = False
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

            if not self._stop_event.is_set():
                self._wait(retry_delay)
                retry_delay = min(10.0, retry_delay * 2.0)

    def _connection_loop(self, connection, configuration_version, identity):
        with self._lock:
            self._room_state_messages_to_skip = 1
        connection.send(json.dumps(build_join_message(self._local_state), ensure_ascii=False))
        last_signature = None
        next_send_at = 0.0
        last_server_message_at = time.monotonic()
        next_heartbeat_at = last_server_message_at + HEARTBEAT_INTERVAL_SECONDS

        while not self._stop_event.is_set():
            with self._lock:
                if (
                    not self._enabled
                    or self._configuration_version != configuration_version
                    or sync_identity(self._local_state) != identity
                ):
                    return
                state = deepcopy(self._local_state)

            now = time.monotonic()
            if now - last_server_message_at >= CONNECTION_STALE_TIMEOUT_SECONDS:
                raise ConnectionError("同步服务器长时间未响应")
            with self._lock:
                next_sequence = self._client_sequence + 1
            payload = build_damage_update(state, next_sequence)
            signature = json.dumps(payload["game"], ensure_ascii=False, sort_keys=True)
            if signature != last_signature and now >= next_send_at:
                with self._lock:
                    self._client_sequence += 1
                    payload["sequence"] = self._client_sequence
                connection.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                last_signature = signature
                next_send_at = now + self.interval_seconds

            if now >= next_heartbeat_at:
                connection.send(
                    json.dumps(
                        {
                            "type": "ping",
                            "protocol_version": PROTOCOL_VERSION,
                            "client_timestamp_ms": int(time.time() * 1000),
                        },
                        separators=(",", ":"),
                    )
                )
                next_heartbeat_at = now + HEARTBEAT_INTERVAL_SECONDS

            try:
                incoming = connection.recv()
                if incoming is None or incoming == "":
                    raise ConnectionError("同步连接已关闭")
                last_server_message_at = time.monotonic()
                with self._lock:
                    self._last_received_at = time.time()
                self.handle_server_message(incoming)
            except Exception as exc:
                if self._is_receive_timeout(exc):
                    pass
                else:
                    raise
            self._wait(0.01)

    @staticmethod
    def _is_receive_timeout(exc):
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        return exc.__class__.__name__ in ("WebSocketTimeoutException", "TimeoutError")

    def _wait(self, seconds):
        self._wake_event.wait(max(0.0, float(seconds)))
        self._wake_event.clear()
