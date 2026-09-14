"""candidate test plan (spec §3.3 'test'): for each (freq, sf, bw, cr) candidate, a long passive dwell at
exactly those settings — energy at that bw, CAD at that sf/bw, decode for every known preset on that
frequency — then a ranked report card."""
from __future__ import annotations
from typing import Iterator
from .survey import Step
from ..networks import presets_on


def parse_candidates(text: str) -> list[tuple[int, int, int, int]]:
    """'905.0/9/125,921.0/11/250/8' -> [(905000000, 9, 125, 5), (921000000, 11, 250, 8)]."""
    out = []
    for part in text.split(","):
        bits = part.strip().split("/")
        if len(bits) < 3:
            raise ValueError(f"candidate {part!r}: need MHz/SF/BW[/CR]")
        mhz, sf, bw = float(bits[0]), int(bits[1]), int(bits[2])
        cr = int(bits[3]) if len(bits) > 3 else 5
        out.append((int(round(mhz * 1e6)), sf, bw, cr))
    return out


def candidate_plan(cands: list[tuple[int, int, int, int]], dwell_s: float = 30.0, cad_n: int = 200) -> Iterator[Step]:
    for f, sf, bw, cr in cands:
        yield Step(f, bw, dwell_s, "energy")
        yield Step(f, bw, dwell_s, "cad", sf=sf, cr=cr)
        for net, p in presets_on(f):
            yield Step(f, p.bw_khz, dwell_s, "decode", sf=p.sf, cr=p.cr, network=net, preset=p.name)


def rank_candidates(rows: list[dict]) -> list[dict]:
    """Score = busy fraction + CAD hit rate + decoded frames per minute / 10 (all lower is better);
    ties broken by the lower floor. Adds 'score' and 'rank' to copies of the rows."""
    scored = []
    for r in rows:
        score = float(r.get("busy_mean", 0.0)) + float(r.get("cad_hit_rate", 0.0)) + float(r.get("decoded", 0)) / 10.0
        scored.append({**r, "score": round(score, 4)})
    scored.sort(key=lambda r: (r["score"], r.get("floor_med", 0.0)))
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    return scored
