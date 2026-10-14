"""BackroomsVR accepted log snapshots -> local avatar OSC parameters."""

import os
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

from osc_output import build_osc_message, normalize_osc_host, normalize_osc_port


DEFAULT_BACKROOMS_WORLD_ID = "wrld_59030639-7913-4abe-b67e-b3b72746eac3"
JOIN_ROOM = re.compile(r"\[Behaviour\] Joining (wrld_[0-9a-fA-F-]+):([^\s]+)")
FIELDS = re.compile(r"\b([A-Za-z]+)=([^\s]+)")
BREAKER_READY = "TBVR_BreakerOSCReady"
RESONANCE_READY = "TBVR_ResonanceOSCReady"
HEARTBEAT = "TBVR_LocationOSCHeartbeat"
READ_BLOCK_BYTES = 1024 * 1024


def normalize_world_id(value):
    value = str(value or "").strip().lower()
    if not re.fullmatch(r"wrld_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        raise ValueError("世界 ID 必须为 wrld_ 开头的完整 UUID")
    return value


@dataclass
class ObjectiveSnapshot:
    bits: int
    epoch: int = 0
    completed_mask: int = 0
    ready: bool = False
    confirmed_epoch: bool = False
    retired_epochs: set = field(default_factory=set)

    def reset(self, epoch=0, confirmed=False):
        if self.epoch and self.epoch != epoch:
            self.retired_epochs.add(self.epoch)
        self.epoch = epoch
        self.completed_mask = 0
        self.ready = False
        self.confirmed_epoch = confirmed and epoch != 0

    def accept(self, epoch, mask):
        if not epoch:
            self.reset()
            return
        if epoch in self.retired_epochs:
            return
        if self.confirmed_epoch and self.epoch != epoch:
            return
        if epoch != self.epoch:
            self.reset(epoch)
        # Accepted snapshots are complete sets. A regressing snapshot is ignored.
        mask &= (1 << self.bits) - 1
        if mask & self.completed_mask != self.completed_mask:
            return
        self.completed_mask = mask
        self.ready = True


class BackroomsState:
    def __init__(self, world_id=DEFAULT_BACKROOMS_WORLD_ID):
        self.target_world_id = normalize_world_id(world_id)
        self.clear()

    def clear(self):
        self.world_id = ""
        self.instance_id = ""
        self.breaker = ObjectiveSnapshot(32)
        self.resonance = ObjectiveSnapshot(5)
        self.revision = getattr(self, "revision", 0) + 1
        self.revocation = getattr(self, "revocation", 0) + 1

    @property
    def in_world(self):
        return self.world_id == self.target_world_id and bool(self.instance_id)

    def signature(self):
        return (self.world_id, self.instance_id, self.breaker.epoch,
                self.breaker.completed_mask, self.breaker.ready, self.resonance.epoch,
                self.resonance.completed_mask, self.resonance.ready)

    def apply_line(self, line):
        if not any(marker in line for marker in (
            "[Behaviour]", "[MEGBreaker]", "[ObjectiveState]",
        )):
            return
        before = self.signature()
        if ("[Behaviour] Entering Room:" in line or "[Behaviour] OnLeftRoom" in line
                or "[Behaviour] OnDisconnected:" in line):
            self.clear()
            return
        joined = JOIN_ROOM.search(line)
        if joined:
            world, instance = joined.groups()
            if (world.lower(), instance) != (self.world_id, self.instance_id):
                self.clear()
                self.world_id, self.instance_id = world.lower(), instance
            return
        if not self.in_world:
            return
        fields = dict(FIELDS.findall(line))
        try:
            if "[MEGBreaker] generated " in line:
                self.breaker.reset()
                self.revocation += 1
            elif "[MEGBreaker]" in line and (
                "[MEGBreaker] epoch-reset " in line or fields.get("action") == "epoch-reset"
            ):
                epoch = int(fields["epoch"])
                if epoch not in self.breaker.retired_epochs:
                    self.breaker.reset(epoch, confirmed=True)
                    self.revocation += 1
            elif "[MEGBreaker] recv " in line:
                self.breaker.accept(int(fields["acceptedEpoch"]), int(fields["acceptedMask"]))
            elif "[MEGBreaker] accept " in line:
                self.breaker.accept(int(fields["epoch"]), int(fields["new"]))
            elif "[ObjectiveState]" in line and fields.get("region") == "area.a_l0_chaos.name":
                action = fields.get("action")
                epoch = int(fields["epoch"])
                if action == "epoch-reset":
                    if epoch not in self.resonance.retired_epochs:
                        self.resonance.reset(epoch, confirmed=True)
                        self.revocation += 1
                elif action in ("deserialize", "owner-apply"):
                    self.resonance.accept(epoch, int(fields["mask"]))
        except (KeyError, ValueError):
            return
        after = self.signature()
        if before != after:
            self.revision += 1
            # New/zero epochs must revoke the preceding state before publishing.
            if before[2] != after[2] or before[5] != after[5]:
                self.revocation += 1

    def summary(self):
        return {
            "in_world": self.in_world,
            "world_id": self.world_id,
            "instance_id": self.instance_id,
            "breaker_ready": self.breaker.ready,
            "breaker_done": self.breaker.completed_mask.bit_count(),
            "breaker_mask": self.breaker.completed_mask,
            "breaker_epoch": self.breaker.epoch,
            "resonance_ready": self.resonance.ready,
            "resonance_done": self.resonance.completed_mask.bit_count(),
            "resonance_mask": self.resonance.completed_mask,
            "resonance_epoch": self.resonance.epoch,
        }


class BackroomsLogReader:
    """Replay one selected client in bounded blocks, then follow complete byte lines."""

    def __init__(self, state):
        self.state = state
        self.path = ""
        self.offset = 0
        self.partial = b""
        self.recovering = False

    def reset(self, path=""):
        self.state.clear()
        self.path = str(path or "")
        self.offset = 0
        self.partial = b""
        self.recovering = bool(self.path)

    def poll(self, path):
        path = str(path or "")
        if path != self.path:
            self.reset(path)
        if not path:
            return
        try:
            size = os.path.getsize(path)
            if size < self.offset:
                self.reset(path)
            with open(path, "rb") as log_file:
                log_file.seek(self.offset)
                data = log_file.read(READ_BLOCK_BYTES)
            self.offset += len(data)
            lines = (self.partial + data).split(b"\n")
            self.partial = lines.pop()
            for line in lines:
                self.state.apply_line(line.rstrip(b"\r").decode("utf-8", errors="replace"))
            self.recovering = self.offset < size
        except OSError:
            self.reset()
            raise


class BackroomsOscOutput:
    def __init__(self, host="127.0.0.1", port=9000, socket_factory=socket.socket):
        self.host = normalize_osc_host(host)
        self.port = normalize_osc_port(port)
        self.socket_factory = socket_factory
        self.heartbeat = False
        self.last_heartbeat_at = None

    def publish(self, state, valid=True, now=None):
        now = time.monotonic() if now is None else now
        messages = [(BREAKER_READY, False), (RESONANCE_READY, False)]
        breaker_valid = valid and state.in_world and state.breaker.ready
        resonance_valid = valid and state.in_world and state.resonance.ready
        messages.extend((f"TBVR_BreakerOSCDone{index:02d}",
                         bool(breaker_valid and (state.breaker.completed_mask >> index) & 1))
                        for index in range(32))
        messages.extend((f"TBVR_ResonanceOSCDoneArea{area}",
                         bool(resonance_valid and (state.resonance.completed_mask >> area) & 1))
                        for area in range(5))
        if breaker_valid:
            messages.append((BREAKER_READY, True))
        if resonance_valid:
            messages.append((RESONANCE_READY, True))
        heartbeat = self.heartbeat
        heartbeat_at = self.last_heartbeat_at
        if valid and (heartbeat_at is None or now - heartbeat_at >= 1.0):
            heartbeat = not heartbeat
            heartbeat_at = now
        messages.append((HEARTBEAT, heartbeat))
        # OSC immediate bundle: revoke Ready, replace every bit, then restore Ready.
        packet = bytearray(b"#bundle\x00" + struct.pack(">Q", 1))
        for name, value in messages:
            message = build_osc_message(f"/avatar/parameters/{name}", value)
            packet.extend(struct.pack(">I", len(message)))
            packet.extend(message)
        with self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as osc_socket:
            osc_socket.sendto(bytes(packet), (self.host, self.port))
        self.heartbeat = heartbeat
        self.last_heartbeat_at = heartbeat_at


class BackroomsBridge:
    """Worker owns the parser, reader and UDP output; Tk consumes immutable summaries."""

    def __init__(self, path_provider):
        self.path_provider = path_provider
        self.lock = threading.RLock()
        self.state = BackroomsState()
        self.reader = BackroomsLogReader(self.state)
        self.output = BackroomsOscOutput()
        self.enabled = False
        self.error = ""
        self.last_publish_at = -float("inf")
        self.sent_revision = -1
        self.sent_revocation = -1
        self.sent_valid = None
        self.stop_event = threading.Event()
        self.thread = None

    def configure(self, enabled, world_id, host, port):
        world_id = normalize_world_id(world_id)
        host, port = normalize_osc_host(host), normalize_osc_port(port)
        with self.lock:
            destination_changed = (host, port) != (self.output.host, self.output.port)
            if self.enabled and (not enabled or destination_changed):
                self.output.publish(self.state, valid=False)
            if world_id != self.state.target_world_id:
                self.state.target_world_id = world_id
                self.reader.reset()
            self.output.host, self.output.port = host, port
            self.enabled = bool(enabled)
            self.last_publish_at = -float("inf")
            self.sent_revision = -1
            self.sent_revocation = -1
            self.sent_valid = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="BackroomsOSC", daemon=True)
            self.thread.start()

    def step(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            try:
                self.reader.poll(self.path_provider())
                valid = not self.reader.recovering
                if self.enabled:
                    if self.sent_revocation != self.state.revocation:
                        self.output.publish(self.state, valid=False, now=now)
                        self.sent_revocation = self.state.revocation
                    if (self.sent_revision != self.state.revision
                            or self.sent_valid != valid
                            or (valid and self.output.last_heartbeat_at is not None
                                and now - self.output.last_heartbeat_at >= 1.0)
                            or now - self.last_publish_at >= 1.0):
                        self.output.publish(self.state, valid=valid, now=now)
                        self.sent_revision = self.state.revision
                        self.sent_valid = valid
                        self.last_publish_at = now
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
                # A failed read must immediately withdraw cached completion bits.
                if self.enabled:
                    try:
                        self.output.publish(self.state, valid=False, now=now)
                    except OSError:
                        pass
                self.sent_revision = -1

    def snapshot(self):
        with self.lock:
            return dict(self.state.summary(), enabled=self.enabled, error=self.error,
                        recovering=self.reader.recovering, path=self.reader.path)

    def _run(self):
        while not self.stop_event.is_set():
            self.step()
            self.stop_event.wait(0.1)

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        with self.lock:
            if self.enabled:
                try:
                    self.output.publish(self.state, valid=False)
                except OSError:
                    pass
            self.enabled = False
