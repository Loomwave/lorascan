"""Loomwave/lorascan#5: sync-word finder — sweep 8-bit sync words at one PHY and report the ones that decode."""
from lorascan import cli
from lorascan.syncfind import parse_syncs, sync_find
from lorascan.hal.fake import FakeHal
from lorascan.radio.sx126x import Sx126x, IRQ_RX_DONE, IRQ_CRC_ERR
from lorascan.profile import load_profile
from lorascan.store.db import Store


def test_parse_syncs_ranges_lists_and_default_all():
    assert parse_syncs(None) == list(range(256))
    assert parse_syncs("0x12,0x34,0x2B") == [0x12, 0x34, 0x2B]
    assert parse_syncs("0x10-0x13,0xF0") == [0x10, 0x11, 0x12, 0x13, 0xF0]


def _hal_that_decodes_only(sync_regs):
    hal = FakeHal({0x17: bytes(4), 0x13: bytes([0, 0, 21, 0]), 0x14: bytes([0, 0, 0xC8, 0x14, 0xC8]), 0x1E: bytes(64)})
    def irq(tx):
        writes = [t for t in hal.log if t[0] == 0x0D and t[1:3] == bytes([0x07, 0x40])]
        cur = bytes(writes[-1][3:5]) if writes else b""
        return bytes([0, 0]) + ((IRQ_RX_DONE if cur == sync_regs else 0).to_bytes(2, "big"))
    hal.replies[0x12] = irq
    return hal


def test_sync_find_reports_only_the_sync_that_decodes():
    hal = _hal_that_decodes_only(bytes([0x34, 0x44]))                 # 0x34 -> regs (0x34, 0x44)
    radio = Sx126x(hal, load_profile("nebra-duo-hat"))
    rows = sync_find(radio, 915_000_000, sf=9, bw_khz=125, cr=5, syncs=[0x12, 0x34, 0x2B], dwell_s=0.02, clock=lambda: hal.clock)
    found = [r for r in rows if r.n_ok]
    assert len(rows) == 3 and [r.network for r in rows] == ["sync-0x12", "sync-0x34", "sync-0x2b"]
    assert len(found) == 1 and found[0].network == "sync-0x34" and found[0].preset == "sf9/bw125/cr5" and found[0].rssi_med == -100.0


def test_cli_syncfind_writes_rows_and_prints_the_winner(tmp_path, capsys, monkeypatch):
    import lorascan.cli as c
    monkeypatch.setattr(c, "_open_radio", lambda prof, f: (lambda h: (h, Sx126x(h, prof)))(_hal_that_decodes_only(bytes([0x24, 0xB4]))))
    db = str(tmp_path / "sf.db")
    rc = cli.main(["syncfind", "--profile", "fake", "--db", db, "--freq", "915.0", "--sf", "9", "--bw", "125", "--syncs", "0x12,0x2B", "--sync-dwell", "0.02"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0x2B" in out and "sync-0x2b" in out.lower() and "found" in out.lower()
    s = Store(db)
    assert s.runs()[-1]["kind"] == "syncfind" and len(list(s.iter_decode())) == 2
