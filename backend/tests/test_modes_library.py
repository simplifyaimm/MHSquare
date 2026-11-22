"""Explanation modes, script library, full-quality render."""

import json
import uuid

from app.core.db import get_sessionmaker
from app.models import Project
from app.models.style import StyleProfile
from app.services import style as S
from app.services.generation import prompts as GP
from app.services.style import defaults as D
from tests.test_generation_flow import PLAN, fakes, ready_project  # noqa: F401
from tests.test_ingest_flow import SCRIPT_MD


def test_mode_css_and_docs():
    with get_sessionmaker()() as db:
        prof = S.active(db)
        p = Project(name="x", content_type="long_form", pillar="tools")
        cv = S.canvas(prof, p)
        for mode, marker in [
            ("flow", ".fl-step"),
            ("lecture", ".lx-table"),
            ("sketch", ".sk-note"),
            ("tech", ".vsc"),
        ]:
            css = S.theme_css(prof, p, mode, cv)
            assert marker in css and ".mh-footer" in css and "--accent:#9d4edd" in css
        assert ".st-bubble" in S.theme_css(prof, p, "story", cv)
    assert "lifeline" in GP.scene_system("flow") and "lx-chapter" in GP.scene_system("lecture")
    assert "mhSketchPath" in GP.scene_system("sketch") and "mhSketchPath" in GP.RUNTIME_JS
    assert "Caveat" in D.ANIMATION_STYLE["allowed_fonts"]


def test_existing_profile_is_upgraded_with_modes_as_new_version():
    with get_sessionmaker()() as db:
        a = {k: v for k, v in D.ANIMATION_STYLE.items() if k != "explain_modes"}
        db.add(StyleProfile(version=1, is_active=True, **{**D.default_profile(), "animation_style": a}))
        db.commit()
        S.ensure_seeded(db)
        cur = S.active(db)
        assert cur.version == 2 and "sketch" in cur.animation_style["explain_modes"]
        assert "Caveat" in cur.animation_style["allowed_fonts"]
        brief = S.planner_brief(
            cur, Project(name="x", content_type="long_form", audience_level="beginner"), False
        )
        assert "EXPLANATION MODES" in brief and "flow:" in brief


def test_planner_accepts_new_modes(fakes, ready_project):  # noqa: F811
    llm, rend = fakes
    c, csrf, pid = ready_project
    PLAN["shots"][1]["theme"] = "flow"
    try:
        c.post(f"/api/projects/{pid}/plan", headers={"X-CSRF-Token": csrf})
        shots = c.get(f"/api/projects/{pid}/shots").json()["shots"]
        assert shots[1]["theme"] == "flow"
        r = c.patch(f"/api/shots/{shots[1]['id']}", json={"theme": "sketch"}, headers={"X-CSRF-Token": csrf})
        assert r.json()["theme"] == "sketch"
        c.post(
            f"/api/projects/{pid}/generate",
            json={"shot_ids": [shots[1]["id"]]},
            headers={"X-CSRF-Token": csrf},
        )
        assert ".sk-note" in rend.requests[-1]["theme_css"]
    finally:
        PLAN["shots"][1]["theme"] = "tech"


def test_script_library_copy(logged_in):
    c, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    a = c.post("/api/projects", json={"name": "A"}, headers=h).json()["id"]
    b = c.post("/api/projects", json={"name": "B"}, headers=h).json()["id"]
    c.post(
        f"/api/projects/{a}/assets", data={"kind": "script"}, files={"file": ("rag.md", SCRIPT_MD)}, headers=h
    )
    lib = c.get("/api/scripts").json()
    assert len(lib) == 1 and lib[0]["title"] == "Kafka in 5 minutes" and lib[0]["project"] == "A"
    r = c.post(f"/api/projects/{b}/script/from-library", json={"asset_id": lib[0]["asset_id"]}, headers=h)
    assert r.status_code == 201 and r.json()["kind"] == "script"
    assert c.get(r.json()["url"]).content == SCRIPT_MD
    assert len(c.get("/api/scripts").json()) == 1  # de-duplicated by content
    assert c.post(f"/api/projects/{b}/script/from-library", json={"asset_id": str(uuid.uuid4())},
                  headers=h).status_code == 404  # fmt: skip


def test_render_final_full_quality(fakes, ready_project):  # noqa: F811
    from app.services.assembly.preview import build_preview

    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    c.post(f"/api/projects/{pid}/generate", json={}, headers=h)
    build_preview(pid, "final")
    d = c.get(f"/api/projects/{pid}/shots").json()
    assert d["plan_meta"]["final"]["status"] == "done", d["plan_meta"]
    m = d["final"]["meta"]
    assert (m["width"], m["height"]) == (1920, 1080) and m["has_audio"]
    r = c.get(d["final"]["url"] + "&dl=final.mp4", headers={"Range": "bytes=0-10"})
    assert r.status_code == 206 and 'attachment; filename="final.mp4"' in r.headers["content-disposition"]
    json.dumps(m)
