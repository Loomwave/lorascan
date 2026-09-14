"""quick plan (spec §3.3): the whole grid `passes` times, then the known channels once at 3 s."""
from __future__ import annotations
from typing import Iterator
from .grid import KNOWN_CHANNELS
from .survey import Step


def quick_plan(grid: list[int], passes: int = 2, dwell_s: float = 0.4, bw_khz=125,
               known_dwell_s: float = 3.0) -> Iterator[Step]:
    """bw_khz may be a list (Loomwave/lorascan#2): every width is measured back to back per channel."""
    bws = list(bw_khz) if isinstance(bw_khz, (list, tuple)) else [bw_khz]
    for _ in range(passes):
        for f in grid:
            for bw in bws:
                yield Step(f, bw, dwell_s)
    for f, _label in KNOWN_CHANNELS:
        for bw in bws:
            yield Step(f, bw, known_dwell_s)
