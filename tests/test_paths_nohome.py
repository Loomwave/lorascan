"""#22 follow-up (pinztrek, 2026-09-19T00:12Z): ~/.config (or any default lorascan dir) may not exist at
all — a balena container's HOME can be absent or read-only. lorascan must continue without error."""
import os


def _run(argv, capsys):
    from lorascan import cli
    rc = cli.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_missing_home_with_data_dir_is_fully_functional_and_leaves_home_alone(tmp_path, monkeypatch, capsys):
    ghost = tmp_path / "no-such-home"           # never created
    monkeypatch.setenv("HOME", str(ghost))
    monkeypatch.delenv("LORASCAN_DIR", raising=False)
    from lorascan import paths
    paths.set_data_dir(None)
    d = tmp_path / "data" / "lorascan"          # /data/lorascan-style: parent exists, leaf does not
    rc, out, err = _run(["-d", str(d), "scan", "quick", "--profile", "fake", "--duration", "1s"], capsys)
    assert rc == 0, err
    rc, out, err = _run(["-d", str(d), "report"], capsys)
    assert rc == 0, err
    rc, out, err = _run(["-d", str(d), "share", "--cell", "33.9,-84.3"], capsys)
    assert rc == 0, err
    assert (d / "lorascan.db").exists() and (d / "lorascan-report.html").exists() and (d / "token").exists()
    assert not ghost.exists(), "HOME must not be created as a side effect"
    assert "Traceback" not in err


def test_missing_home_without_data_dir_read_only_commands_still_run(tmp_path, monkeypatch, capsys):
    ghost = tmp_path / "no-such-home"
    monkeypatch.setenv("HOME", str(ghost))
    monkeypatch.delenv("LORASCAN_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    from lorascan import paths
    paths.set_data_dir(None)
    rc, out, err = _run(["scan", "quick", "--profile", "fake", "--duration", "1s"], capsys)
    assert rc == 0, err
    rc, out, err = _run(["status"], capsys)
    assert rc == 0, err
    rc, out, err = _run(["report"], capsys)
    assert rc == 0, err
    assert not ghost.exists()
    assert "Traceback" not in err


def test_setup_style_write_with_data_dir_never_needs_home(tmp_path, monkeypatch):
    ghost = tmp_path / "no-such-home"
    monkeypatch.setenv("HOME", str(ghost))
    from lorascan import paths, station_config as sc
    paths.set_data_dir(str(tmp_path / "d"))
    cfg = sc.load()                              # legacy path probed, absent: fine
    p = sc.save(cfg)                             # goes under the data dir only
    assert p.startswith(str(tmp_path / "d"))
    assert not ghost.exists()
    paths.set_data_dir(None)
