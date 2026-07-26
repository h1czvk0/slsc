import re
import time
from dataclasses import dataclass
from datetime import datetime


ROOM_PATTERN = re.compile(r"\[Behaviour\]\s+Entering Room:\s+(.+?)\s*$", re.IGNORECASE)
PATTERNS = {
    "session": re.compile(r"ECLIPTICA\s+(?:loaded|saving)\s+SESSION ID\s+(\d+)", re.IGNORECASE),
    "session_blank": re.compile(r"ECLIPTICA\s+loaded blank session ID", re.IGNORECASE),
    "stage": re.compile(
        r"ECLIPTICA\s+-\s+now in stage:\s+(.+?)\s+on phase:\s+([0-9.]+)\s+as class:\s+(.+?)\s*$",
        re.IGNORECASE,
    ),
    "boss": re.compile(
        r"ECLIPTICA\s+-\s+now fighting boss:\s+(.+?)\s+on phase:\s+([0-9.]+)\s*$",
        re.IGNORECASE,
    ),
    "boss_dead": re.compile(r"Boss\s+(.+?)\s+dead,\s+personal damage dealt:", re.IGNORECASE),
    "strike_damage": re.compile(r"(?<!NON-)\bSTRIKE DMG:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    "non_strike_damage": re.compile(r"\bNON-STRIKE DMG:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    "damage_taken": re.compile(
        r"damage has been taken:\s*([0-9]+(?:\.[0-9]+)?),\s*from source:\s*(.*?)\s*$",
        re.IGNORECASE,
    ),
    "stage_progress": re.compile(r"Advancing Stage Progress to:\s*([0-9]+)", re.IGNORECASE),
    "intermission": re.compile(r"ECLIPTICA\s+-\s+now in intermission", re.IGNORECASE),
    "lobby": re.compile(r"ECLIPTICA\s+-\s+now in lobby", re.IGNORECASE),
}

TIMESTAMP_PATTERN = re.compile(r"(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})")
PHASE_SUFFIX_PATTERN = re.compile(r"(?:\s*[-_ ]?Phase\s*(\d+))$", re.IGNORECASE)


@dataclass(frozen=True)
class EclipticaEvent:
    kind: str
    groups: tuple[str, ...] = ()
    timestamp: float | None = None


def parse_log_timestamp(line: str):
    match = TIMESTAMP_PATTERN.search(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y.%m.%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def is_ecliptica_room(room_name: str) -> bool:
    return "ecliptica" in str(room_name or "").lower()


def clean_stage_name(raw_name: str) -> str:
    name = str(raw_name or "").strip()
    if name.lower().startswith("stage_"):
        name = name[6:]
    return name.replace("_", " ") or "-"


def split_boss_name(raw_name: str):
    name = re.sub(r"\(Clone\)\s*$", "", str(raw_name or "").strip(), flags=re.IGNORECASE)
    phase_match = PHASE_SUFFIX_PATTERN.search(name)
    phase = int(phase_match.group(1)) if phase_match else 1
    base_name = name[:phase_match.start()].strip() if phase_match else name
    base_name = base_name or name or "Unknown"
    key = re.sub(r"[^a-z0-9]+", "", base_name.lower())
    return base_name, key, phase


def parse_ecliptica_line(line: str):
    text = str(line or "").strip()
    if not text:
        return None
    timestamp = parse_log_timestamp(text)
    room_match = ROOM_PATTERN.search(text)
    if room_match:
        return EclipticaEvent("room_entered", (room_match.group(1).strip(),), timestamp)
    for kind, pattern in PATTERNS.items():
        match = pattern.search(text)
        if match:
            return EclipticaEvent(kind, match.groups(), timestamp)
    return None


def line_might_affect_ecliptica_state(line: str) -> bool:
    return parse_ecliptica_line(line) is not None


class EclipticaState:
    AGGRO_LOCAL_SECONDS = 6.0
    AGGRO_STALE_SECONDS = 15.0
    SETTLEMENT_DEDUP_SECONDS = 90.0

    def __init__(self):
        self.world_name = ""
        self.reset(preserve_world=True)

    def reset(self, preserve_world=False):
        world_name = self.world_name if preserve_world else ""
        self.world_name = world_name
        self.session_id = ""
        self.stage = "-"
        self.stage_progress = None
        self.run_phase = None
        self.class_name = "-"
        self.current_boss = "-"
        self.current_boss_key = ""
        self.current_boss_phase = None
        self.current_boss_started_at = None
        self.current_boss_damage = 0
        self.session_total_damage = 0
        self.session_damage_taken = 0
        self.hit_count = 0
        self.max_hit_taken = 0
        self.defeated_bosses = []
        self._defeated_boss_keys = set()
        self.last_settlement_dps = 0.0
        self.last_settlement_damage = 0
        self.damage_sources = {}
        self.settlements = []
        self.intermission = False
        self._pending_boss_raw = ""
        self._pending_boss_key = ""
        self._pending_boss_name = ""
        self._pending_strike = None
        self._pending_non_strike = None
        self._recent_settlements = {}
        self._settled_phases = set()
        self._aggro_since = None
        self._aggro_last_hit_at = None

    def _event_time(self, event: EclipticaEvent):
        return float(event.timestamp if event.timestamp is not None else time.time())

    def _begin_session(self, session_id: str):
        if self.session_id and self.session_id != session_id:
            world_name = self.world_name
            self.reset(preserve_world=False)
            self.world_name = world_name
        self.session_id = session_id

    def _reset_aggro(self):
        self._aggro_since = None
        self._aggro_last_hit_at = None

    def _finalize_settlement(self, event: EclipticaEvent):
        if self._pending_strike is None or self._pending_non_strike is None:
            return False

        now = self._event_time(event)
        boss_raw = self._pending_boss_raw or self.current_boss
        boss_name, boss_key, boss_phase = split_boss_name(boss_raw)
        strike = int(round(self._pending_strike))
        non_strike = int(round(self._pending_non_strike))
        signature = (boss_raw.lower(), strike, non_strike)
        previous = self._recent_settlements.get(signature)
        duplicate = previous is not None and now - previous <= self.SETTLEMENT_DEDUP_SECONDS
        phase_identity = (boss_key, boss_phase)
        if strike + non_strike == 0 and phase_identity in self._settled_phases:
            duplicate = True
        self._recent_settlements[signature] = now

        self._pending_strike = None
        self._pending_non_strike = None
        self._pending_boss_raw = ""
        self._pending_boss_key = ""
        self._pending_boss_name = ""
        if duplicate:
            return False

        total = strike + non_strike
        self._settled_phases.add(phase_identity)
        duration = 0.0
        if self.current_boss_started_at is not None:
            duration = max(0.0, now - self.current_boss_started_at)
        dps = total / duration if duration > 0 else 0.0
        self.session_total_damage += total
        if boss_key and boss_key == self.current_boss_key:
            self.current_boss_damage += total
        self.last_settlement_damage = total
        self.last_settlement_dps = dps
        self.settlements.insert(
            0,
            {
                "boss": boss_name,
                "phase": boss_phase,
                "strike": strike,
                "non_strike": non_strike,
                "total": total,
                "dps": dps,
                "timestamp": now,
            },
        )
        del self.settlements[20:]
        return True

    def apply(self, event: EclipticaEvent):
        kind = event.kind
        now = self._event_time(event)

        if kind == "room_entered":
            room_name = event.groups[0]
            if is_ecliptica_room(room_name):
                self.world_name = room_name
                return True
            return False

        if kind == "session":
            self._begin_session(event.groups[0])
            return True
        if kind == "session_blank":
            return True
        if kind == "stage":
            self.stage = clean_stage_name(event.groups[0])
            self.run_phase = float(event.groups[1])
            self.class_name = event.groups[2].strip() or "-"
            self.intermission = False
            self.current_boss = "-"
            self.current_boss_key = ""
            self.current_boss_phase = None
            self.current_boss_started_at = None
            self.current_boss_damage = 0
            self._reset_aggro()
            return True
        if kind == "boss":
            boss_name, boss_key, boss_phase = split_boss_name(event.groups[0])
            changed = boss_key != self.current_boss_key or boss_phase != self.current_boss_phase
            if boss_key != self.current_boss_key:
                self.current_boss_damage = 0
            self.current_boss = boss_name
            self.current_boss_key = boss_key
            self.current_boss_phase = boss_phase
            self.run_phase = float(event.groups[1])
            self.intermission = False
            if changed:
                self.current_boss_started_at = now
                self._reset_aggro()
            return True
        if kind == "boss_dead":
            boss_name, boss_key, _boss_phase = split_boss_name(event.groups[0])
            self._pending_boss_raw = event.groups[0].strip()
            self._pending_boss_key = boss_key
            self._pending_boss_name = boss_name
            self._pending_strike = None
            self._pending_non_strike = None
            return True
        if kind == "strike_damage":
            self._pending_strike = float(event.groups[0])
            return self._finalize_settlement(event) or True
        if kind == "non_strike_damage":
            self._pending_non_strike = float(event.groups[0])
            return self._finalize_settlement(event) or True
        if kind == "damage_taken":
            amount = max(0, int(round(float(event.groups[0]))))
            source = event.groups[1].strip() or "未知来源"
            self.session_damage_taken += amount
            self.hit_count += 1
            self.max_hit_taken = max(self.max_hit_taken, amount)
            self.damage_sources[source] = self.damage_sources.get(source, 0) + amount
            if self.current_boss_key:
                if self._aggro_last_hit_at is None or now - self._aggro_last_hit_at > self.AGGRO_LOCAL_SECONDS:
                    self._aggro_since = now
                self._aggro_last_hit_at = now
            return True
        if kind == "stage_progress":
            self.stage_progress = int(event.groups[0])
            return True
        if kind in ("intermission", "lobby"):
            if self.current_boss_key and self.current_boss_key not in self._defeated_boss_keys:
                self._defeated_boss_keys.add(self.current_boss_key)
                self.defeated_bosses.append(self.current_boss)
            self.intermission = True
            self.current_boss = "-"
            self.current_boss_key = ""
            self.current_boss_phase = None
            self.current_boss_started_at = None
            self._reset_aggro()
            return True
        return False

    def aggro_snapshot(self, now=None):
        current_time = float(time.time() if now is None else now)
        if not self.current_boss_key:
            return {
                "state": "inactive",
                "target": "-",
                "is_local": False,
                "status": "未在 Boss 战",
                "stale": True,
                "locked_secs": 0,
            }
        if self._aggro_last_hit_at is None:
            age = max(0.0, current_time - (self.current_boss_started_at or current_time))
            return {
                "state": "unknown",
                "target": "某玩家",
                "is_local": False,
                "status": "仇恨中",
                "stale": age > self.AGGRO_STALE_SECONDS,
                "locked_secs": int(age),
            }

        age = max(0.0, current_time - self._aggro_last_hit_at)
        if age <= self.AGGRO_LOCAL_SECONDS:
            since = self._aggro_since if self._aggro_since is not None else self._aggro_last_hit_at
            return {
                "state": "local",
                "target": "你",
                "is_local": True,
                "status": "正在追击你",
                "stale": False,
                "locked_secs": max(0, int(current_time - since)),
            }
        return {
            "state": "other",
            "target": "其他玩家",
            "is_local": False,
            "status": "追击其他玩家",
            "stale": age > self.AGGRO_STALE_SECONDS,
            "locked_secs": int(age),
        }

    def snapshot(self, now=None):
        aggro = self.aggro_snapshot(now)
        return {
            "world": self.world_name or "Ecliptica",
            "session_id": self.session_id or "-",
            "stage": self.stage,
            "stage_progress": self.stage_progress,
            "run_phase": self.run_phase,
            "class_name": self.class_name,
            "current_boss": self.current_boss,
            "current_boss_phase": self.current_boss_phase,
            "current_boss_damage": self.current_boss_damage,
            "session_total_damage": self.session_total_damage,
            "last_settlement_damage": self.last_settlement_damage,
            "last_settlement_dps": self.last_settlement_dps,
            "session_damage_taken": self.session_damage_taken,
            "hit_count": self.hit_count,
            "max_hit_taken": self.max_hit_taken,
            "defeated_count": len(self.defeated_bosses),
            "intermission": self.intermission,
            "aggro": aggro,
        }
