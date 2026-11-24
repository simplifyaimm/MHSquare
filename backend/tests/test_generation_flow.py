"""Phase 3 + code-animation slice through the API, with a fake LLM and a fake (ffmpeg) renderer."""

import json
import subprocess
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.llm import client
from app.llm.errors import ErrorKind, LLMError
from app.models import Asset, AssetKind, Project, ProjectStatus, Transcript
from app.services.generation import prompts as GP
from app.services.generation import renderer
from app.services.storage import get_storage
from app.workers.celery_app import celery_app
from tests.test_scene_and_llm import GOOD

WORDS = [("Meet", 0, 300), ("Robin.", 350, 900), ("Kafka", 1500, 1900), ("is", 1950, 2050), ("a", 2100, 2150),
         ("log.", 2200, 2700), ("That", 3500, 3700), ("is", 3750, 3850), ("all.", 3900, 4400)]  # fmt: skip
DURATION = 5000

PLAN = {
    "style_guide": {"summary": "navy brand explainer", "mood": "calm", "primary_mode": "tech", "characters": [],
                    "entities": [{"id": "x", "label": "X", "icon": "user-round", "color": "accent"},
                                 {"id": "kafka", "label": "Kafka", "icon": "server", "color": "series"}]},
    "shots": [
        {"from_segment": 1, "to_segment": 1, "visual_type": "code_animation", "theme": "scenario",
         "prompt": "X persona appears; callout 'Meet X!'", "entities": ["x"]},
        {"from_segment": 2, "to_segment": 2, "visual_type": "code_animation", "theme": "tech",
         "prompt": "Card 'Kafka' with eyebrow 'COMMIT LOG'"},
        {"from_segment": 3, "to_segment": 3, "visual_type": "text_card", "theme": "tech",
         "prompt": "closing card",
         "card": {"eyebrow": "THE END", "headline": "That is all", "sub": "Thanks for watching", "highlight": ["all"]}},
    ],
}  # fmt: skip
SVG = (
    '```svg\n<svg viewBox="0 0 400 800" xmlns="http://www.w3.org/2000/svg"><g class="legs"/><g class="body"/>'
    '<g class="arm-l"/><g class="arm-r"/><g class="head"><g class="eyes"/><g class="mouth"/></g></svg>\n```'
)


DIRECTION = {
    "primary_mode": "tech",
    "modes": ["tech", "scenario", "story", "nope"],
    "templates": ["compare", "hub", "dragon"],
    "persona": "X, a support engineer",
    "hook": "Kafka log fills up on screen",
    "pacing": "medium",
    "density": "balanced",
    "reasons": ["system explainer"],
    "avoid": ["cartoons"],
}


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail_next: LLMError | None = None

    def chat(self, alias, messages, *, system=None, fallbacks=None, **kw):
        self.calls.append({"alias": alias, "system": system, "messages": messages})
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        if system and "creative director" in system:
            text = json.dumps(DIRECTION)
        elif system and "director of mhsquare" in system:
            text = json.dumps(PLAN)
        elif system == GP.CHARACTER_SYSTEM:
            text = SVG
        else:
            text = GOOD
        return text, client.CallResult(alias=alias, cost_usd=0.01)


class FakeRenderer:
    def __init__(self):
        self.requests: list[dict] = []
        self.fail_times = 0

    def render(self, **req):
        self.requests.append(req)
        if self.fail_times:
            self.fail_times -= 1
            raise renderer.RenderError("pageerror: tl is not defined", ["console.error: boom"])
        out = Path(get_settings().render_out_dir)
        out.mkdir(parents=True, exist_ok=True)
        n = len(self.requests)
        v, t = out / f"r{n}.mp4", out / f"r{n}.jpg"
        dur = req["duration_ms"] / 1000
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",  # noqa: S603, S607
                        "-i", f"testsrc2=size=320x180:rate={req['fps']}:duration={dur}", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", str(v)], check=True)  # fmt: skip
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(v), "-frames:v", "1", str(t)],  # noqa: S603, S607
                       check=True)  # fmt: skip
        return renderer.RenderResult(video=v, thumb=t, info={"render_s": 1.0, "timeline_s": dur, "logs": []})


@pytest.fixture
def fakes(monkeypatch):
    celery_app.conf.task_always_eager = True
    llm, rend = FakeLLM(), FakeRenderer()
    monkeypatch.setattr(client, "chat", llm.chat)
    monkeypatch.setattr(renderer, "render", rend.render)
    Path(get_settings().routing_path).unlink(missing_ok=True)  # use built-in default routing
    yield llm, rend
    celery_app.conf.task_always_eager = False


