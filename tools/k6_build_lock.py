"""Inter-process serialization of K6 firmware builds sharing Aura output state."""
from contextlib import contextmanager
import os
from pathlib import Path
import time

LOCK = Path(__file__).resolve().parents[1] / 'firmware/k6-aura/out-fd6818b/.build.lock'


@contextmanager
def exclusive_build():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+b') as handle:
        if os.name == 'nt':
            import errno
            import msvcrt
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
