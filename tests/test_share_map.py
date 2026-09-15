"""First front end for share.lorascan.app: GET / (inline-SVG map page) and GET /v1/map.json (fleet aggregates)."""
import json, threading, urllib.request
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
