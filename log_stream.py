"""Byte-oriented helpers for logs that may be written while being read."""

import os


def split_complete_lines(data: bytes, partial: bytes = b"") -> tuple[list[str], bytes]:
    """Decode only complete LF/CRLF lines, preserving split UTF-8 characters."""
    chunks = (partial + data).split(b"\n")
    return [line.rstrip(b"\r").decode("utf-8", errors="replace") for line in chunks[:-1]], chunks[-1]


def read_log_tail(path, limit: int) -> tuple[str, int, int]:
    """Return complete tail text, scan start and the exact resumable byte offset.

    Bound the read to one file-size snapshot. A writer may append more data during
    recovery; the caller must resume at the returned offset, not a fresh stat().
    """
    with open(path, "rb") as log_file:
        size = os.fstat(log_file.fileno()).st_size
        start = max(0, size - limit)
        log_file.seek(max(0, start - 1))
        at_line_start = start == 0 or log_file.read(1) == b"\n"
        log_file.seek(start)
        data = log_file.read(size - start)
    end = start + len(data)
    if not at_line_start:
        newline = data.find(b"\n")
        if newline < 0:
            return "", start, start
        data = data[newline + 1:]
    last_newline = data.rfind(b"\n")
    partial_size = len(data) - last_newline - 1
    return data[:last_newline + 1].decode("utf-8", errors="replace"), start, end - partial_size
