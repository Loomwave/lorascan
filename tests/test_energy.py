from lorascan.measure.energy import stats, hist33, polled_energy, EnergyRow
from lorascan.hal.fake import FakeHal
from lorascan.profile import load_profile
from lorascan.radio.sx126x import Sx126x

def test_stats_floor_p90_peak_busy():
    s = stats([-110.0] * 90 + [-90.0] * 10, busy_t_db=8.0)
    assert s["floor_dbm"] == -110.0 and s["p90"] == -90.0 and s["peak"] == -90.0
    assert abs(s["busy_frac"] - 0.10) < 1e-9 and s["p50"] == -110.0

def test_hist33_levels_4db_apart_and_underflow_bin():
    h = hist33([-11.0, -15.0, -139.0, -13.1], offset_dbm=-11)
    assert h[0] == 1 and h[1] == 2 and h[32] == 1 and sum(h) == 4 and len(h) == 33

def test_polled_energy_drives_radio_and_discards_unsettled():
    vals = iter([bytes([0, 0, 0x00]), bytes([0, 0, 0xFF])] + [bytes([0, 0, 0xD2])] * 1000)   # 0 dBm, -127.5 dBm discarded
    hal = FakeHal({0x15: lambda tx: next(vals), 0x17: bytes(4)})
    r = Sx126x(hal, load_profile("nebra-duo-hat"))
    row = polled_energy(r, 911_500_000, 125, dwell_s=0.02, clock=lambda: hal.clock, sample_gap_s=0.001)
    assert isinstance(row, EnergyRow) and row.engine == "poll"
    assert row.discarded == 2 and row.n >= 10 and row.floor_dbm == -105.0 and row.peak == -105.0
    ops = [t[0] for t in hal.log]
    assert ops[:2] == [0x86, 0x82] and ops[-1] == 0x80 and 0x15 in ops
    assert row.freq_hz == 911_500_000 and row.bw_hz == 125_000 and sum(row.hist) == row.n
