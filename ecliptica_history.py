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
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        number = 0.0
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(int(round(number)))


def format_history_duration(value) -> str:
    try:
        total_seconds = max(0, int(round(float(value or 0))))
    except (TypeError, ValueError, OverflowError):
        total_seconds = 0
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}分{seconds:02d}秒"
    return f"{seconds}秒"


def format_history_time(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def format_history_phase(value) -> str:
    try:
        phase = float(value)
    except (TypeError, ValueError, OverflowError):
        return "-"
    return str(int(phase)) if phase.is_integer() else f"{phase:g}"


def order_history_settlements(rows) -> list[dict]:
    valid_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    boss_latest = {}
    for row in valid_rows:
        boss = str(row.get("boss") or "-")
        try:
            settled_at = max(0, int(row.get("settled_at_ms") or 0))
        except (TypeError, ValueError, OverflowError):
            settled_at = 0
        boss_latest[boss] = max(boss_latest.get(boss, 0), settled_at)

    def sort_key(row):
        boss = str(row.get("boss") or "-")
        try:
            phase = float(row.get("phase"))
        except (TypeError, ValueError, OverflowError):
            phase = -1.0
        try:
            settled_at = max(0, int(row.get("settled_at_ms") or 0))
        except (TypeError, ValueError, OverflowError):
            settled_at = 0
        return (
            -boss_latest.get(boss, 0),
            boss.casefold(),
            -phase,
            str(row.get("vrc_username") or "").casefold(),
            -settled_at,
        )

    return sorted(valid_rows, key=sort_key)


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

    def sessions(self, limit=100, offset=0):
        payload = self._get(
            "/api/sessions",
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
    def __init__(self, parent, api_client):
        self.parent = parent
        self.api_client = api_client
        self.window = Toplevel(parent)
        self.window.title("Ecliptica 历史对局")
        self.window.geometry("1120x720")
        self.window.minsize(850, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.status_var = StringVar(master=self.window, value="正在读取历史对局…")
        self.detail_title_var = StringVar(master=self.window, value="选中对局的 BOSS 伤害结算")
        self._closed = False
        self._request_tokens = {}
        self._result_queue = queue.Queue()
        self._poll_after_id = None
        self._session_rows = {}
        self._build_ui()
        self._poll_after_id = self.window.after(50, self._poll_results)
        self.window.after(100, self._load_sessions)

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
        session_frame = ttk.LabelFrame(self.window, text="历史对局", padding=6)
        session_frame.pack(fill=BOTH, expand=True, padx=10, pady=(10, 5))
        session_columns = ("Time", "Session", "Players", "Status")
        self.session_tree = ttk.Treeview(
            session_frame,
            columns=session_columns,
            show="headings",
            height=10,
        )
        labels = {
            "Time": "时间",
            "Session": "对局",
            "Players": "同一对局的玩家",
            "Status": "状态",
        }
        widths = {"Time": 170, "Session": 150, "Players": 560, "Status": 80}
        for column in session_columns:
            self.session_tree.heading(column, text=labels[column])
            self.session_tree.column(
                column,
                width=widths[column],
                anchor=W if column == "Players" else CENTER,
            )
        self.session_tree.pack(side=LEFT, fill=BOTH, expand=True)
        session_scroll = ttk.Scrollbar(
            session_frame,
            orient=VERTICAL,
            command=self.session_tree.yview,
        )
        session_scroll.pack(side=RIGHT, fill=Y)
        self.session_tree.configure(yscrollcommand=session_scroll.set)
        self.session_tree.bind("<Double-1>", lambda _event: self._load_selected_session())

        detail_frame = ttk.LabelFrame(
            self.window,
            textvariable=self.detail_title_var,
            padding=6,
        )
        detail_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        detail_columns = (
            "Username",
            "Boss",
            "Phase",
            "Strike",
            "NonStrike",
            "Total",
            "Duration",
            "DPS",
        )
        self.detail_tree = ttk.Treeview(
            detail_frame,
            columns=detail_columns,
            show="headings",
            height=12,
        )
        detail_labels = {
            "Username": "玩家名称",
            "Boss": "BOSS",
            "Phase": "阶段",
            "Strike": "直击",
            "NonStrike": "非直击",
            "Total": "总伤害",
            "Duration": "耗时",
            "DPS": "DPS",
        }
        detail_widths = {
            "Username": 170,
            "Boss": 150,
            "Phase": 60,
            "Strike": 100,
            "NonStrike": 100,
            "Total": 100,
            "Duration": 100,
            "DPS": 80,
        }
        for column in detail_columns:
            self.detail_tree.heading(column, text=detail_labels[column])
            self.detail_tree.column(
                column,
                width=detail_widths[column],
                anchor=W if column == "Username" else CENTER,
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
        ttk.Button(action_frame, text="刷新对局", command=self._load_sessions).pack(side=RIGHT)
        ttk.Button(
            action_frame,
            text="查看选中对局",
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

    def _load_sessions(self):
        self.status_var.set("正在读取历史对局…")
        self._run_request("sessions", self.api_client.sessions, self._show_sessions)

    def _show_sessions(self, sessions):
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
            player_names = session.get("player_names")
            if not isinstance(player_names, (list, tuple)):
                player_names = []
            names_text = "、".join(str(name) for name in player_names if str(name).strip()) or "-"
            row_id = f"history-{index}"
            self._session_rows[row_id] = {
                "id": game_session_id,
                "session": str(session.get("external_session_id") or "-"),
            }
            self.session_tree.insert(
                "",
                END,
                iid=row_id,
                values=(
                    format_history_time(session.get("started_at")),
                    session.get("external_session_id") or "-",
                    names_text,
                    session.get("status") or "-",
                ),
            )
            count += 1
        self.status_var.set(f"共 {count} 局；双击对局查看所有玩家的 BOSS 伤害结算")

    def _load_selected_session(self):
        selection = self.session_tree.selection()
        if not selection:
            self.status_var.set("请先选择历史对局")
            return
        session = self._session_rows.get(selection[0])
        if not session:
            self.status_var.set("历史对局 ID 无效")
            return
        self.status_var.set("正在读取该对局的 BOSS 伤害结算…")
        self._run_request(
            "details",
            lambda: self.api_client.session_details(session["id"]),
            lambda details: self._show_session_details(session["session"], details),
        )

    def _show_session_details(self, session_name, details):
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        settlements = details.get("settlements", []) if isinstance(details, dict) else []
        ordered = order_history_settlements(settlements)
        for settlement in ordered:
            self.detail_tree.insert(
                "",
                END,
                values=(
                    settlement.get("vrc_username") or "-",
                    settlement.get("boss") or "-",
                    format_history_phase(settlement.get("phase")),
                    format_history_number(settlement.get("strike")),
                    format_history_number(settlement.get("non_strike")),
                    format_history_number(settlement.get("total")),
                    format_history_duration(settlement.get("duration")),
                    f"{float(settlement.get('dps') or 0):.1f}",
                ),
            )
        self.detail_title_var.set(f"对局 {session_name} · BOSS 伤害结算")
        self.status_var.set(f"该对局共有 {len(ordered)} 条玩家阶段结算记录")
