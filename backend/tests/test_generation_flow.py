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
from app.services.planner import prompt as PP
from app.services.storage import get_storage
from app.workers.celery_app import celery_app
from tests.test_scene_and_llm import GOOD

WORDS = [("Meet", 0, 300), ("Robin.", 350, 900), ("Kafka", 1500, 1900), ("is", 1950, 2050), ("a", 2100, 2150),
         ("log.", 2200, 2700), ("That", 3500, 3700), ("is", 3750, 3850), ("all.", 3900, 4400)]  # fmt: skip
DURATION = 5000

PLAN = {
    "style_guide": {"summary": "navy tech + teal story", "mood": "calm",
                    "characters": [{"id": "robin", "name": "Robin", "description": "glasses, red tie"}]},
    "shots": [
        {"from_segment": 1, "to_segment": 1, "visual_type": "code_animation", "theme": "story",
         "prompt": "Robin waves; bubble 'Meet Robin!'", "characters": ["robin"]},
        {"from_segment": 2, "to_segment": 2, "visual_type": "code_animation", "theme": "tech",
         "prompt": "Card 'Kafka' with eyebrow 'COMMIT LOG'"},
        {"from_segment": 3, "to_segment": 3, "visual_type": "text_card", "theme": "tech",
         "prompt": "Title 'That is all' sub 'Thanks for watching'"},
    ],
}  # fmt: skip
SVG = (
    '```svg\n<svg viewBox="0 0 400 800" xmlns="http://www.w3.org/2000/svg"><g class="legs"/><g class="body"/>'
    '<g class="arm-l"/><g class="arm-r"/><g class="head"><g class="eyes"/><g class="mouth"/></g></svg>\n```'
)


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail_next: LLMError | None = None

    def chat(self, alias, messages, *, system=None, fallbacks=None, **kw):
        self.calls.append({"alias": alias, "system": system, "messages": messages})
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        if system == PP.SYSTEM:
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
        (0, 1500, "code_animation", "story"),
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
    assert data["characters_drawn"] == ["robin"]  # character rig drawn once
    t0 = data["shots"][0]["takes"][0]
    assert t0["video_url"] and t0["thumb_url"] and t0["cost_usd"] > 0
    # story scene got the character template + helper runtime; text card is free (no LLM)
    story_req = rend.requests[0]
    assert 'id="mh-char-robin"' in story_req["html"] and "mhCharacter" in story_req["js"]
    assert "st-bg" in story_req["theme_css"] and story_req["duration_ms"] == 1500
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
