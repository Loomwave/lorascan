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


# GFSK RX bandwidth codes, datasheet Table 13-45 (DSB kHz -> ModParam5)
GFSK_RX_BW = {4.8: 0x1F, 5.8: 0x17, 7.3: 0x0F, 9.7: 0x1E, 11.7: 0x16, 14.6: 0x0E, 19.5: 0x1D, 23.4: 0x15, 29.3: 0x0D,
              39.0: 0x1C, 46.9: 0x14, 58.6: 0x0C, 78.2: 0x1B, 93.8: 0x13, 117.3: 0x0B, 156.2: 0x1A, 187.2: 0x12,
              234.3: 0x0A, 312.0: 0x19, 373.6: 0x11, 467.0: 0x09}
OP_SET_PACKET_TYPE, OP_SET_MOD_PARAMS, OP_SET_PKT_PARAMS, OP_SET_BUFFER_BASE = 0x8A, 0x8B, 0x8C, 0x8F
REG_RX_GAIN = 0x08AC


def gfsk_bw_code(bw_khz: float) -> int:
    """Nearest GFSK RX bandwidth at or above the requested measurement bandwidth."""
    for k in sorted(GFSK_RX_BW):
        if k >= bw_khz - 1e-6:
            return GFSK_RX_BW[k]
    return GFSK_RX_BW[467.0]


def setup_scan_mode(radio, bw_khz: float = 125.0) -> None:
    """The scan patch samples RSSI through the GFSK receiver. Configure it the way Semtech's
    util_spectral_scan does (loragw_sx1261.c sx1261_setup / sx1261_set_rx_params): STDBY_RC, buffer base
    0x80/0x80, register 0x08AC = 0xCB (Semtech's 'sensitivity adjustment'), packet type GFSK, mod params
    bitrate 0x001400 / no shaping / RX BW code / fdev 0x02E90F, packet params preamble 32 bits, detector
    16 bits, sync 32 bits, variable length, 255 B, CRC off, no whitening, RSSI averaging window 0x14.
    Leaves the LoRa modem configuration behind: call radio.init() to go back to LoRa."""
    radio.cmd(bytes([OP_SET_STANDBY, 0x00]))
    radio.cmd(bytes([OP_SET_BUFFER_BASE, 0x80, 0x80]))
    radio.write_reg(REG_RX_GAIN, bytes([0xCB]))
    radio.cmd(bytes([OP_SET_PACKET_TYPE, 0x00]))
    radio.write_reg(REG_RSSI_AVG_WINDOW, bytes([WINDOW_DEFAULT]))
    radio.cmd(bytes([OP_SET_MOD_PARAMS, 0x00, 0x14, 0x00, 0x00, gfsk_bw_code(bw_khz), 0x02, 0xE9, 0x0F]))
    radio.cmd(bytes([OP_SET_PKT_PARAMS, 0x00, 0x20, 0x05, 0x20, 0x00, 0x01, 0xFF, 0x01, 0x00]))
    radio._scan_mode_bw = bw_khz
    radio.mode = "gfsk"


def spectral_scan(radio, nb_scan: int = 2048, interval: int = SCAN_INTERVAL_8_20_US, window: int = WINDOW_DEFAULT,
                  timeout_s: float = 2.0, clock=time.monotonic) -> list[int]:
    """Run one histogram scan on the current frequency; returns 33 counts (level i = offset - 4i dBm,
    level 32 = below level 31). Raises ScanAborted / ScanTimeout. Aborts any previous scan first, as
    RadioLib and util_spectral_scan do, so the status/result registers start clean."""
    radio.write_reg(REG_RSSI_AVG_WINDOW, bytes([0x00]))          # abort / clear
    radio.write_reg(REG_RSSI_AVG_WINDOW, bytes([window]))
    radio.cmd(bytes([OP_SET_RX, 0xFF, 0xFF, 0xFF]))
    radio.cmd(bytes([CMD_SET_SPECTR_SCAN_PARAMS, (nb_scan >> 8) & 0xFF, nb_scan & 0xFF, interval]))
    # the status register keeps the previous scan's COMPLETED until the new scan is running: wait the
    # nominal scan time (nb_scan x ~8.2 us) before the first status read, else a stale 0xFF is read and
    # a partial histogram comes back (seen on the bench 2026-09-14)
    radio.hal.sleep(max(0.002, nb_scan * 8.2e-6 * 1.05))
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
    hist = [(raw[2 * i] << 8) | raw[2 * i + 1] for i in range(NUM_LEVELS)]
    if sum(hist) != nb_scan:
        raise ScanError(f"incomplete histogram: {sum(hist)} of {nb_scan} samples")
    return hist


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
    if getattr(radio, "mode", None) != "gfsk" or getattr(radio, "_scan_mode_bw", None) != bw_khz:
        setup_scan_mode(radio, bw_khz)
    radio.set_frequency(freq_hz)
    radio.hal.set_rxen(True)
    hist = spectral_scan(radio, nb_scan, clock=clock)
    radio.standby()
    st = hist_stats(hist, offset_dbm, busy_t_db)
    return EnergyRow(ts=ts if ts is not None else time.time(), freq_hz=freq_hz, bw_hz=bw_khz * 1000,
                     engine="scan", n=sum(hist), hist=hist, discarded=0, **st)
