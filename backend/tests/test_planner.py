import pytest

from app.services.planner import PlanError, build_shots, extract_json, segment_words, validate_plan


def W(w, s, e):
    return {"w": w, "start_ms": s, "end_ms": e}


WORDS = [W("Kafka", 0, 400), W("is", 450, 600), W("a", 620, 680), W("log.", 700, 1000),
         W("Producers", 1500, 2000), W("write", 2050, 2300), W("events.", 2350, 2900),
         W("Consumers", 3000, 3500), W("read", 3550, 3800), W("them.", 3850, 4300)]  # fmt: skip

SG = {"summary": "navy", "characters": [{"id": "robin", "name": "Robin", "description": "glasses"}]}


def shot(f, t, vt="code_animation", **kw):
    card = {"card": {"headline": "A short headline"}} if vt == "text_card" else {}
    return {"from_segment": f, "to_segment": t, "visual_type": vt, "theme": "tech", "prompt": "a precise brief",
            **card, **kw}  # fmt: skip


def test_text_card_needs_card_and_prompt_becomes_canonical():
    with pytest.raises(PlanError, match="needs a `card`"):
        validate_plan(
            {"style_guide": SG, "shots": [shot(1, 1, "text_card", card=None)]}, 1, ["text_card"], False
        )
    card = {"eyebrow": "02 · THE ANSWER", "headline": "Java isn't baggage", "sub": "It's your edge",
            "highlight": ["edge"]}  # fmt: skip
    plan = validate_plan(
        {"style_guide": SG, "shots": [shot(1, 1, "text_card", card=card)]}, 1, ["text_card"], False
    )
    segs = [{"n": 1, "start_ms": 0, "end_ms": 900, "text": "x"}]
    built = build_shots(plan, segs, [{"w": "x", "start_ms": 0, "end_ms": 900}], 1000)
    assert built[0]["prompt"] == (
        "eyebrow: 02 · THE ANSWER\nheadline: Java isn't baggage\nsub: It's your edge\nhighlight: edge"
    )


def test_card_fields_from_canonical_and_legacy_prompts():
    from app.services.generation import card_fields

    f = card_fields("eyebrow: SKILL 2\nheadline: Evaluation\nsub: Confident. And wrong.\nhighlight: wrong")
    assert (f["eyebrow"], f["headline"], f["sub"], f["highlight"]) == (
        "SKILL 2", "Evaluation", "Confident. And wrong.", ["wrong"])  # fmt: skip
    legacy = {
        "Punchline card: 'Java isn't baggage.' Second line in teal: 'It's the edge.'":
            ("", "Java isn't baggage.", "It's the edge."),
        "Skill card. Tag: 'SKILL 2'. Headline: 'Evaluation'. Sub-line: 'Confident. And wrong. Why?'.":
            ("SKILL 2", "Evaluation", "Confident. And wrong. Why?"),
        "Chapter card. Tag in JetBrains Mono: '01 · TWO WORLDS'. Oswald headline: 'Where does \"learn Python\" come from?'.":
            ("01 · TWO WORLDS", 'Where does "learn Python" come from?', ""),
        "Robin's plan: Title 'That is all' sub 'Thanks for watching'": ("", "That is all", "Thanks for watching"),
    }  # fmt: skip
    for prompt, want in legacy.items():
        f = card_fields(prompt)
        assert (f["eyebrow"], f["headline"], f["sub"]) == want, prompt


def test_segments_break_at_sentences_and_pauses():
    segs = segment_words(WORDS)
    assert [s["text"] for s in segs] == ["Kafka is a log.", "Producers write events.", "Consumers read them."]
    assert segs[1]["start_ms"] == 1500 and segs[1]["end_ms"] == 2900


def test_extract_json_from_fenced_or_bare():
    assert extract_json('Here:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}


def test_validate_requires_contiguous_full_coverage():
    ok = validate_plan({"style_guide": SG, "shots": [shot(1, 2), shot(3, 3)]}, 3, ["code_animation"], False)
    assert len(ok.shots) == 2
    with pytest.raises(PlanError, match="expected 2"):
        validate_plan({"style_guide": SG, "shots": [shot(1, 1), shot(3, 3)]}, 3, ["code_animation"], False)
    with pytest.raises(PlanError, match="end at segment 2"):
        validate_plan({"style_guide": SG, "shots": [shot(1, 2)]}, 3, ["code_animation"], False)


def test_validate_rejects_unavailable_types_aroll_and_unknown_characters():
    with pytest.raises(PlanError, match="not available"):
        validate_plan({"style_guide": SG, "shots": [shot(1, 3, "ai_clip")]}, 3, ["code_animation"], False)
    with pytest.raises(PlanError, match="no face-cam"):
        validate_plan({"style_guide": SG, "shots": [shot(1, 3, "a_roll")]}, 3, ["a_roll"], False)
    with pytest.raises(PlanError, match="not defined"):
        validate_plan(
            {"style_guide": SG, "shots": [shot(1, 3, characters=["zed"], theme="story")]},
            3,
            ["code_animation"],
            False,
        )


def test_build_shots_contiguous_from_zero_to_duration():
    segs = segment_words(WORDS)
    plan = validate_plan({"style_guide": SG, "shots": [shot(1, 1), shot(2, 3, "text_card")]}, 3,
                         ["code_animation", "text_card"], False)  # fmt: skip
    out = build_shots(plan, segs, WORDS, 4800)
    assert [(s["start_ms"], s["end_ms"]) for s in out] == [(0, 1500), (1500, 4800)]
    assert out[1]["transcript_text"] == "Producers write events. Consumers read them."


def test_long_ai_clip_is_split_to_8s_and_chained():
    words = [W("x", i * 1000, i * 1000 + 500) for i in range(20)]
    segs = [{"n": 1, "start_ms": 0, "end_ms": 19500, "text": "x"}]
    plan = validate_plan({"style_guide": SG, "shots": [shot(1, 1, "ai_clip")]}, 1, ["ai_clip"], False)
    out = build_shots(plan, segs, words, 20000)
    assert len(out) == 3 and all(s["end_ms"] - s["start_ms"] <= 8000 for s in out)
    assert out[0]["start_ms"] == 0 and out[-1]["end_ms"] == 20000
    assert [s["depends_on_previous"] for s in out] == [False, True, True]
