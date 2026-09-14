"""SX126x command layer — the init/command sequences proven on the Loomwave fleet
(infra/crates/sx126x, E22P-915M30S bring-up), receive-only: this class has no transmit method
and SetTxParams is only ever issued with the profile's max_tx_dbm (bench default -9 dBm)."""
from __future__ import annotations
from ..profile import BoardProfile

FREQ_STEP = 32e6 / 2**25          # PLL step, datasheet §13.4.1

OP_SET_STANDBY = 0x80
OP_SET_RX = 0x82
OP_SET_RF_FREQUENCY = 0x86
OP_CALIBRATE = 0x89
OP_SET_PACKET_TYPE = 0x8A
OP_SET_MOD_PARAMS = 0x8B
OP_SET_TX_PARAMS = 0x8E
OP_SET_BUFFER_BASE = 0x8F
OP_SET_PA_CONFIG = 0x95
OP_SET_DIO3_TCXO = 0x97
OP_CALIBRATE_IMAGE = 0x98
OP_SET_DIO2_RF_SWITCH = 0x9D
OP_WRITE_REGISTER = 0x0D
OP_READ_REGISTER = 0x1D
OP_GET_RSSI_INST = 0x15
OP_GET_DEVICE_ERRORS = 0x17
OP_CLEAR_DEVICE_ERRORS = 0x07
OP_SET_DIO_IRQ = 0x08
OP_GET_STATUS = 0xC0

REG_LORA_SYNC_MSB = 0x0740
REG_RX_GAIN = 0x08AC
RX_GAIN_BOOSTED = 0x96
RX_GAIN_POWER_SAVE = 0x94
SYNC_PRIVATE = 0x1424              # SX126x "private network" default (reads 0x14 0x24 out of reset)

OP_SET_CAD_PARAMS = 0x88
OP_SET_CAD = 0xC5
OP_GET_IRQ_STATUS = 0x12
OP_CLEAR_IRQ = 0x02
OP_SET_PKT_PARAMS = 0x8C
OP_GET_RX_BUFFER_STATUS = 0x13
OP_GET_PACKET_STATUS = 0x14
OP_READ_BUFFER = 0x1E

# IRQ bits, datasheet Table 13-29
IRQ_TX_DONE, IRQ_RX_DONE, IRQ_PREAMBLE, IRQ_SYNC_VALID = 0x0001, 0x0002, 0x0004, 0x0008
IRQ_HEADER_VALID, IRQ_HEADER_ERR, IRQ_CRC_ERR, IRQ_CAD_DONE, IRQ_CAD_DETECTED, IRQ_TIMEOUT = 0x0010, 0x0020, 0x0040, 0x0080, 0x0100, 0x0200
IRQ_TERMINAL = IRQ_TX_DONE | IRQ_RX_DONE | IRQ_TIMEOUT | IRQ_CRC_ERR   # mask kept from infrad
IRQ_ALL = 0x03FF
_CAD_SYMBOL_CODE = {1: 0x00, 2: 0x01, 4: 0x02, 8: 0x03, 16: 0x04}

_BW_CODE = {7: 0x00, 10: 0x08, 15: 0x01, 20: 0x09, 31: 0x02, 41: 0x0A, 62: 0x03, 125: 0x04, 250: 0x05, 500: 0x06}


class SxError(Exception):
    pass


class BusyTimeout(SxError):
    pass


class DeviceError(SxError):
    def __init__(self, errors: int):
        super().__init__(f"SX126x device errors 0x{errors:04X}")
        self.errors = errors


