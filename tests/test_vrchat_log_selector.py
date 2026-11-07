import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vrchat_log_selector import (  # noqa: E402
    VrchatLogSelector,
    VrchatProcess,
    log_boot_timestamp,
    match_processes_to_logs,
)


def write_log(path, username, user_id, room, extra=""):
    path.write_text(
        f"User Authenticated: {username} ({user_id})\n"
        f"[Behaviour] Entering Room: {room}\n"
        f"{extra}",
        encoding="utf-8",
    )


class VrchatLogSelectorTests(unittest.TestCase):
    def make_process(self, path, pid):
        return VrchatProcess(pid=pid, started_at=log_boot_timestamp(str(path)) - 3.0)

    def test_processes_are_matched_one_to_one_by_client_boot_time(self):
        paths = [
            r"C:\logs\output_log_2026-08-29_04-13-56.txt",
            r"C:\logs\output_log_2026-08-29_04-15-10.txt",
        ]
        processes = [
            VrchatProcess(101, log_boot_timestamp(paths[0]) - 2.0),
            VrchatProcess(202, log_boot_timestamp(paths[1]) - 4.0),
        ]

        self.assertEqual(
            match_processes_to_logs(paths, processes),
            {paths[0]: 101, paths[1]: 202},
        )

    def test_sole_ecliptica_instance_wins_over_newer_home_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = pathlib.Path(directory)
            ecliptica = log_dir / "output_log_2026-08-29_04-13-56.txt"
            home = log_dir / "output_log_2026-08-29_04-15-10.txt"
            write_log(
                ecliptica,
                "Player One",
                "usr_player_one",
                "Ecliptica - Demo Playtest",
                "ECLIPTICA saving SESSION ID 1234\n",
            )
            write_log(home, "Player Two", "usr_player_two", "VRChat Home")
            processes = [self.make_process(ecliptica, 101), self.make_process(home, 202)]
            selector = VrchatLogSelector(
                str(log_dir),
                process_provider=lambda: processes,
                foreground_pid_provider=lambda: 202,
            )

            selected = selector.select()

        self.assertEqual(selected.path, str(ecliptica))
        self.assertEqual(selected.pid, 101)
        self.assertEqual(selected.vrc_username, "Player One")

    def test_variant_world_with_ecliptica_session_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = pathlib.Path(directory)
            path = log_dir / "output_log_2026-08-29_04-13-56.txt"
            write_log(
                path,
                "Player One",
                "usr_player_one",
                "看看蓝白碗",
                "ECLIPTICA Loading Settings...\n"
                "ECLIPTICA MASTER Setting SESSION ID to 12445\n"
                "ECLIPTICA - now in stage: Stage_Hall of Beginnings on phase: 0 as class: Spellsword\n",
            )
            processes = [self.make_process(path, 101)]
            selector = VrchatLogSelector(
                str(log_dir),
                process_provider=lambda: processes,
                foreground_pid_provider=lambda: 101,
            )

            selected = selector.select()

        self.assertIsNotNone(selected)
        self.assertTrue(selected.in_ecliptica)
        self.assertEqual(selected.room_name, "看看蓝白碗")

    def test_current_ecliptica_selection_is_sticky_when_other_instance_is_foreground(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = pathlib.Path(directory)
            first = log_dir / "output_log_2026-08-29_04-13-56.txt"
            second = log_dir / "output_log_2026-08-29_04-15-10.txt"
            write_log(first, "One", "usr_one", "Ecliptica - Demo Playtest")
            write_log(second, "Two", "usr_two", "VRChat Home")
            processes = [self.make_process(first, 101), self.make_process(second, 202)]
            selector = VrchatLogSelector(
                str(log_dir),
                process_provider=lambda: processes,
                foreground_pid_provider=lambda: 202,
            )

            selected = selector.select(str(first))

        self.assertEqual(selected.path, str(first))

    def test_post_login_identity_labels_the_selected_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_2026-11-01_16-07-46.txt"
            path.write_text(
                "[Authentication - ÎÎÍÏÎÏÌÎ] Preforming post login checks for "
                "测试玩家 (usr_local)\n"
                "[Behaviour] Entering Room: Ecliptica - Demo Playtest\n"
                "[Behaviour] OnPlayerJoined Other Player (usr_other)\n",
                encoding="utf-8",
            )
            selector = VrchatLogSelector(
                str(path.parent),
                process_provider=lambda: [self.make_process(path, 101)],
                foreground_pid_provider=lambda: 101,
            )

            selected = selector.select()
            self.assertEqual(selected.vrc_username, "测试玩家")
            self.assertEqual(selected.vrc_user_id, "usr_local")
            self.assertEqual(selected.label, "测试玩家｜Ecliptica - Demo Playtest｜PID 101")

            with path.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    "[Authentication - NewState] Preforming post login checks for "
                    "Second Player (usr_second)\n"
                    "[Behaviour] OnPlayerJoined Other Player (usr_other)\n"
                )
            updated = selector.select(str(path))
            self.assertEqual(updated.vrc_username, "Second Player")
            self.assertEqual(updated.vrc_user_id, "usr_second")

    def test_multiple_ecliptica_instances_require_foreground_or_manual_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = pathlib.Path(directory)
            first = log_dir / "output_log_2026-08-29_04-13-56.txt"
            second = log_dir / "output_log_2026-08-29_04-15-10.txt"
            write_log(first, "One", "usr_one", "Ecliptica - Demo Playtest")
            write_log(second, "Two", "usr_two", "Ecliptica - Demo Playtest")
            processes = [self.make_process(first, 101), self.make_process(second, 202)]
            selector = VrchatLogSelector(
                str(log_dir),
                process_provider=lambda: processes,
                foreground_pid_provider=lambda: 0,
            )

            self.assertIsNone(selector.select())
            self.assertTrue(selector.snapshot()["ambiguous"])

            selector.set_manual_path(str(second))
            selected = selector.select()

        self.assertEqual(selected.path, str(second))
        self.assertFalse(selector.snapshot()["ambiguous"])

    def test_target_process_exit_invalidates_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = pathlib.Path(directory)
            path = log_dir / "output_log_2026-08-29_04-13-56.txt"
            write_log(path, "One", "usr_one", "Ecliptica - Demo Playtest")
            processes = [self.make_process(path, 101)]
            selector = VrchatLogSelector(
                str(log_dir),
                process_provider=lambda: processes,
                foreground_pid_provider=lambda: 101,
            )
            self.assertIsNotNone(selector.select())

            processes.clear()

            self.assertIsNone(selector.select(str(path)))

    def test_appended_room_change_updates_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = pathlib.Path(directory)
            path = log_dir / "output_log_2026-08-29_04-13-56.txt"
            write_log(path, "One", "usr_one", "Ecliptica - Demo Playtest")
            processes = [self.make_process(path, 101)]
            selector = VrchatLogSelector(
                str(log_dir),
                process_provider=lambda: processes,
                foreground_pid_provider=lambda: 101,
            )
            self.assertTrue(selector.select().in_ecliptica)

            with path.open("a", encoding="utf-8") as log_file:
                log_file.write("[Behaviour] Entering Room: VRChat Home\n")

            self.assertFalse(selector.select(str(path)).in_ecliptica)

    def test_initial_partial_identity_and_utf8_append_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_2026-08-29_04-13-56.txt"
            identity = "User Authenticated: 测试玩家 (usr_test)\n".encode("utf-8")
            cut = identity.index("测".encode("utf-8")) + 1
            path.write_bytes(identity[:cut])
            selector = VrchatLogSelector(directory)
            self.assertEqual(selector._read_candidate(str(path)).vrc_username, "")
            with path.open("ab") as file:
                file.write(identity[cut:cut + 1])
            self.assertEqual(selector._read_candidate(str(path)).vrc_username, "")
            with path.open("ab") as file:
                file.write(identity[cut + 1:])
            self.assertEqual(selector._read_candidate(str(path)).vrc_username, "测试玩家")

    def test_identity_straddling_initial_tail_boundary_is_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_2026-08-29_04-13-56.txt"
            write_log(path, "测试玩家", "usr_test", "Ecliptica")
            selector = VrchatLogSelector(directory)
            with patch("vrchat_log_selector.INITIAL_TAIL_SCAN_BYTES", path.stat().st_size - 10):
                candidate = selector._read_candidate(str(path))
            self.assertEqual(candidate.vrc_username, "测试玩家")
            self.assertTrue(candidate.in_ecliptica)

    def test_large_append_is_read_in_order_without_skipping_room_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_2026-08-29_04-13-56.txt"
            write_log(path, "One", "usr_one", "Ecliptica")
            selector = VrchatLogSelector(directory)
            self.assertTrue(selector._read_candidate(str(path)).in_ecliptica)
            with path.open("ab") as file:
                file.write(b"[Behaviour] Entering Room: Home\n" + b"noise\n" * 100)
            with patch("vrchat_log_selector.APPEND_SCAN_LIMIT_BYTES", 64):
                candidate = selector._read_candidate(str(path))
                self.assertFalse(candidate.in_ecliptica)
                self.assertLess(candidate.size, path.stat().st_size)
                while candidate.size < path.stat().st_size:
                    candidate = selector._read_candidate(str(path))
            self.assertEqual(candidate.room_name, "Home")

    def test_leaving_room_clears_stale_world_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "output_log_2026-08-29_04-13-56.txt"
            write_log(path, "One", "usr_one", "Ecliptica", "ECLIPTICA saving SESSION ID 12\n")
            selector = VrchatLogSelector(directory)
            candidate = selector._read_candidate(str(path))
            self.assertTrue(candidate.in_ecliptica)
            with path.open("ab") as file:
                file.write(b"[Behaviour] OnLeftRoom\n")
            candidate = selector._read_candidate(str(path))
            self.assertFalse(candidate.in_ecliptica)
            self.assertEqual(candidate.session_id, "")

    def test_unreadable_log_does_not_block_another_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            first = pathlib.Path(directory) / "output_log_2026-08-29_04-13-56.txt"
            second = pathlib.Path(directory) / "output_log_2026-08-29_04-15-10.txt"
            for path in (first, second):
                write_log(path, "One", "usr_one", "Ecliptica")
            selector = VrchatLogSelector(
                directory, process_provider=lambda: [self.make_process(first, 1), self.make_process(second, 2)],
                foreground_pid_provider=lambda: 2,
            )
            read_candidate = selector._read_candidate

            def read(path):
                if path == str(first):
                    raise PermissionError(path)
                return read_candidate(path)

            with patch.object(selector, "_read_candidate", side_effect=read):
                self.assertEqual(selector.select().path, str(second))


if __name__ == "__main__":
    unittest.main()
