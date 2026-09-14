"""Loomwave/lorascan#1: export --csv wrote only the energy table."""
import csv, os
from lorascan import cli
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow
from lorascan.measure.decode import DecodeRow


def _db(tmp_path):
    s = Store(str(tmp_path / "w.db")); rid = s.new_run("watch", "fake", "")
    s.add_energy(rid, EnergyRow(ts=1.0, freq_hz=906_875_000, bw_hz=125_000, engine="poll", n=10, floor_dbm=-110, p50=-105, p90=-100, peak=-90, busy_frac=0.1))
    for k in range(3):
        s.add_cad(rid, CadRow(ts=2.0 + k, freq_hz=906_875_000, bw_hz=250_000, sf=7 + k, symbols=2, n_cad=50, hits=k, longest_run=1, det_peak=22, det_min=10))
    s.add_decode(rid, DecodeRow(ts=6.0, freq_hz=910_525_000, network="meshcore", preset="us-narrow", dwell_s=10.0, n_ok=1, n_crc_err=0, rssi_med=-33.0, snr_med=12.0, len_med=40))
    s.close(); return str(tmp_path / "w.db")


def _rows(p):
    with open(p) as f:
        return list(csv.DictReader(f))


def test_export_writes_cad_and_decode_tables_beside_energy(tmp_path, capsys):
    db = _db(tmp_path); out = str(tmp_path / "watch.csv")
    assert cli.main(["export", "--db", db, "--csv", out]) == 0
    e, c, d = _rows(out), _rows(str(tmp_path / "watch-cad.csv")), _rows(str(tmp_path / "watch-decode.csv"))
    assert len(e) == 1 and len(c) == 3 and len(d) == 1
    assert c[2]["sf"] == "9" and c[2]["hits"] == "2" and set(c[0]) >= {"ts", "freq_hz", "bw_hz", "sf", "n_cad", "hits", "hit_rate", "longest_run", "timeouts"}
    assert d[0]["network"] == "meshcore" and d[0]["n_ok"] == "1" and "payload" not in d[0]
    msg = capsys.readouterr().out
    assert "watch-cad.csv (3 rows)" in msg and "watch-decode.csv (1 rows)" in msg and "watch.csv (1 rows)" in msg


def test_export_single_table_flag(tmp_path):
    db = _db(tmp_path); out = str(tmp_path / "only.csv")
    assert cli.main(["export", "--db", db, "--csv", out, "--table", "cad"]) == 0
    assert len(_rows(out)) == 3 and not os.path.exists(str(tmp_path / "only-cad.csv")) and not os.path.exists(str(tmp_path / "only-decode.csv"))


def test_export_skips_empty_side_tables(tmp_path):
    s = Store(str(tmp_path / "e.db")); rid = s.new_run("quick", "fake", "")
    s.add_energy(rid, EnergyRow(ts=1.0, freq_hz=902_000_000, bw_hz=125_000, engine="poll", n=1)); s.close()
    out = str(tmp_path / "q.csv")
    assert cli.main(["export", "--db", str(tmp_path / "e.db"), "--csv", out]) == 0
    assert os.path.exists(out) and not os.path.exists(str(tmp_path / "q-cad.csv")) and not os.path.exists(str(tmp_path / "q-decode.csv"))
