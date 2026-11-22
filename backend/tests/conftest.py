import os
import tempfile

# Configure env BEFORE app modules are imported (settings are cached).
_tmpdir = tempfile.mkdtemp(prefix="mhsquare-test-")
os.environ.update(
    {
        "MHSQUARE_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",  # gitleaks:allow (test-only)
        "MHSQUARE_DATABASE_URL": f"sqlite:///{_tmpdir}/test.db",
        "MHSQUARE_ADMIN_EMAIL": "Me@Example.com",
        "MHSQUARE_ADMIN_PASSWORD": "correct horse battery staple",
        "MHSQUARE_UI_ORIGIN": "https://testserver",
        "MHSQUARE_LITELLM_URL": "http://127.0.0.1:9",
        "MHSQUARE_RENDERER_URL": "http://127.0.0.1:9",
        "MHSQUARE_REDIS_URL": "redis://127.0.0.1:9/0",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import limiter  # noqa: E402
from app.core.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

EMAIL = "me@example.com"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    limiter._hits.clear()
    yield


@pytest.fixture
def client():
    # https base URL so Secure cookies are sent back, like a real browser on TLS/localhost.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def logged_in(client):
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client, r.json()["csrf_token"]
