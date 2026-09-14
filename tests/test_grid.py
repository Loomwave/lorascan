from lorascan.plan.grid import band_grid, KNOWN_CHANNELS, label_for

def test_default_grid_is_130_channels_at_200khz():
    g = band_grid()
    assert len(g) == 130 and g[0] == 902_000_000 and g[-1] == 927_800_000

def test_custom_grid():
    assert band_grid(905_000_000, 906_000_000, 250_000) == [905_000_000, 905_250_000, 905_500_000, 905_750_000]

def test_known_channels_are_the_kd4hme_fourteen():
    freqs = [f for f, _ in KNOWN_CHANNELS]
    assert freqs == [902_500_000, 903_000_000, 906_875_000, 908_000_000, 910_525_000, 911_500_000,
                     912_875_000, 913_125_000, 915_000_000, 917_000_000, 919_000_000, 921_000_000, 923_000_000, 925_000_000]
    assert label_for(911_500_000) == "Loomwave fleet" and label_for(906_875_000) == "Mesh LongFast"
    assert label_for(904_000_000) == ""
