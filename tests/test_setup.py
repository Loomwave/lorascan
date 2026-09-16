import os
from lorascan import setup, station_config as sc

def test_scripted_io_consumes_answers():
    io = setup.ScriptedIO(["yes", "2", "hello"])
    assert io.confirm("ok?") is True
    assert io.choose("pick", ["a", "b", "c"]) == 1     # "2" -> index 1
    assert io.ask("name?") == "hello"

def test_run_stops_and_returns_nonzero_on_quit():
    # a wizard whose first step asks to continue; scripted "quit"
    io = setup.ScriptedIO(["quit"])
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    # a one-step run for the framework test: inject a single failing fail-closed step
    w._steps = [lambda ww: setup.StepResult(False, "blocked", "fix me")]
    rc = setup.run(w)
    assert rc != 0
    assert any("not configured" in m for m in io.said)

def test_run_returns_zero_when_all_steps_ok():
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    w._steps = [lambda ww: setup.StepResult(True, "did a thing")]
    assert setup.run(w) == 0

def test_diagnose_maps_symptoms():
    assert "SPI" in setup.diagnose({"verdict": "BAD", "status": 0xFF}, None)
    assert "busy" in setup.diagnose({"verdict": "GOOD"}, {"ok": False, "reason": "busy-stuck"}).lower()
    assert "reset" in setup.diagnose({"verdict": "GOOD"}, {"ok": False, "reason": "init-timeout"}).lower()

def test_validate_loops_until_probe_good():
    class R(setup.Runner):
        def __init__(self): self.n = 0
        def probe(self, p): self.n += 1; return {"verdict": "GOOD" if self.n > 1 else "BAD", "status": 0xFF}
        def selftest(self, p): return {"ok": True}
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, R(), home="/tmp"); w.state["profile_path"] = "fake"
    r1 = setup.step_validate(w); assert r1.ok is False       # first probe BAD
    r2 = setup.step_validate(w); assert r2.ok is True        # second probe GOOD + selftest ok

def test_pins_import_from_meshtasticd(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from lorascan.profile import BoardProfile
    class R(setup.Runner):
        def import_daemon(self, source, config):
            prof = BoardProfile(name="auto-meshtasticd", bus_type="spidev", bus_dev="/dev/spidev0.0",
                                bus_hz=2_000_000, pins={"nss": 8, "reset": 22, "busy": 23, "dio1": 24,
                                                        "rxen": None, "txen": None})
            return SimpleNamespace(profile=prof, location=None)
    io = setup.ScriptedIO(["1", "meshtasticd", ""])   # choose "import", source, default config
    w = setup.Wizard(io, R(), home=str(tmp_path))
    res = setup.step_pins(w)
    assert res.ok and os.path.exists(w.state["profile_path"])   # a real profile written under home

def test_location_range_checked():
    io = setup.ScriptedIO(["999,0", "33.9,-84.3"])   # first out of range, then valid
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    res = setup.step_location(w)
    assert res.ok and w.state["location"] == (33.9, -84.3)

def test_upload_verifies_and_sets_endpoint():
    class R(setup.Runner):
        def endpoint_health(self, url): return {"ok": True, "status": 200, "watermark": None}
        def first_upload(self, db, cfg): return {"ok": True, "sent": 3, "accepted": 3}
    io = setup.ScriptedIO(["", "yes"])               # default endpoint, opt in to first upload
    w = setup.Wizard(io, R(), home="/tmp"); w.state["db"] = "x.db"
    res = setup.step_upload(w)
    assert res.ok and w.state["endpoint"].startswith("https://")

def test_upload_saves_endpoint_even_when_unreachable():
    class R(setup.Runner):
        def endpoint_health(self, url): return {"ok": False, "error": "offline in test"}
    io = setup.ScriptedIO([""])               # accept default endpoint
    w = setup.Wizard(io, R(), home="/tmp")
    res = setup.step_upload(w)
    assert res.ok and w.state["endpoint"] == "https://share.lorascan.app"

def test_firstlight_draws_graph_from_sweep():
    class R(setup.Runner):
        def sweep(self, p): return [(915_000_000, -50), (920_000_000, -70)]
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, R(), home="/tmp"); w.state["profile_path"] = "fake"
    res = setup.step_firstlight(w)
    assert res.ok and any("MHz" in m for m in io.said)

def test_write_persists_config(tmp_path):
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, setup.Runner(), home=str(tmp_path))
    w.state.update(profile_path=str(tmp_path/".config/lorascan/profiles/manual.yaml"),
                   location=(33.9, -84.3), endpoint="https://share.lorascan.app", granularity="hour")
    res = setup.step_write(w)
    got = sc.load(home=str(tmp_path))
    assert res.ok and got.location == (33.9, -84.3) and got.endpoint == "https://share.lorascan.app"
