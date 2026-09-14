"""Optional MQTT publisher: per-channel summaries for Grafana / Home Assistant users.

`--mqtt mqtt://[user:pass@]host[:port][/prefix]`. Needs paho-mqtt (`pip install paho-mqtt`);
everything else in lorascan works without it. A broker failure never stops a scan: the error
is counted and the scan continues.

Topics (prefix defaults to lorascan/<hostname>):
  <prefix>/energy/<MHz>            one JSON per energy row (no histogram)
  <prefix>/cad/<MHz>/sf<SF>        one JSON per CAD sweep
  <prefix>/decode/<MHz>/<network>  one JSON per decode dwell (counts only, never payloads)
  <prefix>/status                  retained JSON: run id, row counts, last row time
"""
from __future__ import annotations

import dataclasses
import json
import socket
from urllib.parse import urlparse, unquote


def parse_mqtt_url(url: str) -> dict:
    u = urlparse(url if "://" in url else "mqtt://" + url)
    if u.scheme not in ("mqtt", "mqtts"):
        raise ValueError(f"--mqtt: unsupported scheme {u.scheme!r} (use mqtt:// or mqtts://)")
    site = socket.gethostname().split(".")[0] or "site"
    prefix = u.path.strip("/") or f"lorascan/{site}"
    return {"host": u.hostname or "localhost", "port": u.port or (8883 if u.scheme == "mqtts" else 1883),
            "user": unquote(u.username) if u.username else None, "password": unquote(u.password) if u.password else None,
            "tls": u.scheme == "mqtts", "prefix": prefix, "site": site}


def make_client(cfg: dict):
    """Build a connected paho client. Replaced in tests."""
    try:
        import paho.mqtt.client as pm
    except ImportError as e:
        raise RuntimeError("--mqtt needs paho-mqtt: pip install paho-mqtt") from e
    try:
        c = pm.Client(pm.CallbackAPIVersion.VERSION2)   # paho 2.x
    except AttributeError:
        c = pm.Client()                                  # paho 1.x
    if cfg.get("tls"):
        c.tls_set()
    return c


def _row_dict(row) -> dict:
    d = dataclasses.asdict(row) if dataclasses.is_dataclass(row) else dict(row)
    d.pop("hist", None)
    return d


class MqttPublisher:
    def __init__(self, cfg: dict, client=None):
        self.cfg = cfg
        self.prefix = cfg["prefix"]
        self.errors = 0
        self.client = client if client is not None else make_client(cfg)
        if cfg.get("user"):
            self.client.username_pw_set(cfg["user"], cfg.get("password"))
        self.client.connect(cfg["host"], cfg["port"], 60)   # a bad broker fails here, loudly, before the radio is opened
        self.client.loop_start()

    def _pub(self, topic: str, obj: dict, retain: bool = False):
        try:
            self.client.publish(f"{self.prefix}/{topic}", json.dumps(obj, separators=(",", ":")), qos=0, retain=retain)
        except Exception:
            self.errors += 1

    def publish_energy(self, row, label: str = ""):
        d = _row_dict(row); d["label"] = label
        self._pub(f"energy/{row.freq_hz / 1e6:.3f}", d)

    def publish_cad(self, row):
        self._pub(f"cad/{row.freq_hz / 1e6:.3f}/sf{row.sf}", _row_dict(row))

    def publish_decode(self, row):
        self._pub(f"decode/{row.freq_hz / 1e6:.3f}/{row.network}", _row_dict(row))

    def publish_status(self, status: dict):
        self._pub("status", status, retain=True)

    def close(self):
        try:
            self.client.loop_stop(); self.client.disconnect()
        except Exception:
            self.errors += 1
