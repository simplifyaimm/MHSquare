"""Phase 2 done-criterion, end to end through the API with real ffmpeg and a fake transcriber:
sample audio → proposed cuts → approve → cleaned+cut audio + final transcript with word timestamps."""

import subprocess
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.ingest import media
from app.services.ingest.transcribe import FakeTranscriber
from app.services.storage import get_storage
from app.workers.celery_app import celery_app
from tests.conftest import TMPDIR

SCRIPT_MD = SCRIPT = b"# Kafka in 5 minutes\n\nKafka is a distributed log.\n"


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)  # noqa: S603, S607


@pytest.fixture(scope="module")
def sample_audio() -> Path:
    """5 s tone with gaps: speech 0–1 s, silence 1–2.5 s, 'um' 2.5–3 s, silence, speech 3.2–5 s."""
    out = Path(TMPDIR) / "narration.wav"
    vol = "volume='if(between(t,1,2.5)+between(t,3,3.2),0,1)':eval=frame"
    _ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=5:sample_rate=44100", "-af", vol, str(out))
    return out


@pytest.fixture(scope="module")
def sample_facecam() -> Path:
    out = Path(TMPDIR) / "facecam.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=5", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", str(out))  # fmt: skip
    return out


@pytest.fixture(autouse=True)
def _eager(monkeypatch):
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    words = [("Kafka", 0, 400), ("is", 450, 600), ("fast.", 650, 1000),
             ("um,", 2500, 3000), ("It", 3200, 3400), ("scales.", 3450, 5000)]  # fmt: skip
    monkeypatch.setattr(FakeTranscriber, "script", words)
    yield
    celery_app.conf.task_always_eager = False


def _upload(client, csrf, pid, kind, path_or_bytes, filename):
    data = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    h = {"X-CSRF-Token": csrf}
    init = client.post(f"/api/projects/{pid}/uploads", json={"kind": kind, "filename": filename, "size": len(data)},
                       headers=h)  # fmt: skip
    assert init.status_code == 201, init.text
    uid, chunk = init.json()["id"], 3000  # tiny chunks to exercise resumable offsets
    for off in range(0, len(data), chunk):
        r = client.put(f"/api/uploads/{uid}?offset={off}", content=data[off : off + chunk],
                       headers=h | {"Content-Type": "application/octet-stream"})  # fmt: skip
        assert r.status_code == 200, r.text
    done = client.post(f"/api/uploads/{uid}/complete", headers=h)
    assert done.status_code == 201, done.text
    return done.json()


