"""YouTube details: the model writes the words; chapter times, lengths and YouTube's rules are enforced in code."""

import json
from types import SimpleNamespace

import pytest

from app.llm import client
from app.services import publish as PUB
from tests.test_direction import _add_script
from tests.test_generation_flow import fakes, ready_project  # noqa: F401


def shot(i: int, start_s: float, end_s: float):
    return SimpleNamespace(index=i, start_ms=int(start_s * 1000), end_ms=int(end_s * 1000),
                           transcript_text=f"shot {i} words")  # fmt: skip


SHOTS = [shot(0, 0, 8), shot(1, 8, 40), shot(2, 40, 44), shot(3, 44, 95), shot(4, 95, 180), shot(5, 180, 185)]


def test_chapters_use_real_shot_times_and_youtube_rules():
    # first chapter moves to 0:00; shot 3 (0:44) is < 10 s after shot 2 (0:40); the last (3:00) is < 10 s long;
    # shot 99 does not exist
    picked = [PUB.Chapter(shot=1, title="Why"), PUB.Chapter(shot=2, title="The idea"),
              PUB.Chapter(shot=3, title="Too soon"), PUB.Chapter(shot=5, title="Outro"),
              PUB.Chapter(shot=99, title="Ghost")]  # fmt: skip
    assert PUB.chapters(picked, SHOTS) == []  # only 2 valid chapters left → YouTube would ignore them
    picked.append(PUB.Chapter(shot=4, title="Build it"))
    chs = PUB.chapters(picked, SHOTS)
    assert [(c["at"], c["title"]) for c in chs] == [
        ("0:00", "Why"),
        ("0:40", "The idea"),
        ("1:35", "Build it"),
    ]
    assert [c["ms"] for c in chs] == [0, 40000, 95000]


def test_fewer_than_three_valid_chapters_are_dropped():
    assert PUB.chapters([PUB.Chapter(shot=0, title="All")], SHOTS) == []


def test_pack_trims_titles_tags_and_hashtags():
    pack = PUB.Pack.model_validate({
        "titles": ["A" * 130, "Same", "Same"], "hook": "h", "tags": [f"tag number {k}" for k in range(80)],
        "hashtags": ["#Java AI", "python", "#Java AI"]})  # fmt: skip
    assert len(pack.titles[0]) == PUB.TITLE_MAX and pack.titles[1:] == ["Same"]
    assert sum(len(t) + 3 for t in pack.tags) <= PUB.TAGS_MAX and len(pack.tags) > 10
    assert pack.hashtags == ["#JavaAI", "#python"]


def test_description_has_hook_first_chapters_and_shorts_tag():
    pack = PUB.Pack(titles=["t"], hook="Hook line.", learn=["one"], hashtags=["#AI"])
    chs = [{"at": "0:00", "title": "Intro"}, {"at": "1:00", "title": "Core"}, {"at": "2:00", "title": "End"}]
    d = PUB.compose_description(
        pack, chs, {"name": "Simplify AI", "tagline": "No hype.", "handle": "@x"}, False
    )
    assert d.startswith("Hook line.") and "0:00 Intro\n1:00 Core" in d and "#AI" in d and "@x" in d
    s = PUB.compose_description(pack, [], {}, True)
    assert "Chapters" not in s and s.endswith("#AI #Shorts")


def test_generate_and_edit_through_the_api(fakes, ready_project, monkeypatch):  # noqa: F811
    c, csrf, pid = ready_project
    h = {"X-CSRF-Token": csrf}
    assert c.post(f"/api/projects/{pid}/publish", headers=h).status_code == 422  # no script yet
    _add_script(pid)
    seen = []

    def chat(alias, messages, *, system=None, **kw):
        seen.append((system, messages[0]["content"]))
        return json.dumps({"titles": ["Kafka explained for Java devs", "How Kafka really works"],
                           "hook": "Kafka is a log, not a queue.", "learn": ["what a log is"],
                           "chapters": [], "tags": ["kafka", "apache kafka"], "hashtags": ["#Kafka"],
                           "pinned_comment": "What do you use Kafka for?"}), \
            client.CallResult(alias=alias, cost_usd=0.01)  # fmt: skip

    monkeypatch.setattr(client, "chat", chat)
    r = c.post(f"/api/projects/{pid}/publish", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "Kafka explained for Java devs" and d["description"].startswith("Kafka is a log")
    assert d["source"] == "ai" and d["tags"] == ["kafka", "apache kafka"]
    assert "Kafka is a log. Orders" in seen[0][1] and "Simplify AI" in seen[0][0]
    e = c.put(f"/api/projects/{pid}/publish", json={"title": "How Kafka really works", "tags": "kafka, streams"},
              headers=h)  # fmt: skip
    assert e.status_code == 200 and e.json()["title"] == "How Kafka really works"
    assert e.json()["tags"] == ["kafka", "streams"] and e.json()["source"] == "edited"
    assert c.get(f"/api/projects/{pid}").json()["publish"]["title"] == "How Kafka really works"


@pytest.mark.parametrize(("change", "msg"), [
    ({"title": ""}, "empty"), ({"title": "x" * 101}, "100"), ({"description": "a <b> c"}, "< or >"),
    ({"tags": [f"a long tag number {k}" for k in range(60)]}, "500"),
])  # fmt: skip
def test_edits_respect_youtube_limits(change, msg):
    with pytest.raises(PUB.PublishError, match=msg):
        PUB.edit({"title": "t", "description": "", "tags": []}, change)
