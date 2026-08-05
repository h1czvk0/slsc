import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime


ROOM_PATTERN = re.compile(r"\[Behaviour\]\s+Entering Room:\s+(.+?)\s*$", re.IGNORECASE)
PATTERNS = {
    "authenticated": re.compile(r"User Authenticated:\s*(.+?)\s*\((usr_[^)]+)\)\s*$", re.IGNORECASE),
    "ownership": re.compile(r"ownership of\s+(.+?)\s+transferred to\s+(.+?)\s*$", re.IGNORECASE),
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
    "damage_dealt": re.compile(
        r"\bDealing\s+([0-9]+(?:\.[0-9]+)?)\s+(STRIKE|NON-STRIKE)\s+damage\b",
        re.IGNORECASE,
    ),
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
    AGGRO_STALE_SECONDS = 9.0
    SETTLEMENT_DEDUP_SECONDS = 90.0

    def __init__(self):
        self.world_name = ""
        self.local_player_name = ""
        self.local_player_id = ""
        self.reset(preserve_world=True)

    def reset(self, preserve_world=False):
        world_name = self.world_name if preserve_world else ""
        self.world_name = world_name
        self.session_id = ""
        self.run_active = False
        self.stage = "-"
        self.stage_progress = None
        self.run_phase = None
        self.class_name = "-"
        self.current_boss = "-"
        self.current_boss_key = ""
        self.current_boss_phase = None
        self.current_boss_encounter_id = None
        self.current_boss_started_at = None
        self.current_phase_started_at = None
        self.current_boss_damage = 0
        self.run_started_at = None
        self.run_ended_at = None
        self.session_total_damage = 0
        self.session_damage_taken = 0
        self.hit_count = 0
        self.max_hit_taken = 0
        self.defeated_bosses = []
        self._defeated_boss_encounters = set()
        self.last_settlement_dps = 0.0
        self.last_settlement_damage = 0
        self.damage_sources = {}
        self.settlements = []
        self.intermission = False
        self._pending_boss_raw = ""
        self._pending_boss_key = ""
        self._pending_boss_name = ""
        self._pending_encounter_id = None
        self._pending_strike = None
        self._pending_non_strike = None
        self._recent_settlements = {}
        self._settled_phases = set()
        self._next_boss_encounter_id = 1
        self._latest_encounter_by_boss_key = {}
        self._boss_phase_started_at = {}
        self._phase_damage_totals = {}
        self._phase_damage_taken_totals = {}
        self._recent_damage_events = deque()
        self._aggro_since = None
        self._aggro_updated_at = None
        self._aggro_target_player = ""
        self._aggro_state = "unknown"

    def _event_time(self, event: EclipticaEvent):
        return float(event.timestamp if event.timestamp is not None else time.time())

    def _begin_session(self, session_id: str):
        if self.session_id and self.session_id != session_id:
            world_name = self.world_name
            self.reset(preserve_world=False)
            self.world_name = world_name
        self.session_id = session_id

    def _begin_run(self):
        if self.run_active:
            return
        session_id = self.session_id
        self.reset(preserve_world=True)
        self.session_id = session_id
        self.run_active = True

    def _reset_aggro(self):
        self._aggro_since = None
        self._aggro_updated_at = None
        self._aggro_target_player = ""
        self._aggro_state = "unknown"

    def _finalize_settlement(self, event: EclipticaEvent):
        if self._pending_strike is None or self._pending_non_strike is None:
            return False

        now = self._event_time(event)
        boss_raw = self._pending_boss_raw or self.current_boss
        boss_name, boss_key, boss_phase = split_boss_name(boss_raw)
        encounter_id = self._pending_encounter_id
        if encounter_id is None:
            encounter_id = self._latest_encounter_by_boss_key.get(boss_key)
        if encounter_id is None:
            encounter_id = self.current_boss_encounter_id
        strike = int(round(self._pending_strike))
        non_strike = int(round(self._pending_non_strike))
        signature = (encounter_id, boss_raw.lower(), strike, non_strike)
        previous = self._recent_settlements.get(signature)
        duplicate = previous is not None and now - previous <= self.SETTLEMENT_DEDUP_SECONDS
        phase_identity = (encounter_id, boss_phase)
        if strike + non_strike == 0 and phase_identity in self._settled_phases:
            duplicate = True
        self._recent_settlements[signature] = now

        self._pending_strike = None
        self._pending_non_strike = None
        self._pending_boss_raw = ""
        self._pending_boss_key = ""
        self._pending_boss_name = ""
        self._pending_encounter_id = None
        if duplicate:
            return False

        total = strike + non_strike
        self._settled_phases.add(phase_identity)
        self._phase_damage_totals[phase_identity] = total
        started_at = self._boss_phase_started_at.get(phase_identity)
        if started_at is None and phase_identity == (
            self.current_boss_encounter_id,
            self.current_boss_phase,
        ):
            started_at = self.current_phase_started_at
        duration = max(0.0, now - started_at) if started_at is not None else 0.0
        dps = total / duration if duration > 0 else 0.0
        self.session_total_damage += total
        if encounter_id is not None and encounter_id == self.current_boss_encounter_id:
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
                "duration": duration,
                "dps": dps,
                "timestamp": now,
            },
        )
        del self.settlements[20:]
        return True

    def apply(self, event: EclipticaEvent):
        kind = event.kind
        now = self._event_time(event)

        if kind == "authenticated":
            self.local_player_name = event.groups[0].strip()
            self.local_player_id = event.groups[1].strip()
            return True
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
            self.reset(preserve_world=True)
            return True
        if kind == "stage":
            self._begin_run()
            if self.run_started_at is None or self.run_ended_at is not None:
                self.run_started_at = now
                self.run_ended_at = None
            self.stage = clean_stage_name(event.groups[0])
            self.run_phase = float(event.groups[1])
            self.class_name = event.groups[2].strip() or "-"
            self.intermission = False
            self.current_boss = "-"
            self.current_boss_key = ""
            self.current_boss_phase = None
            self.current_boss_encounter_id = None
            self.current_boss_started_at = None
            self.current_phase_started_at = None
            self.current_boss_damage = 0
            self._reset_aggro()
            return True
        if kind == "boss":
            self._begin_run()
            boss_name, boss_key, boss_phase = split_boss_name(event.groups[0])
            if self.run_started_at is None or self.run_ended_at is not None:
                self.run_started_at = now
                self.run_ended_at = None
            new_encounter = self.current_boss_encounter_id is None or boss_key != self.current_boss_key
            phase_changed = new_encounter or boss_phase != self.current_boss_phase
            if new_encounter:
                self.current_boss_encounter_id = self._next_boss_encounter_id
                self._next_boss_encounter_id += 1
                self._latest_encounter_by_boss_key[boss_key] = self.current_boss_encounter_id
                self.current_boss_started_at = now
                self.current_boss_damage = 0
            self.current_boss = boss_name
            self.current_boss_key = boss_key
            self.current_boss_phase = boss_phase
            self.run_phase = float(event.groups[1])
            self.intermission = False
            if phase_changed:
                self.current_phase_started_at = now
                phase_identity = (self.current_boss_encounter_id, boss_phase)
                self._boss_phase_started_at.setdefault(phase_identity, now)
                self._phase_damage_totals.setdefault(phase_identity, 0)
                self._phase_damage_taken_totals.setdefault(phase_identity, 0)
                self._reset_aggro()
            return True
        if kind == "boss_dead":
            boss_name, boss_key, _boss_phase = split_boss_name(event.groups[0])
            self._pending_boss_raw = event.groups[0].strip()
            self._pending_boss_key = boss_key
            self._pending_boss_name = boss_name
            self._pending_encounter_id = self._latest_encounter_by_boss_key.get(
                boss_key,
                self.current_boss_encounter_id,
            )
            self._pending_strike = None
            self._pending_non_strike = None
            return True
        if kind == "ownership":
            _owner_boss_name, owner_boss_key, owner_boss_phase = split_boss_name(event.groups[0])
            if owner_boss_key != self.current_boss_key or owner_boss_phase != self.current_boss_phase:
                return False

            target_player = event.groups[1].strip()
            if not target_player:
                return False
            target_changed = target_player.casefold() != self._aggro_target_player.casefold()
            if target_changed or self._aggro_since is None:
                self._aggro_since = now
            self._aggro_updated_at = now
            self._aggro_target_player = target_player
            if not self.local_player_name:
                self._aggro_state = "unknown"
            elif target_player.casefold() == self.local_player_name.strip().casefold():
                self._aggro_state = "local"
            else:
                self._aggro_state = "other"
            return True
        if kind == "damage_dealt":
            amount = max(0, int(round(float(event.groups[0]))))
            self._recent_damage_events.append((now, amount))
            if self.current_boss_encounter_id is not None:
                phase_identity = (self.current_boss_encounter_id, self.current_boss_phase)
                self._phase_damage_totals[phase_identity] = (
                    self._phase_damage_totals.get(phase_identity, 0) + amount
                )
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
            if self.current_boss_encounter_id is not None:
                phase_identity = (self.current_boss_encounter_id, self.current_boss_phase)
                self._phase_damage_taken_totals[phase_identity] = (
                    self._phase_damage_taken_totals.get(phase_identity, 0) + amount
                )
            return True
        if kind == "stage_progress":
            self.stage_progress = int(event.groups[0])
            return True
        if kind in ("intermission", "lobby"):
            encounter_id = self.current_boss_encounter_id
            if encounter_id is not None and encounter_id not in self._defeated_boss_encounters:
                self._defeated_boss_encounters.add(encounter_id)
                self.defeated_bosses.append(self.current_boss)
            if kind == "lobby":
                if self.run_started_at is not None:
                    self.run_ended_at = now
                self.run_active = False
            self.intermission = True
            self.current_boss = "-"
            self.current_boss_key = ""
            self.current_boss_phase = None
            self.current_boss_encounter_id = None
            self.current_boss_started_at = None
            self.current_phase_started_at = None
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
        if not self._aggro_target_player or self._aggro_updated_at is None:
            age = max(0.0, current_time - (self.current_boss_started_at or current_time))
            return {
                "state": "unknown",
                "target": "-",
                "is_local": False,
                "status": "等待锁定目标",
                "stale": age > self.AGGRO_STALE_SECONDS,
                "locked_secs": int(age),
            }

        updated_age = max(0.0, current_time - self._aggro_updated_at)
        since = self._aggro_since if self._aggro_since is not None else self._aggro_updated_at
        is_local = self._aggro_state == "local"
        return {
            "state": self._aggro_state,
            "target": self._aggro_target_player,
            "is_local": is_local,
            "status": "正在追击你" if is_local else ("追击其他玩家" if self._aggro_state == "other" else "仇恨中"),
            "stale": updated_age >= self.AGGRO_STALE_SECONDS,
            "locked_secs": max(0, int(current_time - since)),
        }

    def snapshot(self, now=None):
        current_time = float(time.time() if now is None else now)
        aggro = self.aggro_snapshot(current_time)
        phase_identity = (self.current_boss_encounter_id, self.current_boss_phase)
        phase_damage = (
            self._phase_damage_totals.get(phase_identity, 0)
            if self.current_boss_encounter_id is not None
            else 0
        )
        phase_damage_taken = (
            self._phase_damage_taken_totals.get(phase_identity, 0)
            if self.current_boss_encounter_id is not None
            else 0
        )
        current_phase_is_settled = phase_identity in self._settled_phases
        unsettled_phase_damage = 0 if current_phase_is_settled else phase_damage
        live_boss_damage = self.current_boss_damage + unsettled_phase_damage
        live_session_damage = self.session_total_damage + unsettled_phase_damage
        cutoff = current_time - 3.0
        while self._recent_damage_events and self._recent_damage_events[0][0] < cutoff:
            self._recent_damage_events.popleft()
        recent_dps = sum(amount for _timestamp, amount in self._recent_damage_events) / 3.0
        boss_elapsed = (
            max(0.0, current_time - self.current_boss_started_at)
            if self.current_boss_encounter_id is not None and self.current_boss_started_at is not None
            else 0.0
        )
        phase_elapsed = (
            max(0.0, current_time - self.current_phase_started_at)
            if self.current_boss_encounter_id is not None and self.current_phase_started_at is not None
            else 0.0
        )
        run_end = self.run_ended_at if self.run_ended_at is not None else current_time
        total_elapsed = (
            max(0.0, run_end - self.run_started_at)
            if self.run_started_at is not None
            else 0.0
        )
        return {
            "world": self.world_name or "Ecliptica",
            "session_id": self.session_id or "-",
            "run_active": self.run_active,
            "local_player_name": self.local_player_name,
            "local_player_id": self.local_player_id,
            "stage": self.stage,
            "stage_progress": self.stage_progress,
            "run_phase": self.run_phase,
            "class_name": self.class_name,
            "current_boss": self.current_boss,
            "current_boss_phase": self.current_boss_phase,
            "current_boss_damage": self.current_boss_damage,
            "live_current_boss_damage": live_boss_damage,
            "current_phase_damage": phase_damage,
            "current_phase_damage_taken": phase_damage_taken,
            "recent_3s_dps": recent_dps,
            "recent_5s_dps": recent_dps,
            "current_phase_elapsed": phase_elapsed,
            "current_boss_elapsed": boss_elapsed,
            "total_elapsed": total_elapsed,
            "session_total_damage": self.session_total_damage,
            "live_session_total_damage": live_session_damage,
            "last_settlement_damage": self.last_settlement_damage,
            "last_settlement_dps": self.last_settlement_dps,
            "session_damage_taken": self.session_damage_taken,
            "hit_count": self.hit_count,
            "max_hit_taken": self.max_hit_taken,
            "defeated_count": len(self.defeated_bosses),
            "intermission": self.intermission,
            "aggro": aggro,
            "settlements": [dict(settlement) for settlement in self.settlements],
        }
