"""One OS-backed lock for simulations, offseasons and scheduled save snapshots.

The lock file is permanent: deleting it would let another process lock a different inode.
The operating system releases the byte lock when a process exits, including on a crash.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path


class SaveLock:
    def __init__(self, path):
        self.path = Path(path)
        self._gate = threading.Lock()
        self._file = None

    def acquire(self, blocking=False):
        if blocking:
            raise ValueError("Save operations must acquire the lock without waiting")
        if not self._gate.acquire(False):
            return False
        handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                handle.close()
                self._gate.release()
                return False
            self._file = handle
            return True
        except BaseException:
            if handle is not None:
                handle.close()
            self._gate.release()
            raise

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            raise RuntimeError("release of unlocked save lock")
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._gate.release()

    def locked(self):
        return self._gate.locked()


SAVE_LOCK = SaveLock(Path(__file__).resolve().parents[1] / "universe" / ".save-operation.lock")
