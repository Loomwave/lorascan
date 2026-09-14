"""Loomwave/lorascan#4: LoRaWAN US915 decode covered only DR3 (SF7/125)."""
from lorascan.networks import LORAWAN_US915, presets_on, Preset
from lorascan.measure.decode import decode_dwell
from lorascan.hal.fake import FakeHal
from lorascan.radio.sx126x import Sx126x, OP_SET_PKT_PARAMS
from lorascan.profile import load_profile


def test_lorawan_uplink_rates_dr0_to_dr4_and_downlinks_dr8_to_dr13():
    by = {p.name: p for p in LORAWAN_US915.presets}
    for dr, sf in ((0, 10), (1, 9), (2, 8), (3, 7)):
        p = by[f"uplink-dr{dr}"]
        assert (p.sf, p.bw_khz, p.crc_on, p.invert_iq) == (sf, 125, True, False) and len(p.freqs_hz) == 64 and p.freqs_hz[0] == 902_300_000
    p = by["uplink-dr4"]; assert (p.sf, p.bw_khz) == (8, 500) and len(p.freqs_hz) == 8 and p.freqs_hz[1] == 904_600_000
    for dr, sf in ((8, 12), (9, 11), (10, 10), (11, 9), (12, 8), (13, 7)):
        p = by[f"downlink-dr{dr}"]
        assert (p.sf, p.bw_khz, p.crc_on, p.invert_iq) == (sf, 500, False, True) and len(p.freqs_hz) == 8 and p.freqs_hz[0] == 923_300_000
    names = [n for n, _ in presets_on(902_300_000)]
    assert names.count("lorawan-us915") == 4          # DR0-DR3 all listed on a raster frequency
    assert [p.sf for n, p in presets_on(923_300_000) if n == "lorawan-us915"] == [12, 11, 10, 9, 8, 7]


def test_decode_dwell_applies_invert_iq_and_counts_crc_free_frames():
    from lorascan.radio.sx126x import IRQ_RX_DONE
    irqs = iter([IRQ_RX_DONE, 0, IRQ_RX_DONE] + [0] * 100000)     # downlinks carry no PHY CRC: RxDone alone is a frame
    def irq(tx): return bytes([0, 0]) + next(irqs).to_bytes(2, "big")
    hal = FakeHal({0x17: bytes(4), 0x12: irq, 0x13: bytes([0, 0, 12, 0]), 0x14: bytes([0, 0, 100, 20, 100]), 0x1E: bytes(64)})
    radio = Sx126x(hal, load_profile("nebra-duo-hat"))
    p = [p for p in LORAWAN_US915.presets if p.name == "downlink-dr13"][0]
    row = decode_dwell(radio, 923_300_000, LORAWAN_US915, p, dwell_s=0.05, clock=lambda: hal.clock)
    assert row.n_ok == 2 and row.n_crc_err == 0
    pkt = [t for t in hal.log if t[0] == OP_SET_PKT_PARAMS][-1]
    assert pkt[5] == 0x00 and pkt[6] == 0x01          # crc off, invert_iq on
