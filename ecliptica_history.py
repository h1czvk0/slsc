import queue
import threading
from datetime import datetime
from tkinter import BOTH, CENTER, END, LEFT, RIGHT, VERTICAL, W, X, Y, StringVar, Toplevel
from tkinter import ttk
from urllib.parse import quote, urlparse, urlunparse


try:
    import requests
except ImportError:  # pragma: no cover - application dependency status path
    requests = None


class HistoryApiError(RuntimeError):
    pass


def history_api_base(sync_url: str) -> str:
    parsed = urlparse(str(sync_url or "").strip())
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme.lower())
    if not scheme or not parsed.netloc:
        raise ValueError("invalid WebSocket sync URL")
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))


def format_history_number(value) -> str:
    try:
        return f"{max(0, int(value)):,}"
    except (TypeError, ValueError):
        return "0"


def format_history_time(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


class EclipticaHistoryClient:
    def __init__(self, sync_url, request_session=None, timeout_seconds=8.0):
        self.base_url = history_api_base(sync_url)
        self.session = request_session or (requests.Session() if requests is not None else None)
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    def _get(self, path, params=None):
        if self.session is None:
            raise HistoryApiError("未安装 requests，无法查询历史对局")
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise HistoryApiError(f"历史接口请求失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise HistoryApiError("历史接口返回格式无效")
        return payload

    def search_players(self, username, limit=50):
        name = str(username or "").strip()
        if not name:
            raise HistoryApiError("请输入 VRC 用户名")
        payload = self._get(
            "/api/players/search",
            params={"username": name, "limit": max(1, min(50, int(limit)))},
        )
        players = payload.get("players", [])
        return players if isinstance(players, list) else []

    def player_sessions(self, vrc_user_id, limit=100, offset=0):
        player_id = str(vrc_user_id or "").strip()
        if not player_id.startswith("usr_"):
            raise HistoryApiError("VRC 用户 ID 无效")
        payload = self._get(
            f"/api/players/{quote(player_id, safe='')}/sessions",
            params={
                "limit": max(1, min(100, int(limit))),
                "offset": max(0, int(offset)),
            },
        )
        sessions = payload.get("sessions", [])
        return sessions if isinstance(sessions, list) else []

    def session_details(self, game_session_id):
        session_id = str(game_session_id or "").strip()
        if not session_id:
            raise HistoryApiError("历史对局 ID 无效")
        return self._get(f"/api/sessions/{quote(session_id, safe='')}")


class EclipticaHistoryDialog:
    def __init__(self, parent, api_client, initial_username="", initial_user_id=""):
        self.parent = parent
        self.api_client = api_client
        self.window = Toplevel(parent)
        self.window.title("Ecliptica 历史对局")
        self.window.geometry("1180x760")
        self.window.minsize(900, 600)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.search_var = StringVar(master=self.window, value=str(initial_username or ""))
        self.status_var = StringVar(master=self.window, value="可按 VRC 用户名搜索历史记录")
        self._closed = False
        self._request_tokens = {}
        self._result_queue = queue.Queue()
        self._poll_after_id = None
        self._session_rows = {}
        self._build_ui()
        self._poll_after_id = self.window.after(50, self._poll_results)
        if str(initial_user_id or "").startswith("usr_"):
            self.window.after(
                100,
                lambda: self._load_sessions(str(initial_user_id), str(initial_username or "")),
            )

    @property
    def is_open(self):
        return not self._closed and bool(self.window.winfo_exists())

    def show(self):
        if self.is_open:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def close(self):
        self._closed = True
        if self._poll_after_id is not None:
            try:
                self.window.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        try:
            self.window.destroy()
        except Exception:
            pass

    def _build_ui(self):
        search_frame = ttk.LabelFrame(self.window, text="查找玩家", padding=8)
        search_frame.pack(fill=X, padx=10, pady=(10, 5))
        ttk.Label(search_frame, text="VRC 用户名：").pack(side=LEFT)
        entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        entry.pack(side=LEFT, fill=X, expand=True)
        entry.bind("<Return>", lambda _event: self._search_players())
        ttk.Button(search_frame, text="搜索", command=self._search_players, width=10).pack(
            side=LEFT, padx=(8, 0)
        )

        player_frame = ttk.LabelFrame(self.window, text="玩家", padding=6)
        player_frame.pack(fill=X, padx=10, pady=5)
        player_columns = ("Username", "UserId", "Sessions", "LastSeen")
        self.player_tree = ttk.Treeview(
            player_frame,
            columns=player_columns,
            show="headings",
            height=5,
        )
        player_labels = {
            "Username": "VRC 用户名",
            "UserId": "VRC 用户 ID",
            "Sessions": "对局数",
            "LastSeen": "最后记录时间",
        }
        player_widths = {"Username": 200, "UserId": 360, "Sessions": 80, "LastSeen": 170}
        for column in player_columns:
            self.player_tree.heading(column, text=player_labels[column])
            self.player_tree.column(
                column,
                width=player_widths[column],
                anchor=W if column in ("Username", "UserId") else CENTER,
            )
        self.player_tree.pack(side=LEFT, fill=X, expand=True)
        player_scroll = ttk.Scrollbar(player_frame, orient=VERTICAL, command=self.player_tree.yview)
        player_scroll.pack(side=RIGHT, fill=Y)
        self.player_tree.configure(yscrollcommand=player_scroll.set)
        self.player_tree.bind("<Double-1>", lambda _event: self._load_selected_player())

        session_frame = ttk.LabelFrame(self.window, text="该玩家的历史对局", padding=6)
        session_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        session_columns = (
            "Time",
            "Session",
            "Status",
            "Stage",
            "Class",
            "Boss",
            "SettledDamage",
            "DamageTaken",
            "Defeated",
        )
        self.session_tree = ttk.Treeview(
            session_frame,
            columns=session_columns,
            show="headings",
            height=11,
        )
        session_labels = {
            "Time": "开始时间",
            "Session": "会话 ID",
            "Status": "状态",
            "Stage": "阶段",
            "Class": "职业",
            "Boss": "最后 BOSS",
            "SettledDamage": "BOSS 结算总伤害",
            "DamageTaken": "受到伤害",
            "Defeated": "击败 BOSS",
        }
        session_widths = {
            "Time": 145,
            "Session": 110,
            "Status": 70,
            "Stage": 100,
            "Class": 110,
            "Boss": 120,
            "SettledDamage": 125,
            "DamageTaken": 100,
            "Defeated": 85,
        }
        for column in session_columns:
            self.session_tree.heading(column, text=session_labels[column])
            self.session_tree.column(column, width=session_widths[column], anchor=CENTER)
        self.session_tree.pack(side=LEFT, fill=BOTH, expand=True)
        session_scroll = ttk.Scrollbar(
            session_frame,
            orient=VERTICAL,
            command=self.session_tree.yview,
        )
        session_scroll.pack(side=RIGHT, fill=Y)
        self.session_tree.configure(yscrollcommand=session_scroll.set)
        self.session_tree.bind("<Double-1>", lambda _event: self._load_selected_session())

        detail_frame = ttk.LabelFrame(self.window, text="选中对局内使用程序的玩家", padding=6)
        detail_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        detail_columns = (
            "Username",
            "UserId",
            "Class",
            "Boss",
            "SettledDamage",
            "DamageTaken",
            "Defeated",
        )
        self.detail_tree = ttk.Treeview(
            detail_frame,
            columns=detail_columns,
            show="headings",
            height=7,
        )
        detail_labels = {
            "Username": "VRC 用户名",
            "UserId": "VRC 用户 ID",
            "Class": "职业",
            "Boss": "最后 BOSS",
            "SettledDamage": "BOSS 结算总伤害",
            "DamageTaken": "受到伤害",
            "Defeated": "击败 BOSS",
        }
        detail_widths = {
            "Username": 180,
            "UserId": 310,
            "Class": 110,
            "Boss": 120,
            "SettledDamage": 125,
            "DamageTaken": 100,
            "Defeated": 85,
        }
        for column in detail_columns:
            self.detail_tree.heading(column, text=detail_labels[column])
            self.detail_tree.column(
                column,
                width=detail_widths[column],
                anchor=W if column in ("Username", "UserId") else CENTER,
            )
        self.detail_tree.pack(side=LEFT, fill=BOTH, expand=True)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=VERTICAL, command=self.detail_tree.yview)
        detail_scroll.pack(side=RIGHT, fill=Y)
        self.detail_tree.configure(yscrollcommand=detail_scroll.set)

        action_frame = ttk.Frame(self.window, padding=(10, 4, 10, 10))
        action_frame.pack(fill=X)
        ttk.Label(action_frame, textvariable=self.status_var, foreground="#555555").pack(
            side=LEFT, fill=X, expand=True
        )
        ttk.Button(
            action_frame,
            text="查看选中玩家",
            command=self._load_selected_player,
        ).pack(side=RIGHT)
        ttk.Button(
            action_frame,
            text="查看选中对局所有用户",
            command=self._load_selected_session,
        ).pack(side=RIGHT, padx=(0, 8))

    def _run_request(self, key, function, on_success):
        token = object()
        self._request_tokens[key] = token

        def worker():
            try:
                result = function()
                error = None
            except Exception as exc:
                result = None
                error = str(exc)

            self._result_queue.put((key, token, result, error, on_success))

        threading.Thread(target=worker, name=f"history-{key}", daemon=True).start()

    def _poll_results(self):
        self._poll_after_id = None
        if self._closed:
            return
        try:
            while True:
                key, token, result, error, on_success = self._result_queue.get_nowait()
                if self._request_tokens.get(key) is not token:
                    continue
                if error:
                    self.status_var.set(error)
                else:
                    on_success(result)
        except queue.Empty:
            pass
        self._poll_after_id = self.window.after(50, self._poll_results)

    def _search_players(self):
        username = self.search_var.get().strip()
        if not username:
            self.status_var.set("请输入 VRC 用户名")
            return
        self.status_var.set("正在搜索玩家…")
        self._run_request(
            "players",
            lambda: self.api_client.search_players(username),
            self._show_players,
        )

    def _show_players(self, players):
        for item in self.player_tree.get_children():
            self.player_tree.delete(item)
        count = 0
        for player in players:
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("vrc_user_id") or "")
            if not player_id.startswith("usr_"):
                continue
            self.player_tree.insert(
                "",
                END,
                iid=player_id,
                values=(
                    player.get("current_vrc_username") or player.get("vrc_username") or "-",
                    player_id,
                    player.get("session_count", 0),
                    format_history_time(player.get("last_seen_at")),
                ),
            )
            count += 1
        self.status_var.set(f"找到 {count} 名玩家；双击玩家查看历史对局")

    def _load_selected_player(self):
        selection = self.player_tree.selection()
        if not selection:
            self.status_var.set("请先选择玩家")
            return
        player_id = selection[0]
        values = self.player_tree.item(player_id, "values")
        self._load_sessions(player_id, values[0] if values else "")

    def _load_sessions(self, player_id, player_name=""):
        self.status_var.set(f"正在读取 {player_name or player_id} 的历史对局…")
        self._run_request(
            "sessions",
            lambda: self.api_client.player_sessions(player_id),
            lambda sessions: self._show_sessions(player_name or player_id, sessions),
        )

    def _show_sessions(self, player_name, sessions):
        for item in self.session_tree.get_children():
            self.session_tree.delete(item)
        self._session_rows.clear()
        count = 0
        for index, session in enumerate(sessions):
            if not isinstance(session, dict):
                continue
            game_session_id = str(session.get("game_session_id") or session.get("id") or "")
            if not game_session_id:
                continue
            row_id = f"history-{index}"
            self._session_rows[row_id] = game_session_id
            self.session_tree.insert(
                "",
                END,
                iid=row_id,
                values=(
                    format_history_time(session.get("started_at")),
                    session.get("external_session_id") or "-",
                    session.get("status") or "-",
                    session.get("stage") or "-",
                    session.get("class_name") or "-",
                    session.get("boss_name") or "-",
                    format_history_number(session.get("session_total_damage")),
                    format_history_number(session.get("damage_taken")),
                    session.get("defeated_count", 0),
                ),
            )
            count += 1
        self.status_var.set(f"{player_name}：共 {count} 局；双击对局查看所有用户")

    def _load_selected_session(self):
        selection = self.session_tree.selection()
        if not selection:
            self.status_var.set("请先选择历史对局")
            return
        game_session_id = self._session_rows.get(selection[0])
        if not game_session_id:
            self.status_var.set("历史对局 ID 无效")
            return
        self.status_var.set("正在读取对局内所有用户数据…")
        self._run_request(
            "details",
            lambda: self.api_client.session_details(game_session_id),
            self._show_session_details,
        )

    def _show_session_details(self, details):
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        players = details.get("players", []) if isinstance(details, dict) else []
        count = 0
        for player in players:
            if not isinstance(player, dict):
                continue
            self.detail_tree.insert(
                "",
                END,
                values=(
                    player.get("vrc_username") or "-",
                    player.get("vrc_user_id") or "-",
                    player.get("class_name") or "-",
                    player.get("boss_name") or "-",
                    format_history_number(player.get("session_total_damage")),
                    format_history_number(player.get("damage_taken")),
                    player.get("defeated_count", 0),
                ),
            )
            count += 1
        self.status_var.set(f"该对局共有 {count} 名使用程序的玩家记录")
