import threading, urllib.request, socket, os
import pytest
from lorascan import cli
from lorascan.store.db import Store
from lorascan.serve import make_server
from lorascan.hal.lock import acquire_device_lock, DeviceBusy

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def test_serve_renders_the_report_from_the_db(tmp_path):
    db = str(tmp_path / "s.db")
    assert cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001"]) == 0
    port = free_port()
    srv = make_server(db, "127.0.0.1", port, refresh_s=1)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "Plotly.newPlot" in html and "lorascan" in html and 'http-equiv="refresh"' in html
        status = urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=5).read().decode()
        assert '"energy"' in status and '"kind": "quick"' in status
    finally:
        srv.shutdown()

def test_device_lock_refuses_a_second_holder(tmp_path):
    lock_dir = str(tmp_path)
    fd = acquire_device_lock("/dev/spidev0.0", lock_dir=lock_dir)
    assert os.path.exists(os.path.join(lock_dir, "lorascan-spidev0.0.lock"))
    with pytest.raises(DeviceBusy):
        acquire_device_lock("/dev/spidev0.0", lock_dir=lock_dir)
    os.close(fd)
    fd2 = acquire_device_lock("/dev/spidev0.0", lock_dir=lock_dir); os.close(fd2)
