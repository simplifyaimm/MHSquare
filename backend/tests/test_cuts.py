from app.services.ingest.cuts import detect_cuts, keep_segments, removed_ms, snap_ms
from app.services.ingest.transcribe import fill_missing_times

FILLERS = ["um", "uh", "like"]


def W(w, s, e):
    return {"w": w, "start_ms": s, "end_ms": e}


def test_filler_with_tight_gaps_cuts_exactly_the_filler():
    words = [W("so", 0, 1000), W("um", 1050, 1300), W("the", 1350, 1600)]
    cuts = detect_cuts(words, 1600, FILLERS, silence_ms=600, keep_pause_ms=150)
    assert [(c.start_ms, c.end_ms, c.kind, c.text) for c in cuts] == [(1050, 1300, "filler", "um")]


def test_filler_in_long_pause_keeps_natural_pause_each_side():
    words = [W("so", 0, 1000), W("um", 2000, 2300), W("the", 3500, 3800)]
    (c,) = detect_cuts(words, 3800, FILLERS, keep_pause_ms=150)
    assert (c.start_ms, c.end_ms, c.kind) == (1150, 3350, "filler")


def test_consecutive_fillers_become_one_cut():
    words = [W("so", 0, 500), W("um,", 600, 800), W("uh", 850, 1000), W("yes", 1100, 1400)]
    (c,) = detect_cuts(words, 1400, FILLERS)
    assert c.text == "um, uh" and (c.start_ms, c.end_ms) == (600, 1000)


def test_long_silence_is_shortened_short_pause_kept():
    words = [W("a", 0, 500), W("b", 900, 1200), W("c", 3000, 3500)]
    cuts = detect_cuts(words, 3500, FILLERS, silence_ms=600, keep_pause_ms=150)
    assert [(c.start_ms, c.end_ms, c.kind) for c in cuts] == [(1350, 2850, "silence")]


def test_leading_and_trailing_silence_trimmed_without_padding():
    words = [W("hi", 2000, 2500)]
    cuts = detect_cuts(words, 6000, FILLERS, keep_pause_ms=150)
    assert [(c.start_ms, c.end_ms) for c in cuts] == [(0, 1850), (2650, 6000)]


def test_like_is_only_a_filler_when_set_off():
    meaningful = [W("I", 0, 200), W("like", 250, 500), W("Kafka", 550, 900)]
    assert detect_cuts(meaningful, 900, FILLERS) == []
    filler = [W("It's,", 0, 300), W("like,", 350, 600), W("fast", 650, 900)]
    (c,) = detect_cuts(filler, 900, FILLERS)
    assert c.text == "like,"


def test_keep_segments_complement_and_disabled_cuts():
    cuts = [
        {"start_ms": 1000, "end_ms": 2000, "enabled": True},
        {"start_ms": 3000, "end_ms": 3500, "enabled": False},
        {"start_ms": 1900, "end_ms": 2500, "enabled": True},  # overlaps → merged
    ]
    fps = 1000  # 1 ms frames: no snapping effect
    assert keep_segments(cuts, 5000, fps) == [(0, 1000), (2500, 5000)]
    assert removed_ms(cuts, 5000, fps) == 1500


def test_cut_boundaries_snap_to_frame_grid():
    assert snap_ms(1010, 30) == 1000
    assert snap_ms(1020, 30) == 1033
    keeps = keep_segments([{"start_ms": 1010, "end_ms": 2020, "enabled": True}], 3000, 30)
    assert keeps == [(0, 1000), (2033, 3000)]


def test_fill_missing_times_interpolates():
    words = [W("a", 0, 100), {"w": "42", "start_ms": None, "end_ms": None}, W("b", 300, 400)]
    out = fill_missing_times(words)
    assert out[1]["start_ms"] == 100 and out[1]["end_ms"] == 300
