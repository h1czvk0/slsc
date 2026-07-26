import socket
import struct
import time


DEFAULT_OSC_HOST = "127.0.0.1"
DEFAULT_OSC_PORT = 9000
VRCHAT_CHATBOX_ADDRESS = "/chatbox/input"
OSC_RESEND_INTERVAL_SECONDS = 10.0


def normalize_osc_host(value):
    return str(value or "").strip() or DEFAULT_OSC_HOST


def normalize_osc_port(value):
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_OSC_PORT
    return port if 1 <= port <= 65535 else DEFAULT_OSC_PORT


def normalize_osc_text(value):
    return str(value or "").replace("\x00", "").strip()


def _encode_osc_string(value):
    encoded = normalize_osc_text(value).encode("utf-8") + b"\x00"
    return encoded + (b"\x00" * ((-len(encoded)) % 4))


def build_osc_message(address, *arguments):
    osc_address = normalize_osc_text(address)
    if not osc_address.startswith("/"):
        raise ValueError("OSC address must start with '/'")

    type_tags = [","]
    payload = bytearray()
    for argument in arguments:
        if isinstance(argument, bool):
            type_tags.append("T" if argument else "F")
        elif isinstance(argument, str):
            type_tags.append("s")
            payload.extend(_encode_osc_string(argument))
        elif isinstance(argument, int):
            type_tags.append("i")
            payload.extend(struct.pack(">i", argument))
        elif isinstance(argument, float):
            type_tags.append("f")
            payload.extend(struct.pack(">f", argument))
        else:
            raise TypeError(f"Unsupported OSC argument type: {type(argument).__name__}")

    return _encode_osc_string(osc_address) + _encode_osc_string("".join(type_tags)) + bytes(payload)


class BossLockOscOutput:
    def __init__(self, host=DEFAULT_OSC_HOST, port=DEFAULT_OSC_PORT, socket_factory=socket.socket):
        self.socket_factory = socket_factory
        self.host = normalize_osc_host(host)
        self.port = normalize_osc_port(port)
        self.last_text = None
        self.last_attempt_at = None
        self.display_active = None

    def configure(self, host, port):
        normalized = (normalize_osc_host(host), normalize_osc_port(port))
        if normalized != (self.host, self.port):
            self.host, self.port = normalized
            self.last_text = None
            self.last_attempt_at = None
            self.display_active = None
        return normalized

    def reset(self):
        self.last_text = None
        self.last_attempt_at = None
        self.display_active = None

    def publish_target(self, target, force=False, now=None, name_only=False):
        player_name = normalize_osc_text(target) or "-"
        text = player_name if name_only else f"Boss 当前锁定：{player_name}"
        current_time = float(time.monotonic() if now is None else now)
        recently_attempted = (
            text == self.last_text
            and self.last_attempt_at is not None
            and current_time - self.last_attempt_at < OSC_RESEND_INTERVAL_SECONDS
        )
        if not force and recently_attempted:
            return False

        packet = build_osc_message(VRCHAT_CHATBOX_ADDRESS, text, True, False)
        self.last_text = text
        self.last_attempt_at = current_time
        with self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as osc_socket:
            osc_socket.sendto(packet, (self.host, self.port))
        self.display_active = True
        return True

    def clear(self, force=False):
        if not force and self.display_active is False:
            return False
        packet = build_osc_message(VRCHAT_CHATBOX_ADDRESS, "", True, False)
        self.last_text = None
        self.last_attempt_at = None
        with self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as osc_socket:
            osc_socket.sendto(packet, (self.host, self.port))
        self.display_active = False
        return True
