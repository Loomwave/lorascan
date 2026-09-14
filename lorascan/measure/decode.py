"""Passive decode layer (spec §1.4): tune to a known network's exact PHY and count CRC-valid frames.
Payload bytes are read only to drain the FIFO and are discarded; nothing but counts, RSSI, SNR and
lengths leaves this function."""
from __future__ import annotations
import time
from dataclasses import dataclass, asdict
from ..radio.sx126x import IRQ_RX_DONE, IRQ_CRC_ERR, IRQ_HEADER_ERR, IRQ_ALL
from ..networks import Network, Preset


@dataclass
class DecodeRow:
    ts: float
    freq_hz: int
    network: str
    preset: str
    dwell_s: float
    n_ok: int
    n_crc_err: int
    rssi_med: float
    snr_med: float
    len_med: int

    def as_dict(self) -> dict:
        return asdict(self)


def _median(v):
    if not v:
        return 0
    s = sorted(v)
    return s[len(s) // 2]


def decode_dwell(radio, freq_hz: int, network: Network, preset: Preset, dwell_s: float,
                 clock=time.monotonic, ts: float | None = None, poll_s: float = 0.002) -> DecodeRow:
    radio.ensure_lora(freq_hz)
    radio.set_lora(preset.sf, preset.bw_khz, preset.cr)
    radio.set_sync_word(network.sync_word)
    radio.set_packet_params_lora(preamble=preset.preamble, payload_len=0xFF, crc_on=preset.crc_on)
    radio.set_frequency(freq_hz)
    radio.set_irq_mask(IRQ_ALL)
    radio.clear_irq()
    radio.rx_continuous()
    n_ok = n_crc = 0
    rssis, snrs, lens = [], [], []
    t0 = clock()
    while clock() - t0 < dwell_s:
        irq = radio.irq_status()
        if irq & (IRQ_RX_DONE | IRQ_CRC_ERR | IRQ_HEADER_ERR):
            if irq & IRQ_RX_DONE and not irq & (IRQ_CRC_ERR | IRQ_HEADER_ERR):
                length, offset = radio.rx_buffer_status()
                radio.read_buffer(offset, length)          # drain; bytes discarded
                rssi, snr, _sig = radio.packet_status()
                n_ok += 1
                rssis.append(rssi); snrs.append(snr); lens.append(length)
            else:
                n_crc += 1
            radio.clear_irq()
        radio.hal.sleep(poll_s)
    radio.clear_irq()
    radio.standby()
    return DecodeRow(ts=ts if ts is not None else time.time(), freq_hz=freq_hz, network=network.name, preset=preset.name,
                     dwell_s=dwell_s, n_ok=n_ok, n_crc_err=n_crc, rssi_med=float(_median(rssis)), snr_med=float(_median(snrs)), len_med=int(_median(lens)))
