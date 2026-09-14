import os, re
from lorascan import cli
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.report.html import auto_bucket_s, build_data

def test_status_reports_runs_rows_and_last_age(tmp_path, capsys):
    db = str(tmp_path / "s.db")
    assert cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001"]) == 0
    assert cli.main(["status", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "run #1 quick" in out and "144 energy" in out and re.search(r"last row .* ago", out)

def test_auto_bucket_keeps_the_heat_map_under_600_columns():
    assert auto_bucket_s(0) == 60 and auto_bucket_s(3600) == 60 and auto_bucket_s(20 * 3600) == 120 and auto_bucket_s(7 * 86400) == 1800

def test_close_checkpoints_the_wal_so_the_db_file_is_self_contained(tmp_path):
    p = str(tmp_path / "c.db"); s = Store(p); rid = s.new_run("quick", "x", "")
    for i in range(50):
        s.add_energy(rid, EnergyRow(ts=1e9 + i, freq_hz=902_000_000, bw_hz=125000, engine="poll", n=10, hist=[0]*33, floor_dbm=-110, p50=-105, p90=-100, peak=-90, busy_frac=0.0))
    s.close()
    assert not os.path.exists(p + "-wal") or os.path.getsize(p + "-wal") == 0
    assert len(list(Store(p).iter_energy())) == 50
