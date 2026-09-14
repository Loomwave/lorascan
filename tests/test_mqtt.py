import json
from lorascan.mqtt import parse_mqtt_url, MqttPublisher
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow
from lorascan import cli, mqtt as mqttmod


class FakeClient:
    def __init__(self):
        self.pub = []; self.connected = None; self.closed = False; self.auth = None
    def username_pw_set(self, u, p): self.auth = (u, p)
    def connect(self, host, port, keepalive=60): self.connected = (host, port)
    def loop_start(self): pass
    def loop_stop(self): pass
    def publish(self, topic, payload, qos=0, retain=False): self.pub.append((topic, payload, retain))
    def disconnect(self): self.closed = True


def test_parse_mqtt_url_defaults_and_auth():
    c = parse_mqtt_url("mqtt://broker.local")
    assert (c["host"], c["port"], c["user"], c["prefix"]) == ("broker.local", 1883, None, "lorascan/" + c["site"])
    c = parse_mqtt_url("mqtt://bob:pw@10.0.0.5:1884/home/rf")
    assert (c["host"], c["port"], c["user"], c["password"], c["prefix"]) == ("10.0.0.5", 1884, "bob", "pw", "home/rf")


def test_publisher_energy_topic_payload_and_retained_status():
    fc = FakeClient()
    p = MqttPublisher(parse_mqtt_url("mqtt://b/rf"), client=fc)
    assert fc.connected == ("b", 1883)
    row = EnergyRow(ts=1.7e9, freq_hz=911_500_000, bw_hz=125_000, engine="poll", n=100, floor_dbm=-110, p50=-105, p90=-95, peak=-40, busy_frac=0.12)
    p.publish_energy(row, label="Loomwave fleet")
    topic, payload, retain = fc.pub[-1]
    assert topic == "rf/energy/911.500" and retain is False
    d = json.loads(payload)
    assert d["busy_frac"] == 0.12 and d["label"] == "Loomwave fleet" and d["floor_dbm"] == -110 and "hist" not in d
    p.publish_cad(CadRow(ts=1.7e9, freq_hz=906_875_000, bw_hz=250_000, sf=7, symbols=2, n_cad=50, hits=3, longest_run=1, det_peak=22, det_min=10))
    assert fc.pub[-1][0] == "rf/cad/906.875/sf7"
    p.publish_status({"run_id": 3, "rows": 10})
    assert fc.pub[-1][0] == "rf/status" and fc.pub[-1][2] is True
    p.close(); assert fc.closed


def test_publisher_swallows_broker_errors_after_connect():
    class Bad(FakeClient):
        def publish(self, *a, **k): raise OSError("broker gone")
    p = MqttPublisher(parse_mqtt_url("mqtt://b"), client=Bad())
    p.publish_status({"x": 1})            # must not raise
    assert p.errors == 1


def test_cli_scan_quick_publishes_over_mqtt(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(mqttmod, "make_client", lambda cfg: made.append(FakeClient()) or made[-1])
    rc = cli.main(["scan", "quick", "--profile", "fake", "--db", str(tmp_path / "q.db"), "--passes", "1", "--dwell", "0.01", "--mqtt", "mqtt://b/t"])
    assert rc == 0 and made
    topics = [t for t, _, _ in made[0].pub]
    assert sum(t.startswith("t/energy/") for t in topics) == 144 and topics[-1] == "t/status"


def test_cli_bad_mqtt_url_is_a_clean_error(tmp_path, capsys):
    rc = cli.main(["scan", "quick", "--profile", "fake", "--db", str(tmp_path / "q.db"), "--passes", "1", "--dwell", "0.01", "--mqtt", "http://b"])
    assert rc == 1 and "unsupported scheme" in capsys.readouterr().err
