import pathlib
import socket
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from backrooms_bridge import (
    BackroomsState, BackroomsLogReader, BackroomsOscOutput, BackroomsBridge,
    DEFAULT_BACKROOMS_WORLD_ID, BREAKER_READY, RESONANCE_READY, HEARTBEAT,
    normalize_world_id,
)
from vrchat_log_selector import VrchatLogSelector, VrchatLogCandidate
from slashco import SlashCoMonitorCN


JOIN = f"[Behaviour] Joining {DEFAULT_BACKROOMS_WORLD_ID}:123~region(jp)\n"


def recv(epoch, mask, raw_epoch=None, raw_mask=None):
    return (f"[MEGBreaker] recv epoch={raw_epoch or epoch} mask={raw_mask or mask} "
            f"acceptedEpoch={epoch} acceptedMask={mask}")


def anchor(epoch, mask, action="deserialize", region="area.a_l0_chaos.name"):
    return f"[ObjectiveState] region={region} action={action} epoch={epoch} mask={mask}"


def match(epoch, state=1, level=0):
    return (f"[MM] @@@ OnDeserialization ON player(id=31)  →  state={state}  "
            f"level={level}  participants=[10,31] count=2  seed={epoch} difficulty=2")


def read_string(data, offset):
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8"), (end + 4) & ~3


def decode_bundle(packet):
    assert packet[:16] == b"#bundle\x00" + struct.pack(">Q", 1)
    result = []
    offset = 16
    while offset < len(packet):
        size = struct.unpack_from(">I", packet, offset)[0]
        message = packet[offset + 4:offset + 4 + size]
        address, tag_offset = read_string(message, 0)
        tags, end = read_string(message, tag_offset)
        assert tags in (",T", ",F") and end == len(message)
        result.append((address.removeprefix("/avatar/parameters/"), tags == ",T"))
        offset += 4 + size
    return result


class RecordingSocket:
    def __init__(self, packets):
        self.packets = packets

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def sendto(self, packet, destination):
        self.packets.append((packet, destination))


