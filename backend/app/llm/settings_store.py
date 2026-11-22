"""Read/write local/llmconfig.env — provider keys, model IDs per alias, and the mhsquare virtual key.

The file is gitignored and edited from the UI. Secrets are WRITE-ONLY through the API: reads
return only `configured` + a masked hint. Writes are atomic (tmp file + rename in the same dir).
LiteLLM's wrapper entrypoint watches this file and restarts the proxy on change.
"""

import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    env_var: str
    prefix: str  # LiteLLM model prefix
    docs: str


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "anthropic",
        "Anthropic",
        "ANTHROPIC_API_KEY",
        "anthropic/",
        "https://console.anthropic.com/settings/keys",
    ),
    Provider(
        "gemini",
        "Google Gemini (AI Studio)",
        "GEMINI_API_KEY",
        "gemini/",
        "https://aistudio.google.com/apikey",
    ),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "openai/", "https://platform.openai.com/api-keys"),
    Provider("openrouter", "OpenRouter", "OPENROUTER_API_KEY", "openrouter/", "https://openrouter.ai/keys"),
)
PROVIDERS_BY_ID = {p.id: p for p in PROVIDERS}


@dataclass(frozen=True)
class ModelSlot:
    alias: str
    env_var: str
    label: str
    kind: str  # llm | image | video
    suggestions: tuple[str, ...]


MODEL_SLOTS: tuple[ModelSlot, ...] = (
    ModelSlot(
        "planner-llm",
        "MH_MODEL_PLANNER_LLM",
        "Shot planner",
        "llm",
        ("anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "gemini/gemini-2.5-pro"),
    ),
    ModelSlot(
        "code-llm",
        "MH_MODEL_CODE_LLM",
        "Manim code writer",
        "llm",
        ("anthropic/claude-sonnet-5-5", "anthropic/claude-opus-5-5", "gemini/gemini-2.5-pro"),
    ),
    ModelSlot(
        "code-llm-backup",
        "MH_MODEL_CODE_LLM_BACKUP",
        "Code writer fallback",
        "llm",
        ("anthropic/claude-haiku-5-5", "gemini/gemini-2.5-flash"),
    ),
    ModelSlot(
        "image-gen",
        "MH_MODEL_IMAGE_GEN",
        "Images & keyframes",
        "image",
        ("gemini/imagen-4.0-generate-001", "openai/gpt-image-1"),
    ),
    ModelSlot(
        "video-gen",
        "MH_MODEL_VIDEO_GEN",
        "AI video clips",
        "video",
        ("gemini/veo-3.1-generate-preview",),
    ),
    ModelSlot(
        "video-gen-fast",
        "MH_MODEL_VIDEO_GEN_FAST",
        "Video fallback",
        "video",
        ("gemini/veo-3.1-fast-generate-preview",),
    ),
    ModelSlot(
        "tts",
        "MH_MODEL_TTS",
        "Draft narration voice",
        "tts",
        ("gemini/gemini-2.5-flash-preview-tts", "openai/gpt-4o-mini-tts"),
    ),
    ModelSlot(
        "embed",
        "MH_MODEL_EMBED",
        "Topic similarity (embeddings)",
        "embed",
        ("gemini/gemini-embedding-001", "openai/text-embedding-3-small"),
    ),
)
SLOTS_BY_ALIAS = {s.alias: s for s in MODEL_SLOTS}

VIRTUAL_KEY_VAR = "MHSQUARE_LITELLM_API_KEY"
SECRET_VARS = {p.env_var for p in PROVIDERS} | {VIRTUAL_KEY_VAR}
ALLOWED_VARS = SECRET_VARS | {s.env_var for s in MODEL_SLOTS}

_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
_SECRET_RE = re.compile(r"^[\x21-\x7e]{8,512}$")  # printable ASCII, no spaces/newlines
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{1,199}$")
_lock = threading.Lock()


class LLMConfigError(ValueError):
    pass


def _path() -> Path:
    return Path(get_settings().llmconfig_path)


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        m = _LINE.match(raw.strip())
        if m and m.group(2):
            out[m.group(1)] = m.group(2)
    return out


def read_defaults() -> dict[str, str]:
    p = Path(get_settings().llm_defaults_path)
    return _parse(p.read_text("utf-8")) if p.exists() else {}


def read_raw() -> dict[str, str]:
    p = _path()
    return _parse(p.read_text("utf-8")) if p.exists() else {}


def validate_secret(value: str) -> str:
    value = value.strip()
    if not _SECRET_RE.match(value):
        raise LLMConfigError("Key must be 8–512 printable characters with no spaces")
    return value


def validate_model(value: str) -> str:
    value = value.strip()
    if not _MODEL_RE.match(value) or "/" not in value:
        raise LLMConfigError(f"Invalid model id {value!r}; expected provider/model, e.g. anthropic/claude-…")
    return value


def update(changes: dict[str, str | None]) -> None:
    """Apply changes atomically. None (or "") removes a variable. Values must be pre-validated."""
    for k in changes:
        if k not in ALLOWED_VARS:
            raise LLMConfigError(f"Unknown setting {k}")
    with _lock:
        current = read_raw()
        for k, v in changes.items():
            if v:
                current[k] = v
            else:
                current.pop(k, None)
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        body = (
            "# mhsquare LLM config — written by the UI (Settings → AI providers). NEVER COMMIT.\n"
            + "".join(f"{k}={current[k]}\n" for k in sorted(current))
        )
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".llmconfig.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"…{value[-4:]}" if len(value) > 12 else "…"


def effective_models() -> dict[str, str]:
    """alias -> model id (llmconfig overrides defaults)."""
    merged = read_defaults() | read_raw()
    return {s.alias: merged.get(s.env_var, "") for s in MODEL_SLOTS}


def virtual_key() -> str | None:
    """The mhsquare LiteLLM virtual key: llmconfig.env first, then env (MHSQUARE_LITELLM_API_KEY)."""
    v = read_raw().get(VIRTUAL_KEY_VAR) or get_settings().litellm_api_key
    if v and v.startswith("sk-change-me"):
        return None
    return v
