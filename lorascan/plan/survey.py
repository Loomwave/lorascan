"""survey plan (spec §3.3): endless round-robin with adaptive revisiting. Each round visits every
channel whose last visit is older than revisit_max_s, plus the most active channels (top 10 % by
the caller-maintained `activity` map, e.g. busy_frac). No channel waits longer than revisit_max_s."""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Iterator, Callable


@dataclass(frozen=True)
class Step:
    freq_hz: int
    bw_khz: int
    dwell_s: float
    layer: str = "energy"


def survey_plan(grid: list[int], dwell_s: float = 0.4, revisit_max_s: float = 600.0,
                activity: dict[int, float] | None = None, bw_khz: int = 125,
                clock: Callable[[], float] = time.monotonic) -> Iterator[Step]:
    activity = activity if activity is not None else {}
    last: dict[int, float] = {}
    n_hot = max(1, len(grid) // 10)
    while True:
        now = clock()
        due = [f for f in grid if now - last.get(f, -1e18) >= revisit_max_s]
        if not due:
            # nothing is due: revisit the oldest tenth so time-resolution is spent, not wasted
            due = sorted(grid, key=lambda f: last.get(f, -1e18))[: n_hot]
        hot = sorted((f for f in grid if activity.get(f, 0.0) > 0.0), key=lambda f: -activity[f])[: n_hot]
        order = due + [f for f in hot if f not in due]
        for f in order:
            yield Step(f, bw_khz, dwell_s)
            last[f] = clock()
