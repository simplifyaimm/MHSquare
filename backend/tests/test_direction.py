"""Creative direction: an AI recommendation per script (modes, templates, persona, hook), editable, followed by
the planner — no video starts from a default theme."""

import uuid

from app.core.db import get_sessionmaker
from app.models import Asset, AssetKind, Project
from app.services import direction as DIR
from app.services import style
from app.services.storage import get_storage
from tests.test_generation_flow import fakes, ready_project  # noqa: F401


def _add_script(pid: str, text: str = "# Kafka\n\nKafka is a log. Orders and email read from it.") -> None:
    storage = get_storage()
    with get_sessionmaker()() as db:
        p = db.get(Project, uuid.UUID(pid))
        key = storage.new_key(p.id, "md")
        storage.path(key).write_text(text, "utf-8")
        db.add(Asset(project_id=p.id, kind=AssetKind.script, storage_key=key, sha256="x", size=len(text),
                     mime="text/markdown", meta={}))  # fmt: skip
        db.commit()


def test_normalise_keeps_the_recommendation_inside_the_brand(ready_project):  # noqa: F811
    with get_sessionmaker()() as db:
        prof = style.active(db)
        d = DIR.normalise({"primary_mode": "story", "modes": ["flow", "story", "nope", "flow", "sketch", "lecture"],
                           "templates": ["hub", "dragon", "hub", "stat"]}, prof)  # fmt: skip
    assert d.primary_mode == "flow"  # story is disabled → first allowed mode becomes primary
    assert d.modes[0] == "flow" and "story" not in d.modes and "nope" not in d.modes and len(d.modes) <= 3
    assert d.templates == ["hub", "stat"]


def test_recommend_and_edit_through_the_api(fakes, ready_project):  # noqa: F811
    llm, _ = fakes
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    assert c.post(f"/api/projects/{pid}/direction", headers=h).status_code == 422  # no script yet
    _add_script(pid)
    r = c.post(f"/api/projects/{pid}/direction", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (
        d["primary_mode"] == "tech"
        and d["modes"] == ["tech", "scenario"]
        and d["templates"] == ["compare", "hub"]
    )
    assert d["source"] == "ai" and d["hook"].startswith("Kafka log")
    call = llm.calls[-1]
    assert "Kafka is a log" in call["messages"][0]["content"] and "- scenario:" in call["system"]
    assert "- compare:" in call["system"]  # the template library is offered
    e = c.put(
        f"/api/projects/{pid}/direction", json={"primary_mode": "flow", "modes": ["flow", "tech"]}, headers=h
    )
    assert e.status_code == 200 and e.json()["primary_mode"] == "flow" and e.json()["source"] == "edited"
    assert e.json()["templates"] == ["compare", "hub"]  # untouched fields are kept
    assert c.get(f"/api/projects/{pid}").json()["direction"]["primary_mode"] == "flow"


def test_planning_without_a_direction_decides_one_first_and_follows_it(fakes, ready_project):  # noqa: F811
    llm, _ = fakes
    c, csrf, pid = ready_project
    _add_script(pid)
    c.post(f"/api/projects/{pid}/plan", headers={"X-CSRF-Token": csrf})
    systems = [x["system"] or "" for x in llm.calls]
    i_dir = next(i for i, s in enumerate(systems) if "creative director" in s)
    i_plan = next(i for i, s in enumerate(systems) if "director of mhsquare" in s)
    assert i_dir < i_plan  # direction first
    plan_user = llm.calls[i_plan]["messages"][0]["content"]
    assert "CREATIVE DIRECTION" in plan_user and "allowed modes: tech, scenario" in plan_user
    data = c.get(f"/api/projects/{pid}").json()
    assert data["status"] == "planned" and data["direction"]["auto"] is True


def test_brief_is_empty_without_direction():
    assert DIR.brief(None) == "" and DIR.brief({}) == ""
    b = DIR.brief({"primary_mode": "flow", "modes": ["flow"], "templates": ["flow", "steps"], "hook": "x"})
    assert "Primary explanation mode: flow" in b and "flow, steps" in b


def test_wordy_or_odd_answers_are_trimmed_not_rejected(ready_project):  # noqa: F811
    with get_sessionmaker()() as db:
        d = DIR.normalise({"primary_mode": "tech", "modes": ["tech"], "persona": "X, " + "a Java developer " * 20,
                           "hook": "h" * 400, "pacing": "Brisk", "density": "RICH"}, style.active(db))  # fmt: skip
    assert len(d.persona) <= 120 and d.persona.endswith("…") and len(d.hook) <= 240
    assert d.pacing == "medium" and d.density == "rich"