class BackroomsStateTests(unittest.TestCase):
    def setUp(self):
        self.state = BackroomsState()
        self.state.apply_line(JOIN)

    def test_accepted_fields_win_and_signed_mask_includes_bit_31(self):
        self.state.apply_line(recv(7, -2147479552, raw_epoch=8, raw_mask=0))
        self.assertEqual(self.state.breaker.epoch, 7)
        self.assertEqual(self.state.breaker.completed_mask, 0x80001000)
        self.assertTrue(self.state.breaker.ready)

    def test_owner_accept_is_a_complete_snapshot(self):
        self.state.apply_line("[MEGBreaker] accept epoch=9 idx=26 sender=4 active=67112960 old=4096 new=67112960")
        self.assertEqual(self.state.breaker.completed_mask, (1 << 12) | (1 << 26))

    def test_regression_and_retired_epochs_cannot_restore_old_round(self):
        self.state.apply_line(recv(9, 3))
        self.state.apply_line(recv(9, 1))
        self.assertEqual(self.state.breaker.completed_mask, 3)
        self.state.apply_line(recv(2, 4))  # random epoch, numerically smaller
        self.state.apply_line(recv(9, 7))
        self.assertEqual((self.state.breaker.epoch, self.state.breaker.completed_mask), (2, 4))

    def test_generated_is_unknown_not_zero_completion_confirmation(self):
        self.state.apply_line(recv(9, 3))
        self.state.apply_line("[MEGBreaker] generated need=2 players=2")
        self.assertFalse(self.state.breaker.ready)
        self.state.apply_line(recv(9, 3))
        self.assertFalse(self.state.breaker.ready)
        self.state.apply_line(recv(12, 0))
        self.assertTrue(self.state.breaker.ready)

    def test_only_accepted_actions_and_chaos_region_count(self):
        for line in (
            "[MEGBreaker] reject epoch=5 new=4",
            "[MEGBreaker] ignore-old epoch=5 mask=4",
            "[MEGBreaker] recv epoch=5 mask=4",  # accepted fields missing
            anchor(5, 31, action="serialize"),
            anchor(5, 31, region="area.a_l0_yellow.name"),
        ):
            self.state.apply_line(line)
        self.assertFalse(self.state.breaker.ready)
        self.assertFalse(self.state.resonance.ready)

    def test_anchors_use_five_region_bits_and_explicit_epoch_rejects_other_epochs(self):
        self.state.apply_line(anchor(22, 0, action="epoch-reset"))
        self.state.apply_line(anchor(20, 31))
        self.assertFalse(self.state.resonance.ready)
        self.state.apply_line(anchor(22, 31, action="owner-apply"))
        self.assertEqual(self.state.resonance.completed_mask, 31)
        self.state.apply_line(anchor(22, 15))
        self.assertEqual(self.state.resonance.completed_mask, 31)

    def test_zero_epoch_room_change_and_disconnect_revoke(self):
        for reset in (
            recv(0, 0), "[Behaviour] OnLeftRoom", "[Behaviour] OnDisconnected: ClientTimeout",
            "[Behaviour] Entering Room: VRChat Home",
            JOIN.replace(":123", ":456"),
        ):
            with self.subTest(reset=reset):
                self.state = BackroomsState()
                self.state.apply_line(JOIN)
                self.state.apply_line(recv(7, 3))
                self.state.apply_line(reset)
                self.assertFalse(self.state.breaker.ready)
                self.assertEqual(self.state.breaker.completed_mask, 0)

    def test_world_id_is_required_and_configurable(self):
        self.state.clear()
        self.state.apply_line("[Behaviour] Entering Room: The BackroomsVR")
        self.state.apply_line(recv(5, 1))
        self.assertFalse(self.state.breaker.ready)
        self.assertEqual(normalize_world_id(DEFAULT_BACKROOMS_WORLD_ID.upper()), DEFAULT_BACKROOMS_WORLD_ID)
        with self.assertRaises(ValueError):
            normalize_world_id("The BackroomsVR")

    def test_october_16_sparse_breaker_log_and_round_reset(self):
        # Observed sequence: no intermediate breaker recv/accept events in either
        # round, but both have an allFlipped event and complete anchor snapshots.
        self.state.apply_line(match(1602746815))
        self.state.apply_line(recv(1602746815, 0))
        self.state.apply_line("[LevelActivator:Lvl 0] Activate seed=1602746815")
        self.assertTrue(self.state.breaker.ready)
        self.state.apply_line(anchor(1602746815, 18))
        self.state.apply_line("[MEGBreaker] allFlipped 触发 mask=0x200A0402 active=5 target=<未接线> event=''")
        self.assertEqual(self.state.breaker.completed_mask, 0x200A0402)
        self.assertEqual(self.state.resonance.completed_mask, 18)
        self.state.apply_line(match(1602746815, state=2))
        self.assertFalse(self.state.breaker.ready)
        self.assertFalse(self.state.resonance.ready)
        self.state.apply_line(anchor(0, 0))
        self.state.apply_line(match(1602746815, state=0, level=-1))
        self.state.apply_line(match(918123783))
        self.state.apply_line("[LevelActivator:Lvl 0] Activate seed=918123783")
        self.assertFalse(self.state.breaker.ready)
        self.assertEqual(self.state.breaker.completed_mask, 0)
        self.state.apply_line(anchor(918123783, 0))
        self.state.apply_line("[MEGBreaker] allFlipped 触发 mask=0x48C0011 active=6 target=<未接线> event=''")
        self.assertTrue(self.state.breaker.ready)
        self.assertEqual(self.state.breaker.epoch, 918123783)
        self.assertEqual(self.state.breaker.completed_mask, 0x48C0011)
        self.state.apply_line(anchor(918123783, 31))
        self.assertEqual(self.state.resonance.completed_mask, 31)

    def test_new_match_revokes_both_snapshots_before_new_objective_logs(self):
        self.state.apply_line(recv(900, 7))
        self.state.apply_line(anchor(900, 3))
        old_revocation = self.state.revocation
        self.state.apply_line(match(2))
        self.assertGreater(self.state.revocation, old_revocation)
        self.assertFalse(self.state.breaker.ready)
        self.assertFalse(self.state.resonance.ready)
        self.assertEqual(self.state.breaker.epoch, 2)
        self.state.apply_line(recv(900, 31))
        self.state.apply_line(anchor(900, 31))
        self.state.apply_line(recv(8, 31))
        self.state.apply_line("[MEGBreaker] epoch-reset epoch=8")
        self.state.apply_line(anchor(8, 0, action="epoch-reset"))
        self.assertFalse(self.state.breaker.ready)
        self.assertFalse(self.state.resonance.ready)
        self.assertEqual(self.state.breaker.epoch, 2)
        self.assertEqual(self.state.resonance.epoch, 2)
        self.state.apply_line(recv(2, 1))
        self.assertEqual(self.state.breaker.completed_mask, 1)
        self.state.apply_line(match(2))
        self.assertTrue(self.state.breaker.ready)

    def test_terminal_event_requires_a_running_match_and_valid_mask(self):
        terminal = "[MEGBreaker] allFlipped 触发 mask=0x80001000 active=2"
        self.state.apply_line(recv(7, 0))
        self.state.apply_line(terminal)
        self.assertEqual(self.state.breaker.completed_mask, 0)
        self.state.apply_line(match(7))
        for invalid in (
            terminal.replace("active=2", "active=3"),
            terminal.replace("0x80001000", "0x100000000"),
            terminal.replace("0x80001000", "0x0"),
            terminal.replace("0x80001000", "0xZZ"),
            terminal.replace("0x80001000", "80001000"),
        ):
            with self.subTest(line=invalid):
                self.state.apply_line(invalid)
                self.assertEqual(self.state.breaker.completed_mask, 0)
        self.state.apply_line(terminal)
        self.assertEqual(self.state.breaker.completed_mask, (1 << 31) | (1 << 12))

    def test_finished_round_ignores_late_snapshots_and_terminal_events(self):
        self.state.apply_line(match(7))
        self.state.apply_line(recv(7, 1))
        self.state.apply_line(anchor(7, 1))
        self.state.apply_line(match(7, state=2))
        for late in (recv(7, 3), anchor(7, 31),
                     "[MEGBreaker] allFlipped 触发 mask=0x3 active=2"):
            self.state.apply_line(late)
        self.assertFalse(self.state.breaker.ready)
        self.assertFalse(self.state.resonance.ready)
        self.state.apply_line(match(8))
        self.state.apply_line(match(7))
        self.assertEqual(self.state.breaker.epoch, 8)

    def test_level_activation_can_confirm_round_without_mm_log(self):
        self.state.apply_line("[LevelActivator:Lvl 0] Activate seed=918123783")
        self.assertFalse(self.state.breaker.ready)
        self.state.apply_line("[MEGBreaker] allFlipped 触发 mask=0x48C0011 active=6")
        self.assertEqual(self.state.breaker.completed_mask, 0x48C0011)
        self.state.apply_line(JOIN.replace(":123", ":456"))
        self.state.apply_line("[MEGBreaker] allFlipped 触发 mask=0x48C0011 active=6")
        self.assertFalse(self.state.breaker.ready)


