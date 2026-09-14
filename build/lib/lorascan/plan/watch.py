"""watch plan (spec §3.3): a fixed list of frequencies at high time resolution, all three layers —
energy at each measurement bandwidth, CAD at each (sf, bw) pair, decode for every known preset that
lives on that frequency. cycles=None runs forever."""
from __future__ import annotations
from typing import Iterator
from .survey import Step
from ..networks import presets_on


def watch_plan(freqs: list[int], dwell_s: float = 2.0, sfs: list[int] = (7, 9, 11), bws: list[int] = (125, 250),
               decode_dwell_s: float = 10.0, cycles: int | None = None, cad_n: int = 50) -> Iterator[Step]:
    n = 0
    while cycles is None or n < cycles:
        for f in freqs:
            for bw in bws:
                yield Step(f, bw, dwell_s, "energy")
            for sf in sfs:
                for bw in bws:
                    yield Step(f, bw, dwell_s, "cad", sf=sf)
            for net, p in presets_on(f):
                yield Step(f, p.bw_khz, decode_dwell_s, "decode", sf=p.sf, cr=p.cr, network=net, preset=p.name)
        n += 1
