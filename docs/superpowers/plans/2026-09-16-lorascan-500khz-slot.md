# lorascan 500 kHz Slot Recommendation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a prominent, auto-appearing "best 500 kHz slot" recommendation in the lorascan report, driven by the true 500 kHz-bandwidth energy the scan captures plus CAD/decode presence, ranking a free grid of 500 kHz windows across the band.

**Architecture:** a bandwidth-aware channel summary in the store (root fix), a pure ranking module, a report section + one-line terminal print, wired through `build_data`/`cmd_report`. Reuses the exclusion logic and the existing additive slot-scoring shape.

**Tech Stack:** Python 3.11, standard library only, pytest, the existing `store`/`report`/`exclusions` modules.

**Spec:** `docs/superpowers/specs/2026-09-16-lorascan-500khz-slot-design.md`.

## Global Constraints

- Target **Python 3.11** (Debian 12 / Pi OS): `python3.11 -m compileall` clean; no PEP 701 f-string forms (no backslash in a replacement field, no nested f-string reusing the enclosing quote). Keep `tests/test_py311_syntax.py` green.
- **Standard library only**; no new dependencies.
- **Receive-only**; analysis only.
- **Exclusion zones** from `exclusions.DEFAULT_EXCLUSIONS` (902.000–903.250 / 926.750–928.000 MHz); a window overlapping a zone is struck, never recommended, but still listed.
- **Backward compatible**: the existing `--slot N` view, candidate card, heat map, quietest-channels, and every current report section are unchanged; the new work is additive.
- Score is additive, **lower = better**, and includes a worst-floor term so a steady carrier cannot rank clean (reuse `slots.slot_view`'s shape).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA` (use your active attribution reminder's model line).

## File structure

- `lorascan/store/db.py` (modify) — add `channel_summary_by_bw`.
- `lorascan/report/slot_recommend.py` (new) — `recommend_slots` (pure).
- `lorascan/report/html.py` (modify) — populate `d["slot_recommend"]` in `build_data`/`build_data_from_share`; render the section in `render_from_data`.
- `lorascan/cli.py` (modify) — `--recommend-bw` option; print the one-line pick in `cmd_report`.
- `README.md` (modify) — note the auto recommendation.
- Tests: `tests/test_slot_recommend.py` (new), additions to `tests/test_store.py`, `tests/test_report.py`, `tests/test_cli.py`.

---

### Task 1: `channel_summary_by_bw` in the store

**Files:**
- Modify: `lorascan/store/db.py` (add a method next to `channel_summary`).
- Test: `tests/test_store.py` (add).

**Interfaces:**
- Produces: `Store.channel_summary_by_bw(run_id=None, since=None) -> list[dict]`, one row per `(freq_hz, bw_hz)`: `{freq_hz, bw_hz, floor_med, p90_med, peak_max, busy_mean, n_rows, n_samples}` — same fields as `channel_summary` but split by bandwidth. `channel_summary` is unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
from lorascan.store import Store
from lorascan.measure.energy import EnergyRow

def _row(freq, bw, floor, busy):
    return EnergyRow(ts=1.0, freq_hz=freq, bw_hz=bw, engine="poll", n=10, hist=[0]*10,
                     floor_dbm=floor, p50=floor+5, p90=floor+10, peak=floor+30, busy_frac=busy, discarded=0, dwell_s=1.0)

