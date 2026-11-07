"""Replace writable settings and downloaded assets only after a successful write."""

import os
import tempfile
from contextlib import contextmanager


@contextmanager
def atomic_open(path, mode="w", encoding="utf-8"):
    """Write a sibling temporary file, then atomically replace the destination."""
    if mode not in ("w", "wb"):
        raise ValueError("atomic_open supports only w and wb")
    destination = os.path.abspath(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode=mode,
            encoding=encoding if mode == "w" else None,
            dir=os.path.dirname(destination),
            prefix=f".{os.path.basename(destination)}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = file.name
            yield file
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
