"""Seeded defaults of the Style Profile (spec v2.1). Editable + versioned in the UI; never hard-coded
into prompts — prompts read the active profile version."""

CONTENT_TYPES = ["long_form", "short_series", "short_growth", "short_tools", "short_advice", "reel"]
PILLARS = ["series", "growth", "tools", "advice"]

BRAND = {
    "name": "Simplify AI",
    "tagline": "No hype. Real systems.",
    "handle": "@simplifyai.mm",
    "watermark": "@simplifyai.mm",
    "pillars": {
        "series": {"label": "Series", "color": "#48cae4"},
        "growth": {"label": "Growth", "color": "#e63946"},
        "tools": {"label": "Tools", "color": "#9d4edd"},
        "advice": {"label": "Advice", "color": "#3a86ff"},
    },
}

VOICE_RULES = """1. Conversational teacher-to-student. Warm and relatable. Talk to one student, not an audience.
2. Build intuition step by step. Use rhetorical questions to move the explanation forward ("So what happens when…?", "Why does this matter?"). Natural English cue phrases are allowed: "look", "imagine", "think about it", "here's the thing". No Hindi cue words.
3. Actually explain how the thing works. Show the mechanism, not just a definition. Thin, surface-level scripts are a failure.
4. At least one concrete real-world example, carried from start to finish. It must be a real scenario, not only an analogy. Analogies may support it but never replace it.
5. End with a clear takeaway the viewer can remember or act on.
6. "Natural" means conversational tone plus a carried example — not just simplified language.
7. India-specific angle where it genuinely fits: costs in ₹, Indian job market, Indian company or enterprise context. Don't force it.
8. Default audience: students and beginners new to AI (unless "advanced" is selected).
9. English only. No Hinglish and no Hindi lines.
10. No hype: no clickbait claims, no "this will change everything", no invented numbers. State things plainly and accurately."""

JUDGE_RUBRIC = [
    {"key": "hook", "label": "Hook in the first 15 s", "kind": "score"},
    {
        "key": "mechanism",
        "label": "Explains the mechanism (how it works, not just what it is)",
        "kind": "score",
    },
    {"key": "example", "label": "Carried real-world example (present start to finish)", "kind": "score"},
    {"key": "takeaway", "label": "Clear takeaway", "kind": "score"},
    {"key": "clarity", "label": "Beginner clarity (or advanced depth if selected)", "kind": "score"},
    {"key": "flow", "label": "Spoken flow: short sentences, easy to say aloud", "kind": "score"},
    {"key": "no_hype", "label": "No hype / accuracy", "kind": "score"},
    {"key": "pacing", "label": "Pacing vs. target length", "kind": "score"},
    {"key": "english_only", "label": "English-only check", "kind": "pass_fail"},
]

_SHORT = {
    "max_minutes": 1.2,
    "wpm": 150,
    "warn_words": 180,
    "first_person": False,
    "canvas": "shorts",
    "rules": "Recorded length ≤ ~1.2 minutes. Beginner-friendly by default. Must still explain how the thing "
    "works, use a real-world example and give a clear takeaway — short does not mean thin. Full-canvas "
    "animation with no face during animation segments. Topics may be concepts or practical frameworks, "
    "languages and tools (Python for AI, LangChain, LangGraph, AI coding assistants…).",
}

PRESETS = {
    "long_form": {
        "label": "YouTube long-form",
        "wpm": 150,
        "warn_words": None,
        "first_person": False,
        "canvas": "landscape",
        "rules": "Very detailed but easy enough for students. No presenter intro and no first-person credibility "
        'lines (banned: "Hi, I\'m…", "I\'ve been writing Java for 15 years"). Narrate in third person around '
        'a character called "X" who carries the real-world example through the video ("X is building a '
        'chatbot for…"). Length comes from the target minutes; no hard cap. The full frame always shows '
        "visuals; the face cam is a small circle bottom-right, never a full-screen shot.",
    },
    "short_series": {**_SHORT, "label": "Short — Series"},
    "short_growth": {**_SHORT, "label": "Short — Growth"},
    "short_tools": {**_SHORT, "label": "Short — Tools"},
    "short_advice": {
        **_SHORT,
        "label": "Short — Advice",
        "first_person": True,
        "rules": "Genuine, experience-based career advice. First person is allowed here (and only here). Same "
        "length cap, English only.",
    },
    "reel": {
        **_SHORT,
        "label": "Instagram Reel",
        "rules": _SHORT["rules"] + " Captions follow the Publish Kit rules.",
    },
}

