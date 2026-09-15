"""Exclusion zones (Loomwave/lorascan#9): frequencies that are measured but never RECOMMENDED — the band
edges and the 33 cm amateur repeater segments. Defaults 902.000–903.250 and 926.750–928.000 MHz; override
with --exclude a-b,c-d (MHz) or switch off with --no-exclude. Zones only affect rankings and shading; every
row is still collected, stored, exported and shared."""
from __future__ import annotations

DEFAULT_EXCLUSIONS: list[tuple[int, int]] = [(902_000_000, 903_250_000), (926_750_000, 928_000_000)]


def parse_exclusions(text: str | None) -> list[tuple[int, int]]:
    """None -> the defaults; '' -> none; '902.0-903.25,926.75-928.0' (MHz) -> zones."""
    if text is None:
        return list(DEFAULT_EXCLUSIONS)
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition("-")
        if not sep:
            raise ValueError(f"--exclude: expected LOW-HIGH in MHz, got {part!r}")
        lo, hi = int(round(float(a) * 1e6)), int(round(float(b) * 1e6))
        if hi <= lo:
            raise ValueError(f"--exclude: {part!r} is empty or reversed")
        out.append((lo, hi))
    return sorted(out)


def excluded(freq_hz: int, zones) -> bool:
    """A channel is excluded when its centre lies inside a zone (lower edge inclusive, upper exclusive)."""
    return any(lo <= freq_hz < hi for lo, hi in (zones or []))


def overlaps(start_hz: int, end_hz: int, zones) -> bool:
    return any(start_hz < hi and end_hz > lo for lo, hi in (zones or []))


def tag_channels(channels: list, zones) -> list:
    for c in channels:
        c["excluded"] = excluded(int(c["freq_hz"]), zones)
    return channels


def zones_mhz(zones) -> list:
    return [[lo / 1e6, hi / 1e6] for lo, hi in (zones or [])]