def _new_project(client, csrf, name="Kafka explainer"):
    r = client.post("/api/projects", json={"name": name}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    return r.json()["id"]


def test_full_ingest_flow(logged_in, sample_audio, sample_facecam):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    pid = _new_project(client, csrf)

    # ingest refuses to start without inputs
    assert client.post(f"/api/projects/{pid}/ingest", headers=h).status_code == 422

    _upload(client, csrf, pid, "script", SCRIPT, "script.md")
    audio = _upload(client, csrf, pid, "audio", sample_audio, "take1.wav")
    assert audio["mime"] == "audio/wav" and 4900 <= audio["meta"]["duration_ms"] <= 5100
    _upload(client, csrf, pid, "facecam", sample_facecam, "cam.mp4")

    r = client.post(f"/api/projects/{pid}/ingest", headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "cuts_review", r.json()["error"]

    cuts = client.get(f"/api/projects/{pid}/cuts").json()
    kinds = [(c["kind"], c["text"]) for c in cuts["ranges"]]
    assert kinds == [("filler", "um,")]  # 1.0→3.2 s gap contains the filler: one cut
    assert cuts["ranges"][0]["start_ms"] == 1150 and cuts["ranges"][0]["end_ms"] == 3050
    assert cuts["audio"]["meta"]["cleaner"] == "ffmpeg"

    raw = client.get(f"/api/projects/{pid}/transcript?final=false").json()
    assert raw["is_final"] is False and raw["words"][0]["w"] == "Kafka"
    assert client.get(f"/api/projects/{pid}/waveform?stage=clean").json()["peaks"]

    # approving with an unknown id is rejected; the real approve applies the cut
    assert (
        client.post(f"/api/projects/{pid}/cuts/approve", json={"enabled_ids": ["zzz"]}, headers=h).status_code
        == 409
    )
    r = client.post(f"/api/projects/{pid}/cuts/approve", json={"enabled_ids": ["c1"]}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ready", r.json()["error"]

    assets = {a["kind"]: a for a in r.json()["assets"]}
    final_ms = assets["audio_final"]["meta"]["duration_ms"]
    removed = assets["audio_final"]["meta"]["removed_ms"]
    assert removed == 1934  # 1150–3050 snapped to the 30 fps grid → 1133–3067
    assert abs(final_ms - (5000 - removed)) <= 40
    # face-cam is cut with the same (frame-snapped) segments → same length as the audio
    cam = assets["facecam_final"]["meta"]
    assert cam["fps"] == 30 and abs(cam["duration_ms"] - final_ms) <= 70

    final = client.get(f"/api/projects/{pid}/transcript").json()
    assert final["is_final"] is True and final["version"] == 2
    assert all({"w", "start_ms", "end_ms"} <= w.keys() for w in final["words"])
    assert client.get(f"/api/projects/{pid}/waveform?stage=final").status_code == 200

    # inputs are locked after ingest
    assert client.post(f"/api/projects/{pid}/uploads", json={"kind": "script", "filename": "a.md", "size": 3},
                       headers=h).status_code == 409  # fmt: skip


def test_one_talking_head_video_is_enough(logged_in, sample_audio):
    """No separate audio: the face-cam recording's own sound becomes the narration; picture and sound stay in
    sync through the cuts."""
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    video = Path(TMPDIR) / "talking.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=5", "-i", str(sample_audio),
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            str(video))  # fmt: skip
    pid = _new_project(client, csrf, "One recording")
    _upload(client, csrf, pid, "script", SCRIPT, "script.md")
    _upload(client, csrf, pid, "facecam", video, "talking.mp4")
    client.patch(f"/api/projects/{pid}", json={"facecam_offset_ms": 300}, headers=h)  # ignored: same file
    r = client.post(f"/api/projects/{pid}/ingest", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "cuts_review", r.json()["error"]
    cuts = client.get(f"/api/projects/{pid}/cuts").json()
    assert cuts["audio"]["meta"]["from_video"] is True and [c["text"] for c in cuts["ranges"]] == ["um,"]
    r = client.post(f"/api/projects/{pid}/cuts/approve", json={"enabled_ids": ["c1"]}, headers=h).json()
    assert r["status"] == "ready" and r["facecam_offset_ms"] == 0, r["error"]
    assets = {a["kind"]: a for a in r["assets"]}
    assert (
        abs(assets["facecam_final"]["meta"]["duration_ms"] - assets["audio_final"]["meta"]["duration_ms"])
        <= 70
    )


def test_failure_is_recorded_and_retry_resumes(logged_in, sample_audio, monkeypatch):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    pid = _new_project(client, csrf)
    _upload(client, csrf, pid, "script", SCRIPT, "s.txt")
    _upload(client, csrf, pid, "audio", sample_audio, "a.wav")

    def boom(*a, **k):
        raise media.MediaError("disk on fire")

    monkeypatch.setattr(media.FfmpegCleaner, "clean", boom)
    r = client.post(f"/api/projects/{pid}/ingest", headers=h).json()
    assert r["status"] == "failed" and "disk on fire" in r["error"] and r["ingest_step"] == "cleanup"

    monkeypatch.undo()
    monkeypatch.setattr(FakeTranscriber, "script", [("hi", 0, 4000)])
    r = client.post(f"/api/projects/{pid}/ingest", headers=h).json()
    assert r["status"] == "cuts_review" and r["error"] is None


@pytest.mark.parametrize(
    ("kind", "filename", "content", "msg"),
    [
        ("audio", "evil.exe", b"MZ....", "allowed file types"),
        ("audio", "fake.wav", b"#!/bin/sh\nrm -rf /\n" * 10, "not an accepted type"),
        ("script", "s.md", b"\xff\xfe\x00bad", "UTF-8"),
        ("character_ref", "c.png", b"GIF89a......", "not an accepted type"),
        ("facecam", "cam.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100, "could not read media"),
    ],
)
def test_upload_validation(logged_in, kind, filename, content, msg):
    client, csrf = logged_in
    pid = _new_project(client, csrf)
    r = client.post(f"/api/projects/{pid}/assets", data={"kind": kind},
                    files={"file": (filename, content)}, headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert r.status_code == 422 and msg in r.json()["detail"], r.text
    # nothing left behind in storage
    assert not list((Path(get_settings().storage_root) / "projects" / pid).glob("*"))


def test_chunked_upload_rejects_wrong_offset_and_oversize(logged_in):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    pid = _new_project(client, csrf)
    uid = client.post(f"/api/projects/{pid}/uploads", json={"kind": "script", "filename": "a.md", "size": 5},
                      headers=h).json()["id"]  # fmt: skip
    assert client.put(f"/api/uploads/{uid}?offset=2", content=b"abc", headers=h).status_code == 409
    assert client.put(f"/api/uploads/{uid}?offset=0", content=b"abcdefgh", headers=h).status_code == 413
    assert client.put(f"/api/uploads/{uid}?offset=0", content=b"abc", headers=h).json()["received"] == 3
    assert client.post(f"/api/uploads/{uid}/complete", headers=h).status_code == 409  # incomplete
    assert client.get(f"/api/uploads/{uid}").json()["received"] == 3  # resumable


def test_character_ref_limit(logged_in):
    client, csrf = logged_in
    pid = _new_project(client, csrf)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    for i in range(3):
        r = client.post(f"/api/projects/{pid}/assets", data={"kind": "character_ref"},
                        files={"file": (f"c{i}.png", png)}, headers={"X-CSRF-Token": csrf})  # fmt: skip
        assert r.status_code == 201
    r = client.post(f"/api/projects/{pid}/assets", data={"kind": "character_ref"},
                    files={"file": ("c4.png", png)}, headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert r.status_code == 422


def test_media_signed_url_and_ranges(logged_in):
    client, csrf = logged_in
    pid = _new_project(client, csrf)
    asset = client.post(f"/api/projects/{pid}/assets", data={"kind": "script"},
                        files={"file": ("s.md", SCRIPT)}, headers={"X-CSRF-Token": csrf}).json()  # fmt: skip
    url = asset["url"]
    full = client.get(url)
    assert full.status_code == 200 and full.content == SCRIPT and full.headers["accept-ranges"] == "bytes"
    part = client.get(url, headers={"Range": "bytes=2-6"})
    assert part.status_code == 206 and part.content == SCRIPT[2:7]
    assert part.headers["content-range"] == f"bytes 2-6/{len(SCRIPT)}"
    assert client.get(url, headers={"Range": "bytes=-5"}).content == SCRIPT[-5:]
    assert client.get(url, headers={"Range": "bytes=9999-"}).status_code == 416
    assert client.get(url.replace("sig=", "sig=x")).status_code == 403
    client.cookies.clear()  # signed URL works without a session (for <video> tags)
    assert client.get(url).status_code == 200


def test_delete_project_removes_files(logged_in):
    client, csrf = logged_in
    pid = _new_project(client, csrf)
    client.post(f"/api/projects/{pid}/assets", data={"kind": "script"},
                files={"file": ("s.md", SCRIPT)}, headers={"X-CSRF-Token": csrf})  # fmt: skip
    proj_dir = get_storage().root / "projects" / pid
    assert any(proj_dir.iterdir())
    assert client.delete(f"/api/projects/{pid}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert not proj_dir.exists()
    assert client.get(f"/api/projects/{pid}").status_code == 404
