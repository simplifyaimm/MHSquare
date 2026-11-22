import os
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.llm import settings_store

H = {"X-CSRF-Token": ""}


@pytest.fixture(autouse=True)
def _clean_llmconfig():
    p = Path(get_settings().llmconfig_path)
    p.unlink(missing_ok=True)
    yield
    p.unlink(missing_ok=True)


def _resp(code, url, json=None):
    return httpx.Response(code, json=json, request=httpx.Request("GET", url))


@pytest.fixture
def fake_litellm(monkeypatch):
    """Stand-in for the LiteLLM proxy admin + key endpoints."""
    state = {"keys": {}}

    def fake_get(url, headers=None, timeout=None, **_):
        if url.endswith("/health/liveliness"):
            return _resp(200, url, json="I'm alive!")
        if url.endswith("/key/info"):
            key = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
            if key not in state["keys"]:
                return _resp(401, url, json={"error": "bad key"})
            return _resp(200, url, json={"info": state["keys"][key]})
        return _resp(404, url)

    def fake_post(url, headers=None, json=None, timeout=None, **_):
        assert headers["Authorization"] == "Bearer sk-test-master" or url.endswith("/chat/completions")
        if url.endswith("/key/generate"):
            assert json["budget_duration"] == "1d"
            state["keys"]["sk-virtual-1234567890"] = {
                "spend": 0.0,
                "max_budget": json["max_budget"],
                "budget_duration": "1d",
                "budget_reset_at": None,
            }
            return _resp(200, url, json={"key": "sk-virtual-1234567890"})
        if url.endswith("/key/update"):
            state["keys"][json["key"]]["max_budget"] = json["max_budget"]
            return _resp(200, url, json={})
        return _resp(404, url)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    return state


def test_requires_auth(client):
    assert client.get("/api/settings/llm").status_code == 401


def test_keys_are_write_only_and_masked(logged_in, fake_litellm):
    client, csrf = logged_in
    secret = "sk-ant-api03-THISISAFAKEKEYabcd"
    r = client.put(
        "/api/settings/llm", json={"provider_keys": {"anthropic": secret}}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200, r.text
    assert secret not in r.text
    anth = next(p for p in r.json()["providers"] if p["id"] == "anthropic")
    assert anth == anth | {"configured": True, "hint": "…abcd"}
    assert r.json()["restarting"] is True

    # stored in the env file, not returned by GET
    assert f"ANTHROPIC_API_KEY={secret}\n" in Path(get_settings().llmconfig_path).read_text()
    assert secret not in client.get("/api/settings/llm").text

    # anthropic models now report their provider as configured
    planner = next(m for m in r.json()["models"] if m["alias"] == "planner-llm")
    assert planner["provider"] == "anthropic" and planner["provider_configured"]


def test_put_requires_csrf(logged_in):
    client, _ = logged_in
    r = client.put("/api/settings/llm", json={"provider_keys": {"anthropic": "sk-whatever-123"}})
    assert r.status_code == 403


@pytest.mark.parametrize("bad", ["short", "has space inside key", "abc\nEVIL=1xxxxxxxx", "x" * 600])
def test_rejects_bad_keys(logged_in, bad):
    client, csrf = logged_in
    r = client.put(
        "/api/settings/llm", json={"provider_keys": {"gemini": bad}}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 422
    assert not Path(get_settings().llmconfig_path).exists()


def test_rejects_unknown_provider_and_bad_model(logged_in):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    assert (
        client.put(
            "/api/settings/llm", json={"provider_keys": {"evil": "sk-123456789"}}, headers=h
        ).status_code
        == 422
    )
    assert (
        client.put("/api/settings/llm", json={"models": {"planner-llm": "no-slash"}}, headers=h).status_code
        == 422
    )
    assert (
        client.put("/api/settings/llm", json={"models": {"planner-llm": "a/b;rm -rf"}}, headers=h).status_code
        == 422
    )


def test_model_override_and_reset(logged_in, fake_litellm):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    r = client.put("/api/settings/llm", json={"models": {"planner-llm": "gemini/gemini-2.5-pro"}}, headers=h)
    planner = next(m for m in r.json()["models"] if m["alias"] == "planner-llm")
    assert planner["model"] == "gemini/gemini-2.5-pro" and not planner["is_default"]
    r = client.put("/api/settings/llm", json={"models": {"planner-llm": None}}, headers=h)
    planner = next(m for m in r.json()["models"] if m["alias"] == "planner-llm")
    assert planner["model"] == "anthropic/claude-opus-5-5" and planner["is_default"]


def test_remove_key(logged_in, fake_litellm):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    client.put("/api/settings/llm", json={"provider_keys": {"openai": "sk-proj-abcdefgh1234"}}, headers=h)
    r = client.put("/api/settings/llm", json={"provider_keys": {"openai": None}}, headers=h)
    assert not next(p for p in r.json()["providers"] if p["id"] == "openai")["configured"]
    assert "OPENAI_API_KEY" not in Path(get_settings().llmconfig_path).read_text()


def test_budget_creates_then_updates_virtual_key(logged_in, fake_litellm):
    client, csrf = logged_in
    h = {"X-CSRF-Token": csrf}
    r = client.put("/api/settings/llm/budget", json={"daily_usd": 5}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    assert r.json()["budget"]["max_budget"] == 5
    assert "sk-virtual-1234567890" not in r.text
    assert settings_store.virtual_key() == "sk-virtual-1234567890"

    r = client.put("/api/settings/llm/budget", json={"daily_usd": 12.5}, headers=h)
    assert r.json()["created"] is False
    assert r.json()["budget"]["max_budget"] == 12.5
    assert len(fake_litellm["keys"]) == 1


def test_write_is_atomic_and_private(fake_litellm):
    settings_store.update({"GEMINI_API_KEY": "AIzaFAKEFAKEFAKE"})
    p = Path(get_settings().llmconfig_path)
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".llmconfig.")]
    assert leftovers == []
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600
