"""Community sharing (spec §4a): an opt-in share file of per-channel AGGREGATES plus a coarse location
cell. Nothing else: no payloads, no raw samples, no precise position. `build_share` makes the document;
the upload endpoint (lorascan.app) is a later phase, so the CLI writes the file for the user to read."""
from __future__ import annotations
import datetime as dt
import json
import os
import secrets
from . import __version__

SHARE_FORMAT = "lorascan-share/1"
DEFAULT_CELL_DEG = 0.1
TOKEN_PATH = os.path.expanduser("~/.config/lorascan/token")


def coarse_cell(lat: float | None, lon: float | None, size_deg: float = DEFAULT_CELL_DEG) -> tuple[float, float] | None:
    if lat is None or lon is None:
        return None
    q = 1.0 / size_deg
    return round(round(lat * q) / q, 6), round(round(lon * q) / q, 6)


def submitter_token(path: str = TOKEN_PATH) -> str:
    """A random id generated on first share (no account); deleting the file revokes it."""
    if os.path.exists(path):
        with open(path) as f:
            t = f.read().strip()
            if t:
                return t
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    t = secrets.token_hex(16)
    with open(path, "w") as f:
        f.write(t + "\n")
    os.chmod(path, 0o600)
    return t


def _hour(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def build_share(store, cell: tuple[float, float] | None, token: str, profile_name: str, rssi_offset_db: float = 0.0,
                cell_size_deg: float = DEFAULT_CELL_DEG, run_id=None) -> dict:
    # energy aggregates per (freq, bw, hour bucket)
    acc: dict[tuple, dict] = {}
    for r in store.iter_energy(run_id):
        k = (r.freq_hz, r.bw_hz, _hour(r.ts))
        d = acc.setdefault(k, {"freq_hz": r.freq_hz, "bw_hz": r.bw_hz, "hour": k[2], "floors": [], "p90s": [], "busy": [], "n_rows": 0, "n_samples": 0, "peak": -999.0})
        d["floors"].append(r.floor_dbm + rssi_offset_db); d["p90s"].append(r.p90 + rssi_offset_db); d["busy"].append(r.busy_frac)
        d["n_rows"] += 1; d["n_samples"] += r.n; d["peak"] = max(d["peak"], r.peak + rssi_offset_db)
    energy = []
    for k in sorted(acc):
        d = acc[k]; fl = sorted(d["floors"]); p9 = sorted(d["p90s"])
        energy.append({"freq_hz": d["freq_hz"], "bw_hz": d["bw_hz"], "hour": d["hour"], "n_rows": d["n_rows"], "n_samples": d["n_samples"],
                       "hours": round(d["n_rows"] / max(1, d["n_rows"]) * 0.0 + d["n_samples"] * 8.2e-6 / 3600, 4),
                       "floor_p10_med": fl[len(fl) // 2], "p90_med": p9[len(p9) // 2], "peak_max": d["peak"], "busy_mean": round(sum(d["busy"]) / len(d["busy"]), 4)})
    cad = [{"freq_hz": c["freq_hz"], "bw_hz": c["bw_hz"], "sf": c["sf"], "n_cad": c["n_cad"], "hits": c["hits"], "hit_rate": round(c["hit_rate"], 4), "longest_run": c["longest_run"]}
           for c in store.cad_summary(run_id)]
    decode = [{"freq_hz": d["freq_hz"], "network": d["network"], "preset": d["preset"], "dwell_s": d["dwell_s"], "n_ok": d["n_ok"], "n_crc_err": d["n_crc_err"],
               "rssi_med": d["rssi_med"] + rssi_offset_db if d["n_ok"] else None} for d in store.decode_summary(run_id)]
    return {
        "format": SHARE_FORMAT, "tool": f"lorascan {__version__}", "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "submitter": token, "board": profile_name,
        "calibration": "calibrated (offset %+.1f dB applied)" % rssi_offset_db if rssi_offset_db else "relative (uncalibrated)",
        "cell": {"lat": cell[0], "lon": cell[1], "size_deg": cell_size_deg} if cell else None,
        "energy": energy, "cad": cad, "decode": decode,
    }


def write_share(doc: dict, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=1)
