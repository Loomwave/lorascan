"""Sync-word finder (Loomwave/lorascan#5): CAD says "LoRa here", decode says nothing because the sync
word is not in the table. Sweep the 8-bit sync words at one PHY hypothesis (freq, SF, BW, CR) and report
the ones that yield CRC-valid frames. Receive-only; payloads are drained and never stored, as everywhere."""
from __future__ import annotations
import time
from .measure.decode import decode_dwell, DecodeRow
from .networks import Network, Preset


def parse_syncs(text: str | None) -> list[int]:
    """None -> all 256; '0x12,0x34' or '0x10-0x13,0xF0' (hex or decimal)."""
    if not text:
        return list(range(256))
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a, 0), int(b, 0) + 1))
        else:
            out.append(int(part, 0))
    for v in out:
        if not 0 <= v <= 0xFF:
            raise ValueError(f"sync word out of range: {v:#x}")
    return out


def sync_find(radio, freq_hz: int, sf: int, bw_khz: int, cr: int = 5, syncs=None, dwell_s: float = 1.0,
              preamble: int = 8, clock=time.monotonic, ts: float | None = None, progress=None) -> list[DecodeRow]:
    """One decode dwell per sync word; rows are named network='sync-0xNN', preset='sfS/bwB/crC'."""
    rows = []
    preset = Preset(f"sf{sf}/bw{bw_khz}/cr{cr}", sf, bw_khz, cr, preamble=preamble)
    for s in (syncs if syncs is not None else range(256)):
        net = Network(f"sync-0x{s:02x}", s, (preset,))
        row = decode_dwell(radio, freq_hz, net, preset, dwell_s, clock=clock, ts=ts)
        rows.append(row)
        if progress:
            progress(s, row)
    return rows
