"""Transcription with word-level timestamps.

WhisperX (faster-whisper + wav2vec2 forced alignment) runs only in the `transcriber` container
(heavy ML deps). Tests and dev without ML use a fake via MHSQUARE_TRANSCRIBER=fake.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)

# Whisper tends to drop disfluencies. A prompt containing fillers makes it transcribe them.
FILLER_PROMPT = "Umm, let me think like, hmm... Okay, uh, here's what I'm, like, thinking."


@dataclass
class TranscriptResult:
    words: list[dict]  # {"w", "start_ms", "end_ms", "score"}
    language: str | None = None
    text: str = ""
    meta: dict = field(default_factory=dict)


class Transcriber(Protocol):
    def transcribe(self, audio: Path, language: str | None = None) -> TranscriptResult: ...


def fill_missing_times(words: list[dict]) -> list[dict]:
    """Aligners leave some tokens (numbers, symbols) without times: interpolate from neighbours."""
    n = len(words)
    for i, w in enumerate(words):
        if w.get("start_ms") is None:
            prev_end = next(
                (words[j]["end_ms"] for j in range(i - 1, -1, -1) if words[j].get("end_ms") is not None), 0
            )
            nxt = next(
                (words[j]["start_ms"] for j in range(i + 1, n) if words[j].get("start_ms") is not None), None
            )
            w["start_ms"] = prev_end
            w["end_ms"] = nxt if nxt is not None and nxt >= prev_end else prev_end + 200
        elif w.get("end_ms") is None:
            w["end_ms"] = w["start_ms"] + 200
    return words


class WhisperXTranscriber:
    def __init__(self, model: str, device: str = "cpu", compute_type: str = "int8", batch_size: int = 8):
        self.model_name, self.device, self.compute_type, self.batch_size = (
            model,
            device,
            compute_type,
            batch_size,
        )

    @lru_cache(maxsize=1)  # noqa: B019 - one model per process is intended
    def _asr(self, language: str | None):
        import whisperx

        return whisperx.load_model(
            self.model_name,
            self.device,
            compute_type=self.compute_type,
            language=language,
            asr_options={"initial_prompt": FILLER_PROMPT, "suppress_tokens": []},
        )

    def transcribe(self, audio: Path, language: str | None = None) -> TranscriptResult:
        import whisperx

        wav = whisperx.load_audio(str(audio))
        result = self._asr(language).transcribe(wav, batch_size=self.batch_size, language=language)
        lang = result.get("language") or language
        align_model, align_meta = whisperx.load_align_model(language_code=lang, device=self.device)
        aligned = whisperx.align(
            result["segments"], align_model, align_meta, wav, self.device, return_char_alignments=False
        )
        words = []
        for seg in aligned.get("segments", []):
            for w in seg.get("words", []):
                text = (w.get("word") or "").strip()
                if not text:
                    continue
                words.append({
                    "w": text,
                    "start_ms": int(w["start"] * 1000) if w.get("start") is not None else None,
                    "end_ms": int(w["end"] * 1000) if w.get("end") is not None else None,
                    "score": round(float(w.get("score") or 0), 3),
                })  # fmt: skip
        words = fill_missing_times(words)
        return TranscriptResult(
            words=words,
            language=lang,
            text=" ".join(w["w"] for w in words),
            meta={"engine": "whisperx", "model": self.model_name},
        )


class FakeTranscriber:
    """Deterministic: returns `FakeTranscriber.script` words spread over the audio (tests/dev)."""

    script: list[tuple[str, int, int]] = []  # (word, start_ms, end_ms) — set by tests

    def transcribe(self, audio: Path, language: str | None = None) -> TranscriptResult:
        words = [{"w": w, "start_ms": s, "end_ms": e, "score": 1.0} for w, s, e in self.script]
        return TranscriptResult(words=words, language=language or "en", text=" ".join(w for w, _, _ in self.script),
                                meta={"engine": "fake"})  # fmt: skip


def get_transcriber() -> Transcriber:
    s = get_settings()
    if s.transcriber == "fake":
        return FakeTranscriber()
    return _whisperx(s.whisper_model, s.whisper_device, s.whisper_compute_type)


@lru_cache
def _whisperx(model: str, device: str, compute_type: str) -> WhisperXTranscriber:
    return WhisperXTranscriber(model, device, compute_type)