@pytest.fixture
def ready_project(logged_in):
    """A project that finished ingest: final audio + final transcript."""
    client_, csrf = logged_in
    pid = client_.post("/api/projects", json={"name": "slice"}, headers={"X-CSRF-Token": csrf}).json()["id"]
    storage = get_storage()
    with get_sessionmaker()() as db:
        p = db.get(Project, __import__("uuid").UUID(pid))
        key = storage.new_key(p.id, "wav")
        path = storage.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",  # noqa: S603, S607
                        f"sine=f=220:d={DURATION / 1000}", "-ar", "48000", "-ac", "1", str(path)], check=True)  # fmt: skip
        db.add(Asset(project_id=p.id, kind=AssetKind.audio_final, storage_key=key, sha256="x", size=1,
                     mime="audio/wav", meta={"duration_ms": DURATION}))  # fmt: skip
        db.add(Transcript(project_id=p.id, version=2, is_final=True, text="",
                          words=[{"w": w, "start_ms": s, "end_ms": e} for w, s, e in WORDS]))  # fmt: skip
        p.status = ProjectStatus.ready
        db.commit()
    return client_, csrf, pid


def test_plan_generate_review_preview(fakes, ready_project):
    llm, rend = fakes
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}

    # ---- plan
    assert c.post(f"/api/projects/{pid}/plan", headers=h).status_code == 202
    data = c.get(f"/api/projects/{pid}/shots").json()
    assert data["status"] == "planned", data["error"]
    shots = data["shots"]
    assert [(s["start_ms"], s["end_ms"], s["visual_type"], s["theme"]) for s in shots] == [
        (0, 1500, "code_animation", "scenario"),
        (1500, 3500, "code_animation", "tech"),
        (3500, 5000, "text_card", "tech"),
    ]
    assert shots[1]["transcript_text"] == "Kafka is a log."
    assert data["estimate_usd"] > 0 and shots[2]["estimate_usd"] == 0.0
    assert data["style_guide"]["version"] == 1
    planner_call = llm.calls[0]["messages"][0]["content"]
    assert "[2] 1.50–2.70s  Kafka is a log." in planner_call

    # ---- generate everything
    r = c.post(f"/api/projects/{pid}/generate", json={}, headers=h)
    assert r.json()["queued"] == 3
    data = c.get(f"/api/projects/{pid}/shots").json()
    assert [s["state"] for s in data["shots"]] == ["review"] * 3, [s["error"] for s in data["shots"]]
    assert data["characters_drawn"] == []  # no cartoon rigs in on-brand modes
    t0 = data["shots"][0]["takes"][0]
    assert t0["video_url"] and t0["thumb_url"] and t0["cost_usd"] > 0
    # scenario scene: brand kit + scenario kit + fixed icon library + vocabulary; text card is free (no LLM)
    story_req = rend.requests[0]
    assert "MH_ICONS" in story_req["js"] and "mhIcon" in story_req["js"]
    assert ".sc-persona" in story_req["theme_css"] and story_req["duration_ms"] == 1500
    scen_prompt = next(x for x in llm.calls if x["system"] and "sc-persona" in x["system"])
    assert (
        "VISUAL VOCABULARY" in scen_prompt["messages"][0]["content"]
        and "icon 'user-round'" in scen_prompt["messages"][0]["content"]
    )
    assert (
        data["shots"][2]["takes"][0]["model"] == "template" and data["shots"][2]["takes"][0]["cost_usd"] == 0
    )
    # word timings are relative to the shot start
    scene_prompt = next(x for x in llm.calls if x["system"] and "vsc-title" in x["system"])
    assert "0.00s  Kafka" in scene_prompt["messages"][0]["content"]
    assert c.get(t0["video_url"]).status_code == 200

    # ---- regenerate with a note → new take, feedback reaches the LLM
    s1 = data["shots"][1]
    c.post(f"/api/shots/{s1['id']}/regenerate", json={"note": "make the card bigger"}, headers=h)
    takes = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["takes"]
    assert [t["attempt"] for t in takes] == [1, 2] and takes[1]["feedback_note"] == "make the card bigger"
    assert "make the card bigger" in llm.calls[-1]["messages"][0]["content"]

    # ---- same seed + no note → cache hit, no paid call
    n_calls = len(llm.calls)
    c.post(f"/api/shots/{s1['id']}/regenerate", json={"keep_seed": True}, headers=h)
    c.post(f"/api/shots/{s1['id']}/regenerate", json={"keep_seed": True}, headers=h)
    takes = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["takes"]
    assert takes[-1]["meta"].get("cache_hit_of") and takes[-1]["cost_usd"] == 0
    assert len(llm.calls) == n_calls + 1  # only the first of the two keep-seed regenerations paid

    # ---- accept take 1
    r = c.post(f"/api/shots/{s1['id']}/takes/{takes[0]['id']}/accept", headers=h)
    assert r.json()["state"] == "accepted" and r.json()["accepted_take_id"] == takes[0]["id"]

    # ---- preview: every shot on the narration timeline
    assert c.post(f"/api/projects/{pid}/preview", headers=h).status_code == 202
    data = c.get(f"/api/projects/{pid}/shots").json()
    assert data["plan_meta"]["preview"] == {"status": "done", "missing_shots": 0}, data["plan_meta"]
    meta = data["preview"]["meta"]
    assert meta["width"] == 1280 and meta["has_audio"] and abs(meta["duration_ms"] - DURATION) <= 100


