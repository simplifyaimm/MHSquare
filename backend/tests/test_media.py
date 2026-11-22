import subprocess
from pathlib import Path

from app.services.ingest import media
from tests.conftest import TMPDIR


def _tone(name: str, volume_db: float, seconds: int = 12) -> Path:
    out = Path(TMPDIR) / name
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",  # noqa: S607
         "-i", f"sine=f=300:d={seconds}", "-af", f"volume={volume_db}dB", str(out)],
        check=True,
    )  # fmt: skip
    return out


def test_cleanup_hits_youtube_loudness_target():
    for vol in (-12, 12):  # lavfi sine is −18 dBFS: ≈ −34 and −10 LUFS inputs → both −14 LUFS
        src = _tone(f"tone{vol}.wav", vol)
        dst = Path(TMPDIR) / f"clean{vol}.wav"
        media.FfmpegCleaner().clean(src, dst)
        assert abs(media.integrated_lufs(dst) - (-14.0)) <= 1.0
        meta = media.media_meta(dst)
        assert meta["sample_rate"] == 48000 and meta["channels"] == 1


def test_sniff():
    assert media.sniff(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert media.sniff(b"ID3\x04") == "audio/mpeg"
    assert media.sniff(b"\x00\x00\x00\x20ftypM4A ") == "audio/mp4"
    assert media.sniff(b"\x00\x00\x00\x20ftypqt  ") == "video/quicktime"
    assert media.sniff(b"\x1a\x45\xdf\xa3") == "video/webm"
    assert media.sniff(b"#!/bin/sh") is None
