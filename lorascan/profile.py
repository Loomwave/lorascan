"""Board profiles (spec §3.1). A profile is JSON-compatible YAML; parsed by a small subset parser so
the tool has no PyYAML dependency. Supported: `key: value`, `key: {k: v, k2: v2}`, `# comments`,
numbers, true/false/null, bare or quoted strings."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Any

PROFILE_DIRS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles"),
    os.path.expanduser("~/.config/lorascan/profiles"),
    "/etc/lorascan/profiles",
]


def _scalar(tok: str) -> Any:
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        return t[1:-1]
    low = t.lower()
    if low in ("null", "~", ""):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        if t.startswith(("0x", "-0x")):
            return int(t, 16)
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _flow_map(body: str) -> dict:
    d: dict[str, Any] = {}
    for part in body.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition(":")
        d[k.strip()] = _scalar(v)
    return d


def parse_mini_yaml(text: str) -> dict:
    d: dict[str, Any] = {}
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        key, sep, val = line.partition(":")
        if not sep:
            raise ValueError(f"profile line without ':' : {raw!r}")
        val = val.strip()
        if val.startswith("{") and val.endswith("}"):
            d[key.strip()] = _flow_map(val[1:-1])
        else:
            d[key.strip()] = _scalar(val)
    return d


@dataclass
class BoardProfile:
    name: str
    bus_type: str
    bus_dev: str
    bus_hz: int
    pins: dict = field(default_factory=dict)
    tcxo_v: float = 1.8
    dio2_rf_switch: bool = True
    rx_boosted: bool = True
    max_tx_dbm: int = -9
    rssi_offset_db: float = 0.0
    scan_offset_dbm: int = -11

    @classmethod
    def from_dict(cls, d: dict) -> "BoardProfile":
        bus, radio, cal = d.get("bus", {}), d.get("radio", {}), d.get("cal", {})
        pins = dict(d.get("pins", {}))
        for k in ("nss", "reset", "busy", "dio1", "rxen", "txen"):
            pins.setdefault(k, None)
        return cls(
            name=str(d.get("name", "unnamed")),
            bus_type=str(bus.get("type", "spidev")),
            bus_dev=str(bus.get("dev", "/dev/spidev0.0")),
            bus_hz=int(bus.get("hz", 2_000_000)),
            pins=pins,
            tcxo_v=float(radio.get("tcxo_v", 1.8)),
            dio2_rf_switch=bool(radio.get("dio2_rf_switch", True)),
            rx_boosted=bool(radio.get("rx_boosted", True)),
            max_tx_dbm=int(radio.get("max_tx_dbm", -9)),
            rssi_offset_db=float(cal.get("rssi_offset_db", 0.0)),
            scan_offset_dbm=int(cal.get("scan_offset_dbm", -11)),
        )


def load_profile(name_or_path: str) -> BoardProfile:
    """A path (contains '/' or ends with .yaml) is read directly; a bare name is looked up in PROFILE_DIRS."""
    candidates = []
    if "/" in name_or_path or name_or_path.endswith((".yaml", ".yml")):
        candidates.append(name_or_path)
    else:
        for d in PROFILE_DIRS:
            candidates.append(os.path.join(d, name_or_path + ".yaml"))
    for c in candidates:
        if os.path.isfile(c):
            with open(c) as f:
                return BoardProfile.from_dict(parse_mini_yaml(f.read()))
    raise FileNotFoundError(f"profile {name_or_path!r} not found; searched profiles dirs: {', '.join(PROFILE_DIRS)}")
