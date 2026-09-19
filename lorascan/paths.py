"""One directory for everything lorascan reads and writes (Loomwave/lorascan#22).

A balena repeater runs a read-only rootfs with only a few directories writable (typically
`/data`), so `lorascan -d /data/lorascan scan …` — or `LORASCAN_DIR=/data/lorascan` for a
systemd unit — puts the station config, board profiles, user network table, submitter token
and every default-named output file under that one directory, creating missing subfolders.

Precedence: the `-d/--data-dir` flag, then `LORASCAN_DIR`, then the legacy behaviour
(`~/.config/lorascan/...` for config, the current directory for outputs).

Two rules keep it predictable:
  * only DEFAULT-valued file arguments move; a path the user typed is used exactly as given;
  * reads fall back (data dir, then the legacy user path, then /etc) so a station can adopt
    `-d` without losing its existing token or config — but writes only ever go to the data dir.
"""
from __future__ import annotations
import os

ENV_VAR = "LORASCAN_DIR"
ETC_DIR = "/etc/lorascan"

_flag_dir: str | None = None          # set once from -d/--data-dir, before any command runs


def _abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def set_data_dir(path: str | None) -> str | None:
    """Apply the -d/--data-dir flag. None clears it, so LORASCAN_DIR (if any) applies again."""
    global _flag_dir
    _flag_dir = _abs(path) if path else None
    return _flag_dir


def data_dir() -> str | None:
    """The active data directory (absolute), or None for the legacy layout."""
    if _flag_dir:
        return _flag_dir
    env = os.environ.get(ENV_VAR)
    return _abs(env) if env else None


def legacy_user_dir(home: str | None = None) -> str:
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".config", "lorascan")


def _under(name: str, home: str | None) -> str:
    d = data_dir()
    return os.path.join(d, name) if d else os.path.join(legacy_user_dir(home), name)


def config_path(home: str | None = None) -> str:
    return _under("config.yaml", home)


def profiles_dir(home: str | None = None) -> str:
    return _under("profiles", home)


def networks_path(home: str | None = None) -> str:
    return _under("networks.yaml", home)


def token_path(home: str | None = None) -> str:
    return _under("token", home)


def _read_chain(name: str, home: str | None, etc: bool) -> list:
    """Where a file is looked for, best first: the data dir, then the legacy user path, then /etc."""
    out = []
    d = data_dir()
    if d:
        out.append(os.path.join(d, name))
    out.append(os.path.join(legacy_user_dir(home), name))
    if etc:
        out.append(os.path.join(ETC_DIR, name))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def config_paths(home: str | None = None) -> list:
    return _read_chain("config.yaml", home, etc=True)


def profile_dirs(home: str | None = None) -> list:
    """Board-profile search path: the profiles shipped with the package first, then the user dirs."""
    pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
    return [pkg] + _read_chain("profiles", home, etc=True)


def networks_paths(home: str | None = None) -> list:
    return _read_chain("networks.yaml", home, etc=True)


def token_paths(home: str | None = None) -> list:
    return _read_chain("token", home, etc=False)


def ensure_dir(path: str) -> str:
    """Create a directory (and its parents) if it is missing. No error when it already exists."""
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def default_output(name: str) -> str:
    """A default output file name: under the data dir when one is set, otherwise unchanged."""
    d = data_dir()
    return os.path.join(d, name) if d else name


def for_output(path: str) -> str:
    """Make sure the parent directory of a file about to be written exists. Returns the path."""
    if path:
        ensure_dir(os.path.dirname(os.path.abspath(path)))
    return path
