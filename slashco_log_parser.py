import re
from dataclasses import dataclass


PATTERNS = {
    "map_landing": re.compile(r"Selected landing spot on map\s+(.+)", re.IGNORECASE),
    "map_slashco": re.compile(r"Logging all doors for map\s+(.+)", re.IGNORECASE),
    "fuel_base": re.compile(r"For a game of \d+ players, (\d+) will be spawned", re.IGNORECASE),
    "fuel_extra": re.compile(r"(\d+) extra fuel cans will appear in sealed rooms", re.IGNORECASE),
    "item_outside": re.compile(r"(\d+) items will spawn outside sealed rooms", re.IGNORECASE),
    "item_inside": re.compile(r"(\d+) items will spawn INside sealed rooms", re.IGNORECASE),
    "item_collision": re.compile(
        r"\((SC_?Item\d+)\) collided with:\s+(.+?)\s+\(UnityEngine\.GameObject\)",
        re.IGNORECASE,
    ),
    "fuel": re.compile(r"Gas fueled to (SC_generator\d+)", re.IGNORECASE),
    "battery_progress": re.compile(
        r"(SC_generator\d+)\s+Progress check\..*updated\s+HAS_BATTERY\s+value:\s*(True|False)",
        re.IGNORECASE,
    ),
    "battery_fixing": re.compile(
        r"Battery for\s+(SC_generator\d+)\s+improperly set\.\s+FIXING NOW\.",
        re.IGNORECASE,
    ),
    "battery_skillcheck_failed": re.compile(r"Generator Battery skillcheck failed", re.IGNORECASE),
    "item": re.compile(r"Assigning item (SC_?Item\d+) as:\s+(.+)", re.IGNORECASE),
    "game_end": re.compile(
        r"(SLASHCO Game Master End\.|SLASHCO Client STOP GAME|Returning to Lobby|Match Ended|"
        r"All players extracted|All players died)",
        re.IGNORECASE,
    ),
    "game_setup": re.compile(r"SLASHCO Game setup", re.IGNORECASE),
    "map_spawns": re.compile(r"Getting Map Spawnpoints", re.IGNORECASE),
    "map_flags": re.compile(r"Establishing Map Flags", re.IGNORECASE),
    "slashco_loading": re.compile(r"SLASHCO now loading data", re.IGNORECASE),
    "player_headstart": re.compile(
        r"Players in-game:\s*(\d+).*?(\d+)\s+fuel will be given for free",
        re.IGNORECASE,
    ),
    "rooms_sealed": re.compile(r"(\d+)\s+Rooms will be SEALED", re.IGNORECASE),
}

ROUND_START_KEYS = ("map_landing", "game_setup", "map_spawns", "map_flags", "slashco_loading")


@dataclass(frozen=True)
class LogEvent:
    kind: str
    groups: tuple[str, ...] = ()


def normalize_item_id(raw_id: str) -> str:
    raw_id = raw_id.strip()
    match = re.match(r"SC_?Item(\d+)", raw_id, re.IGNORECASE)
    if not match:
        return raw_id
    return f"SC_Item{match.group(1)}"


def item_numeric_id(item_id: str) -> int:
    match = re.match(r"SC_Item(\d+)", item_id, re.IGNORECASE)
    if not match:
        return -1
    try:
        return int(match.group(1))
    except Exception:
        return -1


def is_round_start_line(line: str) -> bool:
    return any(PATTERNS[key].search(line) for key in ROUND_START_KEYS)


def is_round_end_line(line: str) -> bool:
    if PATTERNS["game_end"].search(line):
        return True
    map_match = PATTERNS["map_slashco"].search(line)
    return bool(map_match and "lobby" in map_match.group(1).lower())


def line_might_affect_state(line: str) -> bool:
    return any(pattern.search(line) for pattern in PATTERNS.values())


def parse_log_line(line: str):
    line = line.strip()
    if not line:
        return None

    ordered_patterns = (
        "item",
        "item_collision",
        "fuel_base",
        "fuel_extra",
        "item_outside",
        "item_inside",
        "map_landing",
        "map_slashco",
        "fuel",
        "battery_progress",
        "battery_skillcheck_failed",
        "battery_fixing",
        "game_end",
        "player_headstart",
        "rooms_sealed",
        "slashco_loading",
    )
    for key in ordered_patterns:
        match = PATTERNS[key].search(line)
        if match:
            return LogEvent(key, match.groups())
    return None
