"""The first front end for share.lorascan.app: fleet aggregates from the share database and a
self-contained HTML page (inline SVG, no JavaScript, no CDN). Spec §4a: cells weighted by distinct
submitters x hours, single-submitter cells hatched, flagged uploads never merged."""
from __future__ import annotations
import datetime as dt
import html
from .plan.grid import label_for
from .report.svg import busy_colour, band_svg, when_svg, grid_ribbon_svg
from .exclusions import DEFAULT_EXCLUSIONS, excluded, overlaps, zones_mhz
from .report.slot_recommend import recommend_grid_slots, grid_centers, grid_width_rows


def map_data(db, exclusions=None) -> dict:
    exclusions = list(DEFAULT_EXCLUSIONS) if exclusions is None else list(exclusions)
    con = db.con
    subs = con.execute("SELECT submitter, tool, board, calibration, cell_lat, cell_lon, cell_size, uploads, last_seen FROM submitters ORDER BY last_seen DESC").fetchall()
    flagged = {r[0] for r in con.execute("SELECT DISTINCT submitter FROM energy WHERE flagged = 1")}
    rows = con.execute("SELECT submitter, freq_hz, bw_hz, bucket_s, bucket, hours, floor_p10_med, p90_med, peak_max, busy_mean FROM energy WHERE flagged = 0").fetchall()
    cell_of = {s[0]: ((s[4], s[5], s[6]) if s[4] is not None else None) for s in subs}
    # band summary across all unflagged submitters, per frequency
    band_acc: dict[int, dict] = {}
    cell_acc: dict[tuple, dict] = {}
    when_acc: dict[tuple, list] = {}
    bybw_acc: dict[tuple, dict] = {}
    for sub, f, bw, bs, bucket, hours, fl, p90, pk, busy in rows:
        b = band_acc.setdefault(f, {"floors": [], "busy": [], "peak": -999.0, "subs": set(), "hours": 0.0})
        b["floors"].append(fl); b["busy"].append(busy); b["peak"] = max(b["peak"], pk); b["subs"].add(sub); b["hours"] += hours or 0.0
        c = cell_of.get(sub)
        if c:
            ca = cell_acc.setdefault(c, {"subs": set(), "hours": 0.0, "chan": {}})
            ca["subs"].add(sub); ca["hours"] += hours or 0.0
            ch = ca["chan"].setdefault(f, {"busy": [], "floors": []}); ch["busy"].append(busy); ch["floors"].append(fl)
        bb = bybw_acc.setdefault((f, bw), {"floors": [], "busy": [], "peak": -999.0, "p90s": []})
        bb["floors"].append(fl); bb["busy"].append(busy); bb["peak"] = max(bb["peak"], pk); bb["p90s"].append(p90)
        if bs == 3600 and len(bucket) >= 13:
            try:
                t = dt.datetime.strptime(bucket[:13], "%Y-%m-%dT%H")
                w = when_acc.setdefault((t.weekday(), t.hour), [0.0, 0]); w[0] += busy; w[1] += 1
            except ValueError:
                pass
    def med(v):
        v = sorted(v); return v[len(v) // 2] if v else None
    band = []
    for f in sorted(band_acc):
        b = band_acc[f]
        band.append({"freq_hz": f, "mhz": f / 1e6, "label": label_for(f), "floor_med": med(b["floors"]), "p90_med": med(b["floors"]) if not b["floors"] else med(b["floors"]),
                     "peak_max": b["peak"], "busy_mean": round(sum(b["busy"]) / len(b["busy"]), 4), "n_submitters": len(b["subs"]), "hours": round(b["hours"], 2),
                     "excluded": excluded(f, exclusions)})
    cells = []
    for (lat, lon, size), ca in sorted(cell_acc.items()):
        chans = [{"mhz": f / 1e6, "busy": round(sum(v["busy"]) / len(v["busy"]), 4), "floor": med(v["floors"]), "label": label_for(f), "excluded": excluded(f, exclusions)} for f, v in sorted(ca["chan"].items())]
        allbusy = [c["busy"] for c in chans]
        cells.append({"lat": lat, "lon": lon, "size_deg": size, "submitters": len(ca["subs"]), "hours": round(ca["hours"], 2),
                      "busy_mean": round(sum(allbusy) / len(allbusy), 4) if allbusy else None,
                      "quietest": sorted(chans, key=lambda c: (c["excluded"], c["busy"], c["floor"] if c["floor"] is not None else 0))[:5],
                      "busiest": sorted(chans, key=lambda c: -c["busy"])[:5]})
    when = [[(round(when_acc[(wd, h)][0] / when_acc[(wd, h)][1], 4) if (wd, h) in when_acc else None) for h in range(24)] for wd in range(7)]
    # per-(freq, bw) aggregate across submitters, feeding the grid-aligned 500 kHz slot recommendation
    by_bw = []
    for (f, bw), bb in sorted(bybw_acc.items()):
        by_bw.append({"freq_hz": f, "bw_hz": bw, "floor_med": max(bb["floors"]) if bb["floors"] else None,
                      "busy_mean": round(sum(bb["busy"]) / len(bb["busy"]), 4) if bb["busy"] else 0.0,
                      "peak_max": bb["peak"], "p90_med": max(bb["p90s"]) if bb["p90s"] else None,
                      "n_rows": 0, "n_samples": 0})
    rec = recommend_grid_slots(by_bw, [], [], 500_000, exclusions)
    centers = grid_centers()
    synth = {r["freq_hz"]: r for r in grid_width_rows(by_bw, centers, 500_000)}
    rec_center = rec["recommended"]["center_hz"] if rec.get("recommended") else None
    grid = []
    for c in centers:
        s = synth.get(c)
        grid.append({"center_mhz": c / 1e6, "busy": (s["busy_mean"] if s else None), "floor": (s["floor_med"] if s else None),
                     "excluded": overlaps(c - 250_000, c + 250_000, exclusions), "recommended": (c == rec_center)})
    return {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "submitters": len(subs),
            "flagged_submitters": len(flagged), "no_location_submitters": sum(1 for s in subs if s[4] is None and s[0] not in flagged),
            "cells": cells, "band": band, "when": when, "exclusions": zones_mhz(exclusions), "exclusions_hz": [list(z) for z in exclusions],
            "grid": grid, "grid_recommend": rec,
            "submitter_rows": [{"id": s[0][:8], "tool": s[1], "board": s[2], "calibration": s[3], "cell": (f"{s[4]:.1f},{s[5]:.1f}" if s[4] is not None else "none"),
                                "uploads": s[7], "last_seen": dt.datetime.fromtimestamp(s[8], dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ") if s[8] else "", "flagged": s[0] in flagged} for s in subs]}


def cells_svg(cells: list, width: int = 700) -> str:
    """Equirectangular plot of the 0.1-degree cells (no basemap: nothing is fetched); hatched = one submitter."""
    if not cells:
        return ""
    lats = [c["lat"] for c in cells]; lons = [c["lon"] for c in cells]
    pad = max(0.5, (max(lats) - min(lats)) * 0.2, (max(lons) - min(lons)) * 0.2)
    lat0, lat1, lon0, lon1 = min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad
    ph = int(width * max(0.4, min(1.2, (lat1 - lat0) / (lon1 - lon0)))) if lon1 > lon0 else 400
    ml, mt = 50, 20
    pw = width - ml - 20
    x_of = lambda lon: ml + (lon - lon0) / (lon1 - lon0) * pw
    y_of = lambda lat: mt + (lat1 - lat) / (lat1 - lat0) * ph
    out = [f'<svg class="static" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {ph + mt + 40}" width="100%" style="max-width:{width}px" role="img" aria-label="cells map" font-family="system-ui,sans-serif">',
           '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="currentColor" stroke-opacity="0.5" stroke-width="2"/></pattern></defs>',
           f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="none" stroke="currentColor" stroke-opacity="0.3"/>']
    for g in range(int(lat0) - 1, int(lat1) + 2):
        if lat0 <= g <= lat1:
            out.append(f'<line x1="{ml}" y1="{y_of(g):.1f}" x2="{ml + pw}" y2="{y_of(g):.1f}" stroke="currentColor" stroke-opacity="0.12"/><text x="{ml - 4}" y="{y_of(g) + 4:.1f}" font-size="10" text-anchor="end" fill="currentColor">{g}°</text>')
    for g in range(int(lon0) - 1, int(lon1) + 2):
        if lon0 <= g <= lon1:
            out.append(f'<line x1="{x_of(g):.1f}" y1="{mt}" x2="{x_of(g):.1f}" y2="{mt + ph}" stroke="currentColor" stroke-opacity="0.12"/><text x="{x_of(g):.1f}" y="{mt + ph + 14}" font-size="10" text-anchor="middle" fill="currentColor">{g}°</text>')
    for c in cells:
        s = c["size_deg"] or 0.1
        x, y = x_of(c["lon"] - s / 2), y_of(c["lat"] + s / 2)
        w, h = max(6.0, x_of(c["lon"] + s / 2) - x), max(6.0, y_of(c["lat"] - s / 2) - y)
        title = f"{c['lat']:.1f}, {c['lon']:.1f}: {c['submitters']} submitter(s), {c['hours']:.1f} h, busy {c['busy_mean'] * 100:.1f} %" if c["busy_mean"] is not None else f"{c['lat']:.1f}, {c['lon']:.1f}"
        out.append(f'<a href="https://www.openstreetmap.org/#map=11/{c["lat"]}/{c["lon"]}" target="_blank" rel="noopener"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{busy_colour(c["busy_mean"])}" stroke="currentColor" stroke-width="1"><title>{html.escape(title)}</title></rect>'
                   + (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="url(#hatch)" stroke="none"/>' if c["submitters"] < 2 else "") + '</a>')
    out.append(f'<text x="{ml + pw / 2:.1f}" y="{mt + ph + 32}" font-size="11" text-anchor="middle" fill="currentColor">cells of 0.1°, colour = mean busy fraction; hatched = single submitter; click a cell for the map</text>')
    out.append("</svg>")
    return "".join(out)


_CSS = """
:root{--bg:#F3F5F7;--paper:#fff;--ink:#1B2430;--muted:#5B6B7A;--line:#D5DCE3;--accent:#0E7C7B}
@media (prefers-color-scheme:dark){:root{--bg:#0F151B;--paper:#161E26;--ink:#E4EAF0;--muted:#98A6B4;--line:#2A3540;--accent:#4FC1BE}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif;padding:1.5rem 1rem 4rem}
main{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:1.2rem}
h1{margin:0;font-size:1.5rem} h2{margin:1rem 0 .3rem;font-size:1.1rem} .meta{color:var(--muted);font-size:.9rem;display:flex;gap:1.2rem;flex-wrap:wrap}
.fig{background:var(--paper);border:1px solid var(--line);padding:.5rem} .fig svg{display:block;color:var(--ink)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.9rem} th,td{padding:.3rem .6rem;border-bottom:1px solid var(--line);text-align:right} th:first-child,td:first-child{text-align:left}
.note{color:var(--muted);font-size:.85rem} a{color:var(--accent)} tr.excluded td{color:var(--muted);text-decoration:line-through}
"""


# Basemap as a progressive enhancement: Leaflet (cdnjs) + OpenStreetMap tiles, drawing the cells from
# /v1/map.json; the inline SVG grid stays as the no-script / no-tiles view. Attribution per OSM policy.
DEFAULT_TILES = {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                 "attribution": "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap contributors</a>"}

_LEAFLET = """
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
(function(){
  var note=document.getElementById('basemap-note');
  if(typeof L==='undefined'){note.textContent='basemap unavailable: Leaflet did not load from cdnjs (offline or blocked); the grid above is the full data.';return;}
  fetch('/v1/map.json').then(function(r){return r.json();}).then(function(m){
    if(!m.cells.length){note.textContent='no located cells yet';return;}
    var el=document.getElementById('leaflet-map'); el.hidden=false;
    var map=L.map(el,{scrollWheelZoom:false});
    var tiles=L.tileLayer(__TILES_URL__,{maxZoom:18,attribution:__TILES_ATTR__}).addTo(map);
    // keep the SVG grid until a real tile has rendered AND Leaflet's stylesheet applied; otherwise the box would be blank
    var shown=false;
    tiles.on('tileload',function(){if(shown)return;if(getComputedStyle(el).overflow!=='hidden'){note.textContent='basemap: tiles loaded but the Leaflet stylesheet did not; keeping the grid.';el.hidden=true;shown=true;return;}
      shown=true;document.getElementById('cells-static').hidden=true;note.textContent='basemap: OpenStreetMap tiles via Leaflet 1.9.4; colour = mean busy fraction, dashed = single submitter; click a cell.';});
    tiles.on('tileerror',function(){if(!shown){note.textContent='basemap: OpenStreetMap tiles did not load (offline or blocked); the grid above is the full data.';el.hidden=true;shown=true;}});
    var stops=[[0,[255,255,204]],[0.25,[254,217,118]],[0.5,[253,141,60]],[0.75,[227,26,28]],[1,[128,0,38]]];
    function col(v){if(v==null)return '#C9CFD6';v=Math.max(0,Math.min(1,v));for(var i=1;i<stops.length;i++){if(v<=stops[i][0]){var t=(v-stops[i-1][0])/(stops[i][0]-stops[i-1][0]),a=stops[i-1][1],b=stops[i][1];return 'rgb('+Math.round(a[0]+(b[0]-a[0])*t)+','+Math.round(a[1]+(b[1]-a[1])*t)+','+Math.round(a[2]+(b[2]-a[2])*t)+')';}}return '#800026';}
    var bounds=[];
    m.cells.forEach(function(c){var s=c.size_deg||0.1,b=[[c.lat-s/2,c.lon-s/2],[c.lat+s/2,c.lon+s/2]];bounds.push(b[0],b[1]);
      var r=L.rectangle(b,{color:'#1B2430',weight:1,fillColor:col(c.busy_mean),fillOpacity:c.submitters<2?0.45:0.75,dashArray:c.submitters<2?'4 3':null}).addTo(map);
      r.bindPopup('<b>'+c.lat.toFixed(1)+', '+c.lon.toFixed(1)+'</b><br>'+c.submitters+' submitter(s), '+c.hours.toFixed(1)+' h, busy '+(c.busy_mean==null?'?':(c.busy_mean*100).toFixed(1)+' %')+'<br>quietest: '+c.quietest.slice(0,3).map(function(q){return q.mhz.toFixed(1)+' ('+(q.busy*100).toFixed(0)+' %)';}).join(', ')+'<br>busiest: '+c.busiest.slice(0,3).map(function(q){return q.mhz.toFixed(1)+' ('+(q.busy*100).toFixed(0)+' %)';}).join(', '));});
    map.fitBounds(bounds,{padding:[30,30],maxZoom:11});
  }).catch(function(e){note.textContent='basemap: could not load /v1/map.json ('+e+'); the grid above is the full data.';});
})();
</script>
"""


def render_map_page(m: dict, tiles: dict | None = None) -> str:
    import json as _json
    tiles = tiles or DEFAULT_TILES
    leaflet = _LEAFLET.replace("__TILES_URL__", _json.dumps(tiles["url"])).replace("__TILES_ATTR__", _json.dumps(tiles["attribution"]))
    e = html.escape
    head = f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>lorascan community map</title><style>{_CSS}</style></head><body><main>'
    parts = [head, "<h1>lorascan community map — 902–928 MHz</h1>",
             f'<div class="meta"><span>generated {e(m["generated"])}</span><span>submitters: {m["submitters"]}</span><span>cells: {len(m["cells"])}</span>'
             f'<span>without location: {m["no_location_submitters"]}</span><span>flagged (miscalibrated, not merged): {m["flagged_submitters"]}</span>'
             '<span><a href="/v1/map.json">map.json</a> · <a href="/v1/stats">stats</a> · <a href="https://github.com/Loomwave/lorascan">lorascan on GitHub</a></span></div>']
    if not m["band"]:
        parts.append('<p class="note">no uploads yet — run <code>lorascan share --db your.db --cell lat,lon --to https://share.lorascan.app</code> after a survey.</p>')
    else:
        parts.append('<h2>Where</h2><div class="note">each square is a 0.1° cell (about 10 km) that at least one scanner has shared; single-submitter cells are hatched. Uploads without a location count in the band summary but not on the map.</div>')
        parts.append('<div class="fig"><div id="cells-static">' + (cells_svg(m["cells"]) or '<p class="note">no cells with a location yet</p>') + '</div>'
                     '<div id="leaflet-map" style="height:480px" hidden></div><div id="basemap-note" class="note">basemap: loading OpenStreetMap tiles via Leaflet… (the grid above is the no-script view)</div></div>')
        parts.append('<h2>Band summary, all submitters</h2><div class="note">bar = median floor to maximum peak across submitters; label = who is known to live there; colour = mean busy fraction, auto-ranged to this band\'s spread.</div>')
        zones_hz = [tuple(z) for z in m.get("exclusions_hz", [])]
        parts.append('<div class="fig">' + band_svg(m["band"], exclusions=zones_hz, auto_range=True) + '</div>')
        if m.get("exclusions"):
            parts.append('<div class="note">Hatched = excluded from recommendations (band edges and the 33 cm amateur repeater segments: ' + ", ".join(f"{lo:.3f}–{hi:.3f} MHz" for lo, hi in m["exclusions"]) + '). Data is still collected there; those channels are listed last and struck through.</div>')
        parts.append('<h2>Best 500 kHz slot (coordination grid)</h2><div class="note">the fixed .250/.750 grid of 52 channels that MeshCore-500 meshes coordinate on; colour auto-ranged to the data\'s spread; struck = exclusion zone.</div>')
        gr = m.get("grid_recommend") or {}
        rc = gr.get("recommended")
        if rc:
            parts.append(f'<p><strong>Recommended: {rc["center_mhz"]:.2f} MHz ({rc["start_mhz"]:.2f}–{rc["end_mhz"]:.2f}) — busy {rc["busy_w"] * 100:.0f}%, floor {rc["floor_w"]:.0f} dBm, clear</strong></p>')
            parts.append('<div class="fig">' + grid_ribbon_svg(m.get("grid") or [], exclusions=zones_hz) + '</div>')
        else:
            parts.append('<p class="note">No submitter has scanned at 500 kHz yet — run a survey with <code>--bw …,500</code> and share it to populate this.</p>')
        def _tr(flag):                  # Python 3.11: no backslashes inside f-string expressions
            return '<tr class="excluded">' if flag else "<tr>"
        rows = "".join(f"{_tr(c.get('excluded'))}<td>{c['mhz']:.3f}{' (excluded)' if c.get('excluded') else ''}</td><td>{e(c['label'])}</td><td>{c['busy_mean'] * 100:.1f}</td><td>{c['floor_med']:.0f}</td><td>{c['peak_max']:.0f}</td><td>{c['n_submitters']}</td><td>{c['hours']:.1f}</td></tr>"
                       for c in sorted(m["band"], key=lambda c: (bool(c.get("excluded")), c["busy_mean"], c["floor_med"] if c["floor_med"] is not None else 0))[:15])
        parts.append('<h2>Quietest channels, fleet-wide</h2><table><thead><tr><th>MHz</th><th>who lives here</th><th>busy %</th><th>floor dBm</th><th>peak dBm</th><th>submitters</th><th>hours</th></tr></thead><tbody>' + rows + '</tbody></table>')
        if any(v is not None for r in m["when"] for v in r):
            parts.append('<h2>When is it busy</h2><div class="note">mean busy fraction by weekday and UTC hour, from hour-granularity uploads.</div><div class="fig">' + when_svg(m["when"]) + '</div>')
        if m["cells"]:
            def _top3(qs):              # Python 3.11: no nested f-string reusing the enclosing quote
                return e(", ".join("%.1f (%.0f %%)%s" % (q["mhz"], q["busy"] * 100, " excl." if q.get("excluded") else "") for q in qs[:3]))
            crows = "".join(f"<tr><td><a href=\"https://www.openstreetmap.org/#map=11/{c['lat']}/{c['lon']}\">{c['lat']:.1f}, {c['lon']:.1f}</a></td><td>{c['submitters']}</td><td>{c['hours']:.1f}</td><td>{(c['busy_mean'] or 0) * 100:.1f}</td>"
                            f"<td>{_top3(c['quietest'])}</td><td>{_top3(c['busiest'])}</td></tr>" for c in m["cells"])
            parts.append('<h2>Cells</h2><table><thead><tr><th>cell</th><th>submitters</th><th>hours</th><th>busy %</th><th>quietest MHz</th><th>busiest MHz</th></tr></thead><tbody>' + crows + '</tbody></table>')
        srows = "".join(f"<tr><td>{e(s['id'])}…{' (flagged)' if s['flagged'] else ''}</td><td>{e(str(s['board']))}</td><td>{e(str(s['tool']))}</td><td>{e(str(s['calibration']))}</td><td>{e(s['cell'])}</td><td>{s['uploads']}</td><td>{e(s['last_seen'])}</td></tr>" for s in m["submitter_rows"])
        parts.append('<h2>Submitters</h2><table><thead><tr><th>token</th><th>board</th><th>tool</th><th>levels</th><th>cell</th><th>uploads</th><th>last upload</th></tr></thead><tbody>' + srows + '</tbody></table>')
    if m["cells"]:
        parts.append(leaflet)
    parts.append('<p class="note">Levels are relative unless a submitter calibrated; busy = samples more than 8 dB above that channel\'s floor. Data are per-channel aggregates only: no payloads, no precise positions.</p></main></body></html>')
    return "".join(parts)