def test_render_error_gets_one_fix_round_then_fails(fakes, ready_project):
    llm, rend = fakes
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    sid = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["id"]

    rend.fail_times = 1  # first render fails → error sent back to the LLM → fixed
    c.post("/api/projects/" + pid + "/generate", json={"shot_ids": [sid]}, headers=h)
    shot = next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)
    assert shot["state"] == "review" and shot["takes"][-1]["meta"]["fix_rounds"] == 1
    assert "tl is not defined" in llm.calls[-1]["messages"][-1]["content"]

    rend.fail_times = 2  # fails again after the fix → failed with logs
    c.post(f"/api/shots/{sid}/regenerate", json={"note": "again"}, headers=h)
    shot = next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)
    assert shot["state"] == "failed" and "after a fix attempt" in shot["error"]
    assert any("console.error: boom" in x for x in shot["takes"][-1]["logs"])


def test_budget_and_policy_errors_map_to_shot_states(fakes, ready_project):
    llm, _ = fakes
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    sid = c.get(f"/api/projects/{pid}/shots").json()["shots"][1]["id"]
    llm.fail_next = LLMError(ErrorKind.budget, "Budget has been exceeded")
    c.post(f"/api/projects/{pid}/generate", json={"shot_ids": [sid]}, headers=h)
    assert (
        next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)["state"]
        == "paused_budget"
    )
    llm.fail_next = LLMError(ErrorKind.content_policy, "blocked due to safety")
    c.post(f"/api/shots/{sid}/regenerate", json={}, headers=h)
    shot = next(s for s in c.get(f"/api/projects/{pid}/shots").json()["shots"] if s["id"] == sid)
    assert shot["state"] == "needs_edit"
    # editing the prompt makes it plannable again
    r = c.patch(f"/api/shots/{sid}", json={"prompt": "a gentler brief"}, headers=h)
    assert r.json()["state"] == "planned"


def test_edit_boundaries_split_merge(fakes, ready_project):
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    s = c.get(f"/api/projects/{pid}/shots").json()["shots"]
    # move boundary between shot 0 and 1
    r = c.patch(f"/api/shots/{s[1]['id']}", json={"start_ms": 1200}, headers=h)
    assert r.status_code == 200
    s = c.get(f"/api/projects/{pid}/shots").json()["shots"]
    assert s[0]["end_ms"] == 1200 and s[1]["start_ms"] == 1200
    assert c.patch(f"/api/shots/{s[1]['id']}", json={"start_ms": 100}, headers=h).status_code == 422
    # split shot 1 at 2.2 s
    after = c.post(f"/api/shots/{s[1]['id']}/split", json={"at_ms": 2200}, headers=h).json()
    assert [(x["start_ms"], x["end_ms"]) for x in after] == [
        (0, 1200),
        (1200, 2200),
        (2200, 3500),
        (3500, 5000),
    ]
    assert after[2]["transcript_text"] == "log."
    # merge them back
    merged = c.post("/api/shots/merge", json={"shot_ids": [after[1]["id"], after[2]["id"]]}, headers=h).json()
    assert [(x["start_ms"], x["end_ms"]) for x in merged] == [(0, 1200), (1200, 3500), (3500, 5000)]
    assert (
        c.post(
            "/api/shots/merge", json={"shot_ids": [merged[0]["id"], merged[2]["id"]]}, headers=h
        ).status_code
        == 422
    )


