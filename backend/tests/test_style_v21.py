"""Spec v2.1 done-criteria: judge flags, shorts length warning, style lint, topic repeat warning,
versioned Style Profile, content-type canvases."""

import pytest

from app.core.db import get_sessionmaker
from app.llm import client
from app.models import Project
from app.services import style as S
from app.services import topics as T
from app.services.generation import scene as SC
from app.services.generation.lint import lint
from app.services.script_check import deterministic, spoken_text


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(client, "_sleep", lambda s: None)


def _profile():
    with get_sessionmaker()() as db:
        p = S.active(db)
        db.expunge(p)
        return p


# ---------------------------------------------------------------- judge (deterministic part)


def test_long_form_flags_presenter_intro_credibility_and_hindi():
    prof = _profile()
    text = "Hi, I'm Manish. I've been writing Java for 15 years. आज हम RAG सीखेंगे. Toh kya hai RAG, yaar?"
    r = deterministic(spoken_text(text), "long_form", prof)
    kinds = {f["kind"] for f in r["flags"]}
    assert {"presenter_intro", "first_person_credibility", "hindi", "hinglish"} <= kinds
    assert r["english_only"] is False


def test_clean_third_person_script_passes_deterministic_checks():
    prof = _profile()
    text = "X is building a support chatbot for an online bookstore. So what happens when a customer asks..."
    r = deterministic(text, "long_form", prof)
    assert r["flags"] == [] and r["english_only"] is True


def test_advice_short_allows_first_person():
    prof = _profile()
    r = deterministic(
        "I've been interviewing engineers for 10 years. Here's the thing.", "short_advice", prof
    )
    assert not any(f["kind"] == "first_person_credibility" for f in r["flags"])


def test_shorts_over_180_words_warns():
    prof = _profile()
    r = deterministic(" ".join(["word"] * 200), "short_tools", prof)
    warn = next(f for f in r["flags"] if f["kind"] == "length")
    assert warn["severity"] == "warn" and "180" in warn["message"]
    assert not any(
        f["kind"] == "length" for f in deterministic(" ".join(["w"] * 170), "short_tools", prof)["flags"]
    )


# ---------------------------------------------------------------- style lint


def _lint(html="", css="", js="window.__scene=1", theme="tech"):
    a = _profile().animation_style
    return lint(SC.Scene(html=html, css=css, js=js), palette=list(a["palette"].values()) + a["neutrals"],
                allowed_fonts=a["allowed_fonts"], theme=theme, story_palette=a["story_palette"],
                handle="@simplifyai.mm", tagline="NO HYPE. REAL SYSTEMS.")  # fmt: skip


def test_lint_passes_brand_scene():
    html = '<div class="mh-bg"></div><span class="mh-pill"><i class="mh-led"></i>Retriever</span>'
    css = (
        ".x{color:#48cae4;background:#0a1626;border:1px solid rgba(255,255,255,.2);font-family:var(--f-head)}"
        ".y{color:#f4a261} .z{font-family:'Oswald',sans-serif} .w{color:#2dd4a7}"
    )
    assert _lint(html, css) == []


@pytest.mark.parametrize(
    ("html", "css", "msg"),
    [
        ("", ".x{color:#ff00ff}", "colours outside"),
        ("", ".x{font-family:'Comic Sans MS'}", "fonts not allowed"),
        ("", ".x{font: 600 40px Inter, sans-serif}", "fonts not allowed"),
        ('<div class="dots"><i style="background:#ff5f56"></i></div>', "", "macOS"),
        ("<button>Replay</button>", "", "control elements"),
        ("", ".x{border:2px dashed #48cae4}", "dashed"),
        ('<div class="person-placeholder"></div>', "", "placeholder"),
        ("<div>@simplifyai.mm</div>", "", "footer"),
    ],
)
def test_lint_catches_violations(html, css, msg):
    assert any(msg in i for i in _lint(html, css)), _lint(html, css)


def test_story_theme_allows_story_palette_only_there():
    css = ".x{fill:#f6d04d}"
    assert _lint(css=css, theme="story") == []
    assert any("colours" in i for i in _lint(css=css, theme="tech"))


# ---------------------------------------------------------------- profile versioning + canvas


