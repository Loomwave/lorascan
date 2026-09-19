"""Loomwave/lorascan#25: `lorascan setup --non-interactive` — every wizard answer comes from a
flag, and a re-run is idempotent (same flags = "unchanged", exit 0, no radio and no network)."""
import os
from types import SimpleNamespace

import pytest

from lorascan import cli, paths, setup, station_config as sc
from lorascan.profile import BoardProfile


@pytest.fixture(autouse=True)
def _isolated_paths():
    """Every test drives the CLI with -d TMP; make sure the global data dir never leaks out."""
    yield
    paths.set_data_dir(None)


class CountingRunner(setup.Runner):
    """Records every runner call so a test can assert the radio/network were not touched."""

    def __init__(self, location=None, verdict="GOOD"):
        self.calls = []
        self._loc = location
        self._verdict = verdict

    def probe(self, profile):
        self.calls.append("probe")
        return {"verdict": self._verdict, "status": 0xFF}

    def selftest(self, profile):
        self.calls.append("selftest")
        return {"ok": True}

    def import_daemon(self, source, config):
        self.calls.append("import_daemon")
        # autoconf names an imported profile auto-<source> (resolve_meshtasticd / resolve_openhop)
        prof = BoardProfile(name="auto-" + source, bus_type="spidev", bus_dev="/dev/spidev0.0",
                            bus_hz=2_000_000,
                            pins={"nss": 8, "reset": 22, "busy": 23, "dio1": 24,
                                  "rxen": None, "txen": None})
        return SimpleNamespace(profile=prof, location=self._loc)

    def endpoint_health(self, url):
        self.calls.append("endpoint_health")
        return {"ok": True, "status": 200, "watermark": None}

    def first_upload(self, db, state):
        self.calls.append("first_upload")
        return {"ok": True, "sent": 1, "accepted": 1}

    def sweep(self, profile):
        self.calls.append("sweep")
        return [(915_000_000, -110, -90)]


def _run_cli(monkeypatch, runner, argv):
    """`lorascan …` with the wizard's Runner replaced; everything else is the real CLI."""
    def build(a):
        w = setup.Wizard(setup.TtyIO(), runner, home=None)
        if getattr(a, "db", None):
            w.state["db"] = a.db
        return w
    monkeypatch.setattr(cli, "_build_wizard", build)
    return cli.main(argv)


def _preflight_ok(monkeypatch):
    monkeypatch.setattr(setup, "step_preflight", lambda w: setup.StepResult(True, "host looks ready (stubbed)."))


def _cfg_argv(tmp_path, *extra):
    daemon_cfg = tmp_path / "openhop.yaml"
    daemon_cfg.write_text("lora: {}\n", encoding="utf-8")
    return ["-d", str(tmp_path), "setup", "--non-interactive", "--from", "openhop",
            "--config", str(daemon_cfg), "--endpoint", "http://x"] + list(extra)


# ---------------------------------------------------------------- FlagsIO

def test_flagsio_answers_every_prompt_from_flags():
    io = setup.FlagsIO(setup.Flags(source="openhop", config="/etc/openhop_repeater/config.yaml",
                                   cell="33.9,-84.3", endpoint="http://x"))
    assert io.choose("Where should the radio pin settings come from?",
                     ["Import from meshtasticd or openHOP", "Pick a shipped board profile",
                      "Enter the pins manually"]) == 0
    assert io.ask("Which daemon? (meshtasticd/openhop)", "meshtasticd") == "openhop"
    assert io.ask("Config path (blank for the default)", "") == "/etc/openhop_repeater/config.yaml"
    assert io.ask("Antenna location as lat,lon (blank to skip)", "") == "33.9,-84.3"
    assert io.ask("Community share endpoint", "https://share.lorascan.app") == "http://x"
    assert io.confirm("Do a first upload now to confirm data flows?") is True
    # --cell beats a location the daemon config carries
    assert io.confirm("Use the location from the daemon config (33.0000,-84.0000)?") is False


