import os
from lorascan import cli
from lorascan.store.db import Store

def test_scan_quick_with_fake_profile_writes_rows_and_report(tmp_path, capsys):
    db = str(tmp_path / "s.db")
    rc = cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001"])
    assert rc == 0
    s = Store(db)
    rows = list(s.iter_energy())
    assert len(rows) == 130 + 14 and all(r.n > 0 for r in rows) and rows[0].engine == "poll"
    out = str(tmp_path / "r.html")
    assert cli.main(["report", "--db", db, "--out", out]) == 0 and os.path.getsize(out) > 5000
    csv = str(tmp_path / "e.csv")
    assert cli.main(["export", "--db", db, "--csv", csv]) == 0
    assert open(csv).readline().startswith("ts,freq_hz,bw_hz,engine,n,floor_dbm")

def test_probe_and_selftest_on_fake(capsys):
    assert cli.main(["probe", "--profile", "fake"]) == 0
    assert "GOOD" in capsys.readouterr().out
    assert cli.main(["selftest", "--profile", "fake", "--dwell", "0.01", "--sample-gap", "0.001"]) == 0
    assert "PASS" in capsys.readouterr().out

def test_scan_survey_duration_limit(tmp_path):
    db = str(tmp_path / "s.db")
    rc = cli.main(["scan", "survey", "--profile", "fake", "--db", db, "--dwell", "0.002", "--sample-gap", "0.001", "--duration", "1s", "--fake-clock"])
    # fake clock: each step costs SETTLE_S (0.02) + dwell (0.002) = 0.022 s, so a 1 s limit yields about 45 rows
    n = len(list(Store(db).iter_energy()))
    assert rc == 0 and 35 <= n <= 50

def test_engine_scan_falls_back_to_poll_on_the_fake_radio(tmp_path, capsys):
    db = str(tmp_path / "s.db")
    rc = cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001", "--engine", "scan"])
    err = capsys.readouterr().err
    assert rc == 0 and "falling back to the polled engine" in err
    rows = list(Store(db).iter_energy())
    assert rows and all(r.engine == "poll" for r in rows) and len(rows) == 144 - 2
