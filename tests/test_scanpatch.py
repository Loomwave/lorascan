import pytest
from lorascan.hal.fake import FakeHal
from lorascan.profile import load_profile
from lorascan.radio.sx126x import Sx126x
from lorascan.radio.patch_scan_bin import PATCH_WORDS
from lorascan.radio.scanpatch import upload_patch, spectral_scan, scan_energy, ScanAborted, ScanTimeout, hist_stats, setup_scan_mode, gfsk_bw_code

def mk(replies):
    base = {0x17: bytes(4)}; base.update(replies)
    hal = FakeHal(base); return hal, Sx126x(hal, load_profile("nebra-duo-hat"))

def test_upload_patch_sequence_matches_radiolib():
    hal, r = mk({})
    upload_patch(r)
    log = hal.log
    assert log[0][:2] == bytes([0x80, 0x00])                      # standby RC
    assert log[1] == bytes([0x0D, 0x06, 0x10, 0x10])              # patch update enable
    words = [t for t in log if t[0] == 0x0D and t[1] == 0x80 or (t[0] == 0x0D and 0x80 <= t[1] <= 0x86)]
    assert len(words) == len(PATCH_WORDS)
    assert words[0] == bytes([0x0D, 0x80, 0x00]) + PATCH_WORDS[0].to_bytes(4, "big")
    assert words[1] == bytes([0x0D, 0x80, 0x04]) + PATCH_WORDS[1].to_bytes(4, "big")
    assert log[-2] == bytes([0x0D, 0x06, 0x10, 0x00])             # patch update disable
    assert log[-1] == bytes([0xD9])                               # PRAM update

def test_spectral_scan_reads_33_counts():
    status = iter([0x0F, 0x0F, 0xFF])
    counts = list(range(33)); counts[5] = 1500
    res = b"".join(c.to_bytes(2, "big") for c in counts)
    def rr(tx):
        if tx[1:3] == bytes([0x07, 0xCD]): return bytes(4) + bytes([next(status)])
        if tx[1:3] == bytes([0x04, 0x01]): return bytes(4) + res
        return bytes(len(tx))
    hal, r = mk({0x1D: rr})
    counts[5] = 2048 - sum(c for i, c in enumerate(counts) if i != 5)   # a complete 2048-sample histogram
    res2 = b"".join(c.to_bytes(2, "big") for c in counts)
    hal.replies[0x1D] = lambda tx: (bytes(4) + bytes([next(status)])) if tx[1:3] == bytes([0x07, 0xCD]) else (bytes(4) + res2 if tx[1:3] == bytes([0x04, 0x01]) else bytes(len(tx)))
    h = spectral_scan(r, nb_scan=2048, interval=11, window=0x14, timeout_s=1.0)
    assert h == counts and hal.clock >= 2048 * 8.2e-6           # waited the nominal scan time before polling
    ops = [t[0] for t in hal.log]
    assert hal.log[0][1:4] == bytes([0x08, 0x9B, 0x00])                        # abort/clear first
    assert hal.log[1][1:4] == bytes([0x08, 0x9B, 0x14])                        # RSSI averaging window
    assert ops[2] == 0x82 and hal.log[3][:4] == bytes([0x9B, 0x08, 0x00, 11])  # SetRx inf, then scan params

def test_spectral_scan_aborted_and_timeout():
    hal, r = mk({0x1D: lambda tx: bytes(4) + bytes([0xF0]) if tx[1:3] == bytes([0x07, 0xCD]) else bytes(len(tx))})
    with pytest.raises(ScanAborted):
        spectral_scan(r, 100)
    hal, r = mk({0x1D: lambda tx: bytes(4) + bytes([0x0F]) if tx[1:3] == bytes([0x07, 0xCD]) else bytes(len(tx))})
    with pytest.raises(ScanTimeout):
        spectral_scan(r, 100, timeout_s=0.05)

def test_hist_stats_from_levels():
    h = [0] * 33; h[24] = 900; h[20] = 100        # 900 samples at -107 dBm, 100 at -91 dBm (offset -11)
    s = hist_stats(h, offset_dbm=-11, busy_t_db=8.0)
    assert s["floor_dbm"] == -107.0 and s["peak"] == -91.0 and s["p90"] == -91.0 and abs(s["busy_frac"] - 0.1) < 1e-9

def test_scan_energy_row():
    counts = [0] * 33; counts[24] = 2000
    res = b"".join(c.to_bytes(2, "big") for c in counts)   # complete: 2000 of nb_scan=2000
    hal, r = mk({0x1D: lambda tx: (bytes(4) + bytes([0xFF])) if tx[1:3] == bytes([0x07, 0xCD]) else (bytes(4) + res if tx[1:3] == bytes([0x04, 0x01]) else bytes(len(tx)))})
    row = scan_energy(r, 911_500_000, 125, nb_scan=2000, offset_dbm=-11)
    assert row.engine == "scan" and row.n == 2000 and row.hist == counts and row.floor_dbm == -107.0 and row.freq_hz == 911_500_000


def test_setup_scan_mode_is_semtechs_gfsk_recipe():
    hal, r = mk({})
    setup_scan_mode(r, 125.0)
    log = hal.log
    assert log[0][:2] == bytes([0x80, 0x00]) and log[1] == bytes([0x8F, 0x80, 0x80])
    assert log[2] == bytes([0x0D, 0x08, 0xAC, 0xCB]) and log[3] == bytes([0x8A, 0x00])
    assert log[4] == bytes([0x0D, 0x08, 0x9B, 0x14])
    assert log[5] == bytes([0x8B, 0x00, 0x14, 0x00, 0x00, 0x1A, 0x02, 0xE9, 0x0F])   # 125 kHz -> nearest GFSK RX BW at/above = 156.2 kHz = 0x1A
    assert log[6] == bytes([0x8C, 0x00, 0x20, 0x05, 0x20, 0x00, 0x01, 0xFF, 0x01, 0x00])

def test_gfsk_bw_code_picks_nearest_at_or_above():
    assert gfsk_bw_code(125.0) == 0x1A and gfsk_bw_code(234.3) == 0x0A and gfsk_bw_code(62.5) == 0x1B and gfsk_bw_code(500) == 0x09


def test_incomplete_histogram_is_an_error():
    from lorascan.radio.scanpatch import ScanError
    counts = [0] * 33; counts[24] = 100
    res = b"".join(c.to_bytes(2, "big") for c in counts)
    hal, r = mk({0x1D: lambda tx: (bytes(4) + bytes([0xFF])) if tx[1:3] == bytes([0x07, 0xCD]) else (bytes(4) + res if tx[1:3] == bytes([0x04, 0x01]) else bytes(len(tx)))})
    with pytest.raises(ScanError):
        spectral_scan(r, 2000)


def test_patch_is_reuploaded_after_a_reset(monkeypatch):
    counts = [0] * 33; counts[24] = 2000
    res = b"".join(c.to_bytes(2, "big") for c in counts)
    hal, r = mk({0x1D: lambda tx: (bytes(4) + bytes([0xFF])) if tx[1:3] == bytes([0x07, 0xCD]) else (bytes(4) + res if tx[1:3] == bytes([0x04, 0x01]) else bytes(len(tx)))})
    upload_patch(r); assert r.patch_loaded
    r.init(911_500_000)                     # reset inside init clears the patch
    assert not r.patch_loaded
    hal.log.clear()
    scan_energy(r, 911_500_000, 125, nb_scan=2000)
    assert bytes([0xD9]) in hal.log         # PRAM update = the patch was uploaded again before scanning
    assert r.patch_loaded
