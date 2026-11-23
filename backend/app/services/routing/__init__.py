"""visual_type → generator + model aliases. Resolution order (spec §5.3):
per-shot override (UI) → routing.yaml → fallbacks (used by the LLM client on provider errors).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from app.core.config import get_settings

Generator = Literal[
    "html_animation", "html_motion", "manim_code", "video", "image", "template", "passthrough"
]
ALL_TYPES = ["code_animation", "ai_clip", "still_image", "text_card", "a_roll"]


class VisualRoute(BaseModel):
    generator: Generator
    alt_generator: Generator | None = None
    model: str | None = None
    llm_model: str | None = None
    keyframe_model: str | None = None
    fallbacks: list[str] = Field(default_factory=list)
    durations_allowed: list[int] | None = None
    motion: str | None = None


class PlannerRoute(BaseModel):
    model: str
    fallbacks: list[str] = Field(default_factory=list)
    allowed_visual_types: list[Literal["code_animation", "ai_clip", "still_image", "text_card", "a_roll"]] = (
        Field(default_factory=lambda: list(ALL_TYPES))
    )


class RoutingConfig(BaseModel):
    visual_types: dict[
        Literal["code_animation", "ai_clip", "still_image", "text_card", "a_roll"], VisualRoute
    ]
    planner: PlannerRoute


DEFAULT = RoutingConfig(
    visual_types={
        "code_animation": VisualRoute(
            generator="html_animation",
            alt_generator="manim_code",
            llm_model="code-llm",
            fallbacks=["code-llm-backup", "planner-llm"],
        ),
        "ai_clip": VisualRoute(
            generator="video",
            model="video-gen",
            keyframe_model="image-gen",
            durations_allowed=[4, 6, 8],
            fallbacks=["video-gen-fast"],
        ),  # fmt: skip
        "still_image": VisualRoute(
            generator="image", model="image-gen", llm_model="code-llm", motion="ken_burns"
        ),
        "text_card": VisualRoute(generator="template"),
        "a_roll": VisualRoute(generator="passthrough"),
    },
    planner=PlannerRoute(model="planner-llm", fallbacks=["code-llm", "code-llm-backup"]),
)


def parse(text: str) -> RoutingConfig:
    return RoutingConfig.model_validate(yaml.safe_load(text) or {})


def load() -> RoutingConfig:
    p = Path(get_settings().routing_path)
    if not p.exists():
        return DEFAULT
    cfg = parse(p.read_text("utf-8"))
    for t in ALL_TYPES:  # tolerate partial files
        cfg.visual_types.setdefault(t, DEFAULT.visual_types[t])  # type: ignore[index]
    return cfg


@dataclass
class Resolved:
    visual_type: str
    generator: str
    model: str | None  # the main alias for this generator (LLM for html_motion)
    fallbacks: list[str]
    route: VisualRoute


def resolve(
    visual_type: str, override_model: str | None = None, cfg: RoutingConfig | None = None
) -> Resolved:
    cfg = cfg or load()
    route = cfg.visual_types[visual_type]  # type: ignore[index]
    gen = "html_animation" if route.generator == "html_motion" else route.generator  # legacy alias
    main = route.llm_model if gen in ("html_animation", "manim_code") else route.model
    return Resolved(
        visual_type=visual_type,
        generator=gen,
        model=override_model or main,
        fallbacks=list(route.fallbacks),
        route=route,
    )