def test_flagsio_picks_the_shipped_profile_branch():
    io = setup.FlagsIO(setup.Flags(profile="nebra-duo-hat"))
    assert io.choose("Where should the radio pin settings come from?", ["a", "b", "c"]) == 1
    assert io.ask("Profile name (generic-spidev / nebra-duo-hat / meshtoad-v3-ch341)",
                  "generic-spidev") == "nebra-duo-hat"
    # no --cell: a location already in state is kept rather than demanded
    assert io.confirm("Use the location from the daemon config (33.0000,-84.0000)?") is True


def test_flagsio_raises_needs_input_naming_the_flag():
    io = setup.FlagsIO(setup.Flags(source="openhop"))
    with pytest.raises(setup.SetupNeedsInput) as e:
        io.ask("Antenna location as lat,lon (blank to skip)", "")
    assert e.value.flag == "--cell"
    assert "Antenna location" in str(e.value)

    with pytest.raises(setup.SetupNeedsInput) as e:
        setup.FlagsIO(setup.Flags()).choose("Where should the radio pin settings come from?", ["a"])
    assert "--from" in e.value.flag

    with pytest.raises(setup.SetupNeedsInput) as e:      # manual pins are not a non-interactive path
        setup.FlagsIO(setup.Flags()).ask("reset", "22")
    assert "--from" in e.value.flag or "--profile" in e.value.flag


def test_flagsio_refuses_to_loop_on_a_rejected_value():
    io = setup.FlagsIO(setup.Flags(cell="nonsense"))
    assert io.ask("Antenna location as lat,lon (blank to skip)", "") == "nonsense"
    with pytest.raises(setup.SetupBadFlag) as e:         # the wizard asked again = value rejected
        io.ask("Antenna location as lat,lon (blank to skip)", "")
    assert "--cell" in str(e.value)


# ---------------------------------------------------------------- end to end

def test_first_run_writes_config_and_profile_without_a_prompt(tmp_path, monkeypatch, capsys):
    _preflight_ok(monkeypatch)
    r = CountingRunner(location=(33.9, -84.3, "openhop config gps.location"))
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0, out
    cfg_path = tmp_path / "config.yaml"
    assert cfg_path.exists() and (tmp_path / "profiles" / "auto-openhop.yaml").exists()
    got = sc.load()
    assert got.profile == "auto-openhop"
    assert got.location == (33.9, -84.3)               # the daemon's 3-tuple, normalised
    assert got.endpoint == "http://x"
    assert "setup: written" in out and str(cfg_path) in out
    assert "import_daemon" in r.calls and "probe" in r.calls


def test_second_identical_run_is_unchanged_and_touches_nothing(tmp_path, monkeypatch, capsys):
    _preflight_ok(monkeypatch)
    first = CountingRunner(location=(33.9, -84.3, "openhop config gps.location"))
    assert _run_cli(monkeypatch, first, _cfg_argv(tmp_path)) == 0
    capsys.readouterr()

    again = CountingRunner(location=(33.9, -84.3, "openhop config gps.location"))
    rc = _run_cli(monkeypatch, again, _cfg_argv(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert again.calls == []                            # no radio, no daemon config, no network
    assert "setup: unchanged" in out and str(tmp_path / "config.yaml") in out


def test_changed_cell_updates_the_location_and_only_writes(tmp_path, monkeypatch, capsys):
    _preflight_ok(monkeypatch)
    assert _run_cli(monkeypatch, CountingRunner(location=(33.9, -84.3, "cfg")),
                    _cfg_argv(tmp_path)) == 0
    capsys.readouterr()

    r = CountingRunner()
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path, "--cell", "40.0,-70.5"))
    out = capsys.readouterr().out
    assert rc == 0 and r.calls == []
    assert "setup: updated location" in out
    assert sc.load().location == (40.0, -70.5)
    assert sc.load().endpoint == "http://x"             # untouched fields survive


def test_skip_radio_never_touches_the_radio(tmp_path, monkeypatch, capsys):
    r = CountingRunner(location=(33.9, -84.3, "cfg"))
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path, "--skip-radio"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "probe" not in r.calls and "selftest" not in r.calls and "sweep" not in r.calls
    assert "import_daemon" in r.calls
    assert "radio checks skipped" in out
    assert sc.load().profile == "auto-openhop"


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    r = CountingRunner()
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path, "--cell", "33.9,-84.3", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert r.calls == []
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / "profiles").exists()
    assert "dry run" in out and "33.9" in out and "http://x" in out


