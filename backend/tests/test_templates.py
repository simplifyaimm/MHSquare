"""Scene templates: deterministic, validated, on-brand scenes from small JSON (no per-shot AI call)."""

import json
import re

import pytest

from app.services.generation import templates as T
from app.services.generation.icons import icons
from app.services.generation.lint import lint_icons
from app.services.generation.scene import check
from app.services.planner import PlanError, build_shots, validate_plan

LONG = dict(
    w=1920, h=1080, short=False, zones=[{"name": "face-cam", "x": 1498, "y": 842, "w": 422, "h": 238}]
)
SHORT = dict(w=1080, h=1440, short=True, zones=[])


def ctx(words=None, **canvas):
    c = canvas or LONG
    return T.Ctx(**c, duration=6.0, words=words or [], icons=frozenset(icons()),
                 entities={"robin": {"id": "robin", "label": "Robin", "icon": "user-round", "color": "accent",
                                     "note": "Java developer"}})  # fmt: skip


@pytest.mark.parametrize("name", list(T.TEMPLATES))
@pytest.mark.parametrize("canvas", [LONG, SHORT], ids=["long", "short"])
def test_every_template_builds_a_safe_scene_from_its_example(name, canvas):
    sc = T.build(name, T.TEMPLATES[name].example, ctx(**canvas))
    check(sc)  # the same security gate LLM scenes pass
    assert not lint_icons(sc, set(icons()))  # only library icons
    assert "tl.set({}, {}, D)" in sc.js and "window.__scene = tl" in sc.js
    assert 'id="ct"' in sc.html and "mh-bg" in sc.html


def test_content_box_avoids_footer_and_face_cam_zone():
    x, y, w, h = ctx().box(header=True)
    assert x == 120 and y == 90 + 190 and y + h <= 842 - 24 and x + w == 1800


def test_on_phrase_lands_on_the_spoken_word_else_staggers():
    words = [{"w": "Most", "t": 0.2}, {"w": "Java", "t": 0.5}, {"w": "devs", "t": 0.8}, {"w": "build", "t": 2.4},
             {"w": "with", "t": 2.6}, {"w": "models.", "t": 2.9}]  # fmt: skip
    b = T.Beats(ctx(words))
    assert b.at("build with", 0, 3) == pytest.approx(2.28)
    assert b.at("Models", 0, 3) == pytest.approx(2.78)  # case/punctuation-insensitive
    assert 0.2 < b.at("not spoken", 1, 3) < b.at("not spoken", 2, 3) < 6.0  # fallback stagger


def test_entity_supplies_label_icon_and_role():
    sc = T.build(
        "scenario", {"persona": {"entity": "robin"}, "items": [{"label": "Insurance client"}]}, ctx()
    )
    assert "Robin" in sc.html and "Java developer" in sc.html and 'data-icon="user-round"' in sc.html


def test_text_is_escaped_and_unknown_icons_fall_back():
    sc = T.build("checklist", {"items": [{"text": "<script>x</script>", "icon": "dragon"},
                                         {"text": "ok"}]}, ctx())  # fmt: skip
    assert "<script>x" not in sc.html and "&lt;script&gt;" in sc.html
    assert 'data-icon="dragon"' not in sc.html


@pytest.mark.parametrize(("name", "params", "msg"), [
    ("nope", {}, "unknown template"),
    ("steps", {"steps": [{"label": "only one"}]}, "steps"),
    ("compare", {"left": {"label": "A"}, "right": {"label": "B"}, "colour": "teal"}, "colour"),
    ("hub", {"center": {}, "spokes": [{"label": "a"}, {"label": "b"}]}, "label or an entity"),
    ("stat", {"label": "policy documents"}, "value"),  # required field missing
])  # fmt: skip
def test_validation_errors_are_specific(name, params, msg):
    with pytest.raises(T.TemplateError, match=msg):
        T.validate(name, params)


def test_prompt_round_trip_and_free_form_is_not_a_template():
    p = T.to_prompt("stat", {"value": "3×", "label": "faster"})
    assert p.startswith("@template stat\n") and T.parse(p) == ("stat", {"value": "3×", "label": "faster"})
    assert T.parse("A hub diagram with Kafka in the middle") is None
    with pytest.raises(T.TemplateError, match="not valid JSON"):
        T.parse("@template stat\n{oops")


def test_code_template_highlights_syntax_and_lines():
    sc = T.build("code", {"filename": "A.java", "lang": "java", "lines": ["public class A {", '  String s = "x"; // hi',
                          "}"], "highlights": [{"lines": [2], "kind": "bad"}]}, ctx())  # fmt: skip
    assert '<span class="kw">public</span>' in sc.html and '<span class="str">&quot;x&quot;</span>' in sc.html
    assert 'class="lnb bad" id="lb1"' in sc.html and "tl.to('#lb1'" in sc.js


