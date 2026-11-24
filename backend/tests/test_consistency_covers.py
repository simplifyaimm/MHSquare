"""Consistency system (vocabulary, mode cap, icon library, vision QA) and cover photos."""

import io
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.llm import client
from app.models import Project
from app.models.style import BrandAsset
from app.services import covers
from app.services import style as S
from app.services.assembly.preview import facecam_geometry
from app.services.generation import renderer
from app.services.generation.icons import icons, runtime_js
from app.services.generation.lint import lint_icons
from app.services.generation.scene import Scene
from app.services.planner import PlanError, validate_plan
from app.services.storage import get_storage
from tests.test_generation_flow import PLAN, fakes, ready_project  # noqa: F401

SG = {"summary": "x", "entities": [{"id": "kafka", "label": "Kafka", "icon": "server", "color": "series"}]}


def _shot(f, t, theme="tech", ents=None):
    return {"from_segment": f, "to_segment": t, "visual_type": "code_animation", "theme": theme,
            "prompt": "a precise brief", "entities": ents or []}  # fmt: skip


def test_icon_library_and_lint():
    assert {"database", "server", "user-round", "indian-rupee"} <= set(icons())
    assert "window.mhIcon" in runtime_js() and "data-icon" in runtime_js()
    sc = Scene(html='<i data-icon="server"></i><i data-icon="unicorn"></i>', css="", js="mhIcon('database')")
    assert lint_icons(sc, set(icons())) == ["unknown icons unicorn — use only names from the icon library"]


def test_plan_enforces_vocabulary_icons_and_mode_cap():
    modes = ["tech", "flow", "lecture", "sketch", "scenario"]
    ok = validate_plan({"style_guide": SG, "shots": [_shot(1, 1, ents=["kafka"]), _shot(2, 2, "flow")]}, 2,
                       ["code_animation"], False, modes=modes, max_modes=3)  # fmt: skip
    assert ok.style_guide.entities[0].icon == "server"
    with pytest.raises(PlanError, match="not in the icon library"):
        bad = {**SG, "entities": [{"id": "k", "label": "K", "icon": "dragon"}]}
        validate_plan({"style_guide": bad, "shots": [_shot(1, 1)]}, 1, ["code_animation"], False, modes=modes)
    with pytest.raises(PlanError, match="at most 3"):
        validate_plan({"style_guide": SG, "shots": [_shot(i, i, m) for i, m in enumerate(modes[:4], 1)]}, 4,
                      ["code_animation"], False, modes=modes, max_modes=3)  # fmt: skip
    with pytest.raises(PlanError, match="not enabled"):
        validate_plan(
            {"style_guide": SG, "shots": [_shot(1, 1, "story")]}, 1, ["code_animation"], False, modes=modes
        )
    with pytest.raises(PlanError, match="not defined in style_guide.entities"):
        validate_plan(
            {"style_guide": SG, "shots": [_shot(1, 1, ents=["redis"])]}, 1, ["code_animation"], False
        )


def test_profile_v3_upgrade_and_shorts_facecam():
    with get_sessionmaker()() as db:
        prof = S.active(db)
        a = prof.animation_style
        assert (
            "story" not in a["enabled_modes"] and "scenario" in a["enabled_modes"] and a["qa_review"] is True
        )
        assert prof.layout["facecam_circle_shorts"] == "composite"
        sh = S.canvas(prof, Project(name="s", content_type="short_series"))
        assert sh.facecam_circle == "composite" and sh.safe_zones == []  # 3:4 → circle in the bottom band
        cx, cy, d = facecam_geometry(sh, 1080, 1920)
        assert cy > sh.offset_y + sh.stage_h  # below the animation canvas
        lf = S.canvas(prof, Project(name="l", content_type="long_form"))
        cx, cy, d = facecam_geometry(lf, 1920, 1080)
        z = lf.safe_zones[0]
        assert z["x"] < cx < 1920 and z["y"] < cy < 1080


