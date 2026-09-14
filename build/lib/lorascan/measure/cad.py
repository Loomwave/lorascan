"""CAD layer (spec §1.3): Channel Activity Detection at one (frequency, SF, BW) is the LoRa-specific
detector — it correlates against LoRa chirps at that SF/BW only. A sweep = n_cad back-to-back CADs;
we record hits and the longest run of consecutive hits (a run separates traffic from single false
alarms). Threshold defaults follow the RadioLib / LoRaMac-node values for 2 symbols; AN1200.48 is the
authority and the values are overridable per call."""
from __future__ import annotations
import time
from dataclasses import dataclass, asdict
from ..radio.sx126x import IRQ_CAD_DONE, IRQ_CAD_DETECTED, IRQ_ALL

DEFAULT_SYMBOLS = 2
DEFAULT_DET_PEAK = {5: 22, 6: 22, 7: 22, 8: 22, 9: 23, 10: 24, 11: 25, 12: 28}
DEFAULT_DET_MIN = 10


@dataclass
class CadRow:
    ts: float
    freq_hz: int
    bw_hz: int
    sf: int
    symbols: int
    n_cad: int
    hits: int
    longest_run: int
    det_peak: int
    det_min: int
    timeouts: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_cad if self.n_cad else 0.0


def cad_params_for(sf: int, symbols: int = DEFAULT_SYMBOLS) -> tuple[int, int, int]:
    if sf not in DEFAULT_DET_PEAK:
        raise ValueError(f"sf {sf} out of range 5..12")
    return symbols, DEFAULT_DET_PEAK[sf], DEFAULT_DET_MIN


def cad_duration_s(sf: int, bw_khz: float, symbols: int) -> float:
    """AN1200.85: N symbols x Tsym + 32/BW."""
    bw = bw_khz * 1000.0
    return symbols * (2 ** sf) / bw + 32.0 / bw


def cad_sweep(radio, freq_hz: int, sf: int, bw_khz: int, n_cad: int = 50, symbols: int = DEFAULT_SYMBOLS,
              det_peak: int | None = None, det_min: int | None = None, clock=time.monotonic,
              ts: float | None = None, cr: int = 5) -> CadRow:
    radio.ensure_lora(freq_hz)
    symbols, dp, dm = cad_params_for(sf, symbols)
    det_peak = dp if det_peak is None else det_peak
    det_min = dm if det_min is None else det_min
    radio.set_lora(sf, bw_khz, cr)
    radio.set_frequency(freq_hz)
    radio.set_irq_mask(IRQ_ALL)
    radio.set_cad_params(symbols, det_peak, det_min)
    one = cad_duration_s(sf, bw_khz, symbols)
    limit = max(0.05, 4 * one + 0.05)
    hits = run = longest = timeouts = done = 0
    for _ in range(n_cad):
        radio.clear_irq()
        radio.cad_start()
        t0 = clock()
        irq = 0
        while clock() - t0 < limit:
            irq = radio.irq_status()
            if irq & IRQ_CAD_DONE:
                break
            radio.hal.sleep(min(0.001, one / 4))
        if not irq & IRQ_CAD_DONE:
            timeouts += 1
            continue
        done += 1
        if irq & IRQ_CAD_DETECTED:
            hits += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    radio.clear_irq()
    radio.standby()
    return CadRow(ts=ts if ts is not None else time.time(), freq_hz=freq_hz, bw_hz=bw_khz * 1000, sf=sf, symbols=symbols,
                  n_cad=done, hits=hits, longest_run=longest, det_peak=det_peak, det_min=det_min, timeouts=timeouts)
