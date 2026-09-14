import itertools
from lorascan.plan.grid import band_grid, KNOWN_CHANNELS
from lorascan.plan.quick import quick_plan
from lorascan.plan.survey import survey_plan, Step

def test_quick_plan_step_count_and_known_tail():
    steps = list(quick_plan(band_grid(), passes=2, dwell_s=0.4))
    assert len(steps) == 130 * 2 + 14
    assert steps[0].freq_hz == 902_000_000 and steps[0].dwell_s == 0.4 and steps[0].layer == "energy"
    assert [s.freq_hz for s in steps[-14:]] == [f for f, _ in KNOWN_CHANNELS] and steps[-1].dwell_s == 3.0

def test_survey_revisits_active_channels_more():
    g = band_grid()
    activity = {902_000_000: 1.0}
    steps = list(itertools.islice(survey_plan(g, dwell_s=0.4, revisit_max_s=600, activity=activity), 30))
    assert sum(1 for s in steps if s.freq_hz == 902_000_000) >= 3

def test_survey_never_starves_a_channel_beyond_revisit_max():
    g = band_grid()
    now = [0.0]
    last = {}
    worst = 0.0
    for s in itertools.islice(survey_plan(g, dwell_s=0.4, revisit_max_s=600, activity={g[3]: 5.0}, clock=lambda: now[0]), 6000):
        now[0] += s.dwell_s + 0.05
        if s.freq_hz in last:
            worst = max(worst, now[0] - last[s.freq_hz])
        last[s.freq_hz] = now[0]
    assert len(last) == 130 and worst <= 600 + 60
