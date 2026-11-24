"""Shorts from the long video: the model picks; code enforces whole shots, length, no overlap, series parts,
posting order and limits. Renders are faked (no renderer, no paid provider)."""

import json
from types import SimpleNamespace

import pytest

from app.llm import client
from app.services import shorts as SH
from tests.test_generation_flow import fakes, ready_project  # noqa: F401


def shot(i: int, start_s: float, end_s: float):
    return SimpleNamespace(index=i, start_ms=int(start_s * 1000), end_ms=int(end_s * 1000), transcript_text=f"t{i}",
                           duration_ms=int((end_s - start_s) * 1000))  # fmt: skip


SHOTS = [shot(i, i * 10, i * 10 + 10) for i in range(20)]  # 20 shots × 10 s


def pick(a, b, score=5, series=None, title="Title"):
    return SH.Pick.model_validate({"from": a, "to": b, "series": series, "hook": "Hook", "title": title,
                                   "description": "What you learn.", "hashtags": ["Java", "#AI", "shorts"],
                                   "score": score})  # fmt: skip


def test_clips_are_whole_shots_of_valid_length_without_overlap():
    out = SH.normalise([pick(0, 2, 9), pick(1, 3, 8),  # overlaps the stronger one → dropped
                        pick(5, 5, 7),  # 10 s: too short
                        pick(6, 14, 7),  # 90 s: too long
                        pick(10, 13, 6), pick(30, 31, 9)],  # 30 s ok; shot 30 doesn't exist
                       SHOTS, long_title="Long video")  # fmt: skip
    assert [(o["shot_from"], o["shot_to"]) for o in out] == [(0, 2), (10, 13)]
    first = out[0]
    assert (first["start_ms"], first["end_ms"], first["index"]) == (0, 30000, 1)
    assert first["hashtags"] == ["#Java", "#AI"] and first["description"].endswith("#Java #AI #Shorts")
    assert "Full video: “Long video”" in first["description"]


def test_series_parts_are_numbered_in_time_order_and_posted_on_consecutive_days():
    out = SH.normalise([pick(10, 12, 6, "RAG"), pick(0, 2, 6, "RAG"), pick(4, 6, 9),
                        pick(14, 16, 5, "Lonely series")],  # a 1-part series is just a clip
                       SHOTS)  # fmt: skip
    rows = {(o["shot_from"]): o for o in out}
    assert rows[4]["index"] == 1  # strongest first
    assert (rows[0]["part"], rows[10]["part"], rows[0]["parts"]) == (1, 2, 2)
    assert rows[10]["index"] == rows[0]["index"] + 1 and "(Part 2/2)" in rows[10]["title"]
    assert rows[14]["series"] is None and rows[14]["part"] is None


def test_existing_rendered_ranges_are_respected():
    out = SH.normalise([pick(0, 2, 9), pick(4, 6, 8)], SHOTS, taken=[(1, 3)])
    assert [(o["shot_from"], o["shot_to"]) for o in out] == [(4, 6)]


