import pytest
from lorascan.hal.ch341 import (reverse_byte, spi_stream_packets, decode_spi_in, uio_out_packet, status_pin_high,
                                OUTPUT_DIRS, CMD_SPI_STREAM, CMD_UIO_STREAM, UIO_STM_OUT, UIO_STM_DIR, UIO_STM_END,
                                PIN_CS, PIN_RXEN, PIN_RESET, PIN_BUSY, PIN_DIO1, PIN_SCK, PIN_MOSI, PIN_MISO, Ch341Hal, SPI_CHUNK, PACKET_LEN)
from lorascan.profile import load_profile

def test_reverse_byte_known_values_and_involution():
    assert reverse_byte(0x00) == 0x00 and reverse_byte(0xFF) == 0xFF and reverse_byte(0x80) == 0x01 and reverse_byte(0x01) == 0x80
    assert reverse_byte(0xA8) == 0x15 and reverse_byte(0b11001010) == 0b01010011
    assert all(reverse_byte(reverse_byte(b)) == b for b in range(256))

def test_spi_stream_packetization():
    p = spi_stream_packets(bytes([0x12, 0, 0, 0]))
    assert len(p) == 1 and p[0][0] == CMD_SPI_STREAM and len(p[0]) == 5 and p[0][1] == reverse_byte(0x12) and p[0][2:] == bytes(3)
    assert len(spi_stream_packets(bytes(SPI_CHUNK))) == 1 and len(spi_stream_packets(bytes(SPI_CHUNK))[0]) == PACKET_LEN
    p = spi_stream_packets(bytes(SPI_CHUNK + 1)); assert len(p) == 2 and len(p[1]) == 2
    p = spi_stream_packets(bytes(258)); assert len(p) == 9 and sum(len(x) - 1 for x in p) == 258
    assert spi_stream_packets(b"") == []
    data = bytes(range(64)); echoed = b"".join(x[1:] for x in spi_stream_packets(data))
    assert echoed != data and decode_spi_in(echoed) == data

def test_uio_packet_and_direction_mask():
    lv = (1 << PIN_CS) | (1 << PIN_RXEN) | (1 << PIN_RESET)
    assert uio_out_packet(lv, lv) == bytes([CMD_UIO_STREAM, UIO_STM_OUT | 0x07, UIO_STM_DIR | 0x07, UIO_STM_END])
    p = uio_out_packet(0xFF, 0xFF); assert p[1] == UIO_STM_OUT | 0x3F and p[2] == UIO_STM_DIR | 0x3F
    assert OUTPUT_DIRS == 0b00101111
    for pin in (PIN_BUSY, PIN_DIO1, PIN_MISO):
        assert not OUTPUT_DIRS & (1 << pin)
    assert status_pin_high(1 << 4, PIN_BUSY) and not status_pin_high(1 << 4, PIN_DIO1) and status_pin_high(1 << 6, PIN_DIO1)

class FakeUsb:
    """Stands in for the libusb device handle: records bulk writes, answers reads from a script."""
    def __init__(self):
        self.writes = []
        self.spi_reply = bytes([0xFF, 0x22, 0x00, 0x00])   # the device's (already bit-reversed on the wire) answer
        self.status0 = 0x00
    def write(self, ep, data, timeout=None):
        self.writes.append((ep, bytes(data))); return len(data)
    def read(self, ep, n, timeout=None):
        last = self.writes[-1][1]
        if last[0] == 0xA0:
            return bytes([self.status0, 0, 0, 0, 0, 0])
        if last[0] == CMD_SPI_STREAM:
            k = len(last) - 1
            return bytes(reverse_byte(b) for b in (self.spi_reply + bytes(64))[:k])
        return b""

def test_ch341_hal_frames_cs_and_reverses_bits():
    prof = load_profile("meshtoad-v3-ch341")
    usb = FakeUsb()
    hal = Ch341Hal(prof, handle=usb)
    assert usb.writes[0][1] == uio_out_packet((1 << PIN_CS) | (1 << PIN_RXEN) | (1 << PIN_RESET), OUTPUT_DIRS)
    rx = hal.xfer(bytes([0xC0, 0x00, 0x00, 0x00]))
    assert rx == bytes([0xFF, 0x22, 0x00, 0x00])
    kinds = [w[1][0] for w in usb.writes[1:]]
    assert kinds == [CMD_UIO_STREAM, CMD_SPI_STREAM, CMD_UIO_STREAM]          # CS low, stream, CS high
    assert usb.writes[2][1][1] == reverse_byte(0xC0)
    usb.status0 = 1 << PIN_BUSY
    assert hal.busy() and not hal.dio1()
    hal.set_reset(False); assert not (usb.writes[-1][1][1] & (1 << PIN_RESET))
    hal.set_rxen(False); assert not (usb.writes[-1][1][1] & (1 << PIN_RXEN))

def test_ch341_without_pyusb_raises_a_clear_error(monkeypatch):
    import lorascan.hal.ch341 as m
    monkeypatch.setattr(m, "_usb_core", None)
    with pytest.raises(m.HalError):
        Ch341Hal(load_profile("meshtoad-v3-ch341"))
