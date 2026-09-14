"""Field bug (share.lorascan.app, 2026-09-14): a polled-engine submitter's cell showed 0.0 hours because the share
document's `hours` was n_samples x 8.2 us (the scan engine's sample period). Store the visit's dwell and use it."""
import sqlite3
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow, polled_energy
from lorascan.radio.scanpatch import scan_energy
from lorascan.share import build_share
from lorascan.hal.fake import FakeHal
from lorascan.radio.sx126x import Sx126x
from lorascan.profile import load_profile


def test_energy_row_dwell_is_stored_and_read_back(tmp_path):
    s = Store(str(tmp_path / "d.db")); rid = s.new_run("quick", "fake", "")
    s.add_energy(rid, EnergyRow(ts=1.0, freq_hz=902_000_000, bw_hz=125_000, engine="poll", n=500, dwell_s=0.4))
    s.add_energy(rid, EnergyRow(ts=2.0, freq_hz=902_000_000, bw_hz=125_000, engine="scan", n=48780))       # dwell unknown (old row)
    rows = list(s.iter_energy())
    assert rows[0].dwell_s == 0.4 and rows[1].dwell_s is None


def test_old_database_without_the_column_is_migrated(tmp_path):
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT, profile TEXT, note TEXT, first_ts REAL, last_ts REAL);"
                      "CREATE TABLE energy (run_id INTEGER, ts REAL, freq_hz INTEGER, bw_hz INTEGER, engine TEXT, n INTEGER, hist_json TEXT, floor_dbm REAL, p50 REAL, p90 REAL, peak REAL, busy_frac REAL, discarded INTEGER);")
    con.execute("INSERT INTO runs VALUES (1, 'quick', 'fake', '', 1.0, 1.0)")
    con.execute("INSERT INTO energy VALUES (1, 1.0, 902000000, 125000, 'poll', 500, '[]', -110, -105, -100, -90, 0.1, 0)")
    con.commit(); con.close()
    s = Store(p)
    assert list(s.iter_energy())[0].dwell_s is None
    s.add_energy(1, EnergyRow(ts=2.0, freq_hz=902_000_000, bw_hz=125_000, engine="poll", n=500, dwell_s=0.4))
    assert [r.dwell_s for r in s.iter_energy()] == [None, 0.4]


def test_measurements_record_their_dwell():
    hal = FakeHal({0x17: bytes(4), 0x15: bytes([0, 0, 0xC8]), 0x12: bytes([0, 0, 0, 0])})
    radio = Sx126x(hal, load_profile("nebra-duo-hat")); radio.init(902_000_000)
    row = polled_energy(radio, 902_000_000, 125, 0.05, clock=lambda: hal.clock, sample_gap_s=0.001)
    assert row.dwell_s == 0.05


def test_share_hours_use_dwell_and_fall_back_by_engine(tmp_path):
    s = Store(str(tmp_path / "h.db")); rid = s.new_run("survey", "fake", "")
    for k in range(9):                                                            # 9 polled visits of 0.4 s = 1 h/1000
        s.add_energy(rid, EnergyRow(ts=1e9 + k, freq_hz=902_000_000, bw_hz=125_000, engine="poll", n=500, dwell_s=0.4))
    s.add_energy(rid, EnergyRow(ts=1e9 + 20, freq_hz=911_500_000, bw_hz=125_000, engine="scan", n=48780))   # old scan row: 48780 x 8.2 us
    s.add_energy(rid, EnergyRow(ts=1e9 + 21, freq_hz=921_000_000, bw_hz=125_000, engine="poll", n=500))     # old polled row: 500 x 0.7 ms
    doc = build_share(s, None, "0123abcd0123abcd", "fake", granularity="day")
    by = {e["freq_hz"]: e["hours"] for e in doc["energy"]}
    assert by[902_000_000] == round(9 * 0.4 / 3600, 6) and by[911_500_000] == round(48780 * 8.2e-6 / 3600, 6) and by[921_000_000] == round(500 * 0.0007 / 3600, 6)
