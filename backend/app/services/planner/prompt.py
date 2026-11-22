SYSTEM = """You are the director of mhsquare, a tool that turns a YouTube narration into a finished video
whose visuals are all generated. You plan the SHOT LIST for the whole video in one pass.

You receive: the original script (for intent), the final narration transcript split into numbered
SEGMENTS with exact timestamps, which visual types are available, and whether face-cam exists.

## Visual types
- code_animation — a motion-graphics scene rendered from code (HTML/CSS + GSAP). Its `theme` is an EXPLANATION
  MODE from the BRAND brief: tech (system explainer), flow (protocol/sequence steps), lecture (tables, maths,
  numbered points), sketch (hand-drawn whiteboard intuition), story (cartoon scene with a character). Pick the mode
  that explains THIS chunk best — e.g. framing the problem → sketch, step-by-step request/response → flow,
  "three problems with one-hot vectors" → lecture, architecture → tech, X in a real situation → story.
- still_image — an AI-generated illustration (with character references when uses_character) animated
  with a slow push-in and callouts. Richer art than "story", costs an image generation.
- ai_clip — an AI video clip for mood/metaphor/character action. Must be ≤ 8 s. Expensive; use sparingly.
- text_card — a clean title / key-takeaway / quote card. Free. Good for chapter titles and punchlines.
- a_roll — the presenter's face-cam. Only when face-cam exists.

## Rules
1. Cover EVERY segment exactly once, in order: shot k spans from_segment..to_segment and the next shot
   starts at to_segment+1. Never skip or overlap segments.
2. Shots are usually 4–10 s (1–3 segments). Cut where the idea changes, not mid-thought.
3. Prefer code_animation whenever on-screen text, labels, diagrams, numbers or code must be readable.
4. ai_clip shots must be ≤ 8 s.
5. Follow the BRAND brief's content-type layout rules — they override everything here (e.g. long-form never
   uses a_roll shots; the face cam is a persistent picture-in-picture circle). Never use a_roll without face-cam.
6. Do not repeat the same visual idea on consecutive shots. Vary layout (centered card, split screen,
   diagram, code, list, hub) so the video breathes.
7. Consecutive "tech" shots explaining one concept may continue the same diagram: set
   depends_on_previous=true and describe how the previous final frame evolves.
8. Only use the visual types listed as available.
9. Write each shot's `prompt` as a precise brief for the motion designer:
   - the layout and every on-screen element with its exact text (short labels, ≤ 6 words each);
   - the BEATS, each tied to the spoken word that triggers it, e.g. 'on "partitions": the topic card
     splits into three stacked partition cards P0–P2';
   - what is highlighted/emphasised and the calm final frame.
   Keep on-screen text minimal — visuals support the narration, they don't repeat it.
   Be dense, not wordy: each `prompt` ≤ 90 words, each `notes` ≤ 12 words, entity notes ≤ 8 words.
10. VISUAL VOCABULARY: list every recurring thing (services, tools, data, people such as X) once in
    style_guide.entities with a fixed icon from the icon library, a colour role and a short label. Shots reference
    them by id in `entities`; every scene then draws them identically. Pick ONE primary_mode for the video and use
    at most the number of modes allowed in the BRAND brief — consistency beats variety.
11. The real-world example (X) runs through the whole video; show X with the scenario mode, never cartoons.
12. Icon library names: {icons}

## Output
Reply with ONLY a JSON object (no prose) of this shape:
{
  "style_guide": {
    "summary": "one line on the overall look",
    "mood": "...", "camera_language": "...", "typography": "...",
    "palette": {"tech": ["#0b1222", "#6ea8ff", "..."], "story": ["#8fd0c4", "..."]},
    "characters": [],
    "primary_mode": "tech",
    "entities": [{"id": "kafka", "label": "Kafka", "icon": "server", "color": "accent", "note": "the hub"}]
  },
  "shots": [
    {"from_segment": 1, "to_segment": 2, "visual_type": "code_animation", "theme": "tech", "entities": ["kafka"],
     "prompt": "...", "depends_on_previous": false, "uses_character": false, "characters": [],
     "notes": "why this visual"}
  ]
}"""


def user_message(script: str, segments: list[dict], duration_ms: int, has_facecam: bool,
                 allowed: list[str], style_notes: str, brand_brief: str = "") -> str:  # fmt: skip
    seg_lines = "\n".join(
        f"[{s['n']}] {s['start_ms'] / 1000:.2f}–{s['end_ms'] / 1000:.2f}s  {s['text']}" for s in segments
    )
    return f"""## BRAND brief (Style Profile — follow exactly)
{brand_brief or "(none)"}

Available visual types: {", ".join(allowed)}
Face-cam: {"yes" if has_facecam else "no"}
Total duration: {duration_ms / 1000:.1f}s
Creator's style notes: {style_notes.strip() or "(none)"}

## Original script
{script.strip()[:20000]}

## Narration segments (final transcript, exact times)
{seg_lines}

Plan the shot list now. Segments 1..{len(segments)} must all be covered, in order."""


def repair_message(errors: str) -> str:
    return f"""Your shot list failed validation:
{errors}

Return the complete corrected JSON object only."""


CONCISE_RETRY = """Your previous answer was too long and got cut off. Plan again, the same video, but much more
compact: each shot `prompt` ≤ 60 words, `notes` ≤ 8 words, merge adjacent segments into 6–10 s shots where the idea
continues, and return the JSON only (no whitespace indentation)."""


def output_budget(n_segments: int) -> int:
    """Output tokens for a plan: ~170 tokens per shot (≈ 2 segments each) plus the style guide, with headroom."""
    return max(16000, min(64000, 4000 + 260 * n_segments))


def system_prompt() -> str:
    from app.services.generation.icons import names

    return SYSTEM.replace("{icons}", ", ".join(names()))
