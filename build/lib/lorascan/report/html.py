"""HTML report (spec §3.5): one self-contained page — heat map (time x frequency), band summary,
hour-by-weekday matrix when the run spans more than an hour, quietest channels table.
plotly.js is loaded from cdnjs at view time; all data is embedded as JSON."""
from __future__ import annotations
import datetime as dt
import html
import json
from ..plan.grid import label_for
from ..plan.candidate import rank_candidates

from .svg import heatmap_svg, band_svg, sfmap_svg, when_svg  # noqa: E402
from .slots import slot_view  # noqa: E402
import os  # noqa: E402

PLOTLY_URL = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js"


def quietest(channels: list[dict], n: int = 10) -> list[dict]:
    """Rank by busy fraction, then by the lowest P90, then by the lowest floor."""
    return sorted(channels, key=lambda c: (round(c["busy_mean"], 3), c["p90_med"], c["floor_med"]))[:n]


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def auto_bucket_s(span_s: float, max_cols: int = 600) -> int:
    """Smallest of 60 s, 2 min, 5 min, 10 min, 30 min, 1 h that keeps the heat map at <= max_cols columns."""
    for b in (60, 120, 300, 600, 1800, 3600):
        if span_s / b <= max_cols:
            return b
    return 3600


def build_data(store, run_id=None, bucket_s: int | None = 60, rssi_offset_db: float = 0.0, since: float | None = None) -> dict:
    if not bucket_s:
        rs = [r for r in store.runs() if (run_id is None or r["id"] == run_id) and r["first_ts"]]
        span = (max(r["last_ts"] for r in rs) - min(r["first_ts"] for r in rs)) if rs else 0.0
        bucket_s = auto_bucket_s(span)
    chans = store.channel_summary(run_id, since)
    for c in chans:
        c["label"] = label_for(c["freq_hz"])
        c["mhz"] = c["freq_hz"] / 1e6
    freqs = [c["freq_hz"] for c in chans]
    tb = store.time_buckets(bucket_s, run_id, since)
    buckets = sorted({b["bucket"] for b in tb})
    idx_f = {f: i for i, f in enumerate(freqs)}
    idx_b = {b: i for i, b in enumerate(buckets)}
    z_busy = [[None] * len(buckets) for _ in freqs]
    z_p90 = [[None] * len(buckets) for _ in freqs]
    for b in tb:
        if b["freq_hz"] in idx_f:
            z_busy[idx_f[b["freq_hz"]]][idx_b[b["bucket"]]] = round(b["busy_mean"], 4)
            z_p90[idx_f[b["freq_hz"]]][idx_b[b["bucket"]]] = round(b["p90_mean"] + rssi_offset_db, 1)
    # hour x weekday occupancy (all channels pooled), only meaningful when the span exceeds an hour
    span = (buckets[-1] - buckets[0]) if len(buckets) > 1 else 0.0
    when = [[None] * 24 for _ in range(7)]
    if span > 3600:
        acc: dict[tuple, list] = {}
        for b in tb:
            d = dt.datetime.fromtimestamp(b["bucket"], dt.timezone.utc)
            acc.setdefault((d.weekday(), d.hour), [0.0, 0]); acc[(d.weekday(), d.hour)][0] += b["busy_mean"]; acc[(d.weekday(), d.hour)][1] += 1
        for (wd, h), (s, n) in acc.items():
            when[wd][h] = round(s / n, 4)
    runs = store.runs()
    # LoRa presence (CAD) per frequency x SF, and decoded networks per frequency
    cads = store.cad_summary(run_id, since)
    sfs = sorted({c["sf"] for c in cads})
    cad_freqs = sorted({c["freq_hz"] for c in cads})
    z = [[None] * len(sfs) for _ in cad_freqs]
    for c in cads:
        i, j = cad_freqs.index(c["freq_hz"]), sfs.index(c["sf"])
        z[i][j] = round(max(c["hit_rate"], z[i][j] or 0.0), 4)      # best bandwidth per (freq, sf)
    decs = store.decode_summary(run_id, since)
    dec_by_f: dict[int, list] = {}
    for d in decs:
        if d["n_ok"]:
            dec_by_f.setdefault(d["freq_hz"], []).append(f"{d['network']}/{d['preset']} {d['n_ok']}")
    for c in chans:
        c["decoded"] = ", ".join(dec_by_f.get(c["freq_hz"], []))
        rates = [x["hit_rate"] for x in cads if x["freq_hz"] == c["freq_hz"]]
        c["cad_hit_rate"] = max(rates) if rates else None
    # candidate report card for 'test' runs
    card = []
    if any(r["kind"] == "test" for r in runs):
        for c in chans:
            cad_here = [x for x in cads if x["freq_hz"] == c["freq_hz"]]
            best = max(cad_here, key=lambda x: x["n_cad"]) if cad_here else None
            card.append({"freq_hz": c["freq_hz"], "mhz": c["mhz"], "sf": best["sf"] if best else 0, "bw_khz": (best["bw_hz"] if best else c["bw_hz"]) // 1000,
                         "busy_mean": c["busy_mean"], "cad_hit_rate": best["hit_rate"] if best else 0.0,
                         "decoded": sum(d["n_ok"] for d in decs if d["freq_hz"] == c["freq_hz"]), "floor_med": c["floor_med"] + rssi_offset_db,
                         "p90_med": c["p90_med"] + rssi_offset_db, "n_samples": c["n_samples"]})
        card = rank_candidates(card)
    fa = None
    ref = store.con.execute("SELECT detail FROM events WHERE kind = 'cad_reference'" + (" AND run_id = ?" if run_id is not None else "") + " ORDER BY ts DESC LIMIT 1",
                            ((run_id,) if run_id is not None else ())).fetchone()
    if ref:
        try:
            f_ref, sf_ref, bw_ref = ref[0].split()
            f_ref, sf_ref, bw_ref = int(f_ref), int(sf_ref[2:]), int(bw_ref[2:]) * 1000
            m = [c for c in cads if (c["freq_hz"], c["sf"], c["bw_hz"]) == (f_ref, sf_ref, bw_ref)]
            if m:
                fa = {"freq_hz": f_ref, "sf": sf_ref, "bw_hz": bw_ref, "rate": round(m[0]["hit_rate"], 4), "n_cad": m[0]["n_cad"]}
        except ValueError:
            fa = None
    return {
        "cad_false_alarm": fa,
        "sfmap": {"freqs_mhz": [f / 1e6 for f in cad_freqs], "sfs": sfs, "z": z},
        "decodes": decs, "card": card,
        "generated": _iso(dt.datetime.now(dt.timezone.utc).timestamp()),
        "runs": runs, "rssi_offset_db": rssi_offset_db,
        "calibration": "calibrated (offset %+.1f dB applied)" % rssi_offset_db if rssi_offset_db else "relative (uncalibrated)",
        "channels": [{**c, "floor_med": c["floor_med"] + rssi_offset_db, "p90_med": c["p90_med"] + rssi_offset_db, "peak_max": c["peak_max"] + rssi_offset_db} for c in chans],
        "heat": {"freqs_mhz": [f / 1e6 for f in freqs], "buckets": [_iso(b) for b in buckets], "busy": z_busy, "p90": z_p90, "bucket_s": bucket_s},
        "when": when, "span_s": span,
        "quietest": [{k: c[k] for k in ("freq_hz", "mhz", "label", "busy_mean", "floor_med", "p90_med", "peak_max", "n_rows")} for c in quietest(
            [{**c, "floor_med": c["floor_med"] + rssi_offset_db, "p90_med": c["p90_med"] + rssi_offset_db, "peak_max": c["peak_max"] + rssi_offset_db} for c in chans], 10)],
    }


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#F3F5F7;--paper:#fff;--ink:#1B2430;--muted:#5B6B7A;--line:#D5DCE3;--accent:#0E7C7B}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0F151B;--paper:#161E26;--ink:#E4EAF0;--muted:#98A6B4;--line:#2A3540;--accent:#4FC1BE}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif;padding:1.5rem 1rem 4rem}}
main{{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:1.2rem}}
h1{{margin:0;font-size:1.5rem}} h2{{margin:1rem 0 .3rem;font-size:1.1rem}} .meta{{color:var(--muted);font-size:.9rem;display:flex;gap:1.2rem;flex-wrap:wrap}}
.fig{{background:var(--paper);border:1px solid var(--line);padding:.5rem}} .static-wrap svg{{display:block;color:var(--ink)}} .plot{{min-height:320px}} #plotly-note{{color:var(--muted);font-size:.8rem}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.9rem}} th,td{{padding:.3rem .6rem;border-bottom:1px solid var(--line);text-align:right}} th:nth-child(2),td:nth-child(2){{text-align:left}}
.note{{color:var(--muted);font-size:.85rem}}
</style></head><body><main>
<h1>{title}</h1>
<div class="meta"><span>generated {generated}</span><span>{source}</span><span>runs: {runs}</span><span>levels: <b>{calibration}</b></span><span>bucket {bucket_s}s</span></div>
<h2>Occupancy heat map</h2><div class="note">colour = fraction of RSSI samples more than the busy threshold above the channel floor (busy fraction); toggle to P90 level with the buttons.</div>
<div class="fig"><div class="static-wrap" id="heat-static">{heat_svg}</div><div id="heat" class="plot" hidden></div></div>
<h2>Band summary</h2><div class="note">bar = floor (P10) to peak per channel; label = busy %.</div>
<div class="fig"><div class="static-wrap" id="band-static">{band_svg}</div><div id="band" class="plot" hidden></div></div>
<div id="when-wrap" {when_hidden}><h2>When is it busy</h2><div class="note">mean busy fraction across all channels by hour of day (UTC) and weekday; needs a run longer than an hour.</div><div class="fig"><div class="static-wrap" id="when-static">{when_svg}</div><div id="when" class="plot" hidden></div></div></div>
<div id="sf-wrap" {sf_hidden}><h2>LoRa presence by spreading factor</h2><div class="note">Channel Activity Detection hit rate per (frequency, SF): the LoRa-specific detector, blind across SFs by design. {fa_note}</div><div class="fig"><div class="static-wrap" id="sfmap-static">{sf_svg}</div><div id="sfmap" class="plot" hidden></div></div></div>
{card_html}
{slot_html}
<h2>Quietest channels</h2>
<table><thead><tr><th>MHz</th><th>who lives here</th><th>busy %</th><th>floor dBm</th><th>P90 dBm</th><th>peak dBm</th><th>rows</th><th>decoded</th></tr></thead><tbody>{quiet_rows}</tbody></table>
<div class="note">Levels are {calibration}. Busy threshold and floor definition: floor = P10 of the dwell's samples, busy = samples above floor + 8 dB (lorascan defaults).</div>
<script id="lorascan-data" type="application/json">{data_json}</script>
<div id="plotly-note">interactive charts: loading plotly.js from cdnjs… (the static charts above work offline)</div>
<script src="{plotly}"></script>
<script>
const NOTE=document.getElementById('plotly-note');
if(typeof Plotly==='undefined'){{NOTE.textContent='interactive charts unavailable: plotly.js did not load from '+{plotly_json}+' (offline or blocked CDN). The static charts above are complete.';}}else{{try{{
const D=JSON.parse(document.getElementById('lorascan-data').textContent);
const dark=matchMedia('(prefers-color-scheme: dark)').matches;
const lay=(t)=>({{margin:{{l:70,r:20,t:30,b:60}},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{{color:dark?'#E4EAF0':'#1B2430'}},title:t}});
const heatBusy={{type:'heatmap',x:D.heat.buckets,y:D.heat.freqs_mhz,z:D.heat.busy,colorscale:'YlOrRd',zmin:0,zmax:1,colorbar:{{title:'busy'}},hovertemplate:'%{{y}} MHz<br>%{{x}}<br>busy %{{z}}<extra></extra>'}};
const heatP90={{type:'heatmap',x:D.heat.buckets,y:D.heat.freqs_mhz,z:D.heat.p90,colorscale:'Viridis',colorbar:{{title:'P90 dBm'}},visible:false,hovertemplate:'%{{y}} MHz<br>%{{x}}<br>P90 %{{z}} dBm<extra></extra>'}};
document.getElementById('heat').hidden=false;Plotly.newPlot('heat',[heatBusy,heatP90],Object.assign(lay(''),{{yaxis:{{title:'MHz'}},xaxis:{{title:'time (UTC)'}},updatemenus:[{{type:'buttons',x:0,y:1.15,buttons:[{{label:'busy fraction',method:'update',args:[{{visible:[true,false]}}]}},{{label:'P90 level',method:'update',args:[{{visible:[false,true]}}]}}]}}]}}),{{responsive:true}});
const C=D.channels;
document.getElementById('band').hidden=false;Plotly.newPlot('band',[{{type:'bar',x:C.map(c=>c.mhz),y:C.map(c=>c.peak_max-c.floor_med),base:C.map(c=>c.floor_med),marker:{{color:C.map(c=>c.busy_mean),colorscale:'YlOrRd',cmin:0,cmax:1}},text:C.map(c=>(c.busy_mean*100).toFixed(1)+'%'+(c.label?' · '+c.label:'')),textposition:'outside',hovertemplate:'%{{x}} MHz<br>floor %{{base}} dBm → peak %{{y}}<extra></extra>',width:0.15}}],Object.assign(lay(''),{{yaxis:{{title:'dBm'}},xaxis:{{title:'MHz'}}}}),{{responsive:true}});
if(D.sfmap.sfs.length){{document.getElementById('sf-wrap').hidden=false;document.getElementById('sfmap').hidden=false;Plotly.newPlot('sfmap',[{{type:'heatmap',x:D.sfmap.sfs.map(s=>'SF'+s),y:D.sfmap.freqs_mhz,z:D.sfmap.z,colorscale:'YlOrRd',zmin:0,zmax:1,colorbar:{{title:'CAD hit rate'}}}}],Object.assign(lay(''),{{yaxis:{{title:'MHz'}}}}),{{responsive:true}});}}
if(D.span_s>3600){{document.getElementById('when-wrap').hidden=false;document.getElementById('when').hidden=false;Plotly.newPlot('when',[{{type:'heatmap',x:[...Array(24).keys()],y:['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],z:D.when,colorscale:'YlOrRd',zmin:0,zmax:1,colorbar:{{title:'busy'}}}}],Object.assign(lay(''),{{xaxis:{{title:'hour (UTC)'}}}}),{{responsive:true}});}}
for(const id of ['heat','band','when','sfmap']){{const s=document.getElementById(id+'-static');if(s&&!document.getElementById(id).hidden)s.hidden=true;}}
NOTE.textContent='interactive charts: plotly.js '+Plotly.version+' (hover for values; buttons toggle layers)';
}}catch(e){{NOTE.textContent='interactive charts failed: '+e+'. The static charts above are complete.';}}}}
</script></main></body></html>
"""


def build_data_from_share(doc: dict) -> dict:
    """The report's data dict from a share document alone (Loomwave/lorascan#3): heat map at the share's
    granularity, per-channel summary as medians over its buckets, SF map from cad rows, when-matrix from
    the document (day granularity) or nothing."""
    gran = doc.get("granularity", "hour")
    bucket_s = 3600 if gran == "hour" else 86400
    rows = doc.get("energy", [])
    freqs = sorted({r["freq_hz"] for r in rows})
    bkeys = sorted({r["bucket"] for r in rows})
    idx_f = {f: i for i, f in enumerate(freqs)}; idx_b = {b: i for i, b in enumerate(bkeys)}
    z_busy = [[None] * len(bkeys) for _ in freqs]; z_p90 = [[None] * len(bkeys) for _ in freqs]
    per_f: dict[int, dict] = {}
    for r in rows:
        z_busy[idx_f[r["freq_hz"]]][idx_b[r["bucket"]]] = r["busy_mean"]
        z_p90[idx_f[r["freq_hz"]]][idx_b[r["bucket"]]] = r["p90_med"]
        d = per_f.setdefault(r["freq_hz"], {"bw_hz": r["bw_hz"], "floors": [], "p90s": [], "busy": [], "peak": -999.0, "n_rows": 0, "n_samples": 0})
        d["floors"].append(r["floor_p10_med"]); d["p90s"].append(r["p90_med"]); d["busy"].append(r["busy_mean"])
        d["peak"] = max(d["peak"], r["peak_max"]); d["n_rows"] += r["n_rows"]; d["n_samples"] += r["n_samples"]
    def med(v):
        v = sorted(v); return v[len(v) // 2] if v else None
    chans = []
    for f in freqs:
        d = per_f[f]
        chans.append({"freq_hz": f, "mhz": f / 1e6, "bw_hz": d["bw_hz"], "label": label_for(f), "floor_med": med(d["floors"]), "p90_med": med(d["p90s"]),
                      "peak_max": d["peak"], "busy_mean": round(sum(d["busy"]) / len(d["busy"]), 4), "n_rows": d["n_rows"], "n_samples": d["n_samples"]})
    cads = doc.get("cad", [])
    sfs = sorted({c["sf"] for c in cads}); cad_freqs = sorted({c["freq_hz"] for c in cads})
    z = [[None] * len(sfs) for _ in cad_freqs]
    for c in cads:
        i, j = cad_freqs.index(c["freq_hz"]), sfs.index(c["sf"])
        z[i][j] = round(max(c["hit_rate"], z[i][j] or 0.0), 4)
    decs = doc.get("decode", [])
    for c in chans:
        c["decoded"] = ", ".join(f"{d['network']}/{d['preset']} {d['n_ok']}" for d in decs if d["freq_hz"] == c["freq_hz"] and d.get("n_ok"))
        rates = [x["hit_rate"] for x in cads if x["freq_hz"] == c["freq_hz"]]
        c["cad_hit_rate"] = max(rates) if rates else None
    when = doc.get("when") or [[None] * 24 for _ in range(7)]
    iso_b = [b if len(b) > 10 else b + "T00:00:00Z" for b in bkeys]
    iso_b = [b.replace("Z", ":00Z") if len(b) == 17 else b for b in iso_b]     # 2026-09-14T13:00Z -> 2026-09-14T13:00:00Z
    span = (len(bkeys) - 1) * bucket_s if bkeys else 0
    return {"cad_false_alarm": None, "sfmap": {"freqs_mhz": [f / 1e6 for f in cad_freqs], "sfs": sfs, "z": z}, "decodes": decs, "card": None,
            "generated": _iso(dt.datetime.now(dt.timezone.utc).timestamp()), "runs": [], "rssi_offset_db": 0.0,
            "calibration": doc.get("calibration", "relative (uncalibrated)"), "channels": chans,
            "heat": {"freqs_mhz": [f / 1e6 for f in freqs], "buckets": iso_b, "busy": z_busy, "p90": z_p90, "bucket_s": bucket_s},
            "when": when, "span_s": span, "quietest": [{k: c[k] for k in ("freq_hz", "mhz", "label", "busy_mean", "floor_med", "p90_med", "peak_max", "n_rows")} for c in quietest(chans, 10)],
            "source": f"from share document ({doc.get('tool', '?')}, {doc.get('board', '?')}, submitter {str(doc.get('submitter', ''))[:8]}…, granularity {gran}, cell {doc.get('cell')})"}


def write_svgs(d: dict, out_dir: str) -> list:
    """Standalone .svg files for each figure the data supports (Loomwave/lorascan#3)."""
    os.makedirs(out_dir, exist_ok=True)
    figs = {"heatmap.svg": heatmap_svg(d["heat"], "busy"), "band.svg": band_svg(d["channels"]), "sfmap.svg": sfmap_svg(d["sfmap"]), "when.svg": when_svg(d["when"])}
    paths = []
    for name, svg in figs.items():
        if not svg:
            continue
        p = os.path.join(out_dir, name)
        with open(p, "w") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n' + svg.replace("currentColor", "#1B2430"))
        paths.append(p)
    return paths


def render_from_data(d: dict, out_path: str, title: str = "lorascan report", slot_hz: int | None = None) -> str:
    bucket_s = d["heat"]["bucket_s"]
    dec_of = {c["freq_hz"]: c.get("decoded", "") for c in d["channels"]}
    rows = "".join(
        f"<tr><td>{c['mhz']:.3f}</td><td>{html.escape(c['label'])}</td><td>{c['busy_mean']*100:.1f}</td><td>{c['floor_med']:.0f}</td><td>{c['p90_med']:.0f}</td><td>{c['peak_max']:.0f}</td><td>{c['n_rows']}</td><td>{html.escape(dec_of.get(c['freq_hz'], ''))}</td></tr>"
        for c in d["quietest"])
    card_html = ""
    if d["card"]:
        card_rows = "".join(f"<tr><td>{c['rank']}</td><td>{c['mhz']:.3f} SF{c['sf']} BW{c['bw_khz']}</td><td>{c['score']:.3f}</td><td>{c['busy_mean']*100:.1f}</td><td>{c['cad_hit_rate']*100:.1f}</td><td>{c['decoded']}</td><td>{c['floor_med']:.0f}</td><td>{c['p90_med']:.0f}</td><td>{c['n_samples']}</td></tr>" for c in d["card"])
        card_html = ("<h2>Candidate report card</h2><div class=\"note\">score = busy fraction + CAD hit rate + decoded frames / 10 (lower is better); every number is from a passive dwell at exactly the candidate settings.</div>"
                     "<table><thead><tr><th>rank</th><th>candidate</th><th>score</th><th>busy %</th><th>CAD hit %</th><th>decoded</th><th>floor dBm</th><th>P90 dBm</th><th>samples</th></tr></thead><tbody>" + card_rows + "</tbody></table>")
    slot_html = ""
    if slot_hz:
        slots = slot_view(d["channels"], d.get("cad_rows", []), d.get("decodes", []), slot_hz)
        d["slots"] = slots
        srows = "".join(f"<tr><td>{i+1}</td><td>{w['start_mhz']:.3f}–{w['end_mhz']:.3f}</td><td>{w['n_channels']}</td><td>{w['score']:.3f}</td><td>{w['busy_max']*100:.1f}</td><td>{w['floor_worst']:.0f} (+{w['floor_penalty']:.2f})</td><td>{w['peak_max']:.0f}</td>"
                        f"<td>{w['cad_hit_max']*100:.1f}{(' SF%d' % w['cad_sf_max']) if w['cad_sf_max'] else ''}</td><td>{html.escape(w['decoded'])}</td><td>{html.escape(w['labels'])}</td></tr>" for i, w in enumerate(slots))
        slot_html = (f"<h2>{slot_hz/1000:g} kHz slots, best first</h2><div class=\"note\">worst case of the channels inside each window: score = busiest channel's busy fraction + highest CAD hit rate + decoded frames / 10 + (worst floor − best floor in the band) / 10 dB (lower is better; the floor term keeps a steady carrier from ranking clean).</div>"
                     "<table><thead><tr><th>rank</th><th>window MHz</th><th>ch</th><th>score</th><th>busy max %</th><th>floor worst dBm (penalty)</th><th>peak dBm</th><th>CAD hit max %</th><th>decoded</th><th>who lives here</th></tr></thead><tbody>" + srows + "</tbody></table>")
    runs = ", ".join(f"#{r['id']} {r['kind']} ({r['profile']}) {_iso(r['first_ts']) if r['first_ts'] else '-'} → {_iso(r['last_ts']) if r['last_ts'] else '-'}" for r in d["runs"]) or "none"
    fa = d.get("cad_false_alarm")
    fa_note = (f"Reference false-alarm rate {fa['rate']*100:.1f} % from {fa['n_cad']} CADs at SF{fa['sf']} on the quietest channel ({fa['freq_hz']/1e6:.3f} MHz): hit rates near that value are noise, not LoRa." if fa else "")
    page = _PAGE.format(title=html.escape(title), generated=d["generated"], runs=html.escape(runs), calibration=d["calibration"], fa_note=fa_note, source=html.escape(d.get("source", "from database")),
                        bucket_s=bucket_s, quiet_rows=rows, card_html=card_html, slot_html=slot_html, data_json=json.dumps(d).replace("</", "<\\/"), plotly=PLOTLY_URL, plotly_json=json.dumps(PLOTLY_URL),
                        heat_svg=heatmap_svg(d["heat"], "busy"), band_svg=band_svg(d["channels"]), sf_svg=sfmap_svg(d["sfmap"]), when_svg=when_svg(d["when"]),
                        when_hidden="" if d["span_s"] > 3600 else "hidden", sf_hidden="" if d["sfmap"]["sfs"] else "hidden")
    with open(out_path, "w") as f:
        f.write(page)
    return page


def render_report(store, out_path: str, title: str = "lorascan report", run_id=None, bucket_s: int | None = None, rssi_offset_db: float = 0.0, since: float | None = None, slot_hz: int | None = None) -> str:
    d = build_data(store, run_id, bucket_s, rssi_offset_db, since)
    d["cad_rows"] = store.cad_summary(run_id, since)
    return render_from_data(d, out_path, title, slot_hz)


def render_report_from_share(doc: dict, out_path: str, title: str = "lorascan report", slot_hz: int | None = None) -> str:
    d = build_data_from_share(doc)
    d["cad_rows"] = doc.get("cad", [])
    return render_from_data(d, out_path, title, slot_hz)
