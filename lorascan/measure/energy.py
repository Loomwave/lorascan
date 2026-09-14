"""Energy layer (spec §1.1, §3.2): modulation-agnostic RSSI statistics per channel.
Engines: 'poll' = host-polled GetRssiInst (this module); 'scan' = the on-chip histogram (radio/scanpatch.py)."""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict

BUSY_T_DB = 8.0            # spec §3.2 default: busy = samples above floor + T
SETTLE_S = 0.020           # discard the first 20 ms after SetRx (unsettled front end)
NUM_LEVELS = 33            # Semtech scan-patch histogram layout: 4 dB per level, level 32 = below the lowest


@dataclass
class EnergyRow:
    ts: float
    freq_hz: int
    bw_hz: int
    engine: str
    n: int
    hist: list = field(default_factory=lambda: [0] * NUM_LEVELS)
    floor_dbm: float = 0.0
    p50: float = 0.0
    p90: float = 0.0
    peak: float = 0.0
    busy_frac: float = 0.0
    discarded: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[i]


def stats(samples: list[float], busy_t_db: float = BUSY_T_DB) -> dict:
    """floor = P10, p50, p90, peak = max, busy_frac = fraction of samples > floor + busy_t_db."""
    if not samples:
        return {"floor_dbm": 0.0, "p50": 0.0, "p90": 0.0, "peak": 0.0, "busy_frac": 0.0}
    s = sorted(samples)
    floor = _pct(s, 0.10)
    thresh = floor + busy_t_db
    busy = sum(1 for v in s if v > thresh) / len(s)
    return {"floor_dbm": floor, "p50": _pct(s, 0.50), "p90": _pct(s, 0.90), "peak": s[-1], "busy_frac": busy}


def hist33(samples: list[float], offset_dbm: int = -11) -> list[int]:
    """Bin samples the way the Semtech scan patch does: level i = offset - 4*i dBm (i = 0..31),
    a sample belongs to the nearest level; anything below level 31 lands in level 32."""
    h = [0] * NUM_LEVELS
    for v in samples:
        i = int(round((offset_dbm - v) / 4.0))
        if i < 0:
            i = 0
        elif i > 31:
            i = 32
        h[i] += 1
    return h


def polled_energy(radio, freq_hz: int, bw_khz: int, dwell_s: float, clock=time.monotonic,
                  sample_gap_s: float = 0.0007, busy_t_db: float = BUSY_T_DB,
                  offset_dbm: int = -11, ts: float | None = None) -> EnergyRow:
    """Tune, RX continuous, discard the settle window, poll GetRssiInst until dwell_s, standby.
    Readings >= -1 dBm or <= -126 dBm are unsettled/SPI-noise and are counted in `discarded`."""
    radio.set_frequency(freq_hz)
    radio.rx_continuous()
    t0 = clock()
    radio.hal.sleep(SETTLE_S)
    samples: list[float] = []
    discarded = 0
    while clock() - t0 < SETTLE_S + dwell_s:
        v = radio.rssi_inst()
        if v >= -1.0 or v <= -126.0:
            discarded += 1
        else:
            samples.append(v)
        radio.hal.sleep(sample_gap_s)
    radio.standby()
    st = stats(samples, busy_t_db)
    return EnergyRow(ts=ts if ts is not None else time.time(), freq_hz=freq_hz, bw_hz=bw_khz * 1000,
                     engine="poll", n=len(samples), hist=hist33(samples, offset_dbm), discarded=discarded, **st)