class BackroomsReaderTests(unittest.TestCase):
    def test_replay_only_final_room_and_follow_partial_utf8_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_2026-10-14_01-04-50.txt"
            path.write_bytes((JOIN + recv(3, 1) + "\n[Behaviour] OnLeftRoom\n" + JOIN +
                              anchor(5, 1) + "\n").encode())
            state = BackroomsState()
            reader = BackroomsLogReader(state)
            reader.poll(str(path))
            self.assertFalse(state.breaker.ready)
            self.assertTrue(state.resonance.ready)
            data = (recv(7, 8) + " 来源=测试\n").encode()
            # split a Chinese UTF-8 character as well as the final line delimiter
            split = data.index("测".encode()) + 1
            with path.open("ab") as stream:
                stream.write(data[:split])
            reader.poll(str(path))
            self.assertFalse(state.breaker.ready)
            with path.open("ab") as stream:
                stream.write(data[split:])
            reader.poll(str(path))
            self.assertEqual(state.breaker.completed_mask, 8)

    def test_truncation_switch_and_read_failure_clear_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_a.txt"
            path.write_text(JOIN + recv(4, 1) + "\n", encoding="utf-8")
            state = BackroomsState()
            reader = BackroomsLogReader(state)
            reader.poll(str(path))
            path.write_text(JOIN, encoding="utf-8")
            reader.poll(str(path))
            self.assertFalse(state.breaker.ready)
            reader.poll("")
            self.assertFalse(state.in_world)
            with self.assertRaises(OSError):
                reader.poll(str(path) + ".missing")
            self.assertFalse(state.breaker.ready)


