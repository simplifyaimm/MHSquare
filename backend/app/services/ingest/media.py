"""ffmpeg/ffprobe helpers: probing, content sniffing, audio cleanup, applying cuts, waveform peaks."""

import array
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT_S = 60 * 60


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = FFMPEG_TIMEOUT_S) -> subprocess.CompletedProcess:
    log.info("exec %s", " ".join(cmd[:6]) + (" …" if len(cmd) > 6 else ""))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{cmd[0]} timed out") from e
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-1500:]
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}): {tail}")
    return proc


# ---------------------------------------------------------------- probing / sniffing


def ffprobe(path: Path) -> dict:
    out = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=120,
    ).stdout
    return json.loads(out)


def media_meta(path: Path) -> dict:
    info = ffprobe(path)
    streams = info.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    meta: dict = {"duration_ms": int(float(info.get("format", {}).get("duration") or 0) * 1000)}
    if audio:
        meta |= {
            "has_audio": True,
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": audio.get("channels"),
        }
    if video:
        num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        meta |= {
            "has_video": True,
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": round(fps, 3),
        }
    return meta


# magic-byte sniffing: (kind -> list of (mime, predicate))
def sniff(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        return "video/mp4"  # isom/mp42/… — audio-only m4a also lands here; ffprobe decides
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------------------------------------------------------------- cleanup (pluggable)


class AudioCleaner(Protocol):
    name: str

    def clean(self, src: Path, dst: Path) -> None: ...


class FfmpegCleaner:
    """High-pass + loudness normalisation to −14 LUFS integrated / −1.5 dBTP (YouTube). 48 kHz mono."""

    name = "ffmpeg"

    def __init__(self, highpass_hz: int = 80, lufs: float = -14.0, true_peak: float = -1.5):
        self.highpass_hz, self.lufs, self.true_peak = highpass_hz, lufs, true_peak

    TOLERANCE_LU = 0.5
    MAX_PASSES = 4
    MAX_GAIN_DB = 30.0

    def clean(self, src: Path, dst: Path) -> None:
        """Speech has a high crest factor: reaching −14 LUFS needs more gain than the peak ceiling
        allows, and ffmpeg's loudnorm then silently falls back to dynamic mode and undershoots.
        Instead: gain → 4×-oversampled peak limiter at the true-peak ceiling, measure, and correct
        the pre-limiter gain until the integrated loudness is within ±0.5 LU of the target."""
        hp = f"highpass=f={self.highpass_hz}"
        ceiling = 10 ** (self.true_peak / 20)
        with tempfile.TemporaryDirectory() as td:
            filtered = Path(td) / "hp.wav"
            _run(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn", "-af", hp,
                  "-ar", "48000", "-ac", "1", "-c:a", "pcm_f32le", str(filtered)])  # fmt: skip
            gain = self.lufs - integrated_lufs(filtered)
            prev: tuple[float, float] | None = None  # (gain, loudness) of the previous pass
            error = 0.0
            for _ in range(self.MAX_PASSES):
                gain = max(-self.MAX_GAIN_DB, min(self.MAX_GAIN_DB, gain))
                af = (
                    f"volume={gain:.2f}dB,aresample=192000,"
                    f"alimiter=limit={ceiling:.4f}:attack=2:release=60:level=disabled,aresample=48000"
                )
                _run(["ffmpeg", "-nostdin", "-y", "-i", str(filtered), "-af", af,
                      "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])  # fmt: skip
                loudness = integrated_lufs(dst)
                error = self.lufs - loudness
                if abs(error) <= self.TOLERANCE_LU:
                    return
                # secant step: the limiter makes loudness grow slower than gain
                slope = 1.0
                if prev and gain != prev[0]:
                    slope = max(0.1, (loudness - prev[1]) / (gain - prev[0]))
                prev = (gain, loudness)
                gain += error / slope
            log.warning("loudness target missed by %.1f LU after %d passes", error, self.MAX_PASSES)


def integrated_lufs(path: Path) -> float:
    err = _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"]
    ).stderr
    lines = [ln for ln in err.decode(errors="replace").splitlines() if ln.strip().startswith("I:")]
    return float(lines[-1].split()[1])


_CLEANERS: dict[str, type] = {"ffmpeg": FfmpegCleaner}


def get_cleaner(name: str = "ffmpeg") -> AudioCleaner:
    """Register an external enhancer by adding it to _CLEANERS."""
    return _CLEANERS[name]()


# ---------------------------------------------------------------- apply cuts


def _audio_concat_filter(keeps: list[tuple[int, int]], fade_s: float = 0.004) -> str:
    """Trim each kept segment with a tiny fade in/out (avoids clicks at cut points), then concat."""
    parts, labels = [], []
    for i, (s, e) in enumerate(keeps):
        dur = (e - s) / 1000
        f = min(fade_s, dur / 4)
        parts.append(
            f"[0:a]atrim=start={s / 1000:.4f}:end={e / 1000:.4f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={f:.4f},afade=t=out:st={max(0.0, dur - f):.4f}:d={f:.4f}[a{i}]"
        )
        labels.append(f"[a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(keeps)}:v=0:a=1[out]")
    return ";".join(parts)


def cut_audio(src: Path, dst: Path, keeps: list[tuple[int, int]]) -> None:
    if not keeps:
        raise MediaError("nothing left after cuts")
    _run(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-filter_complex", _audio_concat_filter(keeps),
          "-map", "[out]", "-c:a", "pcm_s16le", str(dst)])  # fmt: skip


def cut_video(src: Path, dst: Path, keeps: list[tuple[int, int]], offset_ms: int, fps: int) -> None:
    """Face-cam: same keep segments (shifted by the face-cam offset), conformed to CFR `fps`.

    One high-quality intermediate encode (CRF 16); the final render happens once in phase 7.
    """
    parts, labels = [], []
    for i, (s, e) in enumerate(keeps):
        s2, e2 = max(0, s + offset_ms) / 1000, max(0, e + offset_ms) / 1000
        parts.append(f"[0:v]trim=start={s2:.4f}:end={e2:.4f},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(keeps)}:v=1:a=0,fps={fps},format=yuv420p[out]")
    _run(["ffmpeg", "-nostdin", "-y", "-i", str(src), "-filter_complex", ";".join(parts), "-map", "[out]",
          "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-movflags", "+faststart", str(dst)])  # fmt: skip


# ---------------------------------------------------------------- waveform


def waveform_peaks(src: Path, peaks_per_second: int = 50) -> dict:
    """Peak amplitude (0..1) per bucket, from 8 kHz mono PCM."""
    rate = 8000
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(rate),
                "-f", "s16le", "-"]).stdout  # fmt: skip
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    bucket = rate // peaks_per_second
    peaks = []
    for i in range(0, len(samples), bucket):
        chunk = samples[i : i + bucket]
        peaks.append(round(max(max(chunk), -min(chunk)) / 32768, 3) if chunk else 0.0)
    return {"peaks_per_second": peaks_per_second, "duration_ms": len(samples) * 1000 // rate, "peaks": peaks}
