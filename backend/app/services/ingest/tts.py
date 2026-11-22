"""Draft narration from the script (AI voice) — for testing a script before recording it.

The result is stored as the project's narration `audio` asset and then goes through the normal
ingest pipeline (cleanup → transcription → cuts → final transcript).
"""

import logging
import re
import tempfile
import uuid
from pathlib import Path

from app.core.db import get_sessionmaker
from app.llm import client as llm
from app.models import AssetKind, Project
from app.services.ingest import media
from app.services.ingest.pipeline import latest_asset
from app.services.ingest.uploads import finalize
from app.services.storage import get_storage

log = logging.getLogger(__name__)

VOICES = ["Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]
MAX_CHUNK = 2500


def script_to_speech_text(md: str) -> list[str]:
    """Markdown → plain paragraphs suitable for narration."""
    md = re.sub(r"```.*?```", "", md, flags=re.S)  # code blocks aren't read aloud
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)  # images
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)  # links → text
    md = re.sub(r"`([^`]+)`", r"\1", md)
    md = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", md)
    paras = []
    for block in re.split(r"\n\s*\n", md):
        lines = [re.sub(r"^\s*(#+|[-*+]|\d+[.)]|>)\s*", "", ln).strip() for ln in block.splitlines()]
        text = " ".join(ln for ln in lines if ln)
        if not text or re.fullmatch(r"\(?\[?(b-roll|broll|visual|note)[^)\]]*[)\]]?", text, re.I):
            continue
        if lines and block.lstrip().startswith("#") and not text.endswith((".", "!", "?", ":")):
            text += "."
        paras.append(text)
    chunks: list[str] = []
    for ptext in paras:
        if chunks and len(chunks[-1]) + len(ptext) + 1 <= MAX_CHUNK:
            chunks[-1] += "\n\n" + ptext
        else:
            while len(ptext) > MAX_CHUNK:
                cut = ptext.rfind(". ", 0, MAX_CHUNK) + 1 or MAX_CHUNK
                chunks.append(ptext[:cut].strip())
                ptext = ptext[cut:].strip()
            chunks.append(ptext)
    return [c for c in chunks if c]


def run_tts(project_id: str, voice: str) -> None:
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        try:
            script = latest_asset(db, pid, AssetKind.script)
            if script is None:
                raise RuntimeError("upload the script first")
            text = get_storage().path(script.storage_key).read_text("utf-8-sig")
            chunks = script_to_speech_text(text)
            if not chunks:
                raise RuntimeError("the script has no narratable text")
            root = get_storage().root / "tmp"
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as wd:
                work = Path(wd)
                parts = []
                for i, chunk in enumerate(chunks):
                    audio, _ = llm.speech("tts", chunk, voice)
                    raw = work / f"c{i}.bin"
                    raw.write_bytes(audio)
                    wav = work / f"c{i}.wav"
                    media._run(["ffmpeg", "-nostdin", "-y", "-i", str(raw), "-ar", "48000", "-ac", "1",
                                "-af", "apad=pad_dur=0.35", str(wav)])  # fmt: skip
                    parts.append(wav)
                (work / "list.txt").write_text("\n".join(f"file '{x.name}'" for x in parts))
                out = work / "draft-narration.wav"
                media._run(["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                            "-c", "copy", str(out)])  # fmt: skip
                asset = finalize(db, p, AssetKind.audio, out, f"draft-narration-{voice.lower()}.wav")
                asset.meta = {**asset.meta, "tts_voice": voice, "tts": True}
            p.ingest_step, p.error = None, None
            db.commit()
        except Exception as e:  # noqa: BLE001
            log.exception("tts failed")
            db.rollback()
            p = db.get(Project, pid)
            p.ingest_step = None
            p.error = f"Draft narration failed: {type(e).__name__}: {e}"[:2000]
            db.commit()
