import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from log_stream import read_log_tail, split_complete_lines


class LogStreamTests(unittest.TestCase):
    def test_utf8_and_crlf_survive_every_possible_chunk_boundary(self):
        data = "User Authenticated: 测试玩家🎮 (usr_test)\r\n第二行\n".encode("utf-8")
        for cut in range(len(data) + 1):
            with self.subTest(cut=cut):
                first, partial = split_complete_lines(data[:cut])
                second, partial = split_complete_lines(data[cut:], partial)
                self.assertEqual(first + second, ["User Authenticated: 测试玩家🎮 (usr_test)", "第二行"])
                self.assertEqual(partial, b"")

    def test_tail_cursor_resumes_incomplete_line_and_concurrent_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "log.txt"
            complete = "first\n第二行\r\n".encode("utf-8")
            next_line = "测试玩家\n".encode("utf-8")
            path.write_bytes(complete + next_line[:2])
            text, start, cursor = read_log_tail(path, 1024)
            self.assertEqual(text, complete.decode("utf-8"))
            self.assertEqual((start, cursor), (0, len(complete)))
            with path.open("ab") as file:
                file.write(next_line[2:] + b"later\n")
            with path.open("rb") as file:
                file.seek(cursor)
                lines, partial = split_complete_lines(file.read())
            self.assertEqual(lines, ["测试玩家", "later"])
            self.assertEqual(partial, b"")

    def test_tail_keeps_a_line_exactly_on_scan_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "log.txt"
            path.write_bytes(b"old\nfirst\nlast\n")
            self.assertEqual(read_log_tail(path, 11), ("first\nlast\n", 4, 15))
            self.assertEqual(read_log_tail(path, 10), ("last\n", 5, 15))

    def test_empty_and_unterminated_files_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "log.txt"
            for data in (b"", b"incomplete"):
                path.write_bytes(data)
                self.assertEqual(read_log_tail(path, 1024), ("", 0, 0))


if __name__ == "__main__":
    unittest.main()