def test_missing_cell_exits_2_and_names_the_flag(tmp_path, monkeypatch, capsys):
    r = CountingRunner(location=None)                   # the daemon config carries no location
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path, "--skip-radio"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "--cell" in err and "Antenna location" in err
    assert not (tmp_path / "config.yaml").exists()


def test_failing_probe_exits_1_with_the_diagnosis(tmp_path, monkeypatch, capsys):
    _preflight_ok(monkeypatch)
    r = CountingRunner(location=(33.9, -84.3, "cfg"), verdict="BAD")
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path))
    cap = capsys.readouterr()
    assert rc == 1
    assert "did not answer" in cap.err and "SPI" in cap.err
    assert r.calls.count("probe") == 1                  # no retry loop without a TTY
    assert not (tmp_path / "config.yaml").exists()


def test_bad_cell_value_exits_2(tmp_path, monkeypatch, capsys):
    r = CountingRunner()
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path, "--skip-radio", "--cell", "nonsense"))
    err = capsys.readouterr().err
    assert rc == 2 and "--cell" in err
    assert r.calls == []


def test_interactive_setup_is_unchanged(tmp_path, monkeypatch, capsys):
    """Without --non-interactive the wizard still runs (and still uses the TTY IO)."""
    calls = []
    monkeypatch.setattr(setup, "run", lambda w: calls.append(type(w.io).__name__) or 0)
    rc = _run_cli(monkeypatch, CountingRunner(), ["-d", str(tmp_path), "setup"])
    assert rc == 0 and calls == ["TtyIO"]


def test_from_and_profile_together_exit_2(tmp_path, monkeypatch, capsys):
    r = CountingRunner()
    rc = _run_cli(monkeypatch, r, _cfg_argv(tmp_path, "--profile", "generic-spidev", "--skip-radio"))
    err = capsys.readouterr().err
    assert rc == 2 and "--from and --profile" in err and r.calls == []


def test_config_without_from_exits_2(tmp_path, monkeypatch, capsys):
    r = CountingRunner()
    rc = _run_cli(monkeypatch, r, ["-d", str(tmp_path), "setup", "--non-interactive",
                                   "--profile", "generic-spidev", "--config", "/etc/x.yaml", "--skip-radio"])
    err = capsys.readouterr().err
    assert rc == 2 and "--config" in err and r.calls == []


def _mesh_argv(tmp_path, *extra):
    cfg = tmp_path / "meshtasticd.yaml"
    cfg.write_text("Lora: {}\n", encoding="utf-8")
    return ["-d", str(tmp_path), "setup", "--non-interactive", "--from", "meshtasticd",
            "--config", str(cfg), "--endpoint", "http://x"] + list(extra)


def test_switching_daemon_reimports_the_profile(tmp_path, monkeypatch, capsys):
    """An openHOP station pointed at meshtasticd must import the new daemon's pins, not report
    "unchanged" because some profile file happens to be on disk."""
    _preflight_ok(monkeypatch)
    assert _run_cli(monkeypatch, CountingRunner(location=(33.9, -84.3, "cfg")),
                    _cfg_argv(tmp_path)) == 0                       # --from openhop
    capsys.readouterr()
    assert sc.load().profile == "auto-openhop"

    second = CountingRunner(location=(33.9, -84.3, "cfg"))
    rc = _run_cli(monkeypatch, second, _mesh_argv(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert second.calls.count("import_daemon") == 1
    assert "setup: updated profile" in out
    assert sc.load().profile == "auto-meshtasticd"
    assert (tmp_path / "profiles" / "auto-meshtasticd.yaml").exists()

    third = CountingRunner(location=(33.9, -84.3, "cfg"))           # and it settles back down
    assert _run_cli(monkeypatch, third, _mesh_argv(tmp_path)) == 0
    assert third.calls == []
    assert "setup: unchanged" in capsys.readouterr().out
