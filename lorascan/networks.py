"""Known-network PHY table (spec §1.4) for the 902-928 MHz band. Counts only are ever recorded from
these; the table is the seed for a user-editable file in a later phase. Sources: Meshtastic radio
settings docs + RadioLibInterface (sync 0x2B; slot = freqStart + bw/2 + k*bw); LoRa Alliance RP002
US915 (64 x 125 kHz uplinks from 902.3 MHz every 200 kHz, 8 x 500 kHz from 903.0 every 1.6 MHz,
8 x 500 kHz downlinks from 923.3 every 600 kHz, sync 0x34); MeshCore US recommended 910.525 MHz
SF7/BW62.5/CR5 (sync word NOT confirmed from source: RadioLib default 0x12 assumed); Loomwave fleet
911.5 MHz SF9/BW125 private sync."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    name: str
    sf: int
    bw_khz: int
    cr: int = 5
    preamble: int = 8
    crc_on: bool = True
    freqs_hz: tuple = ()        # explicit centre frequencies (LoRaWAN, MeshCore, Loomwave)
    slot_bw_hz: int = 0         # Meshtastic: every slot of this width across the band


@dataclass(frozen=True)
class Network:
    name: str
    sync_word: int
    presets: tuple
    note: str = ""


US_START_HZ, US_END_HZ = 902_000_000, 928_000_000


def meshtastic_slot_freq_hz(slot: int, bw_hz: int) -> int:
    """Meshtastic frequency slot as the app shows it (1-based): freq = freqStart + bw/2 + (slot-1)*bw.
    The firmware's channel_num is 0-based (hash(name) % numChannels); the UI adds one. US915 LongFast:
    hash('LongFast') % 104 = 19 -> UI slot 20 -> 906.875 MHz."""
    return int(US_START_HZ + bw_hz / 2 + (slot - 1) * bw_hz)


def meshtastic_num_slots(bw_hz: int) -> int:
    return int((US_END_HZ - US_START_HZ) // bw_hz)


def _mt(name, sf, bw, cr):
    return Preset(name, sf, bw, cr, preamble=16, slot_bw_hz=bw * 1000)


MESHTASTIC = Network("meshtastic", 0x2B, (
    _mt("ShortTurbo", 7, 500, 5), _mt("ShortFast", 7, 250, 5), _mt("ShortSlow", 8, 250, 5), _mt("MediumFast", 9, 250, 5),
    _mt("MediumSlow", 10, 250, 5), _mt("LongTurbo", 11, 500, 8), _mt("LongFast", 11, 250, 5), _mt("LongModerate", 11, 125, 8),
), "US default LongFast slot 20 = 906.875 MHz; 104 slots at 250 kHz")

LORAWAN_US915 = Network("lorawan-us915", 0x34, (
    Preset("uplink-125k", 7, 125, 5, freqs_hz=tuple(902_300_000 + 200_000 * k for k in range(64))),
    Preset("uplink-500k", 8, 500, 5, freqs_hz=tuple(903_000_000 + 1_600_000 * k for k in range(8))),
    Preset("downlink-500k", 7, 500, 5, freqs_hz=tuple(923_300_000 + 600_000 * k for k in range(8))),
), "uplinks are SF7-SF10 at BW125 (DR0-DR3) and SF8 at BW500 (DR4); downlinks SF7-SF12 at BW500")

MESHCORE = Network("meshcore", 0x12, (
    Preset("us-narrow", 7, 62, 5, preamble=32, freqs_hz=(910_525_000,)),
    Preset("us-legacy", 11, 250, 5, preamble=16, freqs_hz=(910_525_000,)),
), "sync word assumed = RadioLib default 0x12; confirm against MeshCore source before trusting decode counts")

LOOMWAVE = Network("loomwave", 0x12, (
    Preset("fleet", 9, 125, 5, preamble=16, freqs_hz=(911_500_000,)),
    Preset("bench-cell", 9, 125, 5, preamble=16, freqs_hz=(905_000_000,)),
), "private sync 0x12")

NETWORKS: list[Network] = [MESHTASTIC, LORAWAN_US915, MESHCORE, LOOMWAVE]


def sync_word_regs(word8: int) -> tuple[int, int]:
    """0x2B -> (0x24, 0xB4): each nibble spread into the high nibble of a register with 0x4 low."""
    return (word8 & 0xF0) | 0x04, ((word8 & 0x0F) << 4) | 0x04


def presets_on(freq_hz: int, tol_hz: int = 1_000) -> list[tuple[str, Preset]]:
    """Every (network, preset) whose centre frequency is freq_hz (Meshtastic: any slot of its width)."""
    out = []
    for n in NETWORKS:
        for p in n.presets:
            if p.slot_bw_hz:
                k = round((freq_hz - US_START_HZ - p.slot_bw_hz / 2) / p.slot_bw_hz)
                if 0 <= k < meshtastic_num_slots(p.slot_bw_hz) and abs(meshtastic_slot_freq_hz(k, p.slot_bw_hz) - freq_hz) <= tol_hz:
                    out.append((n.name, p))
            elif any(abs(f - freq_hz) <= tol_hz for f in p.freqs_hz):
                out.append((n.name, p))
    return out
