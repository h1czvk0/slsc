import pathlib
import queue
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from slashco import LOG_LIVE_READ_BYTES, UI_LOG_BATCH_SIZE, UI_LOG_MAX_LINES, SlashCoMonitorCN


class MonitorIOTests(unittest.TestCase):
    def test_queued_events_from_previous_log_are_discarded(self):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor._is_shutting_down = False
        monitor._pending_log_lines = queue.Queue()
        monitor._pending_log_lines.put("old")
        monitor._clear_pending_log_lines()
        monitor._process_live_log_lines(["stale live event"], generation=0)
        monitor._enqueue_log_lines(["stale recovery event"], generation=0)
        self.assertTrue(monitor._pending_log_lines.empty())

    def monitor_for(self, path, on_wait):
        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor.is_monitoring = True
        monitor._is_shutting_down = False
        monitor._recovering_log_state = False
        monitor._pending_log_lines = queue.Queue()
        monitor._ui_callback_queue = queue.Queue()
        monitor._monitor_stop_event = SimpleNamespace(wait=on_wait)
        monitor.vrchat_log_selector = SimpleNamespace(
            select=lambda _: SimpleNamespace(path=str(path), pid=1), snapshot=lambda: {},
        )
        monitor._ui_after = lambda function, *args: function(*args)
        monitor._update_ecliptica_log_instance_ui = Mock()
        monitor._reset_for_new_log = Mock()
        monitor.log = Mock()
        monitor._enqueue_log_lines = Mock()
        monitor._enqueue_live_log_lines = Mock()
        return monitor

    def test_recovery_does_not_skip_lines_written_during_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "log.txt"
            path.write_bytes(b"old noise\n")

            def stop(_):
                monitor.is_monitoring = False

            monitor = self.monitor_for(path, stop)
            recover = monitor._get_active_round_recovery_lines

            def scan(path_string):
                lines = recover(path_string)
                with path.open("ab") as file:
                    file.write("[Behaviour] Entering Room: 测试世界\n".encode("utf-8"))
                return lines

            monitor._get_active_round_recovery_lines = scan
            monitor.monitor_loop()
            monitor._enqueue_live_log_lines.assert_called_once_with(["[Behaviour] Entering Room: 测试世界"])

    def test_truncated_log_restarts_from_beginning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "log.txt"
            path.write_bytes(b"old noise\n" * 100)
            waits = []

            def wait(_):
                waits.append(True)
                if len(waits) == 1:
                    path.write_bytes(b"[Behaviour] Entering Room: Home\n")
                else:
                    monitor.is_monitoring = False

            monitor = self.monitor_for(path, wait)
            monitor.monitor_loop()
            self.assertIn((["[Behaviour] Entering Room: Home"],),
                          [call.args for call in monitor._enqueue_live_log_lines.call_args_list])
            self.assertEqual(monitor._reset_for_new_log.call_count, 2)

    def test_live_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "log.txt"
            path.write_bytes(b"old noise\n")

            def stop(_):
                monitor.is_monitoring = False

            monitor = self.monitor_for(path, stop)
            recover = monitor._get_active_round_recovery_lines
            line = b"[Behaviour] Entering Room: Home\n"

            def scan(path_string):
                lines = recover(path_string)
                with path.open("ab") as file:
                    file.write(line * (LOG_LIVE_READ_BYTES // len(line) + 200))
                return lines

            monitor._get_active_round_recovery_lines = scan
            monitor.monitor_loop()
            count = sum(len(call.args[0]) for call in monitor._enqueue_live_log_lines.call_args_list)
            self.assertEqual(count, LOG_LIVE_READ_BYTES // len(line))

    def test_log_widget_batches_and_trims_history(self):
        class Text:
            def __init__(self):
                self.text = "old\n" * UI_LOG_MAX_LINES
                self.insertions = 0

            def insert(self, _, text):
                self.insertions += 1
                self.text += text

            def index(self, _):
                return f"{self.text.count(chr(10)) + 1}.0"

            def delete(self, _, end):
                self.text = "\n".join(self.text.split("\n")[int(end.split('.')[0]) - 1:])

            def see(self, _):
                pass

        monitor = SlashCoMonitorCN.__new__(SlashCoMonitorCN)
        monitor._is_shutting_down = False
        monitor.root = Mock()
        monitor.txt_log = Text()
        monitor._log_queue = queue.Queue()
        for index in range(UI_LOG_BATCH_SIZE + 1):
            monitor._log_queue.put(f"new {index}\n")
        monitor._process_log_queue()
        self.assertEqual(monitor.txt_log.insertions, 1)
        self.assertEqual(monitor.txt_log.text.count("\n"), UI_LOG_MAX_LINES)
        self.assertEqual(monitor._log_queue.qsize(), 1)
        monitor._process_log_queue()
        self.assertTrue(monitor.txt_log.text.endswith(f"new {UI_LOG_BATCH_SIZE}\n"))


if __name__ == "__main__":
    unittest.main()
