"""Semtech SX126x spectral-scan engine (spec §1.2): a RAM patch (radio/patch_scan_bin.py) turns the
chip into an RSSI histogram engine — nb_scan samples at ~8.2 us inside the chip, 33 levels 4 dB apart,
independent of SPI/USB latency. Sequence and register map transcribed from RadioLib
(SX126x::uploadPatch / spectralScanStart / spectralScanGetResult) and sx1302_hal loragw_sx1261.c.
Experimental: undocumented by Semtech for the SX1262; verify on each board; the polled engine remains
the fallback."""
from __future__ import annotations
import time
from ..measure.energy import EnergyRow, NUM_LEVELS, BUSY_T_DB
from .sx126x import OP_SET_STANDBY, OP_SET_RX
from .patch_scan_bin import PATCH_WORDS

REG_VERSION_STRING = 0x0320
REG_SPECTRAL_SCAN_RESULT = 0x0401
REG_PATCH_UPDATE_ENABLE = 0x0610
REG_SPECTRAL_SCAN_STATUS = 0x07CD
REG_RSSI_AVG_WINDOW = 0x089B
REG_PATCH_MEMORY_BASE = 0x8000
PATCH_UPDATE_ENABLED = 0x10
PATCH_UPDATE_DISABLED = 0x00
CMD_PRAM_UPDATE = 0xD9
CMD_SET_SPECTR_SCAN_PARAMS = 0x9B
SCAN_STATUS_NONE, SCAN_STATUS_ON_GOING, SCAN_STATUS_ABORTED, SCAN_STATUS_COMPLETED = 0x00, 0x0F, 0xF0, 0xFF
SCAN_INTERVAL_7_68_US, SCAN_INTERVAL_8_20_US, SCAN_INTERVAL_8_68_US = 10, 11, 12
WINDOW_DEFAULT = 0x05 << 2


class ScanError(Exception):
    pass


class ScanAborted(ScanError):
    pass


class ScanTimeout(ScanError):
    pass


def version_string(radio) -> str:
    return bytes(radio.read_reg(REG_VERSION_STRING, 16)).split(b"\x00")[0].decode("ascii", "replace")


def upload_patch(radio) -> None:
    """RadioLib uploadPatch: STDBY_RC, enable patch update, write the words at 0x8000 (big-endian,
    4 bytes each), disable patch update, PRAM update. Must be repeated after every reset."""
    radio.cmd(bytes([OP_SET_STANDBY, 0x00]))
    radio.write_reg(REG_PATCH_UPDATE_ENABLE, bytes([PATCH_UPDATE_ENABLED]))
    for i, w in enumerate(PATCH_WORDS):
        radio.write_reg(REG_PATCH_MEMORY_BASE + 4 * i, w.to_bytes(4, "big"))
    radio.write_reg(REG_PATCH_UPDATE_ENABLE, bytes([PATCH_UPDATE_DISABLED]))
    radio.cmd(bytes([CMD_PRAM_UPDATE]))


def spectral_scan(radio, nb_scan: int = 2048, interval: int = SCAN_INTERVAL_8_20_US, window: int = WINDOW_DEFAULT,
                  timeout_s: float = 2.0, clock=time.monotonic) -> list[int]:
    """Run one histogram scan on the current frequency; returns 33 counts (level i = offset - 4i dBm,
    level 32 = below level 31). Raises ScanAborted / ScanTimeout."""
    radio.write_reg(REG_RSSI_AVG_WINDOW, bytes([window]))
    radio.cmd(bytes([OP_SET_RX, 0xFF, 0xFF, 0xFF]))
    radio.cmd(bytes([CMD_SET_SPECTR_SCAN_PARAMS, (nb_scan >> 8) & 0xFF, nb_scan & 0xFF, interval]))
    t0 = clock()
    while True:
        st = radio.read_reg(REG_SPECTRAL_SCAN_STATUS, 1)[0]
        if st == SCAN_STATUS_COMPLETED:
            break
        if st == SCAN_STATUS_ABORTED:
            raise ScanAborted("spectral scan aborted by the chip")
        if clock() - t0 > timeout_s:
            radio.write_reg(REG_RSSI_AVG_WINDOW, bytes([0x00]))   # abort
            raise ScanTimeout(f"spectral scan status 0x{st:02X} after {timeout_s} s")
        radio.hal.sleep(0.002)
    raw = radio.read_reg(REG_SPECTRAL_SCAN_RESULT, 2 * NUM_LEVELS)
    return [(raw[2 * i] << 8) | raw[2 * i + 1] for i in range(NUM_LEVELS)]


def hist_stats(hist: list[int], offset_dbm: int = -11, busy_t_db: float = BUSY_T_DB) -> dict:
    """The same statistics as measure.energy.stats, computed from level counts (4 dB resolution)."""
    n = sum(hist)
    if n == 0:
        return {"floor_dbm": 0.0, "p50": 0.0, "p90": 0.0, "peak": 0.0, "busy_frac": 0.0}
    level_dbm = [offset_dbm - 4 * i for i in range(NUM_LEVELS - 1)] + [offset_dbm - 4 * (NUM_LEVELS - 1)]

    def pct(q: float) -> float:            # q-th percentile from the strongest-first level order, weakest-first accumulation
        target = q * n
        acc = 0
        for i in range(NUM_LEVELS - 1, -1, -1):   # weakest level first
            acc += hist[i]
            if acc > target:
                return float(level_dbm[i])
        return float(level_dbm[0])

    floor = pct(0.10)
    thresh = floor + busy_t_db
    busy = sum(c for i, c in enumerate(hist) if level_dbm[i] > thresh) / n
    peak = float(level_dbm[next(i for i in range(NUM_LEVELS) if hist[i] > 0)])
    return {"floor_dbm": floor, "p50": pct(0.50), "p90": pct(0.90), "peak": peak, "busy_frac": busy}


def scan_energy(radio, freq_hz: int, bw_khz: int, nb_scan: int = 2048, offset_dbm: int = -11,
                busy_t_db: float = BUSY_T_DB, ts: float | None = None, clock=time.monotonic) -> EnergyRow:
    radio.set_frequency(freq_hz)
    radio.hal.set_rxen(True)
    hist = spectral_scan(radio, nb_scan, clock=clock)
    radio.standby()
    st = hist_stats(hist, offset_dbm, busy_t_db)
    return EnergyRow(ts=ts if ts is not None else time.time(), freq_hz=freq_hz, bw_hz=bw_khz * 1000,
                     engine="scan", n=sum(hist), hist=hist, discarded=0, **st)