def test_planner_retries_once_with_validation_errors(fakes, ready_project, monkeypatch):
    llm, _ = fakes
    c, csrf, pid = ready_project
    bad = {**PLAN, "shots": PLAN["shots"][:2]}  # misses segment 3
    replies = [json.dumps(bad), json.dumps(PLAN)]

    def chat(alias, messages, *, system=None, **kw):
        llm.calls.append({"messages": messages})
        return replies.pop(0), client.CallResult(alias=alias, cost_usd=0.02)

    monkeypatch.setattr(client, "chat", chat)
    c.post(f"/api/projects/{pid}/plan", headers={"X-CSRF-Token": csrf})
    data = c.get(f"/api/projects/{pid}/shots").json()
    assert data["status"] == "planned" and data["plan_meta"]["attempts"] == 2
    assert "end at segment 2" in llm.calls[-1]["messages"][-1]["content"]
    assert data["plan_meta"]["cost_usd"] == 0.04


def test_template_shot_renders_without_any_ai_call(fakes, ready_project):
    import uuid as _uuid

    from app.models import Shot
    from app.services.generation import templates as T

    llm, rend = fakes
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    with get_sessionmaker()() as db:
        s = db.query(Shot).filter(Shot.project_id == _uuid.UUID(pid), Shot.index == 1).one()
        s.prompt = T.to_prompt("stat", {"value": "50,000", "label": "policy documents", "on": "Kafka"})
        db.commit()
        sid = str(s.id)
    calls_before = len(llm.calls)
    c.post(f"/api/projects/{pid}/generate", json={"shot_ids": [sid]}, headers=h)
    shot = next(x for x in c.get(f"/api/projects/{pid}/shots").json()["shots"] if x["id"] == sid)
    t = shot["takes"][-1]
    assert shot["state"] == "review" and t["model"] == "template:stat" and t["cost_usd"] == 0
    assert len(llm.calls) == calls_before  # no AI call at all
    assert "50,000" in rend.requests[-1]["html"] and "tl.to(o" in rend.requests[-1]["js"]


def test_convert_unfinished_shots_to_templates(fakes, ready_project, monkeypatch):
    import uuid as _uuid

    from app.models import Shot
    from app.services.generation import templates as T

    llm, _ = fakes
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)  # shots 0 (scenario) and 1 (tech) are code_animation
    seen = []

    def chat(alias, messages, *, system=None, **kw):
        seen.append({"system": system, "user": messages[0]["content"]})
        return json.dumps({"shots": [
            {"index": 0, "template": {"name": "scenario", "params": {"persona": {"entity": "x"}, "say": "Hi!"}}},
            {"index": 1, "template": {"name": "steps", "params": {"steps": []}}},  # invalid → stays free-form
        ]}), client.CallResult(alias=alias, cost_usd=0.01)  # fmt: skip

    monkeypatch.setattr(client, "chat", chat)
    r = c.post(f"/api/projects/{pid}/templatize", headers=h)
    assert r.status_code == 202 and r.json()["queued"] == 2
    data = c.get(f"/api/projects/{pid}/shots").json()
    tz = data["plan_meta"]["templatize"]
    assert (tz["status"], tz["converted"], tz["kept"], tz["cost_usd"]) == ("done", 1, 1, 0.01)
    s0, s1 = data["shots"][0], data["shots"][1]
    assert T.parse(s0["prompt"])[0] == "scenario" and "brief: X persona appears" in s0["notes"]
    assert s0["generator"] == "template" and s0["estimate_usd"] == 0
    assert T.parse(s1["prompt"]) is None  # kept its free-form brief
    assert "- scenario:" in seen[0]["system"] and '"narration"' in seen[0]["user"]
    # nothing left that is both unfinished and free-form except shot 1 → converting again still allowed
    with get_sessionmaker()() as db:
        db.query(Shot).filter(Shot.project_id == _uuid.UUID(pid), Shot.index == 1).one().state = "review"
        db.commit()
    assert c.post(f"/api/projects/{pid}/templatize", headers=h).status_code == 409

    # redo: re-choose the template of a finished template shot for a better mix
    with get_sessionmaker()() as db:
        db.query(Shot).filter(Shot.project_id == _uuid.UUID(pid), Shot.index == 0).one().state = "review"
        db.commit()
    seen.clear()

    def rechoose(alias, messages, *, system=None, **kw):
        seen.append(messages[0]["content"])
        return json.dumps({"shots": [{"index": 0, "template": {"name": "hub", "params": {
            "center": {"label": "AI app"}, "spokes": [{"label": "Java"}, {"label": "API"}]}}}]}), \
            client.CallResult(alias=alias, cost_usd=0.01)  # fmt: skip

    monkeypatch.setattr(client, "chat", rechoose)
    assert c.post(f"/api/projects/{pid}/templatize?redo=true", headers=h).json()["queued"] == 1
    s0 = c.get(f"/api/projects/{pid}/shots").json()["shots"][0]
    assert T.parse(s0["prompt"])[0] == "hub" and s0["state"] == "planned"  # next take renders the new one
    assert s0["notes"].count("brief:") == 1 and "X persona appears" in seen[0]  # original brief, not the JSON
    with get_sessionmaker()() as db:  # accepted shots are never re-chosen
        db.query(Shot).filter(Shot.project_id == _uuid.UUID(pid), Shot.index == 0).one().state = "accepted"
        db.commit()
    assert c.post(f"/api/projects/{pid}/templatize?redo=true", headers=h).status_code == 409


