"""Loomwave/lorascan#6: a transient bus error (pyusb USBTimeoutError / USBError) must not end a survey."""
import lorascan.cli as c
from lorascan import cli
from lorascan.hal import HalError
from lorascan.hal.fake import FakeHal
from lorascan.store.db import Store
from lorascan.profile import load_profile


class FlakyHal(FakeHal):
    """Raises HalError on xfer for `fail_at` (call-index, count) pairs; counts reopens."""
    plan: dict = {}
    calls = [0]
    reopened = [0]
    def xfer(self, tx):
        self.calls[0] += 1
        n = self.calls[0]
        for start, count in self.plan.items():
            if start <= n < start + count:
                raise HalError(f"usb: [Errno 110] Operation timed out (call {n})")
        return super().xfer(tx)


def _run(monkeypatch, tmp_path, plan, extra=()):
    FlakyHal.plan = plan; FlakyHal.calls[0] = 0; FlakyHal.reopened[0] = 0
    def fake_open(prof):
        FlakyHal.reopened[0] += 1
        return FlakyHal.for_profile(prof)
    monkeypatch.setattr(c, "open_hal", fake_open)
    monkeypatch.setattr(c, "HAL_BACKOFF_S", [0, 0, 0, 0, 0])
    db = str(tmp_path / "f.db")
    rc = cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001", *extra])
    s = Store(db)
    return rc, s, s.event_counts(s.runs()[-1]["id"])


def test_transient_bus_error_reopens_the_hal_and_the_run_completes(tmp_path, monkeypatch):
    rc, s, ev = _run(monkeypatch, tmp_path, {200: 3})           # three consecutive failing transfers, then healthy
    assert rc == 0 and len(list(s.iter_energy())) == 144
    assert ev.get("hal_error", 0) >= 1 and ev.get("hal_recovered", 0) >= 1 and "hal_giveup" not in ev
    assert FlakyHal.reopened[0] >= 2                            # the original open plus at least one recovery reopen


def test_persistent_bus_error_gives_up_cleanly_after_the_bound(tmp_path, monkeypatch):
    rc, s, ev = _run(monkeypatch, tmp_path, {200: 10_000})       # never comes back
    assert rc == 0 and ev.get("hal_giveup") == 1 and ev.get("hal_error", 0) >= c.HAL_MAX_CONSECUTIVE
    assert 0 < len(list(s.iter_energy())) < 144 and ev.get("stop") == 1


def test_ch341_hal_wraps_pyusb_exceptions_into_halerror(monkeypatch):
    from lorascan.hal import ch341 as m
    class USBErr(Exception):
        pass
    class Dev:
        n = 0
        def write(self, ep, data, timeout=None):
            self.n += 1
            if self.n > 1:                                   # the constructor's pin setup succeeds; the transfer times out
                raise USBErr("[Errno 110] Operation timed out")
            return len(data)
        def read(self, ep, n, timeout=None): raise USBErr("[Errno 16] Resource busy")
    monkeypatch.setattr(m, "_USB_ERRORS", (USBErr,))
    hal = m.Ch341Hal(load_profile("meshtoad-v3-ch341"), handle=Dev())
    try:
        hal.xfer(b"\xc0\x00")
        assert False, "expected HalError"
    except HalError as e:
        assert "110" in str(e) or "timed out" in str(e)
