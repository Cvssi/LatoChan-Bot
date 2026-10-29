"""Cooperative sidecar locks and durable, private atomic file replacement (Linux)."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile


@contextmanager
def locked(path, *, shared=False, blocking=True):
    flags = os.O_RDWR | os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags | os.O_CREAT | os.O_EXCL, 0o660)
        created = True
    except FileExistsError:
        fd = os.open(str(path), flags)
        created = False
    try:
        # Only the creator owns this inode. Group writers may lock it but cannot chmod it.
        if created:
            os.fchmod(fd, 0o660)
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(fd, mode | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def atomic_write(path, data: bytes, mode=0o660):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
