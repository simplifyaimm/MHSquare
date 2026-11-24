"""AI-designed covers: the model plans content, an image model draws the design, the creator's real photo is
composited in code. No test calls a paid provider: the image model is faked with generated PNGs."""

import io
import json

from PIL import Image, ImageDraw

from app.core.db import get_sessionmaker
from app.llm import client
from app.models.style import BrandAsset
from app.services import covers_ai as AI
from app.services.storage import get_storage

CONCEPT = {"eyebrow": "Java developers", "headline": ["Java is", "enough"], "highlight": "ENOUGH",
           "subline": "Do you really need Python for AI?", "pills": ["Skip the Python detour"],
           "visual": {"kind": "hub", "center": "Spring AI", "items": ["OpenAI", "Claude", "pgvector"]},
           "strip": ["RAG", "Evaluation"]}  # fmt: skip


def png(w: int, h: int, busy_right: bool = False) -> bytes:
    img = Image.new("RGB", (w, h), (6, 11, 20))
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, w // 3, h // 3), outline=(230, 57, 70), width=6)  # a card on the left
    if busy_right:  # cards and text drawn where the photo goes
        for k in range(12):
            x = int(w * 0.62) + k * 40
            d.rectangle((x, 100 + k * 60, x + 160, 160 + k * 60), outline=(255, 255, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_concept_is_trimmed_at_word_boundaries_and_uppercased():
    c = AI.Concept.model_validate({**CONCEPT, "visual": {"kind": "versus", "items": ["Python first", "Spring Boot + model"]},
                                   "headline": "Train vs use models today"})  # fmt: skip
    assert c.headline == ["TRAIN VS USE"]  # a single string is one line, cut at a word boundary
    assert c.visual.items[1] == "Spring Boot + model"  # ≤ 22 characters: kept whole
    assert AI._clip("Spring Boot + models in production", 22) == "Spring Boot + models"
    assert AI.Concept.model_validate({**CONCEPT, "visual": {"kind": "rocket"}}).visual.kind == "hub"


def test_image_prompt_fixes_style_and_keeps_the_photo_zone_empty():
    c = AI.Concept.model_validate(CONCEPT)
    p = AI.image_prompt(c, "youtube", "#e63946", person=True)
    assert '"JAVA IS" in white' in p and '"ENOUGH" in bold #e63946' in p
    assert "RIGHT 42% OF THE WIDTH" in p and "EMPTY" in p and "#060b14" in p and "EXACTLY" in p
    assert '"OpenAI"' in p and "simple matching line icon" in p
    assert "RIGHT 42%" not in AI.image_prompt(c, "youtube", "#e63946", person=False)
    assert "BOTTOM 42% OF THE HEIGHT" in AI.image_prompt(c, "short", "#e63946", person=True)


def test_zone_check_separates_clean_and_busy_designs():
    assert AI.zone_busy(png(1920, 1088), "youtube") <= AI.ZONE_BUSY_MAX
    assert AI.zone_busy(png(1920, 1088, busy_right=True), "youtube") > AI.ZONE_BUSY_MAX


def _photo(tmp_path):
    f = tmp_path / "me.png"
    Image.new("RGBA", (300, 400), (200, 150, 120, 255)).save(f)
    return f


def test_photo_is_composited_inside_the_reserved_zone(tmp_path):
    jpg = AI.composite(png(1920, 1088), "youtube", _photo(tmp_path), "#e63946")
    img = Image.open(io.BytesIO(jpg)).convert("RGB")
    assert img.size == (1920, 1080) and len(jpg) < 2_000_000
    x0 = round(1920 * (1 - AI.PHOTO_ZONE))
    assert (
        img.getpixel((x0 + 60, 700))[0] > 150
    )  # the photo (skin tone) in the zone (its bottom edge fades out)
    assert img.getpixel((x0 - 120, 700))[0] < 60  # left of the zone: still the dark design
    short = Image.open(io.BytesIO(AI.composite(png(1088, 1920), "short", _photo(tmp_path), "#e63946")))
    assert short.size == (1080, 1920)


def test_busy_design_is_redrawn_once_and_the_cleaner_one_kept(tmp_path, monkeypatch):
    calls = []

    def fake_image(alias, prompt, *, size, **kw):
        calls.append((size, prompt))
        w, h = map(int, size.split("x"))
        return png(w, h, busy_right=len(calls) == 1), client.CallResult(alias=alias, cost_usd=0.04)

    monkeypatch.setattr(client, "generate_image", fake_image)
    c = AI.Concept.model_validate(CONCEPT)
    project = type("P", (), {"pillar": "growth"})()
    monkeypatch.setattr(AI.style, "accent", lambda profile, p: "#e63946")
    jpg, cost, alias = AI.draw(c, "youtube", project, None, _photo(tmp_path))
    assert len(calls) == 2 and calls[0][0] == "1920x1088" and "previous attempt" in calls[1][1]
    assert cost == 0.08 and jpg[:2] == b"\xff\xd8"
    calls.clear()
    AI.draw(c, "youtube", project, None, None)  # no photo → nothing to keep clear → one drawing
    assert len(calls) == 1


def test_ai_cover_generation_flow(logged_in, monkeypatch):
    c, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    from app.workers.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    try:
        pid = c.post("/api/projects", json={"name": "Java AI", "pillar": "growth"}, headers=h).json()["id"]
        c.post(f"/api/projects/{pid}/assets", data={"kind": "script"},
               files={"file": ("s.md", b"# Java\n\nDo Java devs need Python for AI?")}, headers=h)  # fmt: skip
        with get_sessionmaker()() as db:  # a ready headshot cut-out
            key = get_storage().new_key("brand", "png")
            out = io.BytesIO()
            Image.new("RGBA", (300, 400), (200, 150, 120, 255)).save(out, "PNG")
            get_storage().put_stream(key, io.BytesIO(out.getvalue()))
            db.add(
                BrandAsset(
                    kind="headshot",
                    storage_key=key,
                    cutout_key=key,
                    status="ready",
                    sha256="x",
                    size=1,
                    mime="image/png",
                    meta={},
                )
            )
            db.commit()

        def fake_chat(alias, messages, *, system=None, **kw):
            assert "cover content" in system
            return json.dumps({"concepts": [CONCEPT, {**CONCEPT, "headline": ["Don't switch", "yet"]}]}), \
                client.CallResult(alias=alias, cost_usd=0.01)  # fmt: skip

        def fake_image(alias, prompt, *, size, **kw):
            w, h_ = map(int, size.split("x"))
            return png(w, h_), client.CallResult(alias="image-gen", cost_usd=0.04)

        monkeypatch.setattr(client, "chat", fake_chat)
        monkeypatch.setattr(client, "generate_image", fake_image)
        r = c.post(f"/api/projects/{pid}/covers", json={"count": 2, "formats": ["youtube"]}, headers=h)
        assert r.status_code == 202
        d = c.get(f"/api/projects/{pid}/covers").json()
        assert d["status"]["status"] == "done" and d["status"]["engine"] == "ai" and d["status"]["made"] == 2
        assert d["status"]["with_photo"] and abs(d["status"]["cost_usd"] - 0.09) < 1e-6
        heads = {x["headline"] for x in d["covers"]}
        assert heads == {"JAVA IS ENOUGH", "DON'T SWITCH YET"}
        assert all(x["engine"] == "ai" and x["concept"]["visual"]["kind"] == "hub" for x in d["covers"])
    finally:
        celery_app.conf.task_always_eager = False
        with get_sessionmaker()() as db:
            db.query(BrandAsset).delete()
            db.commit()
