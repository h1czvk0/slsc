import pathlib
import struct
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_jump import (  # noqa: E402
    AutoJumpOscOutput,
    AutoJumpPulseController,
    AutoJumpService,
    SpaceKeyHook,
    WM_KEYDOWN,
    WM_KEYUP,
)


def read_osc_string(packet, offset=0):
    end = packet.index(b"\x00", offset)
    text = packet[offset:end].decode("utf-8")
    return text, (end + 4) & ~3


class FakeSocket:
    def __init__(self, sent):
        self.sent = sent

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def sendto(self, packet, destination):
        self.sent.append((packet, destination))


class AutoJumpTests(unittest.TestCase):
    def test_space_hook_swallows_space_only_while_capture_is_enabled(self):
        hook = SpaceKeyHook(physical_state_provider=lambda: False)

        self.assertFalse(hook.handle_space_event(WM_KEYDOWN))
        hook.set_capture(True)
        self.assertTrue(hook.handle_space_event(WM_KEYDOWN))
        self.assertTrue(hook.is_down())
        self.assertTrue(hook.handle_space_event(WM_KEYUP))
        self.assertFalse(hook.is_down())
        hook.set_capture(False)
        self.assertFalse(hook.handle_space_event(WM_KEYDOWN))

    def test_space_hook_waits_for_release_when_enabled_mid_press(self):
        hook = SpaceKeyHook(physical_state_provider=lambda: True)
        hook.set_capture(True)

        self.assertTrue(hook.awaiting_release)
        self.assertFalse(hook.handle_space_event(WM_KEYDOWN))
        self.assertFalse(hook.handle_space_event(WM_KEYUP))
        self.assertFalse(hook.awaiting_release)
        self.assertTrue(hook.handle_space_event(WM_KEYDOWN))

    def test_jump_output_uses_vrchat_input_address_and_integer_value(self):
        sent = []
        output = AutoJumpOscOutput(socket_factory=lambda *_args: FakeSocket(sent))

        self.assertTrue(output.send(True))
        packet, destination = sent[0]
        address, offset = read_osc_string(packet)
        tags, offset = read_osc_string(packet, offset)

        self.assertEqual(destination, ("127.0.0.1", 9000))
        self.assertEqual(address, "/input/Jump")
        self.assertEqual(tags, ",i")
        self.assertEqual(struct.unpack(">i", packet[offset:offset + 4])[0], 1)

    def test_output_deduplicates_values_and_release_can_be_forced(self):
        sent = []
        output = AutoJumpOscOutput(socket_factory=lambda *_args: FakeSocket(sent))

        self.assertTrue(output.send(True))
        self.assertFalse(output.send(True))
        self.assertTrue(output.release())
        self.assertFalse(output.release())
        self.assertTrue(output.release(force=True))
        self.assertEqual(len(sent), 3)

    def test_pulse_controller_alternates_press_and_release(self):
        pulse = AutoJumpPulseController(press_seconds=0.05, release_seconds=0.05)

        self.assertEqual(pulse.update(True, 0.0), [True])
        self.assertEqual(pulse.update(True, 0.049), [])
        self.assertEqual(pulse.update(True, 0.05), [False])
        self.assertEqual(pulse.update(True, 0.099), [])
        self.assertEqual(pulse.update(True, 0.1), [True])

    def test_pulse_controller_releases_immediately_when_condition_stops(self):
        pulse = AutoJumpPulseController()
        pulse.update(True, 0.0)

        self.assertEqual(pulse.update(False, 0.001), [False])
        self.assertEqual(pulse.update(False, 0.002), [])

    def test_service_requires_enabled_foreground_vrchat_and_space(self):
        sent = []
        state = {"foreground": False, "space": False}
        output = AutoJumpOscOutput(socket_factory=lambda *_args: FakeSocket(sent))
        service = AutoJumpService(
            output=output,
            foreground_provider=lambda: state["foreground"],
            space_down_provider=lambda: state["space"],
            key_hook=False,
        )

        service.tick(now=0.0)
        self.assertEqual(sent, [])
        service.set_enabled(True)
        state.update(foreground=True, space=True)
        service.tick(now=0.1)
        self.assertEqual(len(sent), 1)
        self.assertTrue(service.snapshot()["jumping"])

        state["space"] = False
        service.tick(now=0.11)
        self.assertEqual(len(sent), 2)
        self.assertFalse(service.snapshot()["jumping"])

    def test_service_releases_when_disabled(self):
        sent = []
        output = AutoJumpOscOutput(socket_factory=lambda *_args: FakeSocket(sent))
        service = AutoJumpService(
            output=output,
            foreground_provider=lambda: True,
            space_down_provider=lambda: True,
            key_hook=False,
        )
        service.set_enabled(True)
        service.tick(now=0.0)

        service.set_enabled(False)
        service.tick(now=0.001)

        self.assertEqual(len(sent), 2)
        self.assertEqual(output.last_value, 0)

    def test_service_retries_a_failed_release(self):
        class FailOnceOnRelease(AutoJumpOscOutput):
            def __init__(self):
                super().__init__(socket_factory=lambda *_args: FakeSocket([]))
                self.release_attempts = 0

            def send(self, pressed, force=False):
                if not pressed:
                    self.release_attempts += 1
                    if self.release_attempts == 1:
                        raise OSError("temporary failure")
                return super().send(pressed, force=force)

        output = FailOnceOnRelease()
        service = AutoJumpService(
            output=output,
            foreground_provider=lambda: True,
            space_down_provider=lambda: True,
            key_hook=False,
        )
        service.set_enabled(True)
        service.tick(now=0.0)
        service.set_enabled(False)

        service.tick(now=0.001)
        service.tick(now=0.002)

        self.assertEqual(output.release_attempts, 2)
        self.assertEqual(output.last_value, 0)


if __name__ == "__main__":
    unittest.main()
