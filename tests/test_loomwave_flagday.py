"""The Loomwave fleet moved SF9/BW125 -> SF11/BW500 on 2026-09-24 (the #451 flag day).

A scan at the fleet frequency must now try BOTH:
  - the fleet's CURRENT PHY, SF11/BW500, or lorascan silently stops naming Loomwave traffic;
  - the RETIRED PHY, SF9/BW125, kept as an explicit legacy preset, because boards flashed before
    the flag day keep transmitting it until reflashed — finding them is exactly what an operator
    wants during the cutover.
"""
from lorascan.networks import presets_on

FLEET_HZ = 911_500_000


def _loomwave_at(freq):
    return {p.name: p for n, p in presets_on(freq) if n == "loomwave"}


def test_fleet_preset_is_the_post_flagday_phy():
    fleet = _loomwave_at(FLEET_HZ)["fleet"]
    assert (fleet.sf, fleet.bw_khz, fleet.cr, fleet.preamble) == (11, 500, 5, 16)


def test_the_retired_phy_is_still_decodable_as_an_explicit_legacy_preset():
    legacy = _loomwave_at(FLEET_HZ)["fleet-pre451"]
    assert (legacy.sf, legacy.bw_khz, legacy.cr, legacy.preamble) == (9, 125, 5, 16)


def test_a_fleet_frequency_scan_tries_both_phys():
    phys = {(p.sf, p.bw_khz) for p in _loomwave_at(FLEET_HZ).values()}
    assert {(11, 500), (9, 125)} <= phys
