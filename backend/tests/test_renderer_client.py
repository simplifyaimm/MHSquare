"""Renderer client: a busy renderer (503 busy) is retried outside the render timeout."""

import httpx
import pytest

from app.services.generation import renderer


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "http://r/render"))


def test_busy_renderer_is_retried_then_succeeds(monkeypatch):
    replies = [_resp(503, {"busy": True}), _resp(503, {"busy": True}), _resp(200, {"ok": True})]
    sleeps: list[float] = []
    monkeypatch.setattr(renderer.httpx, "post", lambda *a, **kw: replies.pop(0))
    monkeypatch.setattr(renderer.time, "sleep", sleeps.append)
    r = renderer._post_render({"job_id": "abcdef"}, 10)
    assert r.status_code == 200 and len(sleeps) == 2


def test_other_errors_are_not_retried(monkeypatch):
    calls = []

    def post(*a, **kw):
        calls.append(1)
        return _resp(503, {"error": "overloaded"})  # not the renderer's busy signal

    monkeypatch.setattr(renderer.httpx, "post", post)
    assert renderer._post_render({"job_id": "abcdef"}, 10).status_code == 503 and len(calls) == 1


def test_busy_for_too_long_fails_clearly(monkeypatch):
    monkeypatch.setattr(renderer, "BUSY_WAIT_S", -1)
    monkeypatch.setattr(renderer.httpx, "post", lambda *a, **kw: _resp(503, {"busy": True}))
    monkeypatch.setattr(renderer.time, "sleep", lambda s: None)
    with pytest.raises(renderer.RenderError, match="busy"):
        renderer._post_render({"job_id": "abcdef"}, 10)
