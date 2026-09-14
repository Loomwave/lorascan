"""SpidevGpiodHal: Linux spidev (mode 0, kernel-managed CS) + GPIO via libgpiod v1 (Debian 12 ships
python3-libgpiod 1.6), libgpiod v2, or lgpio as fallbacks. Pin numbers are gpiochip0 line offsets
(BCM numbering on a Raspberry Pi)."""
from __future__ import annotations
import time
from . import HalError


class _GpioV1:
    def __init__(self, chip: str = "gpiochip0"):
        import gpiod
        self._g = gpiod
        self.chip = gpiod.Chip(chip)
        self.lines = {}

    def request_out(self, offset: int, initial: int):
        ln = self.chip.get_line(offset)
        ln.request(consumer="lorascan", type=self._g.LINE_REQ_DIR_OUT, default_vals=[initial])
        self.lines[offset] = ln

    def request_in(self, offset: int):
        ln = self.chip.get_line(offset)
        ln.request(consumer="lorascan", type=self._g.LINE_REQ_DIR_IN)
        self.lines[offset] = ln

    def set(self, offset: int, level: bool):
        self.lines[offset].set_value(1 if level else 0)

    def get(self, offset: int) -> bool:
        return bool(self.lines[offset].get_value())

    def close(self):
        for ln in self.lines.values():
            try:
                ln.release()
            except Exception:
                pass


class _GpioV2:
    def __init__(self, chip: str = "/dev/gpiochip0"):
        import gpiod
        from gpiod.line import Direction, Value
        self._g, self._Dir, self._Val = gpiod, Direction, Value
        self.chip = chip
        self.reqs = {}

    def request_out(self, offset: int, initial: int):
        self.reqs[offset] = self._g.request_lines(self.chip, consumer="lorascan", config={
            offset: self._g.LineSettings(direction=self._Dir.OUTPUT, output_value=self._Val.ACTIVE if initial else self._Val.INACTIVE)})

    def request_in(self, offset: int):
        self.reqs[offset] = self._g.request_lines(self.chip, consumer="lorascan", config={
            offset: self._g.LineSettings(direction=self._Dir.INPUT)})

    def set(self, offset: int, level: bool):
        self.reqs[offset].set_value(offset, self._Val.ACTIVE if level else self._Val.INACTIVE)

    def get(self, offset: int) -> bool:
        return self.reqs[offset].get_value(offset) == self._Val.ACTIVE

    def close(self):
        for r in self.reqs.values():
            r.release()


class _GpioLg:
    def __init__(self, chip: int = 0):
        import lgpio
        self._l = lgpio
        self.h = lgpio.gpiochip_open(chip)

    def request_out(self, offset: int, initial: int):
        self._l.gpio_claim_output(self.h, offset, initial)

    def request_in(self, offset: int):
        self._l.gpio_claim_input(self.h, offset)

    def set(self, offset: int, level: bool):
        self._l.gpio_write(self.h, offset, 1 if level else 0)

    def get(self, offset: int) -> bool:
        return bool(self._l.gpio_read(self.h, offset))

    def close(self):
        self._l.gpiochip_close(self.h)


def _open_gpio():
    try:
        import gpiod
        if hasattr(gpiod, "request_lines"):
            return _GpioV2()
        return _GpioV1()
    except ImportError:
        pass
    try:
        return _GpioLg()
    except ImportError as e:
        raise HalError("no GPIO library: install python3-libgpiod (apt) or lgpio") from e


class SpidevGpiodHal:
    def __init__(self, profile):
        try:
            import spidev
        except ImportError as e:
            raise HalError("python3-spidev is not installed (apt install python3-spidev)") from e
        dev = profile.bus_dev
        bus, cs = dev.replace("/dev/spidev", "").split(".")
        self.spi = spidev.SpiDev()
        self.spi.open(int(bus), int(cs))
        self.spi.mode = 0
        self.spi.max_speed_hz = int(profile.bus_hz)
        self.pins = profile.pins
        self.gpio = _open_gpio()
        for name in ("reset", "rxen", "txen"):
            p = self.pins.get(name)
            if isinstance(p, int):
                self.gpio.request_out(p, 1 if name == "reset" else 0)
        for name in ("busy", "dio1"):
            p = self.pins.get(name)
            if not isinstance(p, int):
                raise HalError(f"profile pin {name!r} must be a GPIO line number")
            self.gpio.request_in(p)
        if isinstance(self.pins.get("nss"), int):
            raise HalError("software chip-select (pins.nss as a GPIO) is not supported in P1; use a kernel CE")

    def xfer(self, tx: bytes) -> bytes:
        return bytes(self.spi.xfer2(list(tx)))

    def busy(self) -> bool:
        return self.gpio.get(self.pins["busy"])

    def dio1(self) -> bool:
        return self.gpio.get(self.pins["dio1"])

    def set_reset(self, level: bool) -> None:
        self.gpio.set(self.pins["reset"], level)

    def set_rxen(self, level: bool) -> None:
        p = self.pins.get("rxen")
        if isinstance(p, int):
            self.gpio.set(p, level)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def close(self) -> None:
        try:
            self.spi.close()
        finally:
            self.gpio.close()
