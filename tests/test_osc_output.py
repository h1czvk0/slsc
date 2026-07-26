import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from osc_output import (  # noqa: E402
    BossLockOscOutput,
    build_osc_message,
    normalize_osc_host,
    normalize_osc_port,
)


def read_osc_string(packet, offset=0):
    end = packet.index(b"\x00", offset)
    text = packet[offset:end].decode("utf-8")
    next_offset = (end + 4) & ~3
    return text, next_offset


class FakeSocket:
    def __init__(self, sent):
        self.sent = sent

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def sendto(self, packet, destination):
        self.sent.append((packet, destination))


class OscOutputTests(unittest.TestCase):
    def test_chatbox_packet_preserves_unicode_player_name(self):
        sent = []
        output = BossLockOscOutput(
            socket_factory=lambda *_args: FakeSocket(sent),
        )

        self.assertTrue(output.publish_target("ಣಪರೀಕ್ಷೆ"))
        packet, destination = sent[0]
        address, offset = read_osc_string(packet)
        tags, offset = read_osc_string(packet, offset)
        message, _offset = read_osc_string(packet, offset)

        self.assertEqual(destination, ("127.0.0.1", 9000))
        self.assertEqual(address, "/chatbox/input")
        self.assertEqual(tags, ",sTF")
        self.assertEqual(message, "Boss 当前锁定：ಣಪರೀಕ್ಷೆ")

    def test_name_only_mode_omits_boss_lock_prefix(self):
        sent = []
        output = BossLockOscOutput(socket_factory=lambda *_args: FakeSocket(sent))

        output.publish_target("ಣಪರೀಕ್ಷೆ", name_only=True)

        packet, _destination = sent[0]
        _address, offset = read_osc_string(packet)
        _tags, offset = read_osc_string(packet, offset)
        message, _offset = read_osc_string(packet, offset)
        self.assertEqual(message, "ಣಪರೀಕ್ಷೆ")

    def test_unchanged_target_is_not_sent_twice(self):
        sent = []
        output = BossLockOscOutput(socket_factory=lambda *_args: FakeSocket(sent))

        self.assertTrue(output.publish_target("Player A", now=0))
        self.assertFalse(output.publish_target("Player A", now=9))
        self.assertTrue(output.publish_target("Player A", now=10))
        self.assertEqual(len(sent), 2)

    def test_clear_sends_empty_chatbox_message_immediately(self):
        sent = []
        output = BossLockOscOutput(socket_factory=lambda *_args: FakeSocket(sent))
        output.publish_target("Player A", now=0)

        output.clear()

        packet, destination = sent[-1]
        address, offset = read_osc_string(packet)
        tags, offset = read_osc_string(packet, offset)
        message, _offset = read_osc_string(packet, offset)
        self.assertEqual(destination, ("127.0.0.1", 9000))
        self.assertEqual(address, "/chatbox/input")
        self.assertEqual(tags, ",sTF")
        self.assertEqual(message, "")
        self.assertIsNone(output.last_text)
        self.assertIsNone(output.last_attempt_at)

    def test_invalid_destination_values_use_defaults(self):
        self.assertEqual(normalize_osc_host(""), "127.0.0.1")
        self.assertEqual(normalize_osc_port("invalid"), 9000)
        self.assertEqual(normalize_osc_port(70000), 9000)

    def test_osc_address_must_start_with_slash(self):
        with self.assertRaises(ValueError):
            build_osc_message("chatbox/input", "test")


if __name__ == "__main__":
    unittest.main()
