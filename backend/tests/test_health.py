def test_liveness(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_readiness_reports_components(client):
    # sqlite is up; redis/litellm/renderer point at a closed port in tests.
    r = client.get("/api/health/ready")
    body = r.json()
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"].startswith("error")
    assert r.status_code == 503
    assert body["status"] == "down"


def test_placeholder_secret_rejected():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(secret_key="change-me-to-a-long-random-string-at-least-32-chars")
