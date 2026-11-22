"""Rough cost estimate per shot (USD), shown before a run. Conservative: includes one fix round.

LiteLLM's cost map doesn't know some new model IDs yet, so these are explicit per-generator
estimates; replace with LiteLLM /model/info prices when available.
"""

PER_GENERATOR = {
    "html_animation": 0.12,  # ~8k in / 7k out tokens on a Sonnet-class model
    "html_motion": 0.12,  # legacy name
    "template": 0.0,
    "passthrough": 0.0,
    "image": 0.08 + 0.10,  # one image + the overlay scene
    "qa_review": 0.02,  # vision QA per shot (3 frames)
    "video_per_second": 0.40,
}
CHARACTER_DESIGN = 0.10


def estimate_shot(generator: str, duration_ms: int) -> float:
    if generator == "video":
        return round(PER_GENERATOR["image"] + PER_GENERATOR["video_per_second"] * duration_ms / 1000, 3)
    return PER_GENERATOR.get(generator, 0.0)