def test_broken_template_json_fails_with_a_clear_message(fakes, ready_project):
    import uuid as _uuid

    from app.models import Shot

    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    with get_sessionmaker()() as db:
        s = db.query(Shot).filter(Shot.project_id == _uuid.UUID(pid), Shot.index == 1).one()
        s.prompt = '@template steps\n{"steps": []}'
        db.commit()
        sid = str(s.id)
    c.post(f"/api/projects/{pid}/generate", json={"shot_ids": [sid]}, headers=h)
    shot = next(x for x in c.get(f"/api/projects/{pid}/shots").json()["shots"] if x["id"] == sid)
    assert shot["state"] == "failed" and "fix the shot's JSON" in shot["error"]


def test_worker_boot_fails_interrupted_takes_and_skips_them(fakes, ready_project):
    import uuid as _uuid

    from app.models import Shot, ShotState, Take
    from app.services import generation

    c, csrf, pid = ready_project
    c.post(f"/api/projects/{pid}/plan", headers={"X-CSRF-Token": csrf})
    with get_sessionmaker()() as db:
        shots = db.query(Shot).filter(Shot.project_id == _uuid.UUID(pid)).order_by(Shot.index).all()
        running, pending = (generation.queue_shot(db, shots[0]), generation.queue_shot(db, shots[1]))
        running.status, shots[0].state = "running", ShotState.generating
        db.commit()
        rid, pend_id = running.id, pending.id
    assert generation.recover_interrupted() == 1
    with get_sessionmaker()() as db:
        t = db.get(Take, rid)
        assert t.status == "failed" and "Interrupted" in t.error
        assert db.get(Shot, t.shot_id).state == ShotState.failed
        assert db.get(Take, pend_id).status == "pending"  # still in the durable queue
    calls = len(fakes[0].calls)
    generation.run_take(str(rid))  # a redelivered message for a failed take is a no-op
    assert len(fakes[0].calls) == calls


def test_worker_boot_fails_a_preview_build_that_died_with_the_worker(ready_project):
    import uuid as _uuid

    from app.services.assembly import preview

    _, _, pid = ready_project
    with get_sessionmaker()() as db:
        p = db.get(Project, _uuid.UUID(pid))
        p.plan_meta = {"preview": {"status": "running"}, "final": {"status": "done"}}
        db.commit()
    assert preview.recover_interrupted() == 1
    with get_sessionmaker()() as db:
        meta = db.get(Project, _uuid.UUID(pid)).plan_meta
    assert meta["preview"]["status"] == "failed" and "Interrupted" in meta["preview"]["error"]
    assert meta["final"] == {"status": "done"}


def test_planner_truncated_reply_retries_compact_with_more_tokens(fakes, ready_project, monkeypatch):
    c, csrf, pid = ready_project
    seen = []

    def chat(alias, messages, *, system=None, max_tokens=0, **kw):
        seen.append((max_tokens, messages[-1]["content"]))
        if len(seen) == 1:
            raise LLMError(ErrorKind.invalid, f"{alias}: response cut off at max_tokens={max_tokens}")
        return json.dumps(PLAN), client.CallResult(alias=alias, cost_usd=0.02)

    monkeypatch.setattr(client, "chat", chat)
    c.post(f"/api/projects/{pid}/plan", headers={"X-CSRF-Token": csrf})
    data = c.get(f"/api/projects/{pid}/shots").json()
    assert data["status"] == "planned" and data["plan_meta"]["attempts"] == 2
    assert seen[0][0] >= 16000 and seen[1][0] == min(64000, seen[0][0] * 2)
    assert "cut off" in seen[1][1] and len(seen) == 2


