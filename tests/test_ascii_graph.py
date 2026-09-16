from lorascan.report.ascii import band_graph

EXCL = [(902_000_000, 903_250_000), (926_750_000, 928_000_000)]

def test_bar_scales_between_floor_and_ceil():
    g = band_graph([(915_000_000, -125), (915_000_000, -20)], width=40, floor_dbm=-125, ceil_dbm=-20)
    lines = [l for l in g.splitlines() if "915.00" in l]
    assert lines[0].count("#") == 0            # at floor: empty bar
    assert lines[1].count("#") >= 14           # at ceil: full-ish bar (barcells=width-24=16 at width=40)

def test_two_row_shapes_accepted():
    a = band_graph([(915_000_000, -60)], width=40)
    b = band_graph([(915_000_000, -120, -60)], width=40)   # peak drives the bar
    assert a.splitlines()[-1].count("#") == b.splitlines()[-1].count("#")

def test_exclusion_zone_marked():
    g = band_graph([(902_500_000, -50), (915_000_000, -50)], width=40)
    lines = {l.split()[0]: l for l in g.splitlines() if "MHz" not in l and l.strip()}
    assert any("902.50" in l and "[X]" in l for l in g.splitlines())
    assert not any("915.00" in l and "[X]" in l for l in g.splitlines())

def test_width_clamped_and_deterministic():
    g1 = band_graph([(915_000_000, -50)], width=5)
    g2 = band_graph([(915_000_000, -50)], width=5)
    assert g1 == g2 and len(g1.splitlines()[-1]) <= 100
