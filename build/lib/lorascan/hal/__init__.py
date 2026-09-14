"""Hardware abstraction under the SX126x command layer (spec §3): one full-duplex SPI transfer with
chip-select held for exactly that transfer, the BUSY/DIO1 level reads, the RESET line, optional
FEM enables, and delays. Implementations: FakeHal (tests), SpidevGpiodHal (Linux SPI + GPIO)."""
from __future__ import annotations
from typing import Protocol


class Hal(Protocol):
    def xfer(self, tx: bytes) -> bytes: ...
    def busy(self) -> bool: ...
    def dio1(self) -> bool: ...
    def set_reset(self, level: bool) -> None: ...
    def set_rxen(self, level: bool) -> None: ...
    def sleep(self, seconds: float) -> None: ...
    def close(self) -> None: ...


class HalError(Exception):
    pass


def open_hal(profile) -> Hal:
    """Pick the HAL by profile.bus_type ('fake' for tests/dry runs, 'spidev' for hardware)."""
    if profile.bus_type == "fake":
        from .fake import FakeHal
        return FakeHal.for_profile(profile)
    if profile.bus_type == "spidev":
        from .spidev_gpiod import SpidevGpiodHal
        return SpidevGpiodHal(profile)
    if profile.bus_type == "ch341":
        from .ch341 import Ch341Hal
        return Ch341Hal(profile)
    raise HalError(f"unsupported bus type {profile.bus_type!r} (supported: fake, spidev, ch341)")
