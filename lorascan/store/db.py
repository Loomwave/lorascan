"""SQLite store (spec §3.4): append-only rows, WAL mode, safe to read while a survey writes."""
from __future__ import annotations
import json
import sqlite3
import time
from typing import Iterator
from ..measure.energy import EnergyRow
from ..measure.cad import CadRow
from ..measure.decode import DecodeRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_ts REAL NOT NULL, kind TEXT NOT NULL, profile TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS energy (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), ts REAL NOT NULL, freq_hz INTEGER NOT NULL, bw_hz INTEGER NOT NULL,
  engine TEXT NOT NULL, n INTEGER NOT NULL, hist_json TEXT NOT NULL, floor_dbm REAL, p50 REAL, p90 REAL, peak REAL, busy_frac REAL, discarded INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS energy_freq_ts ON energy(freq_hz, ts);
CREATE INDEX IF NOT EXISTS energy_run ON energy(run_id);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, run_id INTEGER, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS cad (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts REAL NOT NULL, freq_hz INTEGER NOT NULL, bw_hz INTEGER NOT NULL, sf INTEGER NOT NULL,
  symbols INTEGER NOT NULL, n_cad INTEGER NOT NULL, hits INTEGER NOT NULL, longest_run INTEGER NOT NULL, det_peak INTEGER NOT NULL, det_min INTEGER NOT NULL, timeouts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS cad_freq_ts ON cad(freq_hz, ts);
CREATE TABLE IF NOT EXISTS decode (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts REAL NOT NULL, freq_hz INTEGER NOT NULL, network TEXT NOT NULL, preset TEXT NOT NULL,
  dwell_s REAL NOT NULL, n_ok INTEGER NOT NULL, n_crc_err INTEGER NOT NULL, rssi_med REAL, snr_med REAL, len_med INTEGER);
CREATE INDEX IF NOT EXISTS decode_freq_ts ON decode(freq_hz, ts);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path, timeout=30)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")   # WAL + NORMAL: no fsync per row (SD-card friendly); durable at checkpoints
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(energy)")}
        if "dwell_s" not in cols:                                     # databases written before 0.1.16
            self.con.execute("ALTER TABLE energy ADD COLUMN dwell_s REAL")
        self.con.commit()

    def close(self) -> None:
        try:
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # leave a self-contained .db (users copy the file)
        except sqlite3.Error:
            pass
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
            "INSERT INTO energy(run_id, ts, freq_hz, bw_hz, engine, n, hist_json, floor_dbm, p50, p90, peak, busy_frac, discarded, dwell_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, r.ts, r.freq_hz, r.bw_hz, r.engine, r.n, json.dumps(r.hist), r.floor_dbm, r.p50, r.p90, r.peak, r.busy_frac, r.discarded, r.dwell_s))
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
        for row in self.con.execute("SELECT ts, freq_hz, bw_hz, engine, n, hist_json, floor_dbm, p50, p90, peak, busy_frac, discarded, dwell_s FROM energy" + w + " ORDER BY ts", a):
            ts, f, bw, eng, n, hj, fl, p50, p90, pk, bf, d, dw = row
            yield EnergyRow(ts=ts, freq_hz=f, bw_hz=bw, engine=eng, n=n, hist=json.loads(hj), floor_dbm=fl, p50=p50, p90=p90, peak=pk, busy_frac=bf, discarded=d, dwell_s=dw)

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

    def channel_summary_by_bw(self, run_id: int | None = None, since: float | None = None) -> list[dict]:
        """Like channel_summary but one row per (freq_hz, bw_hz), so 500 kHz-bandwidth energy is not blended
        with the narrow measurements (the 500 kHz slot recommendation needs the width it will actually run)."""
        w, a = self._where(run_id, since)
        per: dict[tuple, dict] = {}
        for f, fl, p90, pk, bf, n, bw in self.con.execute(
                "SELECT freq_hz, floor_dbm, p90, peak, busy_frac, n, bw_hz FROM energy" + w, a):
            d = per.setdefault((f, bw), {"freq_hz": f, "bw_hz": bw, "floors": [], "p90s": [],
                                         "peak_max": -999.0, "busy": [], "n_rows": 0, "n_samples": 0})
            d["floors"].append(fl); d["p90s"].append(p90); d["peak_max"] = max(d["peak_max"], pk)
            d["busy"].append(bf); d["n_rows"] += 1; d["n_samples"] += n
        out = []
        for (f, bw) in sorted(per):
            d = per[(f, bw)]; fl = sorted(d["floors"]); p9 = sorted(d["p90s"])
            out.append({"freq_hz": f, "bw_hz": bw, "floor_med": fl[len(fl) // 2], "p90_med": p9[len(p9) // 2],
                        "peak_max": d["peak_max"], "busy_mean": sum(d["busy"]) / len(d["busy"]),
                        "n_rows": d["n_rows"], "n_samples": d["n_samples"]})
        return out

    def time_buckets(self, bucket_s: int = 60, run_id: int | None = None, since: float | None = None) -> list[dict]:
        w, a = self._where(run_id, since)
        acc: dict[tuple, list] = {}
        for ts, f, bf, p90 in self.con.execute("SELECT ts, freq_hz, busy_frac, p90 FROM energy" + w, a):
            key = (float(int(ts // bucket_s) * bucket_s), f)
            acc.setdefault(key, [0.0, 0.0, 0])
            acc[key][0] += bf; acc[key][1] += p90; acc[key][2] += 1
        return [{"bucket": k[0], "freq_hz": k[1], "busy_mean": v[0] / v[2], "p90_mean": v[1] / v[2], "n": v[2]} for k, v in sorted(acc.items())]

    # ---- CAD --------------------------------------------------------------------------------
    def add_cad(self, run_id: int, r: CadRow) -> None:
        self.con.execute("INSERT INTO cad(run_id, ts, freq_hz, bw_hz, sf, symbols, n_cad, hits, longest_run, det_peak, det_min, timeouts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, r.ts, r.freq_hz, r.bw_hz, r.sf, r.symbols, r.n_cad, r.hits, r.longest_run, r.det_peak, r.det_min, r.timeouts))
        self.con.commit()

    def iter_cad(self, run_id: int | None = None, since: float | None = None) -> Iterator[CadRow]:
        w, a = self._where(run_id, since)
        for row in self.con.execute("SELECT ts, freq_hz, bw_hz, sf, symbols, n_cad, hits, longest_run, det_peak, det_min, timeouts FROM cad" + w + " ORDER BY ts", a):
            yield CadRow(*row)

    def cad_summary(self, run_id: int | None = None, since: float | None = None) -> list[dict]:
        w, a = self._where(run_id, since)
        acc: dict[tuple, dict] = {}
        for f, sf, bw, n, h, lr in self.con.execute("SELECT freq_hz, sf, bw_hz, n_cad, hits, longest_run FROM cad" + w, a):
            d = acc.setdefault((f, sf, bw), {"freq_hz": f, "sf": sf, "bw_hz": bw, "n_cad": 0, "hits": 0, "longest_run": 0, "n_rows": 0})
            d["n_cad"] += n; d["hits"] += h; d["longest_run"] = max(d["longest_run"], lr); d["n_rows"] += 1
        out = []
        for k in sorted(acc):
            d = acc[k]; d["hit_rate"] = d["hits"] / d["n_cad"] if d["n_cad"] else 0.0; out.append(d)
        return out

    # ---- decode -----------------------------------------------------------------------------
    def add_decode(self, run_id: int, r: DecodeRow) -> None:
        self.con.execute("INSERT INTO decode(run_id, ts, freq_hz, network, preset, dwell_s, n_ok, n_crc_err, rssi_med, snr_med, len_med) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, r.ts, r.freq_hz, r.network, r.preset, r.dwell_s, r.n_ok, r.n_crc_err, r.rssi_med, r.snr_med, r.len_med))
        self.con.commit()

    def iter_decode(self, run_id: int | None = None, since: float | None = None) -> Iterator[DecodeRow]:
        w, a = self._where(run_id, since)
        for row in self.con.execute("SELECT ts, freq_hz, network, preset, dwell_s, n_ok, n_crc_err, rssi_med, snr_med, len_med FROM decode" + w + " ORDER BY ts", a):
            yield DecodeRow(*row)

    def decode_summary(self, run_id: int | None = None, since: float | None = None) -> list[dict]:
        w, a = self._where(run_id, since)
        acc: dict[tuple, dict] = {}
        for f, net, pre, dw, ok, bad, rssi in self.con.execute("SELECT freq_hz, network, preset, dwell_s, n_ok, n_crc_err, rssi_med FROM decode" + w, a):
            d = acc.setdefault((f, net, pre), {"freq_hz": f, "network": net, "preset": pre, "dwell_s": 0.0, "n_ok": 0, "n_crc_err": 0, "rssis": []})
            d["dwell_s"] += dw; d["n_ok"] += ok; d["n_crc_err"] += bad
            if ok:
                d["rssis"].append(rssi)
        out = []
        for k in sorted(acc):
            d = acc[k]; r = sorted(d.pop("rssis")); d["rssi_med"] = r[len(r) // 2] if r else 0.0; d["rate_per_min"] = d["n_ok"] / d["dwell_s"] * 60 if d["dwell_s"] else 0.0; out.append(d)
        return out

    def event_counts(self, run_id: int) -> dict:
        return {k: n for k, n in self.con.execute("SELECT kind, COUNT(*) FROM events WHERE run_id = ? GROUP BY kind", (run_id,))}

    def counts(self, run_id: int) -> dict:
        out = {}
        for t in ("energy", "cad", "decode"):
            out[t] = self.con.execute(f"SELECT COUNT(*), MAX(ts) FROM {t} WHERE run_id = ?", (run_id,)).fetchone()
        return out

    def runs(self) -> list[dict]:
        out = []
        for rid, st, kind, prof, note in self.con.execute("SELECT id, started_ts, kind, profile, note FROM runs ORDER BY id"):
            f, l, n = self.con.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM energy WHERE run_id = ?", (rid,)).fetchone()
            out.append({"id": rid, "started_ts": st, "kind": kind, "profile": prof, "note": note, "first_ts": f, "last_ts": l, "n_rows": n})
        return out
