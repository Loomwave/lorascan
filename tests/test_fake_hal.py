from lorascan.hal.fake import FakeHal

def test_xfer_returns_scripted_reply_padded_to_tx_len_and_logs():
    h = FakeHal({0xC0: bytes([0, 0x22])})
    assert h.xfer(bytes([0xC0, 0])) == b"\x00\x22"
    assert h.xfer(bytes([0xC0, 0, 0, 0])) == b"\x00\x22\x00\x00"
    assert h.xfer(bytes([0x15, 0, 0])) == b"\x00\x00\x00"
    assert h.log == [bytes([0xC0, 0]), bytes([0xC0, 0, 0, 0]), bytes([0x15, 0, 0])]

def test_busy_follows_sequence_then_false_and_reset_is_logged():
    h = FakeHal()
    h.busy_sequence = [True, True, False]
    assert [h.busy() for _ in range(4)] == [True, True, False, False]
    h.set_reset(False); h.set_reset(True)
    assert h.reset_log == [False, True]
    assert h.dio1() is False

def test_reply_can_be_a_callable_for_cycling_values():
    vals = iter([bytes([0, 0, 0xC8]), bytes([0, 0, 0xCA])])
    h = FakeHal({0x15: lambda tx: next(vals)})
    assert h.xfer(bytes([0x15, 0, 0]))[2] == 0xC8
    assert h.xfer(bytes([0x15, 0, 0]))[2] == 0xCA

def test_fake_sleep_advances_a_fake_clock():
    h = FakeHal()
    h.sleep(0.5); h.sleep(0.25)
    assert abs(h.clock - 0.75) < 1e-9
