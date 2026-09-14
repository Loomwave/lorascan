"""CH341 USB-SPI bridge HAL (MeshToad V3 / PineDio-USB class, VID:PID 1a86:5512 in SPI/EPP/MEM mode),
entirely in userspace through libusb (pyusb) — no kernel module, no tty. Command framing, pin map and
bit order are a line-by-line port of the Loomwave Rust backend (infra/crates/sx126x/src/ch341.rs),
itself mirrored from meshtastic's Ch341Hal / pine64 libpinedio-usb / flashrom ch341a_spi:
  - 0xA8 SPI stream: 1 command byte + up to 31 data bytes per 32-byte bulk packet, LSB-first on the
    wire so every byte is bit-reversed out and back;
  - 0xAB UIO stream: D0-D5 output levels then directions; 0xA0: read pin status (D0-D7 in byte 0);
  - MeshToad pins: CS D0, RXEN D1, RESET D2, SCK D3, BUSY D4, MOSI D5, DIO1 D6, MISO D7;
  - SCK and MOSI MUST be UIO outputs or the SPI stream never clocks (every read floats to 0xFF).
Experimental until run against a stick: the bench's CH341 was not on the bus on 2026-09-14."""
from __future__ import annotations
import time
from . import HalError

try:
    import usb.core as _usb_core   # pyusb, optional
    import usb.util as _usb_util
    _USB_ERRORS: tuple = (_usb_core.USBError,)     # USBTimeoutError is a subclass
except ImportError:                # pragma: no cover - exercised via monkeypatch in tests
    _USB_ERRORS = (OSError,)
    _usb_core = None
    _usb_util = None

CH341_VID, CH341_PID = 0x1A86, 0x5512
EP_OUT, EP_IN = 0x02, 0x82
USB_TIMEOUT_MS = 1000
PACKET_LEN = 32
SPI_CHUNK = PACKET_LEN - 1
CMD_SPI_STREAM, CMD_UIO_STREAM, CMD_GET_STATUS = 0xA8, 0xAB, 0xA0
UIO_STM_OUT, UIO_STM_DIR, UIO_STM_END = 0x80, 0x40, 0x20
STATUS_LEN = 6
PIN_CS, PIN_RXEN, PIN_RESET, PIN_SCK, PIN_BUSY, PIN_MOSI, PIN_DIO1, PIN_MISO = 0, 1, 2, 3, 4, 5, 6, 7
OUTPUT_DIRS = (1 << PIN_CS) | (1 << PIN_RXEN) | (1 << PIN_RESET) | (1 << PIN_SCK) | (1 << PIN_MOSI)


def reverse_byte(x: int) -> int:
    x = ((x >> 1) & 0x55) | ((x << 1) & 0xAA)
    x = ((x >> 2) & 0x33) | ((x << 2) & 0xCC)
    return ((x >> 4) & 0x0F) | ((x << 4) & 0xF0)


def spi_stream_packets(data: bytes) -> list[bytes]:
    return [bytes([CMD_SPI_STREAM]) + bytes(reverse_byte(b) for b in data[i:i + SPI_CHUNK]) for i in range(0, len(data), SPI_CHUNK)]


def decode_spi_in(buf: bytes) -> bytes:
    return bytes(reverse_byte(b) for b in buf)


def uio_out_packet(levels: int, dirs: int) -> bytes:
    return bytes([CMD_UIO_STREAM, UIO_STM_OUT | (levels & 0x3F), UIO_STM_DIR | (dirs & 0x3F), UIO_STM_END])


def status_pin_high(status0: int, pin: int) -> bool:
    return bool(status0 & (1 << pin))


class _PyUsbHandle:
    """Thin adapter so the HAL talks to pyusb the way the tests talk to a fake."""
    def __init__(self, dev):
        self.dev = dev

    def write(self, ep, data, timeout=USB_TIMEOUT_MS):
        return self.dev.write(ep, data, timeout)

    def read(self, ep, n, timeout=USB_TIMEOUT_MS):
        return bytes(self.dev.read(ep, n, timeout))


