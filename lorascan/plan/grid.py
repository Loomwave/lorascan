"""Frequency grids and the known-user channel table (spec §3.3). KNOWN_CHANNELS = the 14 channels
of the 2026-08-01 KD4HME survey with the labels the Loomwave firmware carries (BANDSCAN_LBL)."""
from __future__ import annotations

BAND_START_HZ = 902_000_000
BAND_STOP_HZ = 928_000_000
DEFAULT_STEP_HZ = 200_000

KNOWN_CHANNELS: list[tuple[int, str]] = [
    (902_500_000, "Loomwave edge"), (903_000_000, "Mesh LongSlow"), (906_875_000, "Mesh LongFast"),
    (908_000_000, "Mesh LongMod"), (910_525_000, "MeshCore"), (911_500_000, "Loomwave fleet"),
    (912_875_000, "Mesh MedSlow"), (913_125_000, "Mesh MedFast"), (915_000_000, "ISM/LoRaWAN ctr"),
    (917_000_000, "cand 917"), (919_000_000, "cand 919"), (921_000_000, "cand 921"),
    (923_000_000, "LoRaWAN-DL"), (925_000_000, "upper"),
]
_LABELS = dict(KNOWN_CHANNELS)


def band_grid(start_hz: int = BAND_START_HZ, stop_hz: int = BAND_STOP_HZ, step_hz: int = DEFAULT_STEP_HZ) -> list[int]:
    """Channel centres from start (inclusive) to stop (exclusive) every step_hz."""
    if step_hz <= 0 or stop_hz <= start_hz:
        raise ValueError("band_grid: need stop > start and step > 0")
    return list(range(start_hz, stop_hz, step_hz))


def label_for(freq_hz: int) -> str:
    return _LABELS.get(freq_hz, "")
