"""Loomwave/lorascan#7: auto-configure from a running meshtasticd / openHOP daemon (read, hold, scan, restore, share)."""
import os, textwrap
import pytest
from lorascan.yamlmini import parse_yaml
from lorascan import autoconf as A
from lorascan.profile import BoardProfile

MT_MAIN = """\
General:
  APIPort: 4403
  ConfigDirectory: /etc/meshtasticd/config.d/
Lora:
  Module: auto        # the real radio is in config.d
"""
MT_SPI = """\
Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  SX126X_MAX_POWER: 18
  spidev: spidev0.1
  IRQ: 27
  Busy: 17
  Reset: 18
"""
MT_USB = """\
Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  SX126X_MAX_POWER: 22
  spidev: ch341
  USB_VID: 0x1A86
  USB_PID: 0x5512
  CS: 0
  IRQ: 6
  Reset: 2
  Busy: 4
  RXen: 1
"""
OH = """\
node:
  name: fort2
ch341:
  pid: 21778        # hex 0x5512
  vid: 6790         # hex 0x1A86
gps:
  enabled: false
  source_path: /dev/ttyACM0
  location:
    lat: 34.1234
    lon: -84.5678
duty_cycle:
  limit: 0.1
"""


def test_yamlmini_parses_nested_blocks_comments_and_scalars():
    d = parse_yaml(MT_SPI)
    assert d["Lora"]["Module"] == "sx1262" and d["Lora"]["IRQ"] == 27 and d["Lora"]["DIO2_AS_RF_SWITCH"] is True and d["Lora"]["spidev"] == "spidev0.1"
    o = parse_yaml(OH)
    assert o["ch341"] == {"pid": 21778, "vid": 6790} and o["gps"]["location"]["lat"] == 34.1234 and o["gps"]["enabled"] is False
    assert parse_yaml(MT_USB)["Lora"]["USB_VID"] == 0x1A86


def _mt_tree(tmp_path, boards):
    root = tmp_path / "meshtasticd"; (root / "config.d").mkdir(parents=True); (root / "available.d").mkdir()
    (root / "config.yaml").write_text(MT_MAIN)
    for name, text in boards.items():
        (root / "config.d" / name).write_text(text)
    (root / "available.d" / "lora-NebraHat_2W.yaml").write_text(MT_SPI)     # available but not active
    return str(root)


def test_meshtasticd_spi_board_resolves_to_a_spidev_profile(tmp_path):
    root = _mt_tree(tmp_path, {"nebrad1.yaml": MT_SPI})
    r = A.resolve_meshtasticd(root=root)
    p = r.profile
    assert isinstance(p, BoardProfile) and p.bus_type == "spidev" and p.bus_dev == "/dev/spidev0.1"
    assert p.pins["dio1"] == 27 and p.pins["busy"] == 17 and p.pins["reset"] == 18 and p.pins["nss"] == "kernel"
    assert p.dio2_rf_switch is True and p.tcxo_v == 1.8 and p.max_tx_dbm == -9          # receive-only: never the daemon's TX power
    assert r.service == "meshtasticd.service" and r.source.endswith("config.d/nebrad1.yaml") and r.hint_max_power == 18


def test_meshtasticd_ch341_board_resolves_to_the_meshtoad_pin_block(tmp_path):
    root = _mt_tree(tmp_path, {})
    cfg = tmp_path / "meshtasticd" / "config-usb.yaml"; cfg.write_text(MT_USB)
    def run(argv):
        if argv[:2] == ["systemctl", "list-units"]:
            return A.Ran(0, "meshtasticd.service loaded active running Meshtastic\nmeshtasticd-usb.service loaded active running Meshtastic USB\n", "")
        if argv[:2] == ["systemctl", "show"]:
            cfgs = {"meshtasticd.service": "/etc/meshtasticd/config.yaml", "meshtasticd-usb.service": "/etc/meshtasticd/config-usb.yaml"}
            return A.Ran(0, f"ExecStart={{ path=/usr/bin/meshtasticd ; argv[]=/usr/bin/meshtasticd --config {cfgs[argv[2]]} }}\nId={argv[2]}\n", "")
        return A.Ran(0, "", "")
    r = A.resolve_meshtasticd(root=root, config=str(cfg), run=run)
    p = r.profile
    assert p.bus_type == "ch341" and p.pins == {"nss": 0, "reset": 2, "busy": 4, "dio1": 6, "rxen": 1, "txen": None}
    assert r.usb == (0x1A86, 0x5512) and r.service == "meshtasticd-usb.service"