class Sx126x:
    def __init__(self, hal, profile: BoardProfile, nobusy: bool = False):
        self.hal = hal
        self.profile = profile
        self.nobusy = nobusy
        self.freq_hz = 0
        self.mode = "unknown"        # 'lora' after init(); 'gfsk' while the scan engine owns the modem
        self.patch_loaded = False    # the spectral-scan RAM patch does not survive reset()
        self._lora = (9, 125, 5)

    # ---- transport ------------------------------------------------------------------------
    def _wait_busy(self) -> None:
        if self.nobusy:
            self.hal.sleep(0.001)
            return
        waited = 0.0
        while self.hal.busy():
            if waited > 1.0:
                raise BusyTimeout("BUSY stayed high > 1 s")
            self.hal.sleep(0.0001)
            waited += 0.0001

    def cmd(self, out: bytes, read_back: int = 0) -> bytes:
        """One SPI command: CS asserted for exactly this transfer. Returns the bytes clocked out
        after `out` (the NOP positions)."""
        self._wait_busy()
        tx = bytes(out) + bytes(read_back)
        rx = self.hal.xfer(tx)
        return rx[len(out):]

    def write_reg(self, addr: int, vals: bytes) -> None:
        self.cmd(bytes([OP_WRITE_REGISTER, addr >> 8, addr & 0xFF]) + bytes(vals))

    def read_reg(self, addr: int, n: int) -> bytes:
        return self.cmd(bytes([OP_READ_REGISTER, addr >> 8, addr & 0xFF, 0x00]), n)

    # ---- state ----------------------------------------------------------------------------
    def reset(self) -> None:
        self.patch_loaded = False
        self.hal.set_reset(False)
        self.hal.sleep(0.002)
        self.hal.set_reset(True)
        self.hal.sleep(0.005)
        self._wait_busy()

    def chip_status(self) -> int:
        return self.cmd(bytes([OP_GET_STATUS]), 1)[0]

    def device_errors(self) -> int:
        r = self.cmd(bytes([OP_GET_DEVICE_ERRORS, 0x00]), 2)
        return (r[0] << 8) | r[1]

    def standby(self) -> None:
        self.cmd(bytes([OP_SET_STANDBY, 0x00]))

    def set_frequency(self, hz: int) -> None:
        frf = int(hz / FREQ_STEP)
        self.cmd(bytes([OP_SET_RF_FREQUENCY]) + frf.to_bytes(4, "big"))
        self.freq_hz = hz

    def rx_continuous(self) -> None:
        self.hal.set_rxen(True)
        self.cmd(bytes([OP_SET_RX, 0xFF, 0xFF, 0xFF]))

    def rssi_inst(self) -> float:
        """Instantaneous RSSI in dBm (= -raw/2, datasheet Table 13-81). Valid only in RX after settle."""
        r = self.cmd(bytes([OP_GET_RSSI_INST, 0x00]), 1)
        return -r[0] / 2.0

    def set_lora(self, sf: int = 9, bw_khz: int = 125, cr: int = 5) -> None:
        if not 5 <= sf <= 12 or bw_khz not in _BW_CODE or not 5 <= cr <= 8:
            raise SxError(f"bad LoRa params sf={sf} bw={bw_khz} cr={cr}")
        ldro = 1 if (2 ** sf) / (bw_khz * 1000.0) >= 0.016 else 0
        self.cmd(bytes([OP_SET_MOD_PARAMS, sf, _BW_CODE[bw_khz], cr - 4, ldro]))

    def init(self, freq_hz: int, sf: int = 9, bw_khz: int = 125, cr: int = 5) -> None:
        """Full bring-up: reset -> STDBY_RC -> TCXO on DIO3 -> clear + calibrate -> LoRa -> image cal
        for 902-928 -> frequency -> PA/TX params (capped) -> DIO2 RF switch -> mod params -> sync word
        -> RX gain -> IRQ params. Mirrors infrad's Sx126x::init byte for byte."""
        p = self.profile
        self.reset()
        self.cmd(bytes([OP_SET_STANDBY, 0x00]))
        tcxo_code = {1.6: 0x00, 1.7: 0x01, 1.8: 0x02, 2.2: 0x03, 2.4: 0x04, 2.7: 0x05, 3.0: 0x06, 3.3: 0x07}.get(p.tcxo_v, 0x02)
        delay = (5 * 64).to_bytes(4, "big")          # 5 ms startup, units of 15.625 us
        self.cmd(bytes([OP_SET_DIO3_TCXO, tcxo_code, delay[1], delay[2], delay[3]]))
        self.cmd(bytes([OP_CLEAR_DEVICE_ERRORS, 0x00, 0x00]))
        self.cmd(bytes([OP_CALIBRATE, 0x7F]))
        self.hal.sleep(0.005)
        errs = self.device_errors()
        if errs & ~0x0001:
            raise DeviceError(errs)
        self.cmd(bytes([OP_SET_PACKET_TYPE, 0x01]))
        self.cmd(bytes([OP_CALIBRATE_IMAGE, 0xE1, 0xE9]))
        self.set_frequency(freq_hz)
        self.cmd(bytes([OP_SET_PA_CONFIG, 0x04, 0x07, 0x00, 0x01]))
        power = max(-9, min(p.max_tx_dbm, -9))       # P1 is receive-only; the PA is parked at -9 dBm
        self.cmd(bytes([OP_SET_TX_PARAMS, power & 0xFF, 0x04]))
        self.cmd(bytes([OP_SET_DIO2_RF_SWITCH, 0x01 if p.dio2_rf_switch else 0x00]))
        self.cmd(bytes([OP_SET_BUFFER_BASE, 0x00, 0x00]))
        self.set_lora(sf, bw_khz, cr)
        self.write_reg(REG_LORA_SYNC_MSB, SYNC_PRIVATE.to_bytes(2, "big"))
        self.write_reg(REG_RX_GAIN, bytes([RX_GAIN_BOOSTED if p.rx_boosted else RX_GAIN_POWER_SAVE]))
        mask = (IRQ_TERMINAL | IRQ_PREAMBLE | IRQ_HEADER_VALID).to_bytes(2, "big")
        d1 = IRQ_TERMINAL.to_bytes(2, "big")
        self.cmd(bytes([OP_SET_DIO_IRQ]) + mask + d1 + bytes(4))
        self.mode = "lora"
        self._lora = (sf, bw_khz, cr)

    def ensure_lora(self, freq_hz: int | None = None) -> None:
        """Bring the modem back to LoRa (a full init) if another engine left it in GFSK."""
        if self.mode != "lora":
            self.init(freq_hz or self.freq_hz or 911_500_000, *self._lora)

    # ---- IRQs -----------------------------------------------------------------------------
    def irq_status(self) -> int:
        r = self.cmd(bytes([OP_GET_IRQ_STATUS, 0x00]), 2)
        return (r[0] << 8) | r[1]

    def clear_irq(self, mask: int = IRQ_ALL) -> None:
        self.cmd(bytes([OP_CLEAR_IRQ, (mask >> 8) & 0xFF, mask & 0xFF]))

    def set_irq_mask(self, mask: int, dio1: int = 0) -> None:
        self.cmd(bytes([OP_SET_DIO_IRQ]) + mask.to_bytes(2, "big") + dio1.to_bytes(2, "big") + bytes(4))

    # ---- CAD (datasheet §13.4.7) -----------------------------------------------------------
    def set_cad_params(self, symbols: int, det_peak: int, det_min: int, exit_mode: int = 0x00, timeout: int = 0) -> None:
        if symbols not in _CAD_SYMBOL_CODE:
            raise SxError(f"cad symbols must be one of {sorted(_CAD_SYMBOL_CODE)}")
        self.cmd(bytes([OP_SET_CAD_PARAMS, _CAD_SYMBOL_CODE[symbols], det_peak & 0xFF, det_min & 0xFF, exit_mode & 0xFF]) + timeout.to_bytes(3, "big"))

    def cad_start(self) -> None:
        self.hal.set_rxen(True)
        self.cmd(bytes([OP_SET_CAD]))

    # ---- packet reception (counts only; payload bytes are read to drain the FIFO, never kept) ----
    def set_sync_word(self, word8: int) -> None:
        """8-bit LoRa sync word (0x12 private, 0x34 LoRaWAN, 0x2B Meshtastic) -> the SX126x register pair."""
        self.write_reg(REG_LORA_SYNC_MSB, bytes([(word8 & 0xF0) | 0x04, ((word8 & 0x0F) << 4) | 0x04]))

    def set_packet_params_lora(self, preamble: int = 8, payload_len: int = 0xFF, crc_on: bool = True, implicit: bool = False, invert_iq: bool = False) -> None:
        self.cmd(bytes([OP_SET_PKT_PARAMS]) + preamble.to_bytes(2, "big") + bytes([0x01 if implicit else 0x00, payload_len, 0x01 if crc_on else 0x00, 0x01 if invert_iq else 0x00]))

    def rx_buffer_status(self) -> tuple[int, int]:
        r = self.cmd(bytes([OP_GET_RX_BUFFER_STATUS, 0x00]), 2)
        return r[0], r[1]

    def read_buffer(self, offset: int, length: int) -> bytes:
        return self.cmd(bytes([OP_READ_BUFFER, offset & 0xFF, 0x00]), length)

    def packet_status(self) -> tuple[float, float, float]:
        """LoRa GetPacketStatus: (RssiPkt dBm, SnrPkt dB, SignalRssiPkt dBm)."""
        r = self.cmd(bytes([OP_GET_PACKET_STATUS, 0x00]), 3)
        snr = r[1] - 256 if r[1] > 127 else r[1]
        return -r[0] / 2.0, snr / 4.0, -r[2] / 2.0

    # ---- diagnostics ---------------------------------------------------------------------
    def probe_bytes(self) -> dict:
        """First-light SPI check that bypasses BUSY (so a mis-wired BUSY cannot hide the bus):
        reset, GetStatus, ReadRegister 0x0740 (expect 0x14 0x24). Verdicts as infrad's sx-probe."""
        self.hal.set_reset(False)
        self.hal.sleep(0.002)
        self.hal.set_reset(True)
        self.hal.sleep(0.010)
        st = self.hal.xfer(bytes([OP_GET_STATUS, 0x00]))
        rr = self.hal.xfer(bytes([OP_READ_REGISTER, 0x07, 0x40, 0x00, 0x00, 0x00]))
        status, sync = st[1], (rr[4], rr[5])
        if sync == (0x14, 0x24):
            verdict = "GOOD"
        elif status == 0xFF and sync == (0xFF, 0xFF):
            verdict = "all 0xFF — MISO never driven (check RESET pulse, BUSY, SCK/MOSI wiring)"
        elif status == 0x00 and sync == (0x00, 0x00):
            verdict = "all 0x00 — MISO stuck low (CS never released or MISO mis-wired)"
        else:
            verdict = "mixed bytes — partial SPI (bit order or CS framing)"
        return {"status": status, "mode": (status >> 4) & 0x7, "sync": sync, "verdict": verdict}