def test_profile_versioning_and_language_lock(logged_in):
    c, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    p1 = c.get("/api/style/profile").json()
    assert p1["version"] == 1 and p1["brand"]["handle"] == "@simplifyai.mm"
    assert p1["brand"]["pillars"]["growth"]["color"] == "#e63946" and "[PLACEHOLDER" in p1["caption_formula"]
    brand = {**p1["brand"], "watermark": "@simplify.test"}
    r = c.put("/api/style/profile", json={"brand": brand, "note": "test"}, headers=h)
    assert (
        r.status_code == 200
        and r.json()["version"] == 2
        and r.json()["brand"]["watermark"] == "@simplify.test"
    )
    assert r.json()["voice_rules"] == p1["voice_rules"]  # untouched fields carried over
    assert c.put("/api/style/profile", json={"language": "hi"}, headers=h).status_code == 422
    bad = {**p1["animation_style"], "palette": {"series": "teal"}}
    assert c.put("/api/style/profile", json={"animation_style": bad}, headers=h).status_code == 422
    assert [v["version"] for v in c.get("/api/style/versions").json()] == [2, 1]
    assert c.post("/api/style/versions/1/activate", headers=h).json()["is_active"] is True
    assert c.get("/api/style/profile").json()["version"] == 1


def test_canvas_per_content_type():
    prof = _profile()
    lf = S.canvas(prof, Project(name="x", content_type="long_form"))
    assert (lf.stage_w, lf.stage_h) == (1920, 1080) and lf.safe_zones[0]["x"] == 1920 - 422
    sh = S.canvas(prof, Project(name="x", content_type="short_tools"))
    assert (sh.stage_w, sh.stage_h, sh.export_h, sh.offset_y) == (
        1080,
        1440,
        1920,
        240,
    ) and sh.safe_zones == []
    prof.layout = {**prof.layout, "shorts_canvas_aspect": "9:16"}
    assert S.canvas(prof, Project(name="x", content_type="reel")).stage_h == 1920
    footer = S.footer_html(prof, lf)
    assert "NO HYPE. REAL SYSTEMS." in footer and "@simplifyai.mm" in footer and "right:446px" in footer
    css = S.theme_css(prof, Project(name="x", content_type="long_form", pillar="growth"), "tech", lf)
    assert "--accent:#e63946" in css and "--grid:54px" in css and "Oswald" in css


def test_planner_brief_forbids_aroll_for_long_form():
    prof = _profile()
    brief = S.planner_brief(
        prof, Project(name="x", content_type="long_form", audience_level="beginner"), True
    )
    assert "NEVER plan a_roll" in brief and "third person" in brief
    short = S.planner_brief(
        prof, Project(name="x", content_type="short_series", audience_level="beginner"), False
    )
    assert "SHORTS layout" in short and "1080×1440" in short


# ---------------------------------------------------------------- topic registry


def test_repeat_warning_lexical_fallback(logged_in):
    """Embeddings unreachable in tests → lexical fallback still warns."""
    c, csrf = logged_in
    r = c.post("/api/topics/check", json={"title": "NotebookLM tips"}, headers={"X-CSRF-Token": csrf}).json()
    assert r["likely_repeat"] and r["matches"][0]["title"] == "NotebookLM" and r["method"] == "lexical"
    r = c.post(
        "/api/topics/check", json={"title": "Kubernetes autoscaling"}, headers={"X-CSRF-Token": csrf}
    ).json()
    assert not r["likely_repeat"]


def test_repeat_warning_with_embeddings(logged_in, monkeypatch):
    c, csrf = logged_in

    def fake_embed(texts, alias="embed", timeout=60):
        vec = lambda t: [1.0, 0.0] if "notebooklm" in t.lower() else [0.0, 1.0]  # noqa: E731
        return [vec(t) for t in texts], client.CallResult(alias="embed", cost_usd=0)

    monkeypatch.setattr(client, "embed", fake_embed)
    r = c.post("/api/topics/check", json={"title": "Using NotebookLM for research"},
               headers={"X-CSRF-Token": csrf}).json()  # fmt: skip
    assert r["method"] == "embedding" and r["matches"][0]["title"] == "NotebookLM"


def test_topics_crud_seed_and_mark_final(logged_in):
    c, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    topics = c.get("/api/topics").json()
    assert any(t["title"] == "GraphRAG" and t["status"] == "planned" for t in topics)
    assert any(t["title"].startswith("Zero to Hero series episode #19") for t in topics)
    t = c.post("/api/topics", json={"title": "Vector databases", "pillar": "series"}, headers=h).json()
    assert (
        c.patch(f"/api/topics/{t['id']}", json={"status": "planned"}, headers=h).json()["status"] == "planned"
    )
    pid = c.post("/api/projects", json={"name": "RAG eval", "topic": "RAG Evaluation deep dive",
                                        "content_type": "long_form", "topic_kind": "authority"}, headers=h).json()["id"]  # fmt: skip
    r = c.post(f"/api/projects/{pid}/mark-final", headers=h).json()
    assert r["title"] == "RAG Evaluation deep dive"
    assert c.get("/api/projects-stats").json()["authority"] == 1


def test_lexical_similarity():
    assert T.lexical("NotebookLM tips", "NotebookLM") == 1.0
    assert T.lexical("Agentic RAG explained", "Agentic RAG") == 1.0
    assert T.lexical("Kafka", "RAG quality") == 0.0