def test_vision_qa_triggers_one_polish_round(fakes, ready_project, monkeypatch):  # noqa: F811
    llm, rend = fakes
    c, csrf, pid = ready_project
    qa_replies = [json.dumps({"ok": False, "score": 4, "issues": ["title overlaps the diagram"]})]
    orig_chat = llm.chat

    def chat(alias, messages, *, system=None, **kw):
        if system and "senior motion designer reviewing" in system:
            llm.calls.append({"alias": alias, "system": system, "messages": messages})
            body = qa_replies.pop(0) if qa_replies else json.dumps({"ok": True, "score": 9, "issues": []})
            return body, client.CallResult(alias=alias, cost_usd=0.005)
        return orig_chat(alias, messages, system=system, **kw)

    monkeypatch.setattr(client, "chat", chat)

    def render_with_qa(**req):
        r = rend.render(**req)
        frames = []
        for k, _t in enumerate(req.get("qa_frames") or []):
            f = Path(get_settings().render_out_dir) / f"qa{uuid.uuid4().hex[:6]}_{k}.jpg"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(r.video), "-frames:v", "1", str(f)],  # noqa: S603, S607
                           check=True)  # fmt: skip
            frames.append(f)
        r.qa_frames = frames
        return r

    monkeypatch.setattr(renderer, "render", render_with_qa)
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    sid = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["id"]
    c.post(f"/api/projects/{pid}/generate", json={"shot_ids": [sid]}, headers=h)
    shot = next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)
    t = shot["takes"][-1]
    assert shot["state"] == "review" and t["meta"]["fix_rounds"] == 1
    assert t["meta"]["qa"]["ok"] is False and "overlaps" in t["meta"]["qa"]["issues"][0]
    qa_call = next(x for x in llm.calls if x["system"] and "senior motion designer reviewing" in x["system"])
    assert sum(1 for p in qa_call["messages"][0]["content"] if p["type"] == "image_url") == 3
    assert "title overlaps the diagram" in llm.calls[-1]["messages"][-1]["content"]  # polish request sent


def test_measured_layout_problems_force_the_polish_even_if_qa_approves(fakes, ready_project, monkeypatch):  # noqa: F811
    llm, rend = fakes
    c, csrf, pid = ready_project
    orig_chat = llm.chat

    def chat(alias, messages, *, system=None, **kw):
        if system and "senior motion designer reviewing" in system:
            ok = json.dumps({"ok": True, "score": 8, "issues": []})
            return ok, client.CallResult(alias=alias, cost_usd=0.005)
        return orig_chat(alias, messages, system=system, **kw)

    monkeypatch.setattr(client, "chat", chat)
    advice = "composition: content fills 30%×20% of the stage, centred at (25%, 30%) — spread it"

    def render_with_qa(**req):
        r = rend.render(**req)
        f = Path(get_settings().render_out_dir) / f"qa{uuid.uuid4().hex[:6]}.jpg"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(r.video), "-frames:v", "1", str(f)],  # noqa: S603, S607
                       check=True)  # fmt: skip
        r.qa_frames = [f] if req.get("qa_frames") else []
        r.info = {**r.info, "layout_warnings": [advice]}
        return r

    monkeypatch.setattr(renderer, "render", render_with_qa)
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    sid = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["id"]
    c.post(f"/api/projects/{pid}/generate", json={"shot_ids": [sid]}, headers=h)
    t = next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)["takes"][-1]
    assert t["meta"]["fix_rounds"] == 1 and t["meta"]["qa"]["ok"] is False
    assert t["meta"]["qa"]["measured"] == [advice] and t["meta"]["qa"]["score"] == 8
    assert advice in llm.calls[-1]["messages"][-1]["content"]  # the polish request carries the measurement


def test_blind_reviews_are_detected_but_real_issues_are_not():
    from app.services.generation import _BLIND

    for blind in ["No frame is visible here.", "No frame image was attached.", "I cannot see the image you sent.",
                  "The images were not provided, so I can't judge.", "I'm unable to view the frames."]:  # fmt: skip
        assert _BLIND.search(blind), blind
    for real in ['{"ok": false, "issues": ["the title is cut off at the frame edge"]}',
                 "There is no frame where Kafka appears", "text does not fit the frame", "image is too dark"]:  # fmt: skip
        assert not _BLIND.search(real), real


def test_measured_problems_get_a_polish_even_with_frame_review_off(fakes, ready_project, monkeypatch):  # noqa: F811
    from app.services import style

    llm, rend = fakes
    c, csrf, pid = ready_project
    real_active = style.active

    def no_qa(db):
        p = real_active(db)
        p.animation_style = {**(p.animation_style or {}), "qa_review": False}
        return p

    monkeypatch.setattr(style, "active", no_qa)
    advice = "composition of the final frame: it is top-heavy (centre y=25%)"

    def render(**req):
        r = rend.render(**req)
        r.info = {**r.info, "layout_warnings": [advice]}
        return r

    monkeypatch.setattr(renderer, "render", render)
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    sid = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["id"]
    c.post(f"/api/projects/{pid}/generate", json={"shot_ids": [sid]}, headers=h)
    t = next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)["takes"][-1]
    assert t["meta"]["fix_rounds"] == 1 and t["meta"]["qa"]["measured"] == [advice]
    assert not any(x.get("system") and "senior motion designer reviewing" in x["system"] for x in llm.calls)
    assert advice in llm.calls[-1]["messages"][-1]["content"]