def test_channel_summary_by_bw_splits_bandwidths(tmp_path):
    st = Store(str(tmp_path / "s.db")); rid = st.new_run("survey", "fake", "")
    # same frequency measured at 62.5 kHz (busy 0.5) and 500 kHz (busy 0.02)
    st.add_energy(rid, _row(915_000_000, 62_500, -110.0, 0.5))
    st.add_energy(rid, _row(915_000_000, 500_000, -119.0, 0.02))
    rows = {(r["freq_hz"], r["bw_hz"]): r for r in st.channel_summary_by_bw()}
    assert (915_000_000, 62_500) in rows and (915_000_000, 500_000) in rows
    assert rows[(915_000_000, 500_000)]["busy_mean"] == 0.02
    assert rows[(915_000_000, 500_000)]["floor_med"] == -119.0
    assert rows[(915_000_000, 62_500)]["busy_mean"] == 0.5
    # channel_summary (blended) still returns one row for the freq
    assert len({c["freq_hz"] for c in st.channel_summary()}) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py::test_channel_summary_by_bw_splits_bandwidths -v`
Expected: FAIL (`channel_summary_by_bw` not defined).

- [ ] **Step 3: Implement** — add to `Store` in `lorascan/store/db.py`, mirroring `channel_summary` but keyed on `(freq_hz, bw_hz)`:

```python
    def channel_summary_by_bw(self, run_id: int | None = None, since: float | None = None) -> list[dict]:
        """Like channel_summary but one row per (freq_hz, bw_hz), so 500 kHz-bandwidth energy is not blended
        with the narrow measurements (the 500 kHz slot recommendation needs the width it will actually run)."""
        w, a = self._where(run_id, since)
        per: dict[tuple, dict] = {}
        for f, fl, p90, pk, bf, n, bw in self.con.execute(
                "SELECT freq_hz, floor_dbm, p90, peak, busy_frac, n, bw_hz FROM energy" + w, a):
            d = per.setdefault((f, bw), {"freq_hz": f, "bw_hz": bw, "floors": [], "p90s": [],
                                         "peak_max": -999.0, "busy": [], "n_rows": 0, "n_samples": 0})
            d["floors"].append(fl); d["p90s"].append(p90); d["peak_max"] = max(d["peak_max"], pk)
            d["busy"].append(bf); d["n_rows"] += 1; d["n_samples"] += n
        out = []
        for (f, bw) in sorted(per):
            d = per[(f, bw)]; fl = sorted(d["floors"]); p9 = sorted(d["p90s"])
            out.append({"freq_hz": f, "bw_hz": bw, "floor_med": fl[len(fl) // 2], "p90_med": p9[len(p9) // 2],
                        "peak_max": d["peak_max"], "busy_mean": sum(d["busy"]) / len(d["busy"]),
                        "n_rows": d["n_rows"], "n_samples": d["n_samples"]})
        return out
```

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_store.py -v` → PASS; `pytest tests/ -q` still green.

- [ ] **Step 5: Commit**

```bash
git add lorascan/store/db.py tests/test_store.py
git commit -F - <<'MSG'
feat(store): channel_summary_by_bw — per-(freq,bw) summary, unblended

The 500 kHz slot recommendation needs the energy at the width it will run, not
a blend of all measured bandwidths. channel_summary is left unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 2: `recommend_slots` — the pure ranking

**Files:**
- Create: `lorascan/report/slot_recommend.py`.
- Test: `tests/test_slot_recommend.py` (create).

**Interfaces:**
- Consumes: `channel_summary_by_bw` rows (Task 1); `cad_summary` rows `{freq_hz, sf, bw_hz, hit_rate, …}`; `decode_summary` rows `{freq_hz, network, preset, n_ok, …}`; `exclusions.overlaps`, `exclusions.DEFAULT_EXCLUSIONS`.
- Produces: `recommend_slots(by_bw, cad, decodes, width_hz=500_000, exclusions=None) -> dict` = `{"width_hz": int, "recommended": window|None, "windows": [window…]}`. A `window` dict: `{center_hz, center_mhz, start_hz, end_hz, start_mhz, end_mhz, busy_w, floor_w, peak_w, cad_hit_max, cad_sf_max, decoded, decoded_frames, carrier_pen, excluded, score, why}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slot_recommend.py
from lorascan.report.slot_recommend import recommend_slots

def _c(freq, bw, floor, busy):
    return {"freq_hz": freq, "bw_hz": bw, "floor_med": floor, "p90_med": floor+10,
            "peak_max": floor+30, "busy_mean": busy, "n_rows": 1, "n_samples": 10}

def test_quiet_500k_window_is_recommended():
    # two 500 kHz-measured centers; 910.0 quiet, 921.0 busy
    by_bw = [_c(910_000_000, 500_000, -119.0, 0.02), _c(921_000_000, 500_000, -119.0, 0.60),
             _c(910_000_000, 125_000, -119.0, 0.02), _c(921_000_000, 125_000, -119.0, 0.60)]
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    assert r["recommended"]["center_hz"] == 910_000_000
    assert r["windows"][0]["center_hz"] == 910_000_000  # quietest first
    assert r["recommended"]["busy_w"] == 0.02

def test_narrowband_carrier_in_window_penalises_it():
    # 908.0 is quiet at 500 kHz but a narrow carrier at 908.1 raises the floor inside the window
    by_bw = [_c(908_000_000, 500_000, -119.0, 0.02), _c(912_000_000, 500_000, -118.0, 0.03),
             _c(908_100_000, 62_500, -70.0, 0.02),   # loud carrier inside 908.0's window
             _c(912_000_000, 62_500, -119.0, 0.03)]
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    w908 = [w for w in r["windows"] if w["center_hz"] == 908_000_000][0]
    assert w908["carrier_pen"] > 0
    assert r["recommended"]["center_hz"] == 912_000_000  # the carrier pushed 908 down

def test_cad_and_decodes_in_window_raise_score():
    by_bw = [_c(909_000_000, 500_000, -119.0, 0.02), _c(913_000_000, 500_000, -119.0, 0.02)]
    cad = [{"freq_hz": 909_100_000, "sf": 9, "bw_hz": 125_000, "hit_rate": 0.4}]
    dec = [{"freq_hz": 909_050_000, "network": "meshcore", "preset": "us", "n_ok": 30}]
    r = recommend_slots(by_bw, cad, dec, width_hz=500_000)
    assert r["recommended"]["center_hz"] == 913_000_000  # 909 has CAD+decodes
    w909 = [w for w in r["windows"] if w["center_hz"] == 909_000_000][0]
    assert w909["cad_hit_max"] == 0.4 and w909["decoded_frames"] == 30

def test_excluded_window_never_recommended_but_listed():
    # 902.5 center → window 902.25–902.75 overlaps the 902.000–903.250 exclusion zone
    by_bw = [_c(902_500_000, 500_000, -119.0, 0.0), _c(915_000_000, 500_000, -110.0, 0.30)]
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    w902 = [w for w in r["windows"] if w["center_hz"] == 902_500_000][0]
    assert w902["excluded"] is True
    assert r["recommended"]["center_hz"] == 915_000_000  # excluded skipped even though quieter

def test_no_width_rows_returns_none():
    by_bw = [_c(915_000_000, 125_000, -110.0, 0.1)]  # no 500 kHz rows
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    assert r["recommended"] is None and r["windows"] == []
```

- [ ] **Step 2: Run test to verify it fails** — module missing.

- [ ] **Step 3: Implement**

```python
# lorascan/report/slot_recommend.py
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
    if carrier_pen > 0:
        loud = max(narrow_by_freq.items(), key=lambda kv: kv[1][1])
        parts.append(f"carrier near {loud[0] / 1e6:.2f} MHz raises the floor")
    else:
        parts.append("clear of exclusion zones")
    return ", ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_slot_recommend.py -v` → PASS. (If a scoring tie makes a "quietest first" assertion brittle, adjust the fixture numbers so the intended winner is unambiguously lowest — never loosen the behavioral assertion.)

- [ ] **Step 5: Commit**

```bash
git add lorascan/report/slot_recommend.py tests/test_slot_recommend.py
git commit -F - <<'MSG'
feat(report): recommend_slots — best width-W slot from true width-W energy

Free grid of width-W windows across the band, scored on the width-W busy/floor
plus CAD/decode presence and a narrowband-carrier term; exclusion-zone windows
struck. Pure, tested.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 3: wire into `build_data` and render the report section

**Files:**
- Modify: `lorascan/report/html.py` (`build_data`, `build_data_from_share`, `render_from_data`).
- Test: `tests/test_report.py` (add).

**Interfaces:**
- Consumes: `recommend_slots` (Task 2), `Store.channel_summary_by_bw` (Task 1), `store.cad_summary`, `store.decode_summary`.
- Produces: `d["slot_recommend"]` in the report data dict; a rendered "Recommended 500 kHz slot" section. `render_from_data`/`render_report` gain a `recommend_bw: int = 500_000` parameter (0 disables).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from lorascan.store import Store
from lorascan.measure.energy import EnergyRow
from lorascan.report.html import build_data, render_from_data

def _e(freq, bw, floor, busy):
    return EnergyRow(ts=1.0, freq_hz=freq, bw_hz=bw, engine="poll", n=10, hist=[0]*10, floor_dbm=floor,
                     p50=floor+5, p90=floor+10, peak=floor+30, busy_frac=busy, discarded=0, dwell_s=1.0)

def test_build_data_has_slot_recommend(tmp_path):
    st = Store(str(tmp_path/"s.db")); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(910_000_000, 500_000, -119.0, 0.02))
    st.add_energy(rid, _e(921_000_000, 500_000, -110.0, 0.50))
    d = build_data(st, bucket_s=0)
    assert d["slot_recommend"]["recommended"]["center_hz"] == 910_000_000

def test_render_shows_recommendation_section(tmp_path):
    st = Store(str(tmp_path/"s.db")); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(910_000_000, 500_000, -119.0, 0.02))
    d = build_data(st, bucket_s=0)
    html = open(render_from_data(d, str(tmp_path/"r.html"))).read()
    assert "Recommended 500 kHz slot" in html and "910.00" in html

def test_render_note_when_no_500k_data(tmp_path):
    st = Store(str(tmp_path/"s.db")); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(915_000_000, 125_000, -110.0, 0.1))
    d = build_data(st, bucket_s=0)
    html = open(render_from_data(d, str(tmp_path/"r.html"))).read()
    assert "--bw" in html  # the "re-scan with --bw ...,500" note
```

- [ ] **Step 2: Run test to verify it fails** — `d["slot_recommend"]` KeyError / section absent.

- [ ] **Step 3: Implement**

In `lorascan/report/html.py`, add the import near the `slot_view` import:

```python
from .slot_recommend import recommend_slots  # noqa: E402
```

In `build_data`, before `return d` (where `cads`/`decodes` are already computed — they are: `cads = store.cad_summary(...)` and the decode summary is fetched for the card), add:

```python
    by_bw = store.channel_summary_by_bw(run_id, since)
    decs = store.decode_summary(run_id, since)
    d["slot_recommend"] = recommend_slots(by_bw, cads, decs, 500_000, exclusions)
```

(If `build_data` already binds a decode-summary variable, reuse it instead of `decs`; if `cads` is named differently, use that name. Do not fetch a summary twice.)

In `build_data_from_share`, compute a by-bw list from the share energy rows (each carries `bw_hz`) with the same per-(freq,bw) aggregation, and set `d["slot_recommend"] = recommend_slots(by_bw, d.get("cad_rows", []), d.get("decodes", []), 500_000, exclusions)`; if no row has `bw_hz == 500_000`, `recommend_slots` already returns `recommended=None`.

In `render_from_data(d, out_path, title=..., slot_hz=None, recommend_bw: int = 500_000)`: build a section string and insert it near the other analysis sections (right after the candidate card / before the `slot_hz` block). Use plain f-strings (no nested same-quote, no backslash in a field):

```python
    rec_html = ""
    sr = d.get("slot_recommend")
    if recommend_bw and sr and sr.get("width_hz") == recommend_bw:
        wk = recommend_bw // 1000
        if sr["recommended"] is None and not sr["windows"]:
            rec_html = (f"<h2>Recommended {wk} kHz slot</h2><div class=\"note\">No {wk} kHz-bandwidth data in "
                        f"this database — re-scan including <code>--bw &hellip;,{wk}</code> to get a {wk} kHz slot "
                        f"recommendation.</div>")
        else:
            rec = sr["recommended"]
            head = (f"<p class=\"rec\"><b>Best {wk} kHz slot: {rec['center_mhz']:.2f} MHz</b> "
                    f"({rec['start_mhz']:.2f}&ndash;{rec['end_mhz']:.2f}) &mdash; {rec['why']}</p>") if rec else \
                   (f"<p class=\"rec\">Every {wk} kHz window overlaps an exclusion zone.</p>")
            rows = "".join(
                f"<tr class=\"{'excl' if w['excluded'] else ''}\"><td>{w['rank']}</td>"
                f"<td>{w['center_mhz']:.2f}</td><td>{w['start_mhz']:.2f}&ndash;{w['end_mhz']:.2f}</td>"
                f"<td>{w['score']:.3f}</td><td>{w['busy_w']*100:.1f}</td><td>{w['floor_w']:.0f}</td>"
                f"<td>{w['cad_hit_max']*100:.1f}</td><td>{w['decoded']}</td></tr>" for w in sr["windows"][:10])
            rec_html = (f"<h2>Recommended {wk} kHz slot</h2>{head}"
                        f"<div class=\"note\">score = busy fraction at {wk} kHz + CAD hit rate + decoded frames / 10 "
                        f"+ (worst in-window floor &minus; band-best floor) / 10 dB (lower is better); struck rows "
                        f"overlap an exclusion zone.</div>"
                        f"<table><tr><th>#</th><th>centre MHz</th><th>range</th><th>score</th><th>busy %</th>"
                        f"<th>floor</th><th>CAD %</th><th>decoded</th></tr>{rows}</table>")
```

Then insert `rec_html` into the page body where the other sections are assembled (e.g. immediately before the `card_html` or the `slot_html` insertion). Add a minimal CSS rule for `.rec` (bold highlight) and reuse the existing struck/excluded row style (the report already styles excluded channels — reuse that class name; if it is `.excl`, keep it).

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_report.py -v` → PASS; `pytest tests/ -q` green.

- [ ] **Step 5: Commit** (`feat(report): auto "Recommended 500 kHz slot" section in the report`).

---

### Task 4: terminal one-line pick + `--recommend-bw` flag

**Files:**
- Modify: `lorascan/cli.py` (`cmd_report`, the `report` subparser, and pass `recommend_bw` through `render_report`/`render_report_from_share`).
- Test: `tests/test_cli.py` (add).

**Interfaces:**
- Consumes: `d["slot_recommend"]` (Task 3), `render_report(..., recommend_bw=...)`.
- Produces: `report` subparser gains `--recommend-bw` (type int, default `500000`); `cmd_report` prints one line.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from lorascan import cli
from lorascan.store import Store
from lorascan.measure.energy import EnergyRow

def _e(freq, bw, floor, busy):
    return EnergyRow(ts=1.0, freq_hz=freq, bw_hz=bw, engine="poll", n=10, hist=[0]*10, floor_dbm=floor,
                     p50=floor+5, p90=floor+10, peak=floor+30, busy_frac=busy, discarded=0, dwell_s=1.0)

def test_report_prints_best_slot(tmp_path, capsys):
    db = str(tmp_path/"s.db"); st = Store(db); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(910_000_000, 500_000, -119.0, 0.02))
    assert cli.main(["report", "--db", db, "--out", str(tmp_path/"r.html")]) == 0
    out = capsys.readouterr().out
    assert "best 500 kHz slot" in out and "910.00" in out

def test_report_recommend_bw_zero_suppresses(tmp_path, capsys):
    db = str(tmp_path/"s.db"); st = Store(db); rid = st.new_run("survey", "fake", "")
    st.add_energy(rid, _e(910_000_000, 500_000, -119.0, 0.02))
    assert cli.main(["report", "--db", db, "--out", str(tmp_path/"r.html"), "--recommend-bw", "0"]) == 0
    assert "best 500 kHz slot" not in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails** — no print / unknown flag.

- [ ] **Step 3: Implement**

Add to the `report` subparser (near its other `add_argument` calls):

```python
    sp.add_argument("--recommend-bw", type=int, default=500_000, help="print/show the best slot at this bandwidth in Hz (0 disables)")
```

Thread `recommend_bw=a.recommend_bw` through `render_report(...)` → `render_from_data(...)` (add the parameter to both, defaulting `500_000`). In `cmd_report`, after `render_report(...)` returns and before/after the `[report] wrote` line, print the pick from the built data. Since `render_report` builds `d` internally, expose it: have `render_report` return the path (unchanged) but also compute the line inside `cmd_report` by calling `build_data` once for the print, OR (preferred, no double build) have `render_report` stash the recommendation — simplest: in `cmd_report`, when `a.recommend_bw`, call `build_data`/`build_data_from_share` to get `d["slot_recommend"]` and print:

```python
    if a.recommend_bw:
        d = build_data(store, a.run, a.bucket, prof_offset, since, exclusions=zones) if not a.from_share else build_data_from_share(doc, exclusions=zones)
        sr = d.get("slot_recommend") or {}
        if sr.get("width_hz") == a.recommend_bw and sr.get("recommended"):
            r = sr["recommended"]; wk = a.recommend_bw // 1000
            print(f"[report] best {wk} kHz slot: {r['center_mhz']:.2f} MHz ({r['start_mhz']:.2f}-{r['end_mhz']:.2f}) {r['why']}")
        elif sr.get("width_hz") == a.recommend_bw:
            wk = a.recommend_bw // 1000
            print(f"[report] no {wk} kHz-bandwidth data — re-scan with --bw ...,{wk} for a {wk} kHz slot pick", file=sys.stderr)
```

(Note: `build_data` is already imported in `cmd_report`. Building it a second time for the print is acceptable and keeps the change local; if you prefer, thread `recommend_bw` into `render_report` and return the recommendation — either is fine, but do not change other commands.)

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_cli.py -v` → PASS; `pytest tests/ -q` green; `python3 -m compileall lorascan/cli.py` clean.

- [ ] **Step 5: Commit** (`feat(cli): report prints the best 500 kHz slot; --recommend-bw`).

---

### Task 5: README note

**Files:**
- Modify: `README.md` (the "Picking a LoRa-clean 500 kHz slot" section).

- [ ] **Step 1** — under that section, add: the report now **automatically** prints and shows a "Recommended 500 kHz slot" (the best-placed 500 kHz window from the true 500 kHz-bandwidth energy, with CAD/decode presence and exclusion zones accounted for) whenever the database has 500 kHz-bandwidth data; `--recommend-bw <hz>` selects the width (default 500000, `0` disables). Keep the existing `--slot` example. One example line:

```
lorascan report --db slot.db --out slot.html   # prints: [report] best 500 kHz slot: 903.30 MHz (903.05-903.55) ...
```

- [ ] **Step 2: Verify** `grep -n "Recommended 500 kHz\|--recommend-bw" README.md` shows the addition; no other section changed.

- [ ] **Step 3: Commit** (`docs(readme): note the automatic 500 kHz slot recommendation`).

---

## After all tasks

- `pytest tests/ -q` green; bench `python3.11 -m compileall lorascan` clean (release2.sh gate); `tests/test_py311_syntax.py` green.
- Bump `pyproject.toml` + `lorascan/__init__.py` to 0.1.21, add a CHANGELOG entry, release (merge to main, tag v0.1.21, wheel + GitHub release), and sync the loomwave `lorascan/` subtree (eng/lorascan-p1) as for 0.1.20.
- PR body carries the 🤖 footer + session URL.
