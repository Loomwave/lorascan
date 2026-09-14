from lorascan import cli
from lorascan.store.db import Store

def test_watch_and_test_modes_run_all_layers_on_the_fake_radio(tmp_path):
    db = str(tmp_path / "w.db")
    rc = cli.main(["scan", "watch", "--profile", "fake", "--db", db, "--freqs", "906.875,921.0", "--sfs", "7,11", "--bws", "125", "--dwell", "0.01", "--decode-dwell", "0.02", "--cycles", "1", "--sample-gap", "0.001"])
    assert rc == 0
    s = Store(db)
    assert len(list(s.iter_energy())) == 2 and len(list(s.iter_cad())) == 4 and len(list(s.iter_decode())) >= 1
    db2 = str(tmp_path / "t.db")
    rc = cli.main(["test", "--profile", "fake", "--db", db2, "--candidates", "905.0/9/125,921.0/11/250", "--dwell", "0.01", "--sample-gap", "0.001"])
    assert rc == 0
    s2 = Store(db2)
    assert s2.runs()[0]["kind"] == "test" and len(list(s2.iter_cad())) == 2
    out = str(tmp_path / "t.html")
    assert cli.main(["report", "--db", db2, "--out", out]) == 0
    html = open(out).read()
    assert "Candidate report card" in html and "905.000" in html

def test_quick_with_cad_adds_cad_rows(tmp_path):
    db = str(tmp_path / "q.db")
    rc = cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001", "--cad", "--cad-n", "3"])
    assert rc == 0
    s = Store(db)
    assert len(list(s.iter_energy())) == 144 and len(list(s.iter_cad())) >= 14 * 2   # known channels x (sf,bw) pairs at least