def test_preview_composites_facecam_circle_and_shorts_canvas(fakes, ready_project):
    """Long-form: face-cam circle bottom-right; shorts: vertical 9:16 proxy with the 3:4 canvas."""
    import uuid as _uuid

    from app.services.assembly.preview import build_preview

    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    storage = get_storage()
    with get_sessionmaker()() as db:
        p = db.get(Project, _uuid.UUID(pid))
        key = storage.new_key(p.id, "mp4")
        path = storage.path(key)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",  # noqa: S603, S607
                        f"testsrc2=size=640x360:rate=30:duration={DURATION / 1000}", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)  # fmt: skip
        db.add(Asset(project_id=p.id, kind=AssetKind.facecam_final, storage_key=key, sha256="x", size=1,
                     mime="video/mp4", meta={"duration_ms": DURATION}))  # fmt: skip
        db.commit()
    c.post(f"/api/projects/{pid}/plan", headers=h)
    # before any shot exists the frame is the dark brand background: the face cam must be cut to a circle,
    # its centre showing the camera and the corner of its square staying background
    from app.services import style
    from app.services.assembly.preview import facecam_geometry
    from app.services.ingest.pipeline import latest_asset

    build_preview(pid)
    with get_sessionmaker()() as db:
        p = db.get(Project, _uuid.UUID(pid))
        cx, cy, dia = facecam_geometry(style.canvas(style.active(db), p), 1280, 720)
        prev = storage.path(latest_asset(db, p.id, "preview").storage_key)
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "0.5", "-i", str(prev), "-frames:v", "1",  # noqa: S603, S607
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout  # fmt: skip

    def px(x: float, y: float) -> tuple[int, ...]:
        i = (int(y) * 1280 + int(x)) * 3
        return tuple(raw[i : i + 3])

    inside, corner = px(cx, cy), px(cx - dia / 2 + 3, cy - dia / 2 + 3)
    assert max(inside) > 60 and max(corner) < 60, (inside, corner)  # testsrc2 is bright; the brand bg is dark

    c.post(f"/api/projects/{pid}/generate", json={}, headers=h)
    build_preview(pid)
    d = c.get(f"/api/projects/{pid}/shots").json()
    assert d["plan_meta"]["preview"]["status"] == "done", d["plan_meta"]
    assert d["preview"]["meta"]["layout"] == "long_form" and d["preview"]["meta"]["width"] == 1280
    # a long-form plan never contains a_roll (face cam is the PiP circle)
    assert all(s["visual_type"] != "a_roll" for s in d["shots"])

    c.patch(f"/api/projects/{pid}", json={"content_type": "short_series"}, headers=h)  # locked once planned
    with get_sessionmaker()() as db:
        db.get(Project, _uuid.UUID(pid)).content_type = "short_series"
        db.commit()
    build_preview(pid)
    meta = c.get(f"/api/projects/{pid}/shots").json()["preview"]["meta"]
    assert (meta["width"], meta["height"], meta["layout"]) == (540, 960, "shorts")


def test_preview_reuses_cached_shot_clips(fakes, ready_project, monkeypatch):
    """Each take's preview clip is made once (right after it renders); rebuilding the preview only joins clips."""
    import uuid as _uuid

    from app.services.assembly import preview

    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    c.post(f"/api/projects/{pid}/plan", headers=h)
    c.post(f"/api/projects/{pid}/generate", json={}, headers=h)  # eager: each take also warms its clip
    cache = preview._proxy_cache(_uuid.UUID(pid))
    warmed = sorted(f.name for f in cache.glob("*.mp4"))
    assert warmed and not list(cache.glob("*.part"))
    encoded = []
    real = preview._take_segment
    monkeypatch.setattr(preview, "_take_segment", lambda *a: (encoded.append(a[1]), real(*a)))
    preview.build_preview(pid)
    assert (
        encoded == []
        and c.get(f"/api/projects/{pid}/shots").json()["plan_meta"]["preview"]["status"] == "done"
    )
    assert sorted(f.name for f in cache.glob("*.mp4")) == warmed  # nothing stale left behind
