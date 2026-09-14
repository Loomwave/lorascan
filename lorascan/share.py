"""Community sharing (spec §4a): an opt-in share file of per-channel AGGREGATES plus a coarse location
cell. Nothing else: no payloads, no raw samples, no precise position. `build_share` makes the document.

Low-bandwidth uplinks (§4a, Matt 2026-09-14): `granularity` hour|day (day ≈ 3 KB gzipped per day),
`choose_by_budget` picks the coarsest document that fits a bytes/day budget, and `upload_share` sends
gzip JSON incrementally — it asks the endpoint for its watermark first and sends only buckets at or
after it (inclusive, so a still-filling bucket is updated); rows are idempotent on
(submitter, freq_hz, bw_hz, bucket_s, bucket). Protocol: docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md"""
from __future__ import annotations
import datetime as dt
import json
import os
import secrets
import gzip
import time
import urllib.request
import urllib.error
from . import __version__

SHARE_FORMAT = "lorascan-share/2"
GRANULARITY_S = {"hour": 3600, "day": 86400}
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


def _bucket(ts: float, granularity: str) -> str:
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:00Z") if granularity == "hour" else d.strftime("%Y-%m-%d")


def build_share(store, cell: tuple[float, float] | None, token: str, profile_name: str, rssi_offset_db: float = 0.0,
                cell_size_deg: float = DEFAULT_CELL_DEG, run_id=None, granularity: str = "hour", tables: tuple = ("cad", "decode")) -> dict:
    if granularity not in GRANULARITY_S:
        raise ValueError(f"granularity must be hour or day, not {granularity!r}")
    # energy aggregates per (freq, bw, bucket); day granularity also pools a 7x24 when-matrix
    acc: dict[tuple, dict] = {}
    when_acc: dict[tuple, list] = {}
    for r in store.iter_energy(run_id):
        k = (r.freq_hz, r.bw_hz, _bucket(r.ts, granularity))
        d = acc.setdefault(k, {"freq_hz": r.freq_hz, "bw_hz": r.bw_hz, "bucket": k[2], "floors": [], "p90s": [], "busy": [], "n_rows": 0, "n_samples": 0, "peak": -999.0})
        if granularity == "day":
            t = dt.datetime.fromtimestamp(r.ts, dt.timezone.utc)
            w = when_acc.setdefault((t.weekday(), t.hour), [0.0, 0]); w[0] += r.busy_frac; w[1] += 1
        d["floors"].append(r.floor_dbm + rssi_offset_db); d["p90s"].append(r.p90 + rssi_offset_db); d["busy"].append(r.busy_frac)
        d["n_rows"] += 1; d["n_samples"] += r.n; d["peak"] = max(d["peak"], r.peak + rssi_offset_db)
    energy = []
    for k in sorted(acc):
        d = acc[k]; fl = sorted(d["floors"]); p9 = sorted(d["p90s"])
        energy.append({"freq_hz": d["freq_hz"], "bw_hz": d["bw_hz"], "bucket": d["bucket"], "bucket_s": GRANULARITY_S[granularity], "n_rows": d["n_rows"], "n_samples": d["n_samples"],
                       "hours": round(d["n_samples"] * 8.2e-6 / 3600, 4),
                       "floor_p10_med": fl[len(fl) // 2], "p90_med": p9[len(p9) // 2], "peak_max": d["peak"], "busy_mean": round(sum(d["busy"]) / len(d["busy"]), 4)})
    cad = [{"freq_hz": c["freq_hz"], "bw_hz": c["bw_hz"], "sf": c["sf"], "n_cad": c["n_cad"], "hits": c["hits"], "hit_rate": round(c["hit_rate"], 4), "longest_run": c["longest_run"]}
           for c in store.cad_summary(run_id)] if "cad" in tables else []
    decode = [{"freq_hz": d["freq_hz"], "network": d["network"], "preset": d["preset"], "dwell_s": d["dwell_s"], "n_ok": d["n_ok"], "n_crc_err": d["n_crc_err"],
               "rssi_med": d["rssi_med"] + rssi_offset_db if d["n_ok"] else None} for d in store.decode_summary(run_id)] if "decode" in tables else []
    doc = {
        "format": SHARE_FORMAT, "tool": f"lorascan {__version__}", "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "submitter": token, "board": profile_name, "granularity": granularity,
        "calibration": "calibrated (offset %+.1f dB applied)" % rssi_offset_db if rssi_offset_db else "relative (uncalibrated)",
        "cell": {"lat": cell[0], "lon": cell[1], "size_deg": cell_size_deg} if cell else None,
        "energy": energy, "cad": cad, "decode": decode,
    }
    if granularity == "day":
        doc["when"] = [[(round(when_acc[(wd, h)][0] / when_acc[(wd, h)][1], 4) if (wd, h) in when_acc else None) for h in range(24)] for wd in range(7)]
    return doc


def write_share(doc: dict, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=1)


def gzip_bytes(doc: dict) -> bytes:
    return gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 9)


def parse_budget(text: str) -> int:
    """'20k/day' -> 20000 bytes per day of survey span ('k' = 1000, 'M' = 1e6; '/day' optional)."""
    t = text.strip().lower().split("/")[0]
    mult = 1
    if t.endswith("k"):
        mult, t = 1000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    try:
        return int(float(t) * mult)
    except ValueError:
        raise ValueError(f"--budget must look like 20k/day or 500/day, not {text!r}")


def _span_days(store, run_id=None) -> float:
    rs = [r for r in store.runs() if (run_id is None or r["id"] == run_id) and r["first_ts"] and r["last_ts"]]
    if not rs:
        return 1.0
    return max(1.0, (max(r["last_ts"] for r in rs) - min(r["first_ts"] for r in rs)) / 86400)


def choose_by_budget(store, cell, token, profile_name, budget_per_day: int, rssi_offset_db: float = 0.0,
                     cell_size_deg: float = DEFAULT_CELL_DEG, run_id=None) -> tuple[dict, int]:
    """Coarsest-first search: hour+all, day+all, day energy-only; returns (doc, gzipped bytes)."""
    days = _span_days(store, run_id)
    last = None
    for gran, tables in (("hour", ("cad", "decode")), ("day", ("cad", "decode")), ("day", ())):
        doc = build_share(store, cell, token, profile_name, rssi_offset_db, cell_size_deg, run_id, granularity=gran, tables=tables)
        size = len(gzip_bytes(doc))
        last = (doc, size)
        if size / days <= budget_per_day:
            return last
    return last


def filter_after_watermark(doc: dict, latest: str | None) -> dict:
    """Keep energy buckets >= the endpoint's latest bucket (inclusive: the last bucket may still be filling)."""
    if not latest:
        return doc
    out = dict(doc)
    out["energy"] = [e for e in doc["energy"] if e["bucket"] >= latest]
    return out


def upload_share(doc: dict, to: str, retries: int = 3, backoff_s: float = 2.0, timeout_s: float = 30.0) -> dict:
    """GET {to}/v1/watermark, then POST {to}/v1/share (gzip JSON). Returns the endpoint's reply plus
    sent/skipped counts and the attempt count. Network errors and 5xx are retried; 4xx raise."""
    base = to.rstrip("/")
    hdr = {"X-Lorascan-Submitter": doc["submitter"], "User-Agent": doc["tool"]}
    latest = None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/v1/watermark", headers=hdr), timeout=timeout_s) as r:
            latest = (json.loads(r.read().decode()).get("latest") or {}).get(doc["granularity"])
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    except (urllib.error.URLError, OSError, ValueError):
        latest = None                      # no watermark = send everything; the endpoint de-duplicates
    send = filter_after_watermark(doc, latest)
    body = gzip_bytes(send)
    req = urllib.request.Request(f"{base}/v1/share", data=body, method="POST",
                                 headers={**hdr, "Content-Type": "application/json", "Content-Encoding": "gzip"})
    attempts = 0
    while True:
        attempts += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                reply = json.loads(r.read().decode() or "{}")
            break
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempts >= retries:
                raise RuntimeError(f"upload rejected by {base}: HTTP {e.code} {e.read().decode(errors='replace')[:200]}") from e
        except (urllib.error.URLError, OSError) as e:
            if attempts >= retries:
                raise RuntimeError(f"upload failed after {attempts} attempts: {e}") from e
        time.sleep(backoff_s * attempts)
    reply.update({"sent": len(send["energy"]), "skipped": len(doc["energy"]) - len(send["energy"]), "bytes": len(body), "attempts": attempts, "watermark": latest})
    return reply
