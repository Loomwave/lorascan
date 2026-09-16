"""Pure ASCII band-energy bar graph for the terminal (immediate feedback in `lorascan setup`)."""
from __future__ import annotations
import shutil

_EXCL = ((902_000_000, 903_250_000), (926_750_000, 928_000_000))


def _in_excl(hz: float) -> bool:
    return any(lo <= hz <= hi for lo, hi in _EXCL)


def _bar_width(width):
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns
    return max(40, min(100, int(width)))


def band_graph(rows, width=None, floor_dbm=-125, ceil_dbm=-20) -> str:
    w = _bar_width(width)
    barcells = max(1, w - 24)                          # leave room for the "NNN.NN MHz [X] " label
    span = float(ceil_dbm - floor_dbm) or 1.0
    out = [f"band energy  {floor_dbm}..{ceil_dbm} dBm  ([X] = exclusion zone)"]
    for row in rows:
        if len(row) == 3:
            hz, _floor, level = row
        else:
            hz, level = row
        frac = (float(level) - floor_dbm) / span
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        fill = int(round(frac * barcells))
        mark = "[X]" if _in_excl(hz) else "   "
        out.append(f"{hz/1e6:7.2f} MHz {mark} {'#' * fill}{'.' * (barcells - fill)}")
    return "\n".join(out)
