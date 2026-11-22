"""Filler + silence detection over word timestamps, and cut → keep-segment math. Pure functions."""

import re
from dataclasses import asdict, dataclass

MIN_CUT_MS = 80

_PUNCT = re.compile(r"[^\w']+", re.UNICODE)


@dataclass
class Cut:
    id: str
    start_ms: int
    end_ms: int
    kind: str  # "filler" | "silence"
    text: str
    enabled: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(word: str) -> str:
    return _PUNCT.sub("", word.lower())


def is_filler(words: list[dict], i: int, fillers: set[str]) -> bool:
    """`like` is ambiguous ("I like Kafka"); count it only when set off by punctuation (Whisper
    writes "like," for the filler use) or when it's the first/last word."""
    w = words[i]["w"]
    n = _norm(w)
    if n not in fillers:
        return False
    if n != "like":
        return True
    prev = words[i - 1]["w"] if i > 0 else ""
    return w.rstrip().endswith((",", "...", "…")) or prev.rstrip().endswith(",") or i == 0


def detect_cuts(
    words: list[dict],
    duration_ms: int,
    fillers: list[str],
    silence_ms: int = 600,
    keep_pause_ms: int = 150,
) -> list[Cut]:
    """Words: [{"w", "start_ms", "end_ms"}] sorted by time.

    For every gap between two consecutive *speech* (non-filler) words:
      - if it contains fillers → cut the fillers plus surrounding dead air, keeping up to
        `keep_pause_ms` of natural pause on each side;
      - elif it is longer than `silence_ms` → shorten it to 2 × keep_pause_ms.
    Leading/trailing silence longer than `silence_ms` is trimmed too.
    """
    fset = {f.lower() for f in fillers}
    speech: list[dict] = []
    pending: list[dict] = []  # fillers since the last speech word
    groups: list[tuple[dict | None, list[dict], dict | None]] = []
    for i, w in enumerate(words):
        if is_filler(words, i, fset):
            pending.append(w)
            continue
        groups.append((speech[-1] if speech else None, pending, w))
        pending = []
        speech.append(w)
    groups.append((speech[-1] if speech else None, pending, None))

    cuts: list[Cut] = []
    for prev, fill, nxt in groups:
        a = prev["end_ms"] if prev else 0
        b = nxt["start_ms"] if nxt else duration_ms
        if b <= a:
            continue
        if fill:
            # keep a natural pause next to real speech, never at the timeline edges
            keep_a = keep_pause_ms if prev else 0
            keep_b = keep_pause_ms if nxt else 0
            start = max(a, min(fill[0]["start_ms"], a + keep_a))
            end = min(b, max(fill[-1]["end_ms"], b - keep_b))
            kind, text = "filler", " ".join(f["w"].strip() for f in fill)
        elif b - a > silence_ms:
            start = a + (keep_pause_ms if prev else 0)
            end = b - (keep_pause_ms if nxt else 0)
            kind, text = "silence", f"{(b - a) / 1000:.1f}s pause"
        else:
            continue
        if end - start >= MIN_CUT_MS:
            cuts.append(
                Cut(id=f"c{len(cuts) + 1}", start_ms=int(start), end_ms=int(end), kind=kind, text=text)
            )
    return cuts


def snap_ms(ms: int, fps: int) -> int:
    """Snap to the video frame grid so audio and face-cam cuts stay in sync (no drift)."""
    frame = 1000 / fps
    return int(round(round(ms / frame) * frame))


def keep_segments(cuts: list[dict], duration_ms: int, fps: int) -> list[tuple[int, int]]:
    """Complement of the enabled cuts, frame-snapped, merged, within [0, duration]."""
    enabled = sorted(
        ((snap_ms(c["start_ms"], fps), snap_ms(c["end_ms"], fps)) for c in cuts if c.get("enabled", True)),
    )
    merged: list[list[int]] = []
    for s, e in enabled:
        s, e = max(0, s), min(duration_ms, e)
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    keeps: list[tuple[int, int]] = []
    cursor = 0
    for s, e in merged:
        if s > cursor:
            keeps.append((cursor, s))
        cursor = e
    if cursor < duration_ms:
        keeps.append((cursor, duration_ms))
    return [(s, e) for s, e in keeps if e - s > 0]


def removed_ms(cuts: list[dict], duration_ms: int, fps: int) -> int:
    return duration_ms - sum(e - s for s, e in keep_segments(cuts, duration_ms, fps))