ANIMATION_STYLE = {
    "background": {"from": "#0a1626", "to": "#060b14", "grid_px": 54, "grid_color": "rgba(72,202,228,0.07)"},
    "palette": {
        "series": "#48cae4",
        "growth": "#e63946",
        "tools": "#9d4edd",
        "advice": "#3a86ff",
        "warning": "#f4a261",
        "success": "#2dd4a7",
    },
    # neutrals that are always allowed (text, panels, borders)
    "neutrals": [
        "#ffffff",
        "#e8eef7",
        "#c5d0e0",
        "#8a9ab3",
        "#5b6b85",
        "#1b2a40",
        "#132036",
        "#0a1626",
        "#060b14",
        "#1e1e1e",
        "#252526",
        "#2d2d2d",
        "#007acc",
    ],  # fmt: skip
    "fonts": {"headline": "Oswald", "body": "Poppins", "mono": "JetBrains Mono", "code": "Consolas"},
    "allowed_fonts": ["Oswald", "Poppins", "JetBrains Mono", "Consolas"],
    "entry": {"stagger_total_s": [4, 6], "hold_final": True},
    "footer": {"enabled": True, "left": "NO HYPE. REAL SYSTEMS.", "right": "@simplifyai.mm"},
    "code_demo": "vscode_windows",  # VS Code on Windows: title-bar controls on the RIGHT, Consolas, #1e1e1e, #007acc
    "rules": [
        "Explain with visuals and diagrams, not text filling the screen. Lean visual. Every element must genuinely "
        "help explain the line being spoken — decorative filler is rejected.",
        "Use pill and LED components. Staggered entry over the first 4–6 s (synced to the spoken words), then hold "
        "the final frame.",
        "Nothing on the stage except the animation: no replay buttons, toggles, dashed borders, guide marks or "
        "person placeholders.",
        "Code demos look like VS Code on Windows: title bar with minimize/maximize/close on the RIGHT (never macOS "
        "dots on the left), Consolas, #1e1e1e background, Dark+ syntax colours, #007acc status bar.",
        "Headlines in Oswald, body in Poppins, meta labels in JetBrains Mono. No other fonts.",
        "The accent colour follows the project's pillar. Warnings amber, success green. No colours outside the "
        "palette.",
    ],
    # the cartoon "story" theme keeps its own palette (allowed by the lint for story scenes)
    "story_palette": [
        "#7fc6ba",
        "#93d3c7",
        "#a9ddd3",
        "#6fb5a9",
        "#5ea397",
        "#2b2f36",
        "#f6cfae",
        "#e8b48f",
        "#6b3e1e",
        "#fffdf6",
        "#c98a52",
        "#a86b3a",
        "#e0473c",
        "#f6d04d",
        "#f39a5b",
        "#8ccf7e",
        "#6aa6e8",
        "#23272e",
        "#f28b82",
        "#b7e3a8",
        "#f8b47f",
        "#fbe08a",
        "#a8cdf3",
        "#3fb56b",
    ],  # fmt: skip
}

LAYOUT = {
    "facecam_circle": "composite",  # composite | leave_space
    "facecam_safe_area": {"w_pct": 22, "h_pct": 22},  # bottom-right, long-form
    "watermark_position": "top-right",
    "footer_on_animations": True,
    "shorts_canvas_aspect": "3:4",  # 3:4 | 9:16  (final export is always 1080×1920)
    "shorts_export": [1080, 1920],
}

CAPTION_FORMULA = """[PLACEHOLDER — fill in your exact save-optimised caption formula here in the Brand page.]
Rule: never restate the script. Teach beyond the video: deeper explanation, variations, trade-offs,
pitfalls, practical tips. English only."""

REPLY_FRAMEWORK = """Agree → Amplify → Tie back.
1. Agree: acknowledge the viewer's point genuinely and specifically (no generic "great question").
2. Amplify: add one concrete insight, nuance or practical tip that goes beyond what they said.
3. Tie back: connect it to the video's core idea (or a related video/series) in one line.
Keep it short, warm, English only, no hype."""

SEED_TOPICS = [
    ("NotebookLM", "tools", "short_tools", "covered"),
    ("Claude Code subagents", "tools", "short_tools", "covered"),
    ("Cursor @Git", "tools", "short_tools", "covered"),
    ("Agent tool overload", "growth", "short_growth", "covered"),
    ("RAG quality", "growth", "short_growth", "covered"),
    *[(f"Zero to Hero series episode #{i}", "series", "short_series", "covered") for i in range(1, 19)],
    ("Zero to Hero series episode #19 — function calling", "series", "short_series", "covered"),
    ("LLM Observability", None, "long_form", "covered"),
    ("Hybrid Search", None, None, "planned"),
    ("Reranking", None, None, "planned"),
    ("RAG Evaluation", None, None, "planned"),
    ("Agentic RAG", None, None, "planned"),
    ("MCP (Model Context Protocol)", None, None, "planned"),
    ("Fine-Tuning vs RAG", None, None, "planned"),
    ("GraphRAG", None, None, "planned"),
]


def default_profile() -> dict:
    return {
        "language": "en",
        "brand": BRAND,
        "voice_rules": VOICE_RULES,
        "judge_rubric": JUDGE_RUBRIC,
        "audience_default": "beginner",
        "presets": PRESETS,
        "animation_style": ANIMATION_STYLE,
        "layout": LAYOUT,
        "caption_formula": CAPTION_FORMULA,
        "reply_framework": REPLY_FRAMEWORK,
    }