def test_captions_highlight_the_spoken_word_in_short_groups():
    words = [{"w": w, "start_ms": 1000 + k * 300, "end_ms": 1250 + k * 300}
             for k, w in enumerate("MCP is a standard. Build once {use} anywhere".split())]  # fmt: skip
    ass = SH.captions_ass(words, 1000, 5000, "#e63946")
    lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
    assert len(lines) == len(words)  # one event per spoken word
    assert lines[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.30") and "{\\c&H004639E6&}MCP" in lines[0]
    assert "standard." in lines[3] and "Build" not in lines[3]  # a sentence end closes the group
    assert "(use)" in ass and "{use}" not in ass  # braces can't inject ASS tags
    assert f",{SH.H - SH.CAPTION_BOTTOM},1" in ass and "PlayResY: 1920" in ass


def test_edit_validates_and_marks_a_changed_hook_for_re_render():
    c = SimpleNamespace(hook="Old", title="t", description="d", hashtags=[], status="done", meta={})
    SH.edit(c, {"hook": "What is MCP?", "hashtags": "java, #AI"})
    assert c.hook == "What is MCP?" and c.meta["stale"] and c.hashtags == ["#java", "#AI"]
    with pytest.raises(SH.PlanError):
        SH.edit(c, {"title": ""})
    with pytest.raises(SH.PlanError):
        SH.edit(c, {"description": "a <b>"})


def test_plan_and_render_through_the_api(fakes, ready_project, monkeypatch, tmp_path):  # noqa: F811
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    assert c.post(f"/api/projects/{pid}/shorts/plan", headers=h).status_code == 409  # no shots yet
    c.post(f"/api/projects/{pid}/plan", headers=h)

    def chat(alias, messages, *, system=None, **kw):
        assert "Shorts" in system and "[0:00" in messages[0]["content"]
        return json.dumps({"shorts": [{"from": 0, "to": 1, "hook": "Why Kafka?", "title": "Kafka in 5 seconds",
                                       "description": "Learn it.", "hashtags": ["#Kafka"], "score": 8}]}), \
            client.CallResult(alias=alias, cost_usd=0.02)  # fmt: skip

    monkeypatch.setattr(client, "chat", chat)
    monkeypatch.setattr(SH, "MIN_MS", 1000)  # the test project is only 5 s long
    from app.workers.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    try:
        assert c.post(f"/api/projects/{pid}/shorts/plan", headers=h).status_code == 202
        d = c.get(f"/api/projects/{pid}/shorts").json()
        assert (
            d["status"]["status"] == "done"
            and len(d["shorts"]) == 1
            and d["layout"]["face_zone"] == [64, 1240, 600, 620]
        )
        s = d["shorts"][0]
        assert s["hook"] == "Why Kafka?" and s["status"] == "planned" and s["url"] is None
        e = c.put(f"/api/shorts/{s['id']}", json={"title": "Kafka, simply"}, headers=h).json()
        assert e["title"] == "Kafka, simply"

        # render: the stage segments and the still frame are faked; ffmpeg composes the real 9:16 file
        import subprocess

        def fake_segment(db, p, profile, shot, cache, words):
            out = tmp_path / f"seg{shot.index}.mp4"
            n = round(shot.end_ms * p.fps / 1000) - round(shot.start_ms * p.fps / 1000)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",  # noqa: S603, S607
                            f"color=c=0x203040:s=1080x1000:r={p.fps}", "-frames:v", str(n), "-pix_fmt", "yuv420p",
                            str(out)], check=True)  # fmt: skip
            return out

        def fake_chrome(p, profile, clip):
            from PIL import Image

            f = tmp_path / "chrome.png"
            Image.new("RGB", (1080, 1920), (6, 11, 20)).save(f)
            return f

        monkeypatch.setattr(SH, "stage_segment", fake_segment)
        monkeypatch.setattr(SH, "chrome_png", fake_chrome)
        ends = []

        def fake_end(p, profile, clip, nxt, long_title, cache):
            ends.append((clip.hook, nxt, long_title))
            out = tmp_path / "end.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",  # noqa: S603, S607
                            f"color=c=0x00ff00:s=1080x880:r={p.fps}:d=3", "-pix_fmt", "yuv420p", str(out)],
                           check=True)  # fmt: skip
            return out

        monkeypatch.setattr(SH, "end_card_video", fake_end)
        assert c.post(f"/api/projects/{pid}/shorts/render", headers=h).json()["queued"] == 1
        s = c.get(f"/api/projects/{pid}/shorts").json()["shorts"][0]
        assert s["status"] == "done", s["error"]
        r = c.get(s["url"])
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_type",  # noqa: S603, S607
                                "-of", "json", "-"], input=r.content, capture_output=True, check=True)  # fmt: skip
        streams = json.loads(probe.stdout)["streams"]
        assert {"codec_type": "video", "width": 1080, "height": 1920} in streams
        assert any(x["codec_type"] == "audio" for x in streams)
        assert s["with_face"] is False  # no face-cam yet: the zone stays empty for the creator

        # with a face-cam recording, the same moment fills the zone below the animation
        import uuid as _uuid

        from app.core.db import get_sessionmaker
        from app.models import Asset, AssetKind
        from app.services.storage import get_storage

        with get_sessionmaker()() as db:
            key = get_storage().new_key(_uuid.UUID(pid), "mp4")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",  # noqa: S603, S607
                            "color=c=0xf0c8a0:s=640x360:r=30:d=6", "-pix_fmt", "yuv420p",
                            str(get_storage().path(key))], check=True)  # fmt: skip
            db.add(Asset(project_id=_uuid.UUID(pid), kind=AssetKind.facecam_final, storage_key=key, sha256="x",
                         size=1, mime="video/mp4", meta={}))  # fmt: skip
            db.commit()
        assert c.post(f"/api/shorts/{s['id']}/render", headers=h).status_code == 202
        s = c.get(f"/api/projects/{pid}/shorts").json()["shorts"][0]
        assert s["status"] == "done" and s["with_face"] is True, s["error"]
        mp4 = tmp_path / "face.mp4"
        mp4.write_bytes(c.get(s["url"]).content)

        def pixel(y: int) -> tuple[int, ...]:
            raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "0.5", "-i", str(mp4), "-frames:v",  # noqa: S603, S607
                                  "1", "-vf", f"crop=2:2:540:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True, check=True).stdout  # fmt: skip
            return tuple(raw[:3])

        assert pixel(1600)[0] > 200  # the (skin-tone) face-cam fills the bottom zone
        assert pixel(700)[0] < 80  # the animation stage above is untouched
        assert c.delete(f"/api/shorts/{s['id']}", headers=h).status_code == 204
        assert c.get(f"/api/projects/{pid}/shorts").json()["shorts"] == []
    finally:
        celery_app.conf.task_always_eager = False


def test_end_card_points_to_the_next_part_or_to_more():
    part1 = SimpleNamespace(series="Two worlds of AI", part=1, parts=3, hook="Where does it come from?")
    part2 = SimpleNamespace(series="Two worlds of AI", part=2, parts=3, hook="Use models <without> training")
    html, js = SH.end_card(part1, part2, "Java devs: do you need Python?")
    assert 'Part <span class="hl">2</span> next' in html and "Follow so you don" in html
    assert "Use models &lt;without&gt; training" in html  # escaped
    assert 'data-icon="bell-ring"' in html and "tl.set({}, {}, D)" in js and "const D = 3.0" in js
    last = SimpleNamespace(series="Two worlds of AI", part=3, parts=3, hook="h")
    html, _ = SH.end_card(last, None, "Java devs: do you need Python?")
    assert "for more" in html and "Full video: Java devs" in html
    alone = SimpleNamespace(series=None, part=None, parts=None, hook="h")
    assert "for more" in SH.end_card(alone, None, "")[0]


def test_bell_icons_are_in_the_library():
    from app.services.generation.icons import icons

    assert {"bell", "bell-ring", "user-plus"} <= set(icons())