def test_stat_counts_up_to_the_value():
    sc = T.build("stat", {"value": "50,000", "label": "docs"}, ctx())
    assert re.search(r"tl\.to\(o, \{v:50000\.0", sc.js) and "toLocaleString" in sc.js


# ---------------------------------------------------------------- planner integration

SG = {"summary": "x", "entities": [{"id": "robin", "label": "Robin", "icon": "user-round"}]}


def test_planner_template_becomes_the_canonical_prompt_and_brief_a_note():
    shot = {"from_segment": 1, "to_segment": 1, "visual_type": "code_animation", "theme": "tech",
            "prompt": "two worlds of AI", "template": {"name": "compare", "params": {
                "left": {"label": "World 1"}, "right": {"label": "World 2"}}}}  # fmt: skip
    plan = validate_plan({"style_guide": SG, "shots": [shot]}, 1, ["code_animation"], False)
    built = build_shots(plan, [{"n": 1, "start_ms": 0, "end_ms": 900, "text": "x"}],
                        [{"w": "x", "start_ms": 0, "end_ms": 900}], 1000)  # fmt: skip
    assert T.parse(built[0]["prompt"])[0] == "compare" and "brief: two worlds of AI" in built[0]["notes"]


def test_planner_rejects_invalid_template_params():
    shot = {"from_segment": 1, "to_segment": 1, "visual_type": "code_animation", "theme": "tech",
            "prompt": "a precise brief", "template": {"name": "steps", "params": {"steps": []}}}  # fmt: skip
    with pytest.raises(PlanError, match="template steps"):
        validate_plan({"style_guide": SG, "shots": [shot]}, 1, ["code_animation"], False)


def test_planner_prompt_lists_every_template():
    from app.services.planner.prompt import system_prompt

    sp = system_prompt()
    assert "{templates}" not in sp and all(f"- {n}:" in sp for n in T.TEMPLATES)
    json.loads(json.dumps(T.TEMPLATES["flow"].example))  # examples are plain JSON


def test_small_overshoots_are_trimmed_not_rejected():
    m = T.validate("scenario", {"persona": {"name": "Robin"}, "badge": "Java is more than enough for this",
                                "items": [{"label": f"item {k}"} for k in range(5)]})  # fmt: skip
    assert len(m.items) == 3 and len(m.badge) <= 28 and m.badge.endswith("…")


def test_flow_accepts_zero_based_actor_numbers():
    m = T.validate("flow", {"actors": [{"label": "App"}, {"label": "LLM"}],
                            "messages": [{"from": 0, "to": 1, "label": "prompt"},
                                         {"from": 1, "to": 0, "label": "answer", "kind": "response"}]})  # fmt: skip
    assert [(x.src, x.to) for x in m.messages] == [(1, 2), (2, 1)]


def test_converter_asks_for_variety_and_maps_content_shape_to_templates():
    from app.services.planner import templatize

    sp = templatize.system_prompt()
    assert "VARIETY IS REQUIRED" in sp and "→ timeline" in sp and "→ steps" in sp
    assert "{templates}" not in sp and "{icons}" not in sp


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_hub_spokes_never_overlap_the_centre_or_each_other_in_landscape(n):
    spokes = [{"label": f"spoke {k}", "icon": "bot"} for k in range(n)]
    sc = T.build("hub", {"title": "t", "center": {"label": "Hub"}, "spokes": spokes}, ctx())
    pos = {
        m[0]: (float(m[1]), float(m[2]))
        for m in re.findall(r'id="(n\d|hub)" style="left:(\d+)px;top:(\d+)px', sc.html)
    }
    half = {"hub": (190, 130)}  # rendered half-sizes (centre card, normal and compact spokes)
    spoke_half = (130, 72) if n >= 5 else (140, 95)
    ids = list(pos)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            (ax, ay), (bx, by) = pos[a], pos[b]
            (aw, ah), (bw, bh) = half.get(a, spoke_half), half.get(b, spoke_half)
            assert abs(ax - bx) >= aw + bw or abs(ay - by) >= ah + bh, (a, b, pos[a], pos[b])


def test_long_code_stays_readable_and_clear_of_the_face_cam_zone():
    lines = [f"  line {k} = call(x);" for k in range(14)]
    sc = T.build("code", {"title": "t", "filename": "A.java", "lang": "java", "lines": lines}, ctx())
    fs = int(re.search(r'vsc-body" style="font-size:(\d+)px', sc.html).group(1))
    win_w = float(re.search(r'id="win" style="width:(\d+)px', sc.html).group(1))
    assert fs >= T.MIN_PX and 120 + win_w <= 1498 - 24  # left of the face-cam zone (x 1498)
    short = T.build("code", {"filename": "A.java", "lang": "java", "lines": lines[:6]}, ctx())
    assert "font-size:30px" in short.html and "width:1680px" in short.html  # short code keeps the full box
