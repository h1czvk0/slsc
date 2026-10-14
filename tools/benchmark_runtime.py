"""Repeatable CPU microbenchmarks without network traffic or visible windows."""

import argparse
import json
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from slashco import EclipticaDesktopHud, SlashCoMonitorCN
from ecliptica_log_parser import EclipticaState
from backrooms_bridge import BackroomsState, BackroomsLogReader


class Widget:
    def __init__(self):
        self.value = ""
        self.writes = 0

    def set(self, value):
        self.value = value
        self.writes += 1

    def get(self):
        return self.value

    def configure(self, **kwargs):
        self.writes += 1

    def winfo_exists(self):
        return True

    def winfo_width(self):
        return 600

    def winfo_height(self):
        return 400


class Tree:
    def __init__(self):
        self.rows = []
        self.insertions = 0

    def get_children(self):
        return tuple(range(len(self.rows)))

    def delete(self, *items):
        self.rows = []

    def insert(self, *args, values, **kwargs):
        self.rows.append(values)
        self.insertions += 1


def run(log_path, repeats=3):
    lines = pathlib.Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()

    def filtering():
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        return {"lines": len(lines), "relevant": sum(monitor._line_might_affect_state(line) for line in lines)}

    def party_render():
        hud = EclipticaDesktopHud(object())
        hud.party_window = Widget()
        hud.party_canvas = Widget()
        hud.layout = {"party_damage": {"width": 600, "height": 400}}
        hud.party_rows = [("Alice", "12.3K"), ("测试玩家", "9.8K"), ("Charlie", "5.1K")]
        hud.party_total_damage = 27200
        uploads = []
        hud._render_layered_image = lambda *args: uploads.append(True)
        for _ in range(150):
            hud._render_party_text()
        return {"requests": 150, "renders": len(uploads)}

    def settlement_refresh():
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.ecliptica_state = EclipticaState()
        monitor.ecliptica_state.settlements = [
            dict(boss=f"Boss{i}", phase=1, strike=1000, non_strike=500,
                 total=1500, duration=50, dps=30.0) for i in range(20)
        ]
        monitor.ecliptica_vars = {key: Widget() for key in (
            "session", "class", "stage", "boss", "phase", "boss_damage", "total_damage",
            "dps", "damage_taken", "defeated", "aggro",
        )}
        monitor.lbl_ecliptica_aggro = Widget()
        monitor.ecliptica_settlement_tree = Tree()
        monitor._update_ecliptica_auto_jump_context = lambda *args: None
        monitor._publish_ecliptica_osc = lambda *args: None
        for _ in range(200):
            monitor._update_ecliptica_ui()
        return {"requests": 200, "insertions": monitor.ecliptica_settlement_tree.insertions,
                "variable_writes": sum(var.writes for var in monitor.ecliptica_vars.values())}

    def idle_log_poll():
        reader = BackroomsLogReader(BackroomsState())
        while True:
            reader.poll(log_path)
            if not reader.recovering:
                break
        started = time.perf_counter()
        for _ in range(500):
            reader.poll(log_path)
        return {"polls": 500, "idle_ms": round((time.perf_counter() - started) * 1000, 3)}

    report = {}
    for name, scenario in (("log_filter", filtering), ("party_render", party_render),
                           ("settlement_refresh", settlement_refresh), ("idle_log_poll", idle_log_poll)):
        durations = []
        for _ in range(repeats):
            started = time.perf_counter()
            details = scenario()
            durations.append((time.perf_counter() - started) * 1000)
        report[name] = dict(details, median_ms=round(statistics.median(durations), 3))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--output")
    arguments = parser.parse_args()
    result = run(arguments.log)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if arguments.output:
        pathlib.Path(arguments.output).write_text(output + "\n", encoding="utf-8")