def test_meshtasticd_multiple_active_boards_needs_config(tmp_path):
    root = _mt_tree(tmp_path, {"a.yaml": MT_SPI, "b.yaml": MT_USB})
    with pytest.raises(A.AutoConfError) as e:
        A.resolve_meshtasticd(root=root)
    assert "a.yaml" in str(e.value) and "b.yaml" in str(e.value) and "--config" in str(e.value)


def test_openhop_resolves_ch341_and_location(tmp_path):
    cfg = tmp_path / "config.yaml"; cfg.write_text(OH)
    r = A.resolve_openhop(config=str(cfg))
    assert r.profile.bus_type == "ch341" and r.usb == (0x1A86, 0x5512) and r.service == "openhop-repeater.service"
    assert r.location == (34.1234, -84.5678, "openhop config gps.location")


# --- SPI SX1262 openHOP radios (#7 follow-up): a radio_type:sx1262 node must resolve to a
# spidev profile, even when a vestigial `ch341:` block remains from a former USB config. ---
OH_SPI = """\
ch341:                            # vestigial — the node moved to SPI, keep but IGNORE
  pid: 21778
  vid: 6790
radio:
  bandwidth: 62500
  coding_rate: 5
  frequency: 910525000.0
  spreading_factor: 7
  preamble_length: 32
radio_type: sx1262
sx1262:
  bus_id: 0
  busy_pin: 23
  cs_id: 0
  cs_pin: -1
  dio3_tcxo_voltage: 1.8
  irq_pin: 24
  reset_pin: 22
  rxen_pin: -1
  txen_pin: -1
  use_dio2_rf: true
repeater:
  latitude: 33.75008167
  longitude: -84.39989167
"""

OH_SPI_UNSUPPORTED_TYPE = """\
radio_type: kiss
"""


def test_openhop_radio_type_sx1262_resolves_to_spidev(tmp_path):
    cfg = tmp_path / "config.yaml"; cfg.write_text(OH_SPI)
    r = A.resolve_openhop(config=str(cfg))
    p = r.profile
    assert p.bus_type == "spidev" and p.bus_dev == "/dev/spidev0.0"     # bus_id 0, cs_id 0
    assert p.pins == {"nss": "kernel", "reset": 22, "busy": 23, "dio1": 24, "rxen": None, "txen": None}
    assert p.tcxo_v == 1.8 and p.dio2_rf_switch is True and p.max_tx_dbm == -9   # receive-only
    assert r.usb is None and r.device == "/dev/spidev0.0" and r.service == "openhop-repeater.service"
    # the vestigial ch341 block must NOT make it a USB radio / a fuser "usb" device
    assert r.profile.bus_type == "spidev"
    # repeater.{latitude,longitude} is used as the config-location fallback
    assert r.location == (33.75008167, -84.39989167, "openhop repeater latitude/longitude")
    # site-channel context surfaced as a note
    assert any("910.525 MHz" in n for n in r.notes)


def test_openhop_unsupported_radio_type_fails_loudly(tmp_path):
    cfg = tmp_path / "config.yaml"; cfg.write_text(OH_SPI_UNSUPPORTED_TYPE)
    with pytest.raises(A.AutoConfError) as e:
        A.resolve_openhop(config=str(cfg))
    assert "kiss" in str(e.value)


