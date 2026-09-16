import json
import os
from lorascan import cli, station_config as sc
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow

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

def _write_cfg(tmp_path, **kw):
    home = tmp_path / "home"; (home / ".config" / "lorascan").mkdir(parents=True)
    sc.save(sc.StationConfig(**kw), path=str(home / ".config" / "lorascan" / "config.yaml"))
    return str(home)

def test_share_uses_config_endpoint_when_no_flag(tmp_path, monkeypatch, capsys):
    home = _write_cfg(tmp_path, endpoint="https://cfg.example", granularity="day")
    monkeypatch.setenv("HOME", home)
    sent = {}
    monkeypatch.setattr("lorascan.cli.upload_share", lambda doc, to, **k: sent.update(to=to) or
                        {"sent": 0, "skipped": 0, "bytes": 0, "attempts": 1, "watermark": None, "accepted": 0})
    # a tiny db with one run so build_share has something; reuse the helper the other cli tests use
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    cli.main(["share", "--db", db, "--out", str(tmp_path / "s.json")])
    assert sent.get("to") == "https://cfg.example"

def test_explicit_flag_beats_config(tmp_path, monkeypatch):
    home = _write_cfg(tmp_path, endpoint="https://cfg.example")
    monkeypatch.setenv("HOME", home)
    sent = {}
    monkeypatch.setattr("lorascan.cli.upload_share", lambda doc, to, **k: sent.update(to=to) or
                        {"sent": 0, "skipped": 0, "bytes": 0, "attempts": 1, "watermark": None, "accepted": 0})
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    cli.main(["share", "--db", db, "--out", str(tmp_path / "s.json"), "--to", "https://flag.example"])
    assert sent.get("to") == "https://flag.example"

def test_no_config_behaves_as_today(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    rc = cli.main(["share", "--db", db, "--out", str(tmp_path / "s.json")])  # no --to, no cfg
    assert rc == 0  # writes the file, uploads nothing, unchanged

def test_share_uses_config_granularity_when_no_flag(tmp_path, monkeypatch):
    home = _write_cfg(tmp_path, granularity="day")
    monkeypatch.setenv("HOME", home)
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    out = str(tmp_path / "s.json")
    rc = cli.main(["share", "--db", db, "--out", out])  # no --granularity
    assert rc == 0
    assert json.load(open(out))["granularity"] == "day"

def test_explicit_granularity_beats_config(tmp_path, monkeypatch):
    home = _write_cfg(tmp_path, granularity="day")
    monkeypatch.setenv("HOME", home)
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    out = str(tmp_path / "s.json")
    rc = cli.main(["share", "--db", db, "--out", out, "--granularity", "hour"])
    assert rc == 0
    assert json.load(open(out))["granularity"] == "hour"

def test_setup_subcommand_runs_with_injected_wizard(monkeypatch, tmp_path):
    # End-to-end over the MANUAL pin path (real BoardProfile, no load_profile lookup), skipping
    # step_preflight (host-specific; covered by its own unit test). Fake Runner so no hardware:
    # manual pins -> validate GOOD -> skip location -> endpoint saved-not-verified -> first light -> write.
    from lorascan import setup
    class R(setup.Runner):
        def probe(self, p): return {"verdict": "GOOD"}
        def selftest(self, p): return {"ok": True}
        def endpoint_health(self, u): return {"ok": False, "error": "offline in test"}
        def sweep(self, p): return [(915_000_000, -55)]
    def fake_build(a):
        io = setup.ScriptedIO(["3", "kernel", "/dev/spidev0.0", "22", "23", "24", "", ""])
        w = setup.Wizard(io, R(), home=str(tmp_path))
        w._steps = setup._default_steps()[1:]   # drop step_preflight for this host-independent e2e
        return w
    monkeypatch.setattr("lorascan.cli._build_wizard", fake_build, raising=False)
    rc = cli.main(["setup"])
    assert rc == 0
    from lorascan import station_config as sc
    assert sc.load(home=str(tmp_path)).profile == "manual"

def test_build_wizard_threads_db_into_state():
    import types
    w = cli._build_wizard(types.SimpleNamespace(db="site.db"))
    assert w.state["db"] == "site.db"
    w2 = cli._build_wizard(types.SimpleNamespace(db=None))
    assert not w2.state.get("db")

def _e(freq, bw, floor, busy):
    return EnergyRow(ts=1.0, freq_hz=freq, bw_hz=bw, engine="poll", n=10, hist=[0] * 10, floor_dbm=floor,
                      p50=floor + 5, p90=floor + 10, peak=floor + 30, busy_frac=busy, discarded=0, dwell_s=1.0)

def test_report_prints_best_slot(tmp_path, capsys):
    db = str(tmp_path / "s.db"); st = Store(db); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(910_000_000, 500_000, -119.0, 0.02))
    assert cli.main(["report", "--db", db, "--out", str(tmp_path / "r.html")]) == 0
    out = capsys.readouterr().out
    assert "best 500 kHz slot" in out and "910.00" in out

def test_report_recommend_bw_zero_suppresses(tmp_path, capsys):
    db = str(tmp_path / "s.db"); st = Store(db); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(910_000_000, 500_000, -119.0, 0.02))
    assert cli.main(["report", "--db", db, "--out", str(tmp_path / "r.html"), "--recommend-bw", "0"]) == 0
    assert "best 500 kHz slot" not in capsys.readouterr().out
