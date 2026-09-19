"""Loomwave/lorascan#22: one option relocates config AND every output.

Balena repeaters run a read-only rootfs with only /data writable, so `lorascan -d /data/lorascan …`
(or LORASCAN_DIR=/data/lorascan for a systemd unit) must put the config, profiles, networks table,
submitter token and every default-named output file under that one directory, creating missing
subfolders. A path the user passes explicitly is still used exactly as given.
"""
import json
import os

import pytest

from lorascan import cli, networks as netmod, paths, profile as profmod, share, station_config as sc
from lorascan.store.db import Store


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("LORASCAN_DIR", raising=False)
    paths.set_data_dir(None)
    share._said_legacy_token = False        # the "token is at the legacy path" notice is once per process
    yield
    paths.set_data_dir(None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".config" / "lorascan").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


# ---------------------------------------------------------------- precedence

def test_unset_is_none():
    assert paths.data_dir() is None


def test_env_sets_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LORASCAN_DIR", str(tmp_path / "d"))
    assert paths.data_dir() == str(tmp_path / "d")


def test_flag_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LORASCAN_DIR", str(tmp_path / "env"))
    paths.set_data_dir(str(tmp_path / "flag"))
    assert paths.data_dir() == str(tmp_path / "flag")


def test_clearing_the_flag_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LORASCAN_DIR", str(tmp_path / "env"))
    paths.set_data_dir(str(tmp_path / "flag"))
    paths.set_data_dir(None)
    assert paths.data_dir() == str(tmp_path / "env")


def test_data_dir_is_absolute_and_expanded(home, monkeypatch):
    paths.set_data_dir("~/rel")
    assert paths.data_dir() == str(home / "rel")
    monkeypatch.chdir(home)
    paths.set_data_dir("sub/dir")
    assert paths.data_dir() == str(home / "sub" / "dir")


# ---------------------------------------------------------- default_output

def test_default_output_relocates_when_set(tmp_path):
    paths.set_data_dir(str(tmp_path / "d"))
    assert paths.default_output("lorascan.db") == str(tmp_path / "d" / "lorascan.db")


def test_default_output_is_unchanged_when_unset():
    assert paths.default_output("lorascan.db") == "lorascan.db"


def test_for_output_creates_nested_parents(tmp_path):
    p = str(tmp_path / "data" / "lorascan" / "x" / "y.db")
    assert paths.for_output(p) == p
    assert os.path.isdir(os.path.dirname(p))


def test_ensure_dir_is_idempotent(tmp_path):
    d = str(tmp_path / "a" / "b")
    assert paths.ensure_dir(d) == d and paths.ensure_dir(d) == d and os.path.isdir(d)


# --------------------------------------------------- config / profiles / token

def test_known_paths_move_under_the_data_dir(tmp_path):
    d = tmp_path / "data" / "lorascan"
    paths.set_data_dir(str(d))
    assert paths.config_path() == str(d / "config.yaml")
    assert paths.profiles_dir() == str(d / "profiles")
    assert paths.networks_path() == str(d / "networks.yaml")
    assert paths.token_path() == str(d / "token")


def test_known_paths_are_legacy_when_unset(home):
    base = home / ".config" / "lorascan"
    assert paths.config_path() == str(base / "config.yaml")
    assert paths.profiles_dir() == str(base / "profiles")
    assert paths.networks_path() == str(base / "networks.yaml")
    assert paths.token_path() == str(base / "token")


def test_station_config_reads_and_writes_the_data_dir(tmp_path, home):
    d = tmp_path / "data" / "lorascan"
    paths.set_data_dir(str(d))
    written = sc.save(sc.StationConfig(profile="datadir-one", endpoint="https://x"))
    assert written == str(d / "config.yaml")
    assert sc.load().profile == "datadir-one"


def test_station_config_falls_back_to_the_legacy_file(tmp_path, home):
    (home / ".config" / "lorascan" / "config.yaml").write_text("profile: legacy-one\n")
    paths.set_data_dir(str(tmp_path / "data" / "lorascan"))
    assert sc.load().profile == "legacy-one"          # adopting -d must not lose an existing config


def test_data_dir_config_wins_over_legacy(tmp_path, home):
    (home / ".config" / "lorascan" / "config.yaml").write_text("profile: legacy-one\n")
    d = tmp_path / "data" / "lorascan"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("profile: datadir-one\n")
    paths.set_data_dir(str(d))
    assert sc.load().profile == "datadir-one"


def test_profile_lookup_falls_back_to_the_legacy_profiles_dir(tmp_path, home):
    pdir = home / ".config" / "lorascan" / "profiles"
    pdir.mkdir(parents=True)
    (pdir / "mine.yaml").write_text("name: mine\nbus: {type: fake, dev: '', hz: 0}\npins: {nss: kernel}\n")
    paths.set_data_dir(str(tmp_path / "data" / "lorascan"))
    assert profmod.load_profile("mine").name == "mine"


def test_profile_lookup_prefers_the_data_dir(tmp_path, home):
    pdir = home / ".config" / "lorascan" / "profiles"
    pdir.mkdir(parents=True)
    (pdir / "mine.yaml").write_text("name: legacy\nbus: {type: fake, dev: '', hz: 0}\npins: {nss: kernel}\n")
    d = tmp_path / "data" / "lorascan" / "profiles"
    d.mkdir(parents=True)
    (d / "mine.yaml").write_text("name: fromdata\nbus: {type: fake, dev: '', hz: 0}\npins: {nss: kernel}\n")
    paths.set_data_dir(str(tmp_path / "data" / "lorascan"))
    assert profmod.load_profile("mine").name == "fromdata"


