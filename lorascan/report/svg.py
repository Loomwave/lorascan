"""Static SVG figures for the report: render with no JavaScript and no network.

plotly.js (CDN) is an enhancement layered on top; these are the figures a tester
sees on an offline Pi, in a mail client, or when the CDN is blocked."""
from __future__ import annotations

import html


def _lerp(a, b, t):
    return a + (b - a) * t


def busy_colour(v) -> str:
    """0..1 busy fraction -> YlOrRd-like hex; None -> hatched grey."""
    if v is None:
        return "#C9CFD6"
    v = max(0.0, min(1.0, float(v)))
    stops = [(0.0, (255, 255, 204)), (0.25, (254, 217, 118)), (0.5, (253, 141, 60)), (0.75, (227, 26, 28)), (1.0, (128, 0, 38))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if v <= t1:
            t = (v - t0) / (t1 - t0) if t1 > t0 else 0.0
            return "#%02x%02x%02x" % tuple(int(round(_lerp(c0[i], c1[i], t))) for i in range(3))
    return "#800026"


def level_colour(v, vmin, vmax) -> str:
    """dBm level -> Viridis-like hex."""
    if v is None:
        return "#C9CFD6"
    t = 0.0 if vmax <= vmin else max(0.0, min(1.0, (float(v) - vmin) / (vmax - vmin)))
    stops = [(0.0, (68, 1, 84)), (0.25, (59, 82, 139)), (0.5, (33, 145, 140)), (0.75, (94, 201, 98)), (1.0, (253, 231, 37))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return "#%02x%02x%02x" % tuple(int(round(_lerp(c0[i], c1[i], u))) for i in range(3))
    return "#fde725"


MAX_COLS = 240   # columns in the static heat map; longer runs merge adjacent buckets (mean of measured cells)


def merge_columns(buckets: list, z: list, max_cols: int = MAX_COLS) -> tuple[list, list, int]:
    """Merge adjacent time buckets so the map has at most max_cols columns. Returns (buckets, z, per)."""
    m = len(buckets)
    if m <= max_cols:
        return buckets, z, 1
    per = -(-m // max_cols)
    nb = [buckets[j] for j in range(0, m, per)]
    nz = []
    for row in z:
        out = []
        for j in range(0, m, per):
            vals = [v for v in row[j:j + per] if v is not None]
            out.append(round(sum(vals) / len(vals), 4) if vals else None)
        nz.append(out)
    return nb, nz, per


def _txt(x, y, s, size=11, anchor="start", extra=""):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="currentColor" {extra}>{html.escape(str(s))}</text>'


def _excl_bands_y(out, zones, y_of_mhz, x0, w, label=True):
    """Shade exclusion zones (#9) as hatched horizontal bands on a frequency (y) axis."""
    for lo, hi in (zones or []):
        y1, y0 = y_of_mhz(hi / 1e6), y_of_mhz(lo / 1e6)
        if y0 <= y1:
            continue
        out.append(f'<rect class="excl" x="{x0:.1f}" y="{y1:.1f}" width="{w:.1f}" height="{y0 - y1:.1f}" fill="url(#exclhatch)" stroke="currentColor" stroke-opacity="0.4" stroke-dasharray="3 3"><title>{lo / 1e6:.3f}–{hi / 1e6:.3f} MHz: excluded from recommendations (band edge / repeater segment) — not an option</title></rect>')
        if label:
            out.append(_txt(x0 + 4, y1 + 10, f"excluded {lo / 1e6:.3f}–{hi / 1e6:.3f}", 9, "start", 'fill-opacity="0.8"'))


_EXCL_DEFS = '<defs><pattern id="exclhatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="currentColor" stroke-opacity="0.35" stroke-width="3"/></pattern></defs>'


def heatmap_svg(heat: dict, layer: str = "busy", width: int = 1060, exclusions=None) -> str:
    """Occupancy heat map: rows = channels (MHz), columns = time buckets."""
    freqs = heat.get("freqs_mhz") or []
    buckets = heat.get("buckets") or []
    z = heat.get(layer) or []
    if not freqs or not buckets or not z:
        return ""
    buckets, z, per = merge_columns(buckets, z)
    ml, mr, mt, mb = 62, 70, 24, 40
    cell_h = max(3, min(14, int(560 / len(freqs))))
    ph = cell_h * len(freqs)
    pw = width - ml - mr
    cell_w = pw / len(buckets)
    vals = [v for row in z for v in row if v is not None]
    vmin, vmax = (min(vals), max(vals)) if vals else (0.0, 1.0)
    colour = (lambda v: busy_colour(v)) if layer == "busy" else (lambda v: level_colour(v, vmin, vmax))
    out = [f'<svg class="static" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {ph + mt + mb}" width="100%" role="img" aria-label="occupancy heat map ({layer})" font-family="system-ui,sans-serif">', _EXCL_DEFS]
    # cells, drawn bottom-up so the lowest frequency is at the bottom like a spectrum display
    n = len(freqs)
    for i, f in enumerate(freqs):
        y = mt + (n - 1 - i) * cell_h
        row = z[i] if i < len(z) else []
        for j in range(len(buckets)):
            v = row[j] if j < len(row) else None
            out.append(f'<rect x="{ml + j * cell_w:.1f}" y="{y}" width="{cell_w + 0.4:.1f}" height="{cell_h}" fill="{colour(v)}"/>')
    if exclusions and n > 1:
        fmin, fmax = min(freqs), max(freqs)
        step_mhz = (fmax - fmin) / (n - 1) if n > 1 else 0.2
        # channel i occupies row (n-1-i); map a frequency to the y of its row edge (linear in the grid)
        def y_of_mhz(m):
            pos = (m - fmin) / step_mhz            # rows from the bottom, in channel units
            return mt + (n - pos - 0.5) * cell_h
        _excl_bands_y(out, [(max(lo, int(fmin * 1e6 - step_mhz * 5e5)), min(hi, int(fmax * 1e6 + step_mhz * 5e5))) for lo, hi in exclusions], y_of_mhz, ml, pw)
    # y labels: at most ~14 evenly spaced channels, always first and last
    step = max(1, n // 13)
    for i in range(0, n, step):
        y = mt + (n - 1 - i) * cell_h + cell_h / 2 + 4
        out.append(_txt(ml - 6, y, f"{freqs[i]:.1f}", 10, "end"))
    if (n - 1) % step:
        out.append(_txt(ml - 6, mt + cell_h / 2 + 4, f"{freqs[-1]:.1f}", 10, "end"))
    out.append(_txt(14, mt + ph / 2, "MHz", 11, "middle", f'transform="rotate(-90 14 {mt + ph / 2:.1f})"'))
    # x labels: ≤ 8 bucket stamps, HH:MM (date on the first)
    m = len(buckets)
    xs = max(1, m // 7)
    for j in range(0, m, xs):
        b = buckets[j]
        lab = b[11:16] if len(b) >= 16 else b
        if j == 0 and len(b) >= 10:
            lab = b[:10] + " " + lab
        out.append(_txt(ml + j * cell_w + cell_w / 2, mt + ph + 16, lab, 10, "middle"))
    note = "time (UTC)" if per == 1 else f"time (UTC); {per} buckets of {heat.get('bucket_s', '?')} s merged per column"
    out.append(_txt(ml + pw / 2, mt + ph + 32, note, 11, "middle"))
    # colour bar
    cx = width - mr + 16
    steps = 20
    for k in range(steps):
        t = 1 - k / (steps - 1)
        v = t if layer == "busy" else vmin + t * (vmax - vmin)
        out.append(f'<rect x="{cx}" y="{mt + k * ph / steps:.1f}" width="14" height="{ph / steps + 0.5:.1f}" fill="{colour(v)}"/>')
    top, bot = ("1.0", "0.0") if layer == "busy" else (f"{vmax:.0f}", f"{vmin:.0f}")
    out.append(_txt(cx + 18, mt + 9, top, 9))
    out.append(_txt(cx + 18, mt + ph, bot, 9))
    out.append(_txt(cx + 7, mt - 8, "busy" if layer == "busy" else "P90 dBm", 9, "middle"))
    out.append("</svg>")
    return "".join(out)


def band_svg(channels: list, width: int = 1060, exclusions=None) -> str:
    """Band summary: one bar per channel from floor (P10) to peak, coloured by busy fraction."""
    chans = [c for c in channels if c.get("floor_med") is not None and c.get("peak_max") is not None]
    if not chans:
        return ""
    ml, mr, mt, mb = 56, 16, 24, 44
    ph = 300
    pw = width - ml - mr
    lo = min(min(c["floor_med"] for c in chans), min(c["peak_max"] for c in chans))
    hi = max(c["peak_max"] for c in chans)
    lo = 5 * ((lo - 5) // 5)
    hi = 5 * ((hi + 5) // 5 + 1)
    span = max(1.0, hi - lo)
    fmin = min(c["mhz"] for c in chans)
    fmax = max(c["mhz"] for c in chans)
    fspan = max(0.001, fmax - fmin)
    x_of = lambda mhz: ml + (mhz - fmin) / fspan * (pw - 8) + 4
    y_of = lambda dbm: mt + (hi - dbm) / span * ph
    bar_w = max(2.0, (pw / max(1, len(chans))) * 0.7)
    out = [f'<svg class="static" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {ph + mt + mb}" width="100%" role="img" aria-label="band summary" font-family="system-ui,sans-serif">']
    # gridlines every 10 dB
    d = lo
    while d <= hi:
        y = y_of(d)
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" stroke="currentColor" stroke-opacity="0.15" stroke-width="1"/>')
        out.append(_txt(ml - 6, y + 4, f"{d:.0f}", 10, "end"))
        d += 10
    out.append(_txt(12, mt + ph / 2, "dBm", 11, "middle", f'transform="rotate(-90 12 {mt + ph / 2:.1f})"'))
    for c in chans:
        x = x_of(c["mhz"]) - bar_w / 2
        y1, y0 = y_of(c["peak_max"]), y_of(c["floor_med"])
        h = max(1.5, y0 - y1)
        title = f"{c['mhz']:.3f} MHz: floor {c['floor_med']:.0f} dBm, P90 {c.get('p90_med', c['floor_med']):.0f} dBm, peak {c['peak_max']:.0f} dBm, busy {c.get('busy_mean', 0) * 100:.1f} %"
        if c.get("label"):
            title += f" ({c['label']})"
        out.append(f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{busy_colour(c.get("busy_mean", 0))}" stroke="currentColor" stroke-opacity="0.35" stroke-width="0.5"><title>{html.escape(title)}</title></rect>')
        if c.get("p90_med") is not None:
            yp = y_of(c["p90_med"])
            out.append(f'<line x1="{x:.1f}" y1="{yp:.1f}" x2="{x + bar_w:.1f}" y2="{yp:.1f}" stroke="currentColor" stroke-width="1"/>')
    # exclusion zones (#9): hatched vertical bands, drawn over the bars, labelled once at the top
    if exclusions:
        out.insert(1, _EXCL_DEFS)
        for lo, hi in exclusions:
            xa, xb = max(ml, x_of(lo / 1e6)), min(ml + pw, x_of(hi / 1e6))
            if xb <= xa:
                continue
            out.append(f'<rect class="excl" x="{xa:.1f}" y="{mt}" width="{xb - xa:.1f}" height="{ph}" fill="url(#exclhatch)" stroke="currentColor" stroke-opacity="0.4" stroke-dasharray="3 3"><title>{lo / 1e6:.3f}–{hi / 1e6:.3f} MHz: excluded from recommendations (band edge / repeater segment) — not an option</title></rect>')
            out.append(_txt((xa + xb) / 2, mt + 12, "not an option", 9, "middle", 'fill-opacity="0.8"'))
    # labelled channels
    for c in chans:
        if c.get("label"):
            xx = x_of(c["mhz"])
            out.append(_txt(xx, y_of(c["peak_max"]) - 4, c["label"], 9, "middle", f'transform="rotate(-60 {xx:.1f} {y_of(c["peak_max"]) - 4:.1f})"'))
    # x ticks every 2 MHz
    t = 2 * ((fmin + 1.999) // 2)
    while t <= fmax:
        out.append(_txt(x_of(t), mt + ph + 16, f"{t:.0f}", 10, "middle"))
        out.append(f'<line x1="{x_of(t):.1f}" y1="{mt + ph}" x2="{x_of(t):.1f}" y2="{mt + ph + 4}" stroke="currentColor"/>')
        t += 2
    out.append(_txt(ml + pw / 2, mt + ph + 34, "MHz  (bar = floor to peak, tick = P90, colour = busy fraction)", 11, "middle"))
    out.append("</svg>")
    return "".join(out)


def sfmap_svg(sfmap: dict, width: int = 600) -> str:
    """CAD hit rate per (frequency, SF)."""
    freqs = sfmap.get("freqs_mhz") or []
    sfs = sfmap.get("sfs") or []
    z = sfmap.get("z") or []
    if not freqs or not sfs or not z:
        return ""
    ml, mr, mt, mb = 62, 16, 24, 36
    cell_h = max(6, min(22, int(400 / len(freqs))))
    ph = cell_h * len(freqs)
    pw = width - ml - mr
    cell_w = pw / len(sfs)
    out = [f'<svg class="static" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {ph + mt + mb}" width="100%" style="max-width:{width}px" role="img" aria-label="CAD hit rate by spreading factor" font-family="system-ui,sans-serif">']
    n = len(freqs)
    for i, f in enumerate(freqs):
        y = mt + (n - 1 - i) * cell_h
        for j, sf in enumerate(sfs):
            v = z[i][j] if i < len(z) and j < len(z[i]) else None
            out.append(f'<rect x="{ml + j * cell_w:.1f}" y="{y}" width="{cell_w + 0.4:.1f}" height="{cell_h}" fill="{busy_colour(v)}"><title>{f:.3f} MHz SF{sf}: {"n/a" if v is None else f"{v * 100:.0f} %"}</title></rect>')
            if v is not None and cell_h >= 12:
                out.append(_txt(ml + j * cell_w + cell_w / 2, y + cell_h / 2 + 3, f"{v * 100:.0f}", 9, "middle", 'fill-opacity="0.85"'))
        out.append(_txt(ml - 6, y + cell_h / 2 + 4, f"{f:.3f}", 10, "end"))
    for j, sf in enumerate(sfs):
        out.append(_txt(ml + j * cell_w + cell_w / 2, mt + ph + 16, f"SF{sf}", 10, "middle"))
    out.append(_txt(ml + pw / 2, mt + ph + 32, "CAD hit rate, % (colour = 0..1)", 11, "middle"))
    out.append("</svg>")
    return "".join(out)


def when_svg(when: list, width: int = 700) -> str:
    """Hour-of-day x weekday occupancy."""
    if not when or all(v is None for row in when for v in row):
        return ""
    ml, mr, mt, mb = 40, 16, 20, 36
    cell_h, pw = 22, width - ml - mr
    cell_w = pw / 24
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ph = cell_h * 7
    out = [f'<svg class="static" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {ph + mt + mb}" width="100%" style="max-width:{width}px" role="img" aria-label="occupancy by hour and weekday" font-family="system-ui,sans-serif">']
    for i, row in enumerate(when[:7]):
        y = mt + i * cell_h
        for h in range(24):
            v = row[h] if h < len(row) else None
            out.append(f'<rect x="{ml + h * cell_w:.1f}" y="{y}" width="{cell_w + 0.4:.1f}" height="{cell_h}" fill="{busy_colour(v)}"><title>{days[i]} {h:02d}:00 UTC: {"n/a" if v is None else f"busy {v * 100:.0f} %"}</title></rect>')
        out.append(_txt(ml - 6, y + cell_h / 2 + 4, days[i], 10, "end"))
    for h in range(0, 24, 3):
        out.append(_txt(ml + h * cell_w + cell_w / 2, mt + ph + 16, f"{h:02d}", 10, "middle"))
    out.append(_txt(ml + pw / 2, mt + ph + 32, "hour (UTC)", 11, "middle"))
    out.append("</svg>")
    return "".join(out)
