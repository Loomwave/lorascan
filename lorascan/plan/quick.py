"""quick plan (spec §3.3): the whole grid `passes` times, then the known channels once at 3 s."""
from __future__ import annotations
from typing import Iterator
from .grid import KNOWN_CHANNELS
from .survey import Step


def quick_plan(grid: list[int], passes: int = 2, dwell_s: float = 0.4, bw_khz: int = 125,
               known_dwell_s: float = 3.0) -> Iterator[Step]:
    for _ in range(passes):
        for f in grid:
            yield Step(f, bw_khz, dwell_s)
    for f, _label in KNOWN_CHANNELS:
        yield Step(f, bw_khz, known_dwell_s)
