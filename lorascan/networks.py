"""Known-network PHY table (spec §1.4) for the 902-928 MHz band. Counts only are ever recorded from
these; the table is the seed for a user-editable file in a later phase. Sources: Meshtastic radio
settings docs + RadioLibInterface (sync 0x2B; slot = freqStart + bw/2 + k*bw); LoRa Alliance RP002
US915 (64 x 125 kHz uplinks from 902.3 MHz every 200 kHz, 8 x 500 kHz from 903.0 every 1.6 MHz,
8 x 500 kHz downlinks from 923.3 every 600 kHz, sync 0x34); MeshCore US recommended 910.525 MHz
SF7/BW62.5/CR5 (sync word NOT confirmed from source: RadioLib default 0x12 assumed); Loomwave fleet
911.5 MHz SF9/BW125 private sync."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import os
from .profile import parse_mini_yaml


@dataclass(frozen=True)
class Preset:
    name: str
    sf: int
    bw_khz: int
    cr: int = 5
    preamble: int = 8
    crc_on: bool = True
    invert_iq: bool = False     # LoRaWAN downlinks (and any RX of a gateway-side signal) use inverted IQ
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

_LW_UP125 = tuple(902_300_000 + 200_000 * k for k in range(64))
_LW_UP500 = tuple(903_000_000 + 1_600_000 * k for k in range(8))
_LW_DOWN = tuple(923_300_000 + 600_000 * k for k in range(8))
LORAWAN_US915 = Network("lorawan-us915", 0x34, (
    # RP002 US915 uplinks DR0-DR3 = SF10/9/8/7 @125 kHz on the 64-channel raster, DR4 = SF8 @500 kHz (#4)
    Preset("uplink-dr0", 10, 125, 5, freqs_hz=_LW_UP125), Preset("uplink-dr1", 9, 125, 5, freqs_hz=_LW_UP125),
    Preset("uplink-dr2", 8, 125, 5, freqs_hz=_LW_UP125), Preset("uplink-dr3", 7, 125, 5, freqs_hz=_LW_UP125),
    Preset("uplink-dr4", 8, 500, 5, freqs_hz=_LW_UP500),
    # downlinks DR8-DR13 = SF12..SF7 @500 kHz: inverted IQ, no PHY CRC (counted on RxDone + valid header)
    Preset("downlink-dr8", 12, 500, 5, crc_on=False, invert_iq=True, freqs_hz=_LW_DOWN), Preset("downlink-dr9", 11, 500, 5, crc_on=False, invert_iq=True, freqs_hz=_LW_DOWN),
    Preset("downlink-dr10", 10, 500, 5, crc_on=False, invert_iq=True, freqs_hz=_LW_DOWN), Preset("downlink-dr11", 9, 500, 5, crc_on=False, invert_iq=True, freqs_hz=_LW_DOWN),
    Preset("downlink-dr12", 8, 500, 5, crc_on=False, invert_iq=True, freqs_hz=_LW_DOWN), Preset("downlink-dr13", 7, 500, 5, crc_on=False, invert_iq=True, freqs_hz=_LW_DOWN),
), "uplinks DR0-DR3 (SF10-SF7 @125) + DR4 (SF8 @500); downlinks DR8-DR13 (SF12-SF7 @500, inverted IQ, no CRC)")

MESHCORE = Network("meshcore", 0x12, (
    Preset("us-narrow", 7, 62, 5, preamble=32, freqs_hz=(910_525_000,)),
    Preset("us-legacy", 11, 250, 5, preamble=16, freqs_hz=(910_525_000,)),
), "sync word 0x12: assumed from the RadioLib default and confirmed on the bench 2026-09-14 (a CRC-valid 74 B us-narrow frame at -33 dBm)")

LOOMWAVE = Network("loomwave", 0x12, (
    Preset("fleet", 9, 125, 5, preamble=16, freqs_hz=(911_500_000,)),
    Preset("bench-cell", 9, 125, 5, preamble=16, freqs_hz=(905_000_000,)),
), "private sync 0x12")

BUILTIN_NETWORKS: list[Network] = [MESHTASTIC, LORAWAN_US915, MESHCORE, LOOMWAVE]
NETWORKS: list[Network] = list(BUILTIN_NETWORKS)      # the active table; set_networks() replaces it (user table, #2 item 4)
def user_network_paths() -> list:
    """<data dir>/networks.yaml (-d/--data-dir), then ~/.config/lorascan/networks.yaml, then /etc."""
    from . import paths
    return paths.networks_paths()


def __getattr__(name):
    """USER_NETWORK_PATHS used to be an import-time constant; -d is applied after import (#22)."""
    if name == "USER_NETWORK_PATHS":
        return user_network_paths()
    raise AttributeError(name)


def set_networks(nets: list) -> None:
    NETWORKS[:] = list(nets)


def _int(v) -> int:
    return int(v, 0) if isinstance(v, str) else int(v)


def load_user_networks(path: str) -> list[Network]:
    """User network table (Loomwave/lorascan#2 item 4). One preset per line:
        network/preset: {sync: 0x12, sf: 9, bw: 125, cr: 5, preamble: 16, crc: true, iq: false, freqs: 905.0 906.5}
    freqs are MHz, space separated; cr/preamble/crc/iq default to 5/8/true/false. A .json file with the
    same keys ({"network/preset": {...}}) is accepted too. Presets of one network share its sync word."""
    if path.endswith(".json"):
        with open(path) as f:
            d = json.load(f)
    else:
        with open(path) as f:
            d = parse_mini_yaml(f.read())
    nets: dict[str, dict] = {}
    for key, v in d.items():
        if "/" not in key or not isinstance(v, dict):
            raise ValueError(f"{path}: expected 'network/preset: {{...}}', got {key!r}")
        nname, pname = key.split("/", 1)
        sync = _int(v.get("sync", 0x12))
        freqs = v.get("freqs", "")
        freqs = [freqs] if isinstance(freqs, (int, float)) else [float(x) for x in str(freqs).split()]
        p = Preset(pname.strip(), _int(v["sf"]), _int(v["bw"]), _int(v.get("cr", 5)), preamble=_int(v.get("preamble", 8)),
                   crc_on=str(v.get("crc", "true")).lower() not in ("false", "0", "no"), invert_iq=str(v.get("iq", "false")).lower() in ("true", "1", "yes"),
                   freqs_hz=tuple(int(round(f * 1e6)) for f in freqs))
        n = nets.setdefault(nname.strip(), {"sync": sync, "presets": []})
        if n["sync"] != sync:
            raise ValueError(f"{path}: network {nname} has two sync words")
        n["presets"].append(p)
    return [Network(name, n["sync"], tuple(n["presets"]), f"user table {path}") for name, n in nets.items()]


def merge_networks(builtin: list, user: list) -> list:
    """User networks are appended; a user preset with a built-in (network, preset) name REPLACES it."""
    out = []
    user_by = {u.name: u for u in user}
    for b in builtin:
        if b.name in user_by:
            u = user_by[b.name]
            ups = {p.name: p for p in u.presets}
            presets = tuple(ups.pop(p.name, p) for p in b.presets) + tuple(ups.values())
            out.append(Network(b.name, u.sync_word, presets, b.note + "; " + u.note))
        else:
            out.append(b)
    out += [u for u in user if u.name not in {b.name for b in builtin}]
    return out


def load_default_user_networks() -> list[Network]:
    for p in user_network_paths():
        if os.path.exists(p):
            return load_user_networks(p)
    return []


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
                if 0 <= k < meshtastic_num_slots(p.slot_bw_hz) and abs(meshtastic_slot_freq_hz(k + 1, p.slot_bw_hz) - freq_hz) <= tol_hz:
                    out.append((n.name, p))
            elif any(abs(f - freq_hz) <= tol_hz for f in p.freqs_hz):
                out.append((n.name, p))
    return out