def test_yamlmini_indentless_sequences_and_block_scalars():
    # indentless sequence ('- key: value' at the SAME indent as its parent key) + block scalar
    s = """\
mqtt_brokers:
  brokers:
  - name: MeshMapper
    host: mqtt.meshmapper.net
    tls:
      enabled: true
  - name: LetsMesh
    host: mqtt-us-v1.letsmesh.net
repeater:
  identity_key: !!binary |
    CiOtvx9IKfJXXxMH+hLdo8JSn2OIbrsKZt5LOV3+66g=
  rules: []
"""
    d = parse_yaml(s)
    br = d["mqtt_brokers"]["brokers"]
    assert len(br) == 2 and br[0]["name"] == "MeshMapper" and br[0]["tls"]["enabled"] is True and br[1]["host"] == "mqtt-us-v1.letsmesh.net"
    assert "identity_key" in d["repeater"] and "CiOtvx9I" in d["repeater"]["identity_key"]
    assert d["repeater"]["rules"] == []


class Runner:
    """Fake systemctl/fuser: records calls; scripted answers."""
    def __init__(self, holders_after_stop=""):
        self.calls = []; self.holders = holders_after_stop; self.active = True
    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["systemctl", "stop"]:
            self.active = False; return A.Ran(0, "", "")
        if argv[:2] == ["systemctl", "start"]:
            self.active = True; return A.Ran(0, "", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return A.Ran(0 if self.active else 3, "active\n" if self.active else "inactive\n", "")
        if argv[0] == "fuser":
            return A.Ran(0 if self.holders else 1, self.holders, "")
        return A.Ran(0, "", "")


def test_hold_scan_restore_order_and_restore_on_failure(tmp_path):
    run = Runner()
    log = []
    def scan():
        log.append("scan"); raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        A.with_radio_held("meshtasticd.service", "/dev/spidev0.1", scan, run=run, settle_s=0)
    kinds = [c[:2] if c[0] == "systemctl" else c[:1] for c in run.calls]
    assert kinds[0] == ["systemctl", "stop"] and ["fuser"] in kinds and kinds[-2] == ["systemctl", "start"] and kinds[-1] == ["systemctl", "is-active"]
    assert log == ["scan"] and run.active


def test_hold_aborts_loudly_when_something_else_holds_the_device():
    run = Runner(holders_after_stop="/dev/spidev0.1:  4242\n")
    with pytest.raises(A.AutoConfError) as e:
        A.with_radio_held("meshtasticd.service", "/dev/spidev0.1", lambda: None, run=run, settle_s=0)
    assert "4242" in str(e.value)
    assert ["systemctl", "start", "meshtasticd.service"] in run.calls          # restored even though we did not scan


def test_cli_auto_dry_run_prints_and_touches_nothing(tmp_path, capsys, monkeypatch):
    from lorascan import cli
    root = _mt_tree(tmp_path, {"nebrad1.yaml": MT_SPI})
    calls = []
    monkeypatch.setattr(A, "_default_run", lambda argv: (calls.append(argv), A.Ran(0, "", ""))[1])
    rc = cli.main(["auto", "--from", "meshtasticd", "--root", root, "--dry-run", "--", "survey", "--duration", "1h", "--cad-grid", "500000"])
    out = capsys.readouterr().out
    assert rc == 0 and "meshtasticd.service" in out and "/dev/spidev0.1" in out and "scan survey" in out and "--duration 1h" in out and "would stop" in out
    assert not any(c[:2] == ["systemctl", "stop"] for c in calls)


def test_yamlmini_indented_sequences_of_scalars_and_mappings():
    """Regression after #8: an ordinary indented list under a key ('key:' then deeper '- item') raised."""
    s = """\
a:
  b: 1
  c:
    - x: 1
      y:
        z: true
    - x: 2
  d: {p: 1, q: two}
  e: |
    line1
    line2
  f: after
  names:
    - alpha
    - beta
g: 0x10
"""
    d = parse_yaml(s)
    assert d["a"]["c"] == [{"x": 1, "y": {"z": True}}, {"x": 2}] and d["a"]["names"] == ["alpha", "beta"]
    assert d["a"]["d"] == {"p": 1, "q": "two"} and d["a"]["e"] == "line1\nline2" and d["a"]["f"] == "after" and d["g"] == 16
