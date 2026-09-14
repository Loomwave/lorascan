import pytest
from lorascan.hal.fake import FakeHal
from lorascan.profile import load_profile
from lorascan.radio.sx126x import Sx126x, IRQ_CAD_DONE, IRQ_CAD_DETECTED
from lorascan.measure.cad import cad_params_for, cad_sweep, CadRow, cad_duration_s

def mk(irq_seq):
    seq = iter(irq_seq)
    def irq(tx):
        v = next(seq)
        return bytes([0, 0]) + v.to_bytes(2, "big")
    hal = FakeHal({0x17: bytes(4), 0x12: irq})
    return hal, Sx126x(hal, load_profile("nebra-duo-hat"))

def test_default_cad_params_per_sf():
    assert cad_params_for(7) == (2, 22, 10) and cad_params_for(9) == (2, 23, 10) and cad_params_for(12) == (2, 28, 10)
    with pytest.raises(ValueError):
        cad_params_for(4)

def test_cad_duration_formula():
    # N symbols x 2^SF/BW + 32/BW: SF7/BW125 2 symbols = 2*1.024 ms + 0.256 ms
    assert abs(cad_duration_s(7, 125, 2) - (2 * 128 / 125000 + 32 / 125000)) < 1e-9

def test_cad_sweep_counts_hits_and_runs_and_issues_the_commands():
    # 6 CADs: done+detected x2, done x1, done+detected x3  -> hits 5, longest run 3
    pattern = [IRQ_CAD_DONE | IRQ_CAD_DETECTED] * 2 + [IRQ_CAD_DONE] + [IRQ_CAD_DONE | IRQ_CAD_DETECTED] * 3
    hal, r = mk(pattern)
    row = cad_sweep(r, 906_875_000, sf=11, bw_khz=250, n_cad=6, clock=lambda: hal.clock)
    assert isinstance(row, CadRow) and row.n_cad == 6 and row.hits == 5 and row.longest_run == 3
    assert row.sf == 11 and row.bw_hz == 250_000 and (row.det_peak, row.det_min, row.symbols) == (25, 10, 2)
    ops = [t[0] for t in hal.log]
    assert 0x8B in ops and 0x86 in ops and 0x88 in ops         # mod params, frequency, cad params
    cadp = [t for t in hal.log if t[0] == 0x88][0]
    assert list(cadp[1:8]) == [0x01, 25, 10, 0x00, 0, 0, 0]      # 2 symbols=0x01, peak, min, CAD_ONLY, timeout 0
    assert ops.count(0xC5) == 6                                  # six SetCad
    assert ops[-1] == 0x80                                       # standby after

def test_cad_sweep_times_out_when_cad_never_completes():
    hal, r = mk([0] * 100000)
    row = cad_sweep(r, 906_875_000, sf=7, bw_khz=125, n_cad=2, clock=lambda: hal.clock)
    assert row.n_cad == 0 and row.timeouts == 2
