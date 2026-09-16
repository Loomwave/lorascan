import os, sys, types
import pytest
from lorascan.profile import BoardProfile

def _prof(nss):
    return BoardProfile(name="t", bus_type="spidev", bus_dev="/dev/spidev0.0",
                        bus_hz=2_000_000, pins={"nss": nss, "reset": 22, "busy": 23,
                                                "dio1": 24, "rxen": None, "txen": None})

class _Spi:
    def __init__(self, log):
        self.log = log; self.mode = None; self.max_speed_hz = None; self._no_cs = None
    def open(self, b, c): self.log.append(("spi_open", b, c))
    @property
    def no_cs(self): return self._no_cs
    @no_cs.setter
    def no_cs(self, v): self._no_cs = v
    def xfer2(self, data): self.log.append(("xfer2", bytes(data))); return [0]*len(data)
    def close(self): self.log.append(("spi_close",))

class _Gpio:
    def __init__(self, log): self.log = log
    def request_out(self, off, init): self.log.append(("req_out", off, init))
    def request_in(self, off): self.log.append(("req_in", off))
    def set(self, off, level): self.log.append(("set", off, 1 if level else 0))
    def get(self, off): return False
    def close(self): self.log.append(("gpio_close",))

@pytest.fixture
def wired(monkeypatch):
    log = []
    import lorascan.hal.spidev_gpiod as m
    monkeypatch.setitem(sys.modules, "spidev", types.SimpleNamespace(SpiDev=lambda: _Spi(log)))
    monkeypatch.setattr(m, "_open_gpio", lambda: _Gpio(log))
    monkeypatch.setattr(m, "acquire_device_lock", lambda dev: os.open(os.devnull, os.O_RDWR))
    return m, log

def test_gpio_cs_is_requested_idle_high_and_no_cs_attempted(wired):
    m, log = wired
    hal = m.SpidevGpiodHal(_prof(8))
    assert ("req_out", 8, 1) in log            # CS active-low, idle high (deasserted)
    assert hal.spi.no_cs is True               # single-radio host: suppress kernel CE

def test_gpio_cs_frames_each_xfer_low_then_high(wired):
    m, log = wired
    hal = m.SpidevGpiodHal(_prof(8))
    log.clear()
    hal.xfer(b"\xAB\xCD")
    assert log == [("set", 8, 0), ("xfer2", b"\xAB\xCD"), ("set", 8, 1)]

def test_kernel_cs_leaves_xfer_and_cs_untouched(wired):
    m, log = wired
    hal = m.SpidevGpiodHal(_prof("kernel"))
    assert hal.spi.no_cs is None               # not set under kernel CE
    assert not any(e[0] == "req_out" and e[1] == "kernel" for e in log)
    log.clear()
    hal.xfer(b"\x01")
    assert log == [("xfer2", b"\x01")]         # no CS toggling

def test_software_cs_no_longer_raises(wired):
    m, _ = wired
    m.SpidevGpiodHal(_prof(8))                 # must not raise HalError