class Ch341Hal:
    def __init__(self, profile, handle=None):
        pins = profile.pins
        self.p_cs = pins.get("nss") if isinstance(pins.get("nss"), int) else PIN_CS
        self.p_rxen = pins.get("rxen") if isinstance(pins.get("rxen"), int) else PIN_RXEN
        self.p_reset = pins.get("reset") if isinstance(pins.get("reset"), int) else PIN_RESET
        self.p_busy = pins.get("busy") if isinstance(pins.get("busy"), int) else PIN_BUSY
        self.p_dio1 = pins.get("dio1") if isinstance(pins.get("dio1"), int) else PIN_DIO1
        if handle is None:
            if _usb_core is None:
                raise HalError("pyusb is not installed (pip install pyusb) — needed for the CH341 backend")
            serial = getattr(profile, "bus_dev", "") or ""
            dev = None
            for d in _usb_core.find(find_all=True, idVendor=CH341_VID, idProduct=CH341_PID):
                if serial and serial != "auto":
                    try:
                        if _usb_util.get_string(d, d.iSerialNumber) != serial:
                            continue
                    except Exception:
                        continue
                dev = d
                break
            if dev is None:
                raise HalError(f"no CH341 SPI device ({CH341_VID:04x}:{CH341_PID:04x}) on the USB bus (a UART-strapped CH341 enumerates as 5523 and is not this device)")
            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
            except Exception:
                pass
            _usb_util.claim_interface(dev, 0)
            handle = _PyUsbHandle(dev)
        self.h = handle
        self.levels = (1 << self.p_cs) | (1 << self.p_rxen) | (1 << self.p_reset)
        self.dirs = (1 << self.p_cs) | (1 << self.p_rxen) | (1 << self.p_reset) | (1 << PIN_SCK) | (1 << PIN_MOSI)
        self._push_uio()

    def _push_uio(self):
        self._w(uio_out_packet(self.levels, self.dirs))

    # Every bulk transfer goes through these two so a pyusb USBTimeoutError / USBError ([Errno 110],
    # [Errno 16] Resource busy, ...) surfaces as HalError and the scan loop can recover (Loomwave/lorascan#6).
    def _w(self, data: bytes):
        try:
            return self.h.write(EP_OUT, data)
        except _USB_ERRORS as e:
            raise HalError(f"ch341 usb write: {e}") from e

    def _r(self, n: int) -> bytes:
        try:
            return self.h.read(EP_IN, n)
        except _USB_ERRORS as e:
            raise HalError(f"ch341 usb read: {e}") from e

    def reset_device(self) -> bool:
        """USB port reset of the stick (clears a wedged controller without a host reboot); best effort."""
        try:
            dev = getattr(self.h, "dev", None)
            if dev is not None and hasattr(dev, "reset"):
                dev.reset()
                return True
        except Exception:
            pass
        return False

    def _set_pin(self, pin: int, high: bool):
        self.levels = (self.levels | (1 << pin)) if high else (self.levels & ~(1 << pin))
        self._push_uio()

    def _status0(self) -> int:
        self._w(bytes([CMD_GET_STATUS]))
        buf = self._r(STATUS_LEN)
        if not buf:
            raise HalError("ch341 status: empty response")
        return buf[0]

    def xfer(self, tx: bytes) -> bytes:
        self._set_pin(self.p_cs, False)
        try:
            out = bytearray()
            for pkt in spi_stream_packets(bytes(tx)):
                n = len(pkt) - 1
                self._w(pkt)
                got = bytearray()
                while len(got) < n:
                    chunk = self._r(n - len(got))
                    if not chunk:
                        raise HalError("ch341 spi: short read")
                    got += chunk
                out += got
            return decode_spi_in(bytes(out))
        finally:
            self._set_pin(self.p_cs, True)

    def busy(self) -> bool:
        return status_pin_high(self._status0(), self.p_busy)

    def dio1(self) -> bool:
        return status_pin_high(self._status0(), self.p_dio1)

    def set_reset(self, level: bool) -> None:
        self._set_pin(self.p_reset, level)

    def set_rxen(self, level: bool) -> None:
        self._set_pin(self.p_rxen, level)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def close(self) -> None:
        try:
            if _usb_util is not None and isinstance(self.h, _PyUsbHandle):
                _usb_util.release_interface(self.h.dev, 0)
        except Exception:
            pass