def test_cover_concepts_normalised():
    o = covers.normalise({"headline": "Why Kafka sits in the middle", "highlight": ["Kafka", "Redis"],
                          "eyebrow": "event streaming series ep", "icons": ["server", "dragon", "users"],
                          "layout": "versus", "left": "Direct", "right": "Kafka"})  # fmt: skip
    assert o["headline"] == "Why Kafka sits in" and o["highlight"] == ["Kafka"]
    assert (
        o["icons"] == ["server", "users"]
        and o["layout"] == "versus"
        and o["eyebrow"] == "event streaming series"
    )
    html, css = covers.cover_scene(o, "youtube", "@simplifyai.mm", None)
    assert '<span class="hl">Kafka</span>' in html and "cv-vs" in html and "@simplifyai.mm" in html


def test_cover_generation_flow(logged_in, monkeypatch):
    c, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    from app.workers.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    try:
        # headshot upload → cut-out task (rembg isn't in the API image → fails gracefully, retryable)
        png = io.BytesIO()
        from PIL import Image

        Image.new("RGB", (40, 60), (120, 90, 80)).save(png, "PNG")
        hs = c.post("/api/brand/headshots", files={"file": ("me.png", png.getvalue())}, headers=h).json()
        assert hs["status"] == "pending" and c.get(hs["url"]).status_code == 200
        # pretend the ML worker produced a cut-out
        with get_sessionmaker()() as db:
            a = db.get(BrandAsset, uuid.UUID(hs["id"]))
            key = get_storage().new_key("brand", "png")
            out = io.BytesIO()
            Image.new("RGBA", (40, 60), (200, 150, 120, 255)).save(out, "PNG")
            get_storage().put_stream(key, out.__class__(out.getvalue()))
            a.cutout_key, a.status = key, "ready"
            db.commit()
        listed = c.get("/api/brand/headshots").json()
        assert (
            listed[0]["status"] == "ready"
            and c.get(listed[0]["cutout_url"]).headers["content-type"] == "image/png"
        )

        pid = c.post("/api/projects", json={"name": "Kafka", "pillar": "series"}, headers=h).json()["id"]
        assert c.post(f"/api/projects/{pid}/covers", json={}, headers=h).status_code == 422  # needs a script
        c.post(f"/api/projects/{pid}/assets", data={"kind": "script"}, files={"file": ("s.md", b"# Kafka\n\nWhy Kafka.")},
               headers=h)  # fmt: skip

        def fake_chat(alias, messages, *, system=None, **kw):
            assert "≤ 4 words" in system and "server" in system
            opts = [{"headline": "Kafka Fixes Fan-out", "highlight": ["Kafka"], "eyebrow": "Series", "icons": ["server", "users"],
                     "layout": "icons"}, {"headline": "Direct vs Kafka", "highlight": ["Kafka"], "eyebrow": "",
                     "icons": ["link", "server"], "layout": "versus", "left": "Direct", "right": "Kafka"}]  # fmt: skip
            return json.dumps({"options": opts}), client.CallResult(alias=alias, cost_usd=0.01)

        stills: list[dict] = []

        def fake_still(**req):
            stills.append(req)
            f = Path(get_settings().render_out_dir) / f"{uuid.uuid4().hex}.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (req["width"], req["height"]), (10, 22, 38)).save(f, "PNG")
            return f

        monkeypatch.setattr(client, "chat", fake_chat)
        monkeypatch.setattr(renderer, "render_still", fake_still)
        r = c.post(f"/api/projects/{pid}/covers", json={"count": 2, "engine": "template"}, headers=h)
        assert r.status_code == 202
        d = c.get(f"/api/projects/{pid}/covers").json()
        assert d["status"]["status"] == "done" and d["status"]["made"] == 4 and d["status"]["with_photo"]
        sizes = sorted({(s["width"], s["height"]) for s in stills})
        assert sizes == [(1080, 1920), (1280, 720)]
        assert all(
            "data:image/png;base64," in s["css"] and "--accent:#48cae4" in s["theme_css"] for s in stills
        )
        yt = [x for x in d["covers"] if x["format"] == "youtube"]
        assert {x["headline"] for x in yt} == {"Kafka Fixes Fan-out", "Direct vs Kafka"}
        chosen = c.post(f"/api/covers/{yt[0]['id']}/choose", headers=h).json()
        assert chosen["chosen"] is True
        assert c.get(yt[0]["url"]).headers["content-type"] == "image/jpeg"
    finally:
        celery_app.conf.task_always_eager = False
