from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow

def row(ts, f, floor, p90=-90.0, busy=0.1):
    return EnergyRow(ts=ts, freq_hz=f, bw_hz=125000, engine="poll", n=100, hist=[0]*33, floor_dbm=floor, p50=floor, p90=p90, peak=p90, busy_frac=busy, discarded=0)

def test_roundtrip_and_summary(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rid = s.new_run("quick", "nebra-duo-hat", "test")
    for ts, fl in ((1000.0, -110.0), (1060.0, -108.0), (1120.0, -112.0)):
        s.add_energy(rid, row(ts, 911_500_000, fl))
    s.add_energy(rid, row(1000.0, 902_500_000, -111.0, p90=-100.0, busy=0.0))
    rows = list(s.iter_energy(rid))
    assert len(rows) == 4 and rows[0].hist == [0]*33 and rows[0].engine == "poll"
    summ = {c["freq_hz"]: c for c in s.channel_summary(rid)}
    assert summ[911_500_000]["floor_med"] == -110.0 and summ[911_500_000]["n_rows"] == 3 and abs(summ[911_500_000]["busy_mean"] - 0.1) < 1e-9
    assert summ[902_500_000]["peak_max"] == -100.0

def test_time_buckets_group_by_minute(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("survey", "x", "")
    s.add_energy(rid, row(1000.0, 911_500_000, -110.0, busy=0.2)); s.add_energy(rid, row(1030.0, 911_500_000, -110.0, busy=0.4)); s.add_energy(rid, row(1070.0, 911_500_000, -110.0, busy=0.0))
    b = s.time_buckets(60, rid)
    # buckets are floor(ts/60)*60: 1000 -> 960 (alone, busy 0.2); 1030 and 1070 -> 1020 (mean of 0.4 and 0.0 = 0.2)
    assert [(x["bucket"], x["freq_hz"], round(x["busy_mean"], 3), x["n"]) for x in b] == [(960.0, 911_500_000, 0.2, 1), (1020.0, 911_500_000, 0.2, 2)]

def test_runs_listed_and_span(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("quick", "p", "n")
    s.add_energy(rid, row(5.0, 902_000_000, -110.0)); s.add_energy(rid, row(65.0, 902_000_000, -110.0))
    r = s.runs()[0]
    assert r["id"] == rid and r["kind"] == "quick" and r["first_ts"] == 5.0 and r["last_ts"] == 65.0 and r["n_rows"] == 2
