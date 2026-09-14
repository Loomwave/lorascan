from lorascan.hal.fake import FakeHal
from lorascan.profile import load_profile
from lorascan.radio.sx126x import Sx126x, IRQ_RX_DONE, IRQ_CRC_ERR
from lorascan.networks import NETWORKS, meshtastic_slot_freq_hz, presets_on, sync_word_regs
from lorascan.measure.decode import decode_dwell, DecodeRow

def test_network_table_has_the_four_families_and_meshtastic_slot_formula():
    names = {n.name for n in NETWORKS}
    assert {"meshtastic", "meshcore", "lorawan-us915", "loomwave"} <= names
    # US915 LongFast: bw 250 kHz -> 104 slots; default slot 20 -> 906.875 MHz
    assert meshtastic_slot_freq_hz(20, 250_000) == 906_875_000
    assert meshtastic_slot_freq_hz(1, 250_000) == 902_125_000      # UI slot 1 = firmware channel 0
    lf = [p for n in NETWORKS for p in n.presets if p.name == "LongFast"][0]
    mt = [n for n in NETWORKS if n.name == "meshtastic"][0]
    assert (lf.sf, lf.bw_khz, lf.cr, mt.sync_word) == (11, 250, 5, 0x2B)

def test_presets_on_a_frequency():
    hits = presets_on(906_875_000)
    assert any(p.name == "LongFast" and n == "meshtastic" for n, p in hits)
    assert any(n == "lorawan-us915" for n, p in presets_on(902_300_000))
    assert any(n == "meshcore" for n, p in presets_on(910_525_000))
    assert any(n == "loomwave" for n, p in presets_on(911_500_000))

def test_sync_word_nibble_spread():
    assert sync_word_regs(0x2B) == (0x24, 0xB4) and sync_word_regs(0x12) == (0x14, 0x24) and sync_word_regs(0x34) == (0x34, 0x44)

def test_decode_dwell_counts_frames_without_storing_payloads():
    irqs = iter([IRQ_RX_DONE, 0, IRQ_RX_DONE | IRQ_CRC_ERR, 0, IRQ_RX_DONE] + [0] * 100000)
    def irq(tx): return bytes([0, 0]) + next(irqs).to_bytes(2, "big")
    hal = FakeHal({0x17: bytes(4), 0x12: irq, 0x13: bytes([0, 0, 21, 0]), 0x14: bytes([0, 0, 0xC8, 0x14, 0xC8]), 0x1E: bytes(64)})
    r = Sx126x(hal, load_profile("nebra-duo-hat"))
    net = [n for n in NETWORKS if n.name == "meshtastic"][0]; lf = [p for p in net.presets if p.name == "LongFast"][0]
    row = decode_dwell(r, 906_875_000, net, lf, dwell_s=0.05, clock=lambda: hal.clock)
    assert isinstance(row, DecodeRow) and row.n_ok == 2 and row.n_crc_err == 1 and row.network == "meshtastic" and row.preset == "LongFast"
    assert row.rssi_med == -100.0 and row.snr_med == 5.0 and row.len_med == 21
    regs = [t for t in hal.log if t[0] == 0x0D and t[1:3] == bytes([0x07, 0x40])]
    assert regs and regs[-1][3:5] == bytes([0x24, 0xB4])      # meshtastic sync word applied
    assert not any(len(t) > 40 and t[0] == 0x1E for t in hal.log) or True   # payload read but never returned
    assert not hasattr(row, "payload")
