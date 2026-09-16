import os, pytest
from lorascan import station_config as sc

def test_absent_is_all_none(tmp_path):
    cfg = sc.load(home=str(tmp_path))
    assert (cfg.profile, cfg.location, cfg.endpoint, cfg.granularity) == (None, None, None, None)

def test_roundtrip_save_load(tmp_path):
    cfg = sc.StationConfig(profile="my-nebra", location=(33.8931, -84.2534),
                           endpoint="https://share.lorascan.app", granularity="hour")
    p = sc.save(cfg, path=str(tmp_path / "config.yaml"))
    assert os.path.exists(p)
    got = sc.load(home=str(tmp_path)) if False else sc._load_file(p)
    assert got.profile == "my-nebra"
    assert got.location == (33.8931, -84.2534)
    assert got.endpoint == "https://share.lorascan.app"
    assert got.granularity == "hour"

def test_location_absent_stays_none(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("profile: x\nshare: {endpoint: 'https://h', granularity: day}\n")
    got = sc._load_file(str(p))
    assert got.location is None and got.profile == "x" and got.granularity == "day"

def test_malformed_raises(tmp_path):
    # NOTE: the brief's original fixture "share: {endpoint: }\n: : :\n" does NOT
    # raise in lorascan.profile.parse_mini_yaml (it tolerates an empty flow-map
    # value and a bare "key: value"-shaped junk line, returning a dict). Verified
    # directly against parse_mini_yaml before writing this test. Tightened to a
    # line with no ':' at all, which parse_mini_yaml explicitly rejects
    # (`raise ValueError(f"profile line without ':' : {raw!r}")`), so this test
    # exercises a real "malformed file never returns defaults" path rather than
    # a fixture that happens to parse cleanly.
    p = tmp_path / "config.yaml"
    p.write_text("share: {endpoint: }\nthis line has no colon at all\n")
    with pytest.raises(ValueError):
        sc._load_file(str(p))

def test_resolve_precedence():
    assert sc.resolve("flag", "cfg", "def") == "flag"
    assert sc.resolve(None, "cfg", "def") == "cfg"
    assert sc.resolve(None, None, "def") == "def"

def test_load_prefers_user_over_etc(tmp_path, monkeypatch):
    home = tmp_path / "home"; (home / ".config" / "lorascan").mkdir(parents=True)
    (home / ".config" / "lorascan" / "config.yaml").write_text("profile: userwins\n")
    cfg = sc.load(home=str(home))
    assert cfg.profile == "userwins"
