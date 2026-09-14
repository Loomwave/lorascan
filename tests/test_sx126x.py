import pytest
from lorascan.hal.fake import FakeHal
from lorascan.profile import load_profile
from lorascan.radio.sx126x import Sx126x, FREQ_STEP, DeviceError, BusyTimeout

def mk(replies=None):
    hal = FakeHal(replies if replies is not None else {0x17: bytes([0, 0, 0, 0]), 0x15: bytes([0, 0, 0xC8])})
    return hal, Sx126x(hal, load_profile("nebra-duo-hat"))

def test_init_issues_the_proven_command_order():
    hal, r = mk()
    r.init(911_500_000)
    ops = [t[0] for t in hal.log]
    assert ops == [0x80, 0x97, 0x07, 0x89, 0x17, 0x8A, 0x98, 0x86, 0x95, 0x8E, 0x9D, 0x8F, 0x8B, 0x0D, 0x0D, 0x08]
    assert hal.reset_log == [False, True]

def test_init_frequency_and_tx_power_and_modparams_bytes():
    hal, r = mk()
    r.init(911_500_000, sf=9, bw_khz=125, cr=5)
    f = [t for t in hal.log if t[0] == 0x86][0]
    assert f[1:5] == int(911.5e6 / FREQ_STEP).to_bytes(4, "big")
    tx = [t for t in hal.log if t[0] == 0x8E][0]
    assert tx[1] == 0xF7 and tx[2] == 0x04            # -9 dBm as u8, 200 us ramp; never higher
    mod = [t for t in hal.log if t[0] == 0x8B][0]
    assert list(mod[1:5]) == [9, 0x04, 1, 0]           # sf9, bw125, cr 4/5 -> 1, ldro off
    regs = [t for t in hal.log if t[0] == 0x0D]
    assert regs[0][1:5] == bytes([0x07, 0x40, 0x14, 0x24])   # private sync word
    assert regs[1][1:4] == bytes([0x08, 0xAC, 0x96])          # rx boosted gain

def test_ldro_on_for_sf12_bw125():
    hal, r = mk(); r.init(905_000_000, sf=12, bw_khz=125)
    mod = [t for t in hal.log if t[0] == 0x8B][0]
    assert mod[4] == 1

def test_rssi_inst_decodes_minus_raw_over_two():
    hal, r = mk()
    assert r.rssi_inst() == -100.0

def test_init_raises_on_device_errors_other_than_rc64k():
    hal, r = mk({0x17: bytes([0, 0, 0x00, 0x08]), 0x15: bytes([0, 0, 0])})
    with pytest.raises(DeviceError):
        r.init(911_500_000)
    hal2, r2 = mk({0x17: bytes([0, 0, 0x00, 0x01]), 0x15: bytes([0, 0, 0])})
    r2.init(911_500_000)   # RC64K calibration flag tolerated

def test_wait_busy_times_out_after_one_second_of_busy():
    hal, r = mk()
    hal.busy_sequence = [True] * 100000
    with pytest.raises(BusyTimeout):
        r.chip_status()
    assert hal.clock >= 1.0

def test_probe_bytes_verdicts():
    hal, r = mk({0xC0: bytes([0, 0x22]), 0x1D: bytes([0, 0, 0, 0, 0x14, 0x24])})
    p = r.probe_bytes()
    assert p["verdict"] == "GOOD" and p["status"] == 0x22 and p["sync"] == (0x14, 0x24)
    hal, r = mk({0xC0: bytes([0xFF, 0xFF]), 0x1D: bytes([0xFF] * 6)})
    assert r.probe_bytes()["verdict"].startswith("all 0xFF")

def test_set_frequency_rx_and_standby_opcodes():
    hal, r = mk()
    r.set_frequency(902_500_000); r.rx_continuous(); r.standby()
    ops = [t[0] for t in hal.log]
    assert ops == [0x86, 0x82, 0x80]
    assert hal.log[1][1:4] == bytes([0xFF, 0xFF, 0xFF])

def test_no_transmit_api_in_p1():
    hal, r = mk()
    assert not hasattr(r, "transmit")
