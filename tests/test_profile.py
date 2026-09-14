import pytest
from lorascan.profile import parse_mini_yaml, load_profile, BoardProfile

NEBRA = """\
name: nebra-duo-hat            # bench Pi 5 HAT
bus: {type: spidev, dev: /dev/spidev0.0, hz: 2000000}
pins: {nss: kernel, reset: 22, busy: 23, dio1: 24, rxen: null, txen: null}
radio: {tcxo_v: 1.8, dio2_rf_switch: true, rx_boosted: true, max_tx_dbm: -9}
cal:   {rssi_offset_db: 0.0, scan_offset_dbm: -11}
"""

def test_parse_mini_yaml_nested_maps_and_scalars():
    d = parse_mini_yaml(NEBRA)
    assert d["name"] == "nebra-duo-hat"
    assert d["bus"] == {"type": "spidev", "dev": "/dev/spidev0.0", "hz": 2000000}
    assert d["pins"] == {"nss": "kernel", "reset": 22, "busy": 23, "dio1": 24, "rxen": None, "txen": None}
    assert d["radio"] == {"tcxo_v": 1.8, "dio2_rf_switch": True, "rx_boosted": True, "max_tx_dbm": -9}
    assert d["cal"] == {"rssi_offset_db": 0.0, "scan_offset_dbm": -11}

def test_load_profile_by_name_reads_shipped_nebra():
    p = load_profile("nebra-duo-hat")
    assert isinstance(p, BoardProfile)
    assert p.pins["busy"] == 23 and p.pins["nss"] == "kernel" and p.pins["rxen"] is None
    assert p.bus_type == "spidev" and p.bus_dev == "/dev/spidev0.0" and p.bus_hz == 2000000
    assert p.tcxo_v == 1.8 and p.dio2_rf_switch and p.rx_boosted and p.max_tx_dbm == -9
    assert p.rssi_offset_db == 0.0 and p.scan_offset_dbm == -11

def test_load_profile_unknown_names_search_dirs():
    with pytest.raises(FileNotFoundError) as e:
        load_profile("no-such-board")
    assert "profiles" in str(e.value)

def test_load_profile_by_path(tmp_path):
    f = tmp_path / "x.yaml"; f.write_text(NEBRA.replace("nebra-duo-hat", "x"))
    assert load_profile(str(f)).name == "x"
