"""Recommend the best width-W (default 500 kHz) slot: rank a free grid of windows across the band using the
true width-W energy the scan captured, plus LoRa presence (CAD/decode) and a narrowband-carrier term, and
strike windows overlapping an exclusion zone. Pure; no I/O. (Loomwave/lorascan: 500 kHz deployment slot.)"""
from __future__ import annotations
from ..exclusions import DEFAULT_EXCLUSIONS, overlaps


def _in_window(freq: int, start: int, end: int) -> bool:
    return start <= freq < end


def recommend_slots(by_bw, cad, decodes, width_hz: int = 500_000, exclusions=None) -> dict:
    zones = list(DEFAULT_EXCLUSIONS) if exclusions is None else list(exclusions)
    half = width_hz // 2
    width_rows = [c for c in by_bw if c["bw_hz"] == width_hz]
    if not width_rows:
        return {"width_hz": width_hz, "recommended": None, "windows": []}
    band_best_floor = min(c["floor_med"] for c in by_bw)
    windows = []
    for wr in width_rows:
        center = wr["freq_hz"]; start = center - half; end = center + half
        cad_hit_max = 0.0; cad_sf_max = None
        for x in cad:
            if _in_window(x["freq_hz"], start, end) and x["hit_rate"] >= cad_hit_max:
                cad_hit_max, cad_sf_max = x["hit_rate"], x["sf"]
        dec: dict[str, int] = {}
        for d in decodes:
            if _in_window(d["freq_hz"], start, end) and d.get("n_ok"):
                nm = f"{d['network']}/{d['preset']}"
                dec[nm] = dec.get(nm, 0) + d["n_ok"]
        frames = sum(dec.values())
        # narrowband-carrier term: worst floor among the NARROWEST bandwidth present per freq inside the window
        narrow_by_freq: dict[int, tuple] = {}
        for c in by_bw:
            if c["bw_hz"] < width_hz and _in_window(c["freq_hz"], start, end):
                cur = narrow_by_freq.get(c["freq_hz"])
                if cur is None or c["bw_hz"] < cur[0]:
                    narrow_by_freq[c["freq_hz"]] = (c["bw_hz"], c["floor_med"])
        worst_narrow_floor = max((v[1] for v in narrow_by_freq.values()), default=wr["floor_med"])
        carrier_pen = round(max(0.0, (worst_narrow_floor - band_best_floor) / 10.0), 4)
        score = round(wr["busy_mean"] + cad_hit_max + frames / 10.0 + carrier_pen, 4)
        excluded = overlaps(start, end, zones)
        why = _why(wr, cad_hit_max, frames, carrier_pen, narrow_by_freq, excluded)
        windows.append({"center_hz": center, "center_mhz": center / 1e6, "start_hz": start, "end_hz": end,
                        "start_mhz": start / 1e6, "end_mhz": end / 1e6, "busy_w": round(wr["busy_mean"], 4),
                        "floor_w": wr["floor_med"], "peak_w": wr["peak_max"], "cad_hit_max": round(cad_hit_max, 4),
                        "cad_sf_max": cad_sf_max, "decoded": ", ".join(f"{n}:{c}" for n, c in sorted(dec.items())),
                        "decoded_frames": frames, "carrier_pen": carrier_pen, "excluded": excluded, "score": score,
                        "why": why})
    windows.sort(key=lambda w: (w["excluded"], w["score"]))
    for i, w in enumerate(windows, 1):
        w["rank"] = i
    recommended = next((w for w in windows if not w["excluded"]), None)
    return {"width_hz": width_hz, "recommended": recommended, "windows": windows}


def grid_centers(width_hz: int = 500_000, base_hz: int = 902_250_000,
                  band: tuple[int, int] = (902_000_000, 928_000_000)) -> list[int]:
    """The fixed MeshCore-500 .250/.750 grid: base_hz + n*width_hz for n=0,1,... while each window stays
    inside band. Stops at the first n whose window pokes past a band edge."""
    half = width_hz // 2
    centers = []
    n = 0
    while True:
        center = base_hz + n * width_hz
        if not (center - half >= band[0] and center + half <= band[1]):
            break
        centers.append(center)
        n += 1
    return centers


def grid_width_rows(by_bw, centers, width_hz: int = 500_000) -> list[dict]:
    """One synthetic width-W row per grid center that has real width-W data in its window, worst-case
    aggregated across the in-window rows so a channel is only as good as its worst measured sub-point.
    Centers with no in-window width-W data are omitted."""
    half = width_hz // 2
    width_rows = [c for c in by_bw if c["bw_hz"] == width_hz]
    out = []
    for center in centers:
        start, end = center - half, center + half
        in_window = [c for c in width_rows if _in_window(c["freq_hz"], start, end)]
        if not in_window:
            continue
        out.append({
            "freq_hz": center,
            "bw_hz": width_hz,
            "busy_mean": max(c.get("busy_mean", 0) for c in in_window),
            "floor_med": max(c.get("floor_med", 0) for c in in_window),
            "p90_med": max(c.get("p90_med", 0) for c in in_window),
            "peak_max": max(c.get("peak_max", 0) for c in in_window),
            "n_rows": sum(c.get("n_rows", 0) for c in in_window),
            "n_samples": sum(c.get("n_samples", 0) for c in in_window),
        })
    return out


def recommend_grid_slots(by_bw, cad, decodes, width_hz: int = 500_000, exclusions=None,
                          base_hz: int = 902_250_000) -> dict:
    """Grid-aligned variant of recommend_slots: rank the fixed .250/.750 MeshCore-500 channels instead of
    a free grid of windows, by feeding recommend_slots synthetic per-grid-center rows built from the real
    scan data. Reuses recommend_slots' scoring/sorting/exclusion logic unchanged."""
    centers = grid_centers(width_hz, base_hz)
    synth = grid_width_rows(by_bw, centers, width_hz)
    narrow_rows = [c for c in by_bw if c["bw_hz"] != width_hz]
    result = recommend_slots(synth + narrow_rows, cad, decodes, width_hz, exclusions)
    result["grid"] = True
    result["base_hz"] = base_hz
    return result


def _why(wr, cad_hit_max, frames, carrier_pen, narrow_by_freq, excluded) -> str:
    if excluded:
        return "overlaps an exclusion zone"
    parts = [f"busy {wr['busy_mean'] * 100:.0f}%", f"floor {wr['floor_med']:.0f} dBm"]
    if frames:
        parts.append(f"{frames} known-LoRa frames")
    elif cad_hit_max > 0:
        parts.append(f"CAD {cad_hit_max * 100:.0f}%")
    else:
        parts.append("no known LoRa")
    if carrier_pen > 0 and narrow_by_freq:
        loud = max(narrow_by_freq.items(), key=lambda kv: kv[1][1])
        parts.append(f"carrier near {loud[0] / 1e6:.2f} MHz raises the floor")
    elif carrier_pen > 0:
        parts.append(f"floor {wr['floor_med']:.0f} dBm above band best raises the score")
    else:
        parts.append("clear of exclusion zones")
    return ", ".join(parts)
