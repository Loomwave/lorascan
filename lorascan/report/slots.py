"""N kHz slot view (Loomwave/lorascan#2 item 3): bucket per-channel rows into fixed windows across the
band and report the WORST case per window, so 'which 500 kHz slot is least hit' is one table."""
from __future__ import annotations

BAND_START_HZ = 902_000_000


def slot_view(channels: list, cad: list, decodes: list, slot_hz: int = 500_000, start_hz: int = BAND_START_HZ) -> list:
    """channels/cad/decodes are the report's per-channel dicts (channel_summary, cad_summary, decode_summary
    or the share document's rows). Returns windows sorted best (quietest) first. score = busy_max +
    cad_hit_max + decoded_frames/10, the candidate-card formula applied to the window's worst members."""
    win: dict[int, dict] = {}
    def key(f):
        return start_hz + ((f - start_hz) // slot_hz) * slot_hz
    for c in channels:
        k = key(c["freq_hz"])
        w = win.setdefault(k, {"start_hz": k, "end_hz": k + slot_hz, "n_channels": 0, "floor_worst": None, "floor_best": None, "peak_max": None,
                               "busy_max": 0.0, "busy_sum": 0.0, "cad_hit_max": 0.0, "cad_sf_max": None, "dec": {}, "labels": set()})
        w["n_channels"] += 1
        w["floor_worst"] = c["floor_med"] if w["floor_worst"] is None else max(w["floor_worst"], c["floor_med"])
        w["floor_best"] = c["floor_med"] if w["floor_best"] is None else min(w["floor_best"], c["floor_med"])
        w["peak_max"] = c["peak_max"] if w["peak_max"] is None else max(w["peak_max"], c["peak_max"])
        w["busy_max"] = max(w["busy_max"], c["busy_mean"]); w["busy_sum"] += c["busy_mean"]
        if c.get("label"):
            w["labels"].add(c["label"])
    for x in cad:
        k = key(x["freq_hz"])
        if k in win and x["hit_rate"] >= win[k]["cad_hit_max"]:
            win[k]["cad_hit_max"], win[k]["cad_sf_max"] = x["hit_rate"], x["sf"]
    for d in decodes:
        k = key(d["freq_hz"])
        if k in win and d.get("n_ok"):
            nm = f"{d['network']}/{d['preset']}"
            win[k]["dec"][nm] = win[k]["dec"].get(nm, 0) + d["n_ok"]
    out = []
    for k in sorted(win):
        w = win[k]
        frames = sum(w["dec"].values())
        out.append({"start_hz": w["start_hz"], "end_hz": w["end_hz"], "start_mhz": w["start_hz"] / 1e6, "end_mhz": w["end_hz"] / 1e6,
                    "n_channels": w["n_channels"], "floor_worst": w["floor_worst"], "floor_best": w["floor_best"], "peak_max": w["peak_max"],
                    "busy_max": round(w["busy_max"], 4), "busy_mean": round(w["busy_sum"] / w["n_channels"], 4),
                    "cad_hit_max": round(w["cad_hit_max"], 4), "cad_sf_max": w["cad_sf_max"],
                    "decoded": ", ".join(f"{n}:{c}" for n, c in sorted(w["dec"].items())), "decoded_frames": frames,
                    "labels": ", ".join(sorted(w["labels"])), "score": round(w["busy_max"] + w["cad_hit_max"] + frames / 10, 4)})
    out.sort(key=lambda w: (w["score"], w["peak_max"] if w["peak_max"] is not None else 0, w["floor_worst"] if w["floor_worst"] is not None else 0))
    return out