def test_user_networks_are_read_from_the_data_dir(tmp_path, home):
    d = tmp_path / "data" / "lorascan"
    d.mkdir(parents=True)
    (d / "networks.yaml").write_text("mynet/one: {sync: 0x34, sf: 9, bw: 125, freqs: 905.0}\n")
    paths.set_data_dir(str(d))
    nets = netmod.load_default_user_networks()
    assert [n.name for n in nets] == ["mynet"] and nets[0].sync_word == 0x34


def test_user_networks_fall_back_to_the_legacy_file(tmp_path, home):
    (home / ".config" / "lorascan" / "networks.yaml").write_text("oldnet/one: {sync: 0x56, sf: 9, bw: 125, freqs: 905.0}\n")
    paths.set_data_dir(str(tmp_path / "data" / "lorascan"))
    assert [n.name for n in netmod.load_default_user_networks()] == ["oldnet"]


def test_token_is_created_in_the_data_dir(tmp_path, home):
    d = tmp_path / "data" / "lorascan"
    paths.set_data_dir(str(d))
    t = share.submitter_token()
    assert len(t) == 32 and (d / "token").read_text().strip() == t


def test_token_is_read_through_from_the_legacy_path(tmp_path, home, capsys):
    (home / ".config" / "lorascan" / "token").write_text("deadbeefdeadbeefdeadbeefdeadbeef\n")
    d = tmp_path / "data" / "lorascan"
    paths.set_data_dir(str(d))
    assert share.submitter_token() == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert "token" in capsys.readouterr().err.lower()      # says so once
    assert not (d / "token").exists()                      # the legacy file is not copied or rewritten


# ------------------------------------------------------------------- the CLI

def test_cli_data_dir_relocates_db_and_report(tmp_path):
    d = tmp_path / "data" / "lorascan"
    assert cli.main(["-d", str(d), "scan", "quick", "--profile", "fake", "--duration", "5s"]) == 0
    assert (d / "lorascan.db").exists()
    assert list(Store(str(d / "lorascan.db")).iter_energy())
    assert cli.main(["-d", str(d), "report"]) == 0
    assert (d / "lorascan-report.html").stat().st_size > 5000


def test_cli_env_var_relocates_db(tmp_path, monkeypatch):
    d = tmp_path / "data" / "lorascan"
    monkeypatch.setenv("LORASCAN_DIR", str(d))
    assert cli.main(["scan", "quick", "--profile", "fake", "--duration", "5s"]) == 0
    assert (d / "lorascan.db").exists()


def test_cli_explicit_paths_are_used_as_given(tmp_path, monkeypatch):
    d = tmp_path / "data" / "lorascan"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["-d", str(d), "scan", "quick", "--profile", "fake",
                     "--duration", "5s", "--db", "mine.db"]) == 0
    assert (tmp_path / "mine.db").exists() and not (d / "lorascan.db").exists()
    out = tmp_path / "mine.html"
    assert cli.main(["-d", str(d), "report", "--db", "mine.db", "--out", "mine.html"]) == 0
    assert out.exists() and not (d / "lorascan-report.html").exists()


def test_cli_share_and_export_land_in_the_data_dir(tmp_path, home):
    d = tmp_path / "data" / "lorascan"
    assert cli.main(["-d", str(d), "scan", "quick", "--profile", "fake", "--duration", "5s"]) == 0
    assert cli.main(["-d", str(d), "share"]) == 0
    doc = json.loads((d / "lorascan-share.json").read_text())
    assert doc["format"] == share.SHARE_FORMAT
    assert (d / "token").exists()
    assert cli.main(["-d", str(d), "export", "--csv", str(d / "sub" / "e.csv")]) == 0
    assert (d / "sub" / "e.csv").exists()          # for_output made the missing subfolder


def test_setup_db_keeps_its_none_default(tmp_path):
    """`setup --db` means 'an existing database to use for the first upload' — absent is not
    'lorascan.db', or the wizard would offer a first upload from a database nobody has."""
    a = cli.build_parser().parse_args(["-d", str(tmp_path), "setup"])
    cli._resolve_paths(a)
    assert a.db is None


def test_setup_wizard_writes_into_the_data_dir(tmp_path, home):
    from lorascan import setup as setupmod
    d = tmp_path / "data" / "lorascan"
    paths.set_data_dir(str(d))
    io = setupmod.ScriptedIO([])
    w = setupmod.Wizard(io, setupmod.Runner(), home=str(home))
    w.state.update(profile_path=str(d / "profiles" / "manual.yaml"), location=(33.9, -84.3),
                   endpoint="https://share.lorascan.app", granularity="hour")
    res = setupmod.step_write(w)
    assert res.ok and (d / "config.yaml").exists()
    assert str(d) in res.summary                       # the summary names the directory
    assert not (home / ".config" / "lorascan" / "config.yaml").exists()
    assert sc.load().endpoint == "https://share.lorascan.app"


def test_auto_writes_its_profile_into_the_data_dir_and_passes_d_down(tmp_path, home, capsys, monkeypatch):
    """`auto` writes the profile it resolved and then re-enters main() for scan/share: both must
    stay inside the data dir."""
    from lorascan import autoconf as A
    from tests.test_autoconf import MT_SPI, _mt_tree
    root = _mt_tree(tmp_path, {"nebrad1.yaml": MT_SPI})
    monkeypatch.setattr(A, "_default_run", lambda argv: A.Ran(0, "", ""))
    d = tmp_path / "data" / "lorascan"
    rc = cli.main(["-d", str(d), "auto", "--from", "meshtasticd", "--root", root, "--dry-run",
                   "--", "survey", "--duration", "1h"])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(d / "profiles") in out and "-d " + str(d) + " scan survey" in out
    assert str(home) not in out                     # nothing points back at ~/.config/lorascan
