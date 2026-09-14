"""One lorascan per radio: an advisory flock on a lock file named after the SPI device, so a second
scan on the same HAT fails fast with a clear message instead of corrupting the first one's transfers."""
from __future__ import annotations
import fcntl
import os

LOCK_DIRS = ["/run/lock", "/tmp"]


class DeviceBusy(Exception):
    pass


def _lock_path(dev: str, lock_dir: str | None) -> str:
    name = "lorascan-" + dev.replace("/dev/", "").replace("/", "_") + ".lock"
    dirs = [lock_dir] if lock_dir else LOCK_DIRS
    for d in dirs:
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, name)
    return os.path.join(dirs[-1], name)


def acquire_device_lock(dev: str, lock_dir: str | None = None) -> int:
    """Returns the lock fd (keep it open for the lifetime of the radio; close it to release)."""
    path = _lock_path(dev, lock_dir)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = ""
        try:
            holder = os.read(fd, 64).decode().strip()
        except OSError:
            pass
        os.close(fd)
        raise DeviceBusy(f"{dev} is in use by another lorascan{(' (pid ' + holder + ')') if holder else ''}; lock {path}")
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd
