"""First front end for share.lorascan.app: GET / (inline-SVG map page) and GET /v1/map.json (fleet aggregates)."""
import json, re, threading, urllib.request
import pytest
from lorascan.share_server import make_share_server
from lorascan.share import build_share, upload_share
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow


def _store(tmp_path, name, hours=26, busy=0.1, floor=-110.0):
    s = Store(str(tmp_path / name)); rid = s.new_run("survey", "fake", "")
    t0 = 1_757_800_000.0
    for h in range(hours):
        for i, f in enumerate((902_000_000, 911_500_000, 921_000_000)):
            s.add_energy(rid, EnergyRow(ts=t0 + h * 3600, freq_hz=f, bw_hz=125_000, engine="poll", n=100, floor_dbm=floor + i, p50=-105, p90=-100, peak=-80, busy_frac=busy * (i + 1)))
    return s


@pytest.fixture
def fleet(tmp_path):
    srv = make_share_server("127.0.0.1", 0, str(tmp_path / "share.sqlite"))
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    url = f"http://127.0.0.1:{srv.server_port}"
    upload_share(build_share(_store(tmp_path, "a.db"), (34.1, -84.4), "aaaaaaaaaaaaaaaa", "nebra-duo-hat", granularity="hour"), url, retries=1, backoff_s=0)
    upload_share(build_share(_store(tmp_path, "b.db", busy=0.2), (34.1, -84.4), "bbbbbbbbbbbbbbbb", "meshtoad-v3-ch341", granularity="day"), url, retries=1, backoff_s=0)
    upload_share(build_share(_store(tmp_path, "c.db", busy=0.05), None, "cccccccccccccccc", "generic-spidev", granularity="day"), url, retries=1, backoff_s=0)
    bad = build_share(_store(tmp_path, "d.db", floor=-50.0), (40.0, -74.0), "dddddddddddddddd", "generic-spidev", granularity="day")   # miscalibrated -> flagged
    upload_share(bad, url, retries=1, backoff_s=0)
    yield url
    srv.shutdown()


def _get(url, path):
    with urllib.request.urlopen(url + path, timeout=10) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode()


def test_map_json_aggregates_cells_band_and_excludes_flagged(fleet):
    st, ct, body = _get(fleet, "/v1/map.json")
    m = json.loads(body)
    assert st == 200 and "json" in ct
    assert len(m["cells"]) == 1 and m["cells"][0]["lat"] == 34.1 and m["cells"][0]["lon"] == -84.4 and m["cells"][0]["submitters"] == 2
    assert m["cells"][0]["quietest"][0]["mhz"] == 911.5 and m["cells"][0]["busiest"][0]["mhz"] == 921.0     # 902.0 quieter but excluded (#9)
    assert [c["mhz"] for c in m["band"]] == [902.0, 911.5, 921.0] and m["band"][0]["n_submitters"] == 3      # a, b, c (d is flagged)
    assert m["no_location_submitters"] == 1 and m["flagged_submitters"] == 1 and m["submitters"] == 4
    assert len(m["when"]) == 7 and any(v is not None for r in m["when"] for v in r)                          # from the hour-granularity rows


def test_root_page_is_selfcontained_html_with_the_map(fleet):
    st, ct, html = _get(fleet, "/")
    assert st == 200 and "text/html" in ct
    assert "<svg" in html and "34.1" in html and "-84.4" in html and "openstreetmap.org" in html
    assert "single submitter" in html and "flagged" in html
    # basemap = progressive enhancement: Leaflet from cdnjs + OSM tiles with attribution, the inline SVG stays as the fallback
    assert "cdnjs.cloudflare.com/ajax/libs/leaflet/" in html and "tile.openstreetmap.org" in html and "OpenStreetMap contributors" in html
    assert 'id="leaflet-map"' in html and "typeof L" in html and html.index("<svg") < html.index("<script")
    assert "tileload" in html                                     # the SVG grid is hidden only once a real tile has rendered
    assert "911.5" in html and "Loomwave fleet" in html and "nebra-duo-hat" in html


def test_root_page_with_no_data(tmp_path):
    srv = make_share_server("127.0.0.1", 0, str(tmp_path / "empty.sqlite"))
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    st, ct, html = _get(f"http://127.0.0.1:{srv.server_port}", "/")
    srv.shutdown()
    assert st == 200 and "no uploads yet" in html


def test_tile_source_is_configurable_with_osm_default(tmp_path, monkeypatch):
    from lorascan.share_page import render_map_page, map_data
    from lorascan.share_server import ShareDB, tiles_config
    db = ShareDB(str(tmp_path / "t.sqlite"))
    m = map_data(db); m["cells"] = [{"lat": 34.1, "lon": -84.4, "size_deg": 0.1, "submitters": 1, "hours": 1.0, "busy_mean": 0.1, "quietest": [], "busiest": []}]; m["band"] = [{"freq_hz": 902_000_000, "mhz": 902.0, "label": "", "floor_med": -110, "p90_med": -100, "peak_max": -80, "busy_mean": 0.1, "n_submitters": 1, "hours": 1}]
    html = render_map_page(m)                                               # default
    assert "tile.openstreetmap.org" in html and "OpenStreetMap contributors" in html
    carto = {"url": "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?api_key=KEY", "attribution": "&copy; CARTO, &copy; OpenStreetMap contributors"}
    html = render_map_page(m, tiles=carto)
    assert "cartocdn.com" in html and "api_key=KEY" in html and "CARTO" in html and "tile.openstreetmap.org" not in html
    monkeypatch.setenv("LORASCAN_TILES_URL", "https://example.test/{z}/{x}/{y}.png"); monkeypatch.setenv("LORASCAN_TILES_ATTRIBUTION", "test attr")
    assert tiles_config(None, None) == {"url": "https://example.test/{z}/{x}/{y}.png", "attribution": "test attr"}
    assert tiles_config("https://flag.test/{z}/{x}/{y}.png", "flag attr")["url"] == "https://flag.test/{z}/{x}/{y}.png"   # flags beat env


def test_map_page_shades_exclusions_and_ranks_viable_channels_first(fleet):
    """Loomwave/lorascan#9 + Matt: highlight the excluded zones on the web map too."""
    st, ct, html = _get(fleet, "/")
    assert 'class="excl"' in html and "not an option" in html and "903.250" in html and "926.750" in html
    st, ct, body = _get(fleet, "/v1/map.json")
    m = json.loads(body)
    assert m["exclusions"] == [[902.0, 903.25], [926.75, 928.0]]
    assert m["band"][0]["mhz"] == 902.0 and m["band"][0]["excluded"] is True and m["band"][1]["excluded"] is False
    # fleet-wide quietest table: 902.0 is the quietest channel in the fixture but excluded, so 911.5 leads
    q = html[html.index("Quietest channels, fleet-wide"):]
    assert q.index("911.500") < q.index("902.000") and "(excluded)" in q
    assert m["cells"][0]["quietest"][0]["mhz"] == 911.5 and m["cells"][0]["quietest"][-1]["excluded"] is True


def _ingest_500khz_row(db, submitter="a" * 16, freq_hz=911_750_000, busy=0.03, floor=-119.0):
    """Directly ingest one 500 kHz energy row (bypasses the HTTP/gzip roundtrip; ShareDB.ingest is the
    same code path the share endpoint uses)."""
    doc = {"format": "lorascan-share/2", "generated": "2026-09-16T00:00:00Z", "submitter": submitter,
           "tool": "lorascan test", "board": "nebra-duo-hat", "granularity": "hour",
           "calibration": "relative (uncalibrated)", "cell": None,
           "energy": [{"freq_hz": freq_hz, "bw_hz": 500_000, "bucket": "2026-09-16T00:00Z", "bucket_s": 3600,
                       "n_rows": 1, "n_samples": 100, "hours": 1.0, "floor_p10_med": floor,
                       "p90_med": floor + 10.0, "peak_max": -80.0, "busy_mean": busy}],
           "cad": [], "decode": []}
    db.ingest(doc, 0)


def test_map_page_shows_best_500khz_slot_grid_ribbon_when_data_present(tmp_path):
    from lorascan.share_page import render_map_page, map_data
    from lorascan.share_server import ShareDB
    db = ShareDB(str(tmp_path / "grid.sqlite"))
    _ingest_500khz_row(db)
    m = map_data(db)
    html = render_map_page(m)
    assert "Best 500 kHz slot" in html
    assert re.search(r"9\d\d\.(25|75)", html), "expected a grid centre ending .25 or .75 in the page"
    assert "911.75" in html
    assert "<script" not in html          # no located cells in this fixture -> no Leaflet enhancement either; page is pure static SVG
    # the recommendation line uses the recommendation's own "why" (busy/floor/LoRa/clearance), not a hardcoded "clear"
    assert "no known LoRa" in html and "clear of exclusion zones" in html
    assert "scanned at 500 kHz yet" not in html


def test_map_page_shows_no_500khz_data_note_when_absent(tmp_path):
    from lorascan.share_page import render_map_page, map_data
    from lorascan.share_server import ShareDB
    db = ShareDB(str(tmp_path / "nogrid.sqlite"))
    m = map_data(db)
    m["band"] = [{"freq_hz": 902_000_000, "mhz": 902.0, "label": "", "floor_med": -110, "p90_med": -100, "peak_max": -80, "busy_mean": 0.1, "n_submitters": 1, "hours": 1, "excluded": False}]
    html = render_map_page(m)
    assert "Best 500 kHz slot" in html
    assert "scanned at 500 kHz yet" in html


def test_map_page_shows_ribbon_and_widen_note_when_all_500khz_data_is_excluded(tmp_path):
    """Loomwave/lorascan review round 1 (blocking): a submitter who only scanned 902.25 MHz (inside the
    default 902.0-903.25 exclusion zone) has REAL 500 kHz data, but recommend_grid_slots' `recommended`
    is None because every measured window is excluded. That must not be conflated with "no data"."""
    from lorascan.share_page import render_map_page, map_data
    from lorascan.share_server import ShareDB
    db = ShareDB(str(tmp_path / "excl_only.sqlite"))
    _ingest_500khz_row(db, freq_hz=902_250_000, busy=0.05, floor=-115.0)   # 902.0-902.5 MHz window, inside 902.0-903.25 exclusion
    m = map_data(db)
    html = render_map_page(m)
    assert "<svg" in html and "902.25" in html                             # the ribbon still renders with the real data
    assert "Every measured 500 kHz channel falls inside an exclusion zone" in html
    assert "scanned at 500 kHz yet" not in html                            # must NOT show the no-data note