class BackroomsOscTests(unittest.TestCase):
    def test_udp_packet_fixed_names_types_order_and_unknown_ready(self):
        state = BackroomsState()
        state.apply_line(JOIN)
        state.apply_line(recv(5, -2147479552))
        # Test actual UDP serialization on a loopback receiver.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1)
            output = BackroomsOscOutput(port=receiver.getsockname()[1])
            output.publish(state, now=0)
            messages = decode_bundle(receiver.recvfrom(8192)[0])
        values = dict(messages)
        self.assertEqual(len(values), 40)
        self.assertEqual(messages[:2], [(BREAKER_READY, False), (RESONANCE_READY, False)])
        self.assertEqual(messages[-2:], [(BREAKER_READY, True), (HEARTBEAT, True)])
        self.assertTrue(values["TBVR_BreakerOSCDone31"])
        self.assertTrue(values["TBVR_BreakerOSCDone12"])
        self.assertFalse(values["TBVR_BreakerOSCDone00"])
        self.assertFalse(values[RESONANCE_READY])
        for area in range(5):
            self.assertFalse(values[f"TBVR_ResonanceOSCDoneArea{area}"])

    def test_resonance_regions_heartbeat_periodic_resend_and_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_a.txt"
            path.write_text(JOIN + anchor(5, 31) + "\n", encoding="utf-8")
            bridge = BackroomsBridge(lambda: str(path))
            packets = []
            bridge.output = BackroomsOscOutput(socket_factory=lambda *args: RecordingSocket(packets))
            bridge.configure(True, DEFAULT_BACKROOMS_WORLD_ID, "127.0.0.1", 9000)
            bridge.step(now=0)
            values = dict(decode_bundle(packets[-1][0]))
            self.assertTrue(values[RESONANCE_READY])
            self.assertFalse(values[BREAKER_READY])
            self.assertTrue(all(values[f"TBVR_ResonanceOSCDoneArea{i}"] for i in range(5)))
            count = len(packets)
            bridge.step(now=0.5)
            self.assertEqual(len(packets), count)
            with path.open("a", encoding="utf-8") as log:
                log.write(recv(5, 1) + "\n")
            bridge.step(now=0.9)
            self.assertTrue(dict(decode_bundle(packets[-1][0]))[HEARTBEAT])
            bridge.step(now=1)
            self.assertFalse(dict(decode_bundle(packets[-1][0]))[HEARTBEAT])
            bridge.stop()
            stopped = dict(decode_bundle(packets[-1][0]))
            self.assertFalse(stopped[RESONANCE_READY])
            self.assertFalse(any(v for k, v in stopped.items() if "Done" in k))

    def test_recovery_never_publishes_historical_ready_and_failure_revokes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_a.txt"
            path.write_text(JOIN + recv(5, 3) + "\n" + ("irrelevant\n" * 30) +
                            "[Behaviour] OnLeftRoom\n", encoding="utf-8")
            bridge = BackroomsBridge(lambda: str(path))
            packets = []
            bridge.output = BackroomsOscOutput(socket_factory=lambda *args: RecordingSocket(packets))
            bridge.enabled = True
            with patch("backrooms_bridge.READ_BLOCK_BYTES", 200):
                bridge.step(now=0)
                self.assertTrue(bridge.reader.recovering)
                for packet, destination in packets:
                    self.assertFalse(dict(decode_bundle(packet))[BREAKER_READY])
                for i in range(1, 6):
                    bridge.step(now=i)
            self.assertFalse(bridge.state.breaker.ready)
            path.unlink()
            bridge.step(now=7)
            self.assertTrue(bridge.error)
            self.assertFalse(dict(decode_bundle(packets[-1][0]))[BREAKER_READY])

    def test_world_selector_records_runtime_id(self):
        candidate = VrchatLogCandidate("log", 0, 0, 0)
        updated = VrchatLogSelector._apply_text(candidate, JOIN)
        self.assertEqual(updated.world_id, DEFAULT_BACKROOMS_WORLD_ID)
        updated = VrchatLogSelector._apply_text(updated, "[Behaviour] OnLeftRoom")
        self.assertEqual(updated.world_id, "")

    def test_multiple_clients_select_only_confirmed_backrooms_client(self):
        home = VrchatLogCandidate(str(pathlib.Path("home.txt").resolve()), 0, 0, 0, pid=1)
        backrooms = VrchatLogCandidate(str(pathlib.Path("backrooms.txt").resolve()), 0, 0, 0,
                                     pid=2, world_id=DEFAULT_BACKROOMS_WORLD_ID)
        selector = VrchatLogSelector("unused", foreground_pid_provider=lambda: 0)
        with patch.object(selector, "_refresh_candidates", return_value=[home, backrooms]):
            self.assertEqual(selector.select().pid, 2)
        second = VrchatLogCandidate(str(pathlib.Path("second.txt").resolve()), 0, 0, 0,
                                  pid=3, world_id=DEFAULT_BACKROOMS_WORLD_ID)
        with patch.object(selector, "_refresh_candidates", return_value=[backrooms, second]):
            self.assertIsNone(selector.select())
            self.assertTrue(selector.snapshot()["ambiguous"])


class Value:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ConfigScreen:
    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080


class BackroomsConfigTests(unittest.TestCase):
    def test_backrooms_settings_persist_alongside_existing_hud_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = str(pathlib.Path(directory) / "config.json")
            with patch("slashco.BooleanVar", Value), patch("slashco.StringVar", Value), \
                    patch("slashco.DoubleVar", Value), patch("slashco.ECLIPTICA_CONFIG_FILENAME", config_path):
                monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
                monitor.root = ConfigScreen()
                monitor.ecliptica_hud = None
                monitor._load_ecliptica_config()
                monitor.backrooms_osc_enabled.set(True)
                monitor.backrooms_osc_port_var.set("9001")
                monitor.ecliptica_hud_font_color = "#12abef"
                monitor._save_ecliptica_config()
                restored = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
                restored.root = ConfigScreen()
                restored._load_ecliptica_config()
                self.assertTrue(restored.backrooms_osc_enabled.get())
                self.assertEqual(restored.backrooms_osc_port_var.get(), "9001")
                self.assertEqual(restored.backrooms_world_id_var.get(), DEFAULT_BACKROOMS_WORLD_ID)
                self.assertEqual(restored.ecliptica_hud_font_color, "#12abef")


if __name__ == "__main__":
    unittest.main()
