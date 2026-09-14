import itertools
from lorascan.plan.survey import Step
from lorascan.plan.watch import watch_plan
from lorascan.plan.candidate import candidate_plan, parse_candidates, rank_candidates

def test_watch_plan_cycles_energy_cad_decode_per_frequency():
    steps = list(watch_plan([906_875_000, 921_000_000], dwell_s=2.0, sfs=[7, 11], bws=[125, 250], decode_dwell_s=10.0, cycles=1))
    layers = [(s.freq_hz, s.layer, s.sf, s.bw_khz) for s in steps]
    assert (906_875_000, "energy", 0, 125) in layers
    assert (906_875_000, "cad", 11, 250) in layers and (906_875_000, "cad", 7, 125) in layers
    dec = [s for s in steps if s.layer == "decode"]
    assert any(s.freq_hz == 906_875_000 and s.network == "meshtastic" and s.preset == "LongFast" for s in dec)
    assert not any(s.freq_hz == 921_000_000 and s.layer == "decode" for s in dec)     # nothing known lives on 921.0
    assert all(isinstance(s, Step) for s in steps)
    two = list(itertools.islice(watch_plan([906_875_000], dwell_s=1.0, sfs=[9], bws=[125], cycles=None), 20))
    assert len(two) == 20                                                           # endless when cycles=None

def test_parse_and_plan_candidates():
    c = parse_candidates("905.0/9/125,921.0/11/250/8")
    assert c == [(905_000_000, 9, 125, 5), (921_000_000, 11, 250, 8)]
    steps = list(candidate_plan(c, dwell_s=30.0))
    kinds = {(s.freq_hz, s.layer) for s in steps}
    assert (905_000_000, "energy") in kinds and (905_000_000, "cad") in kinds and (921_000_000, "cad") in kinds
    cad = [s for s in steps if s.layer == "cad" and s.freq_hz == 905_000_000][0]
    assert (cad.sf, cad.bw_khz) == (9, 125)

def test_rank_candidates_prefers_low_busy_low_cad_low_decode():
    rows = [
        {"freq_hz": 905_000_000, "sf": 9, "bw_khz": 125, "busy_mean": 0.02, "cad_hit_rate": 0.01, "decoded": 0, "floor_med": -110.0},
        {"freq_hz": 921_000_000, "sf": 11, "bw_khz": 250, "busy_mean": 0.30, "cad_hit_rate": 0.20, "decoded": 12, "floor_med": -112.0},
    ]
    ranked = rank_candidates(rows)
    assert ranked[0]["freq_hz"] == 905_000_000 and ranked[0]["rank"] == 1 and ranked[1]["rank"] == 2 and "score" in ranked[0]
