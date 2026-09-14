"""FakeHal: scripted SPI replies keyed by opcode, a consumable BUSY sequence, a fake clock.
Records every transfer so tests assert the exact command order the radio saw."""
from __future__ import annotations
from typing import Callable

Reply = bytes | Callable[[bytes], bytes]


class FakeHal:
    def __init__(self, replies: dict[int, Reply] | None = None):
        self.replies: dict[int, Reply] = dict(replies or {})
        self.log: list[bytes] = []
        self.busy_sequence: list[bool] = []
        self.reset_log: list[bool] = []
        self.rxen_log: list[bool] = []
        self.clock = 0.0
        self.dio1_level = False

    @classmethod
    def for_profile(cls, profile) -> "FakeHal":
        """A fake radio that answers like a live SX1262 at rest: status 0x22 (STDBY_RC), sync word
        0x14 0x24, no device errors, and RSSI readings around -105 dBm with occasional -80 dBm bursts."""
        import random
        rng = random.Random(1)

        def rssi(tx: bytes) -> bytes:
            dbm = -80 if rng.random() < 0.05 else rng.gauss(-105, 1.5)
            return bytes([0, 0, max(0, min(255, int(round(-2 * dbm))))])

        return cls({
            0xC0: bytes([0, 0x22]),
            0x1D: bytes([0, 0, 0, 0, 0x14, 0x24]),
            0x17: bytes([0, 0, 0, 0]),
            0x15: rssi,
        })

    def xfer(self, tx: bytes) -> bytes:
        self.log.append(bytes(tx))
        r = self.replies.get(tx[0])
        data = r(tx) if callable(r) else (r or b"")
        return (bytes(data) + bytes(len(tx)))[: len(tx)]

    def busy(self) -> bool:
        if self.busy_sequence:
            return self.busy_sequence.pop(0)
        return False

    def dio1(self) -> bool:
        return self.dio1_level

    def set_reset(self, level: bool) -> None:
        self.reset_log.append(level)

    def set_rxen(self, level: bool) -> None:
        self.rxen_log.append(level)

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def close(self) -> None:
        pass
