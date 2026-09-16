"""The persistent station config (~/.config/lorascan/config.yaml, then /etc/lorascan/config.yaml).
Written by `lorascan setup`; read by scan/share/upload when a flag is absent. Mini-YAML, no deps."""
from __future__ import annotations
import os
from dataclasses import dataclass
from .profile import parse_mini_yaml

USER_PATH = "~/.config/lorascan/config.yaml"
ETC_PATH = "/etc/lorascan/config.yaml"


@dataclass
class StationConfig:
    profile: str | None = None
    location: tuple[float, float] | None = None
    endpoint: str | None = None
    granularity: str | None = None


def _user_path(home: str | None) -> str:
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".config", "lorascan", "config.yaml")


def _from_dict(d: dict) -> StationConfig:
    loc = None
    lb = d.get("location")
    if isinstance(lb, dict) and lb.get("lat") is not None and lb.get("lon") is not None:
        loc = (float(lb["lat"]), float(lb["lon"]))
    share = d.get("share") if isinstance(d.get("share"), dict) else {}
    prof = d.get("profile")
    return StationConfig(profile=str(prof) if prof else None, location=loc,
                         endpoint=(str(share["endpoint"]) if share.get("endpoint") else None),
                         granularity=(str(share["granularity"]) if share.get("granularity") else None))


def _load_file(path: str) -> StationConfig:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        d = parse_mini_yaml(text)
    except Exception as e:
        raise ValueError(f"{path}: malformed station config: {e}") from e
    if not isinstance(d, dict):
        raise ValueError(f"{path}: station config is not a mapping")
    return _from_dict(d)


def load(home: str | None = None) -> StationConfig:
    for path in (_user_path(home), ETC_PATH):
        if os.path.exists(path):
            return _load_file(path)
    return StationConfig()


def save(cfg: StationConfig, path: str | None = None) -> str:
    path = path or os.path.expanduser(USER_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# written by `lorascan setup`; hand-editable"]
    if cfg.profile:
        lines.append(f"profile: {cfg.profile}")
    if cfg.location:
        lines.append(f"location: {{lat: {cfg.location[0]}, lon: {cfg.location[1]}}}")
    ep = cfg.endpoint or ""
    gr = cfg.granularity or "hour"
    lines.append(f"share:   {{endpoint: {_q(ep)}, granularity: {gr}}}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"' if s else '""'


def resolve(flag, cfg_value, default):
    if flag is not None:
        return flag
    if cfg_value is not None:
        return cfg_value
    return default
