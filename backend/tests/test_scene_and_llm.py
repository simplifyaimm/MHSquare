import httpx
import pytest

from app.llm import client
from app.llm.errors import ErrorKind, LLMError, classify
from app.services.generation import scene as S
from app.services.ingest.tts import script_to_speech_text

GOOD = """Here you go.
```html
<div class="mh-bg"></div><svg class="mh-wires" xmlns="http://www.w3.org/2000/svg"></svg><div id="t" class="mh-title">Kafka</div>
```
```css
#t{left:200px}
```
```js
// the timeline: tl.from below
const tl = gsap.timeline(); window.__scene = tl;
tl.from('#t', {opacity:0, duration:.6}, .1);
tl.set({}, {}, 4.0);
```"""


def test_parse_and_check_good_scene():
    sc = S.parse(GOOD)
    S.check(sc)
    assert "mh-title" in sc.html and "#t" in sc.css


@pytest.mark.parametrize(
    ("html", "js", "css", "msg"),
    [
        ("<img src='x.png'>", "window.__scene=1", "", "external-resource"),
        ("<div></div>", "fetch('/x'); window.__scene=1", "", "network"),
        ("<div></div>", "setTimeout(()=>{},1); window.__scene=1", "", "timers"),
        ("<div style=\"background:url('https://evil.example/a.png')\"></div>", "window.__scene=1", "", "URL"),
        ("<div></div>", "window.__scene=1", "@import 'x.css';", "@import"),
        ("<div></div>", "window.__scene=1", ".a{transition: all 1s}", "CSS animations"),
        ("<div></div>", "const tl=gsap.timeline();", "", "window.__scene"),
        ("<script>alert(1)</script>", "window.__scene=1", "", "script"),
        ("<div></div>", "eval('1'); window.__scene=1", "", "eval"),
    ],
)
def test_check_rejects_unsafe(html, js, css, msg):
    with pytest.raises(S.SceneError, match=msg):
        S.check(S.Scene(html=html, css=css, js=js))


def test_parse_requires_blocks():
    with pytest.raises(S.SceneError):
        S.parse("no code here")


def test_classify_errors():
    assert classify(400, "Budget has been exceeded! Current cost: 3.01") == ErrorKind.budget
    assert classify(429, "You have no credits remaining") == ErrorKind.budget
    assert classify(429, "rate limit reached") == ErrorKind.rate_limit
    assert classify(400, "Output blocked due to safety / content_policy") == ErrorKind.content_policy
    assert classify(503, "overloaded") == ErrorKind.transient
    assert classify(None, "timeout") == ErrorKind.transient
    assert classify(400, "bad request: max_tokens") == ErrorKind.invalid


def _resp(code, body, cost=None):
    h = {"x-litellm-response-cost": str(cost)} if cost is not None else {}
    return httpx.Response(code, json=body, headers=h, request=httpx.Request("POST", "http://litellm"))


def _ok(text):
    return _resp(
        200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {}}, 0.012
    )


@pytest.fixture
def calls(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(client.settings_store, "virtual_key", lambda: "sk-virtual-test")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    return seen


def test_rate_limit_backoff_then_success(monkeypatch, calls):
    seq = [_resp(429, {"error": {"message": "rate limit"}})] * 2 + [_ok("hi")]

    def post(url, headers, json, timeout):
        calls.append(json["model"])
        return seq.pop(0)

    monkeypatch.setattr(httpx, "post", post)
    text, res = client.chat("code-llm", [{"role": "user", "content": "x"}])
    assert text == "hi" and res.cost_usd == 0.012 and calls == ["code-llm"] * 3


def test_transient_falls_back_to_next_alias(monkeypatch, calls):
    def post(url, headers, json, timeout):
        calls.append(json["model"])
        return (
            _ok("ok")
            if json["model"] == "code-llm-backup"
            else _resp(503, {"error": {"message": "overloaded"}})
        )

    monkeypatch.setattr(httpx, "post", post)
    text, res = client.chat("code-llm", [], fallbacks=["code-llm-backup"])
    assert res.alias == "code-llm-backup" and calls == ["code-llm"] * 3 + ["code-llm-backup"]


def test_budget_and_policy_errors_are_not_retried(monkeypatch, calls):
    def post(url, headers, json, timeout):
        calls.append(json["model"])
        return _resp(400, {"error": {"message": "Budget has been exceeded"}})

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(LLMError) as e:
        client.chat("code-llm", [], fallbacks=["code-llm-backup"])
    assert e.value.kind == ErrorKind.budget and calls == ["code-llm"]


def test_script_to_speech_text():
    md = "# Kafka in 5 minutes\n\nKafka is a **distributed** log. See [docs](http://x).\n\n```py\nprint(1)\n```\n\n- fast\n- durable"
    assert script_to_speech_text(md) == [
        "Kafka in 5 minutes.\n\nKafka is a distributed log. See docs.\n\nfast durable"
    ]
