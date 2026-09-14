"""SQLite store (spec §3.4): append-only rows, WAL mode, safe to read while a survey writes."""
from __future__ import annotations
import json
import sqlite3
import time
from typing import Iterator
from ..measure.energy import EnergyRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_ts REAL NOT NULL, kind TEXT NOT NULL, profile TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS energy (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), ts REAL NOT NULL, freq_hz INTEGER NOT NULL, bw_hz INTEGER NOT NULL,
  engine TEXT NOT NULL, n INTEGER NOT NULL, hist_json TEXT NOT NULL, floor_dbm REAL, p50 REAL, p90 REAL, peak REAL, busy_frac REAL, discarded INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS energy_freq_ts ON energy(freq_hz, ts);
CREATE INDEX IF NOT EXISTS energy_run ON energy(run_id);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, run_id INTEGER, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path, timeout=30)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")   # WAL + NORMAL: no fsync per row (SD-card friendly); durable at checkpoints
        self.con.executescript(SCHEMA)
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def new_run(self, kind: str, profile: str, note: str = "", started_ts: float | None = None) -> int:
        cur = self.con.execute("INSERT INTO runs(started_ts, kind, profile, note) VALUES (?,?,?,?)",
                               (started_ts if started_ts is not None else time.time(), kind, profile, note))
        self.con.commit()
        return int(cur.lastrowid)

    def add_event(self, run_id: int | None, kind: str, detail: str = "", ts: float | None = None) -> None:
        self.con.execute("INSERT INTO events(run_id, ts, kind, detail) VALUES (?,?,?,?)",
                         (run_id, ts if ts is not None else time.time(), kind, detail))
        self.con.commit()

    def add_energy(self, run_id: int, r: EnergyRow) -> None:
        self.con.execute(
            "INSERT INTO energy(run_id, ts, freq_hz, bw_hz, engine, n, hist_json, floor_dbm, p50, p90, peak, busy_frac, discarded) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, r.ts, r.freq_hz, r.bw_hz, r.engine, r.n, json.dumps(r.hist), r.floor_dbm, r.p50, r.p90, r.peak, r.busy_frac, r.discarded))
        self.con.commit()

    def _where(self, run_id, since):
        w, a = [], []
        if run_id is not None:
            w.append("run_id = ?"); a.append(run_id)
        if since is not None:
            w.append("ts >= ?"); a.append(since)
        return (" WHERE " + " AND ".join(w)) if w else "", a

    def iter_energy(self, run_id: int | None = None, since: float | None = None) -> Iterator[EnergyRow]:
        w, a = self._where(run_id, since)
        for row in self.con.execute("SELECT ts, freq_hz, bw_hz, engine, n, hist_json, floor_dbm, p50, p90, peak, busy_frac, discarded FROM energy" + w + " ORDER BY ts", a):
            ts, f, bw, eng, n, hj, fl, p50, p90, pk, bf, d = row
            yield EnergyRow(ts=ts, freq_hz=f, bw_hz=bw, engine=eng, n=n, hist=json.loads(hj), floor_dbm=fl, p50=p50, p90=p90, peak=pk, busy_frac=bf, discarded=d)

    def channel_summary(self, run_id: int | None = None, since: float | None = None) -> list[dict]:
        """Per frequency: median floor, median p90, max peak, mean busy fraction, row count, seconds observed."""
        w, a = self._where(run_id, since)
        per: dict[int, dict] = {}
        for f, fl, p90, pk, bf, n, bw in self.con.execute("SELECT freq_hz, floor_dbm, p90, peak, busy_frac, n, bw_hz FROM energy" + w, a):
            d = per.setdefault(f, {"freq_hz": f, "bw_hz": bw, "floors": [], "p90s": [], "peak_max": -999.0, "busy": [], "n_rows": 0, "n_samples": 0})
            d["floors"].append(fl); d["p90s"].append(p90); d["peak_max"] = max(d["peak_max"], pk); d["busy"].append(bf); d["n_rows"] += 1; d["n_samples"] += n
        out = []
        for f in sorted(per):
            d = per[f]
            fl = sorted(d["floors"]); p9 = sorted(d["p90s"])
            out.append({"freq_hz": f, "bw_hz": d["bw_hz"], "floor_med": fl[len(fl) // 2], "p90_med": p9[len(p9) // 2],
                        "peak_max": d["peak_max"], "busy_mean": sum(d["busy"]) / len(d["busy"]), "n_rows": d["n_rows"], "n_samples": d["n_samples"]})
        return out

    def time_buckets(self, bucket_s: int = 60, run_id: int | None = None, since: float | None = None) -> list[dict]:
        w, a = self._where(run_id, since)
        acc: dict[tuple, list] = {}
        for ts, f, bf, p90 in self.con.execute("SELECT ts, freq_hz, busy_frac, p90 FROM energy" + w, a):
            key = (float(int(ts // bucket_s) * bucket_s), f)
            acc.setdefault(key, [0.0, 0.0, 0])
            acc[key][0] += bf; acc[key][1] += p90; acc[key][2] += 1
        return [{"bucket": k[0], "freq_hz": k[1], "busy_mean": v[0] / v[2], "p90_mean": v[1] / v[2], "n": v[2]} for k, v in sorted(acc.items())]

    def runs(self) -> list[dict]:
        out = []
        for rid, st, kind, prof, note in self.con.execute("SELECT id, started_ts, kind, profile, note FROM runs ORDER BY id"):
            f, l, n = self.con.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM energy WHERE run_id = ?", (rid,)).fetchone()
            out.append({"id": rid, "started_ts": st, "kind": kind, "profile": prof, "note": note, "first_ts": f, "last_ts": l, "n_rows": n})
        return out
