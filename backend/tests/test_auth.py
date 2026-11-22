from tests.conftest import EMAIL, PASSWORD


def test_admin_bootstrapped_and_login(client):
    r = client.post("/api/auth/login", json={"email": "ME@example.com ", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == EMAIL
    set_cookie = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("mh_session="))
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=strict" in session_cookie
    csrf_cookie = next(c for c in set_cookie if c.startswith("mh_csrf="))
    assert "HttpOnly" not in csrf_cookie


def test_wrong_password(client):
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": "nope"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_after_login(logged_in):
    client, csrf = logged_in
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["csrf_token"] == csrf


def test_logout_requires_csrf(logged_in):
    client, csrf = logged_in
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_session_token_revoked_server_side(logged_in):
    client, csrf = logged_in
    token = client.cookies.get("mh_session")
    client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    # replaying the old cookie must not work
    client.cookies.set("mh_session", token, domain="testserver")
    assert client.get("/api/auth/me").status_code == 401


def test_login_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": EMAIL, "password": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_password_hash_is_argon2():
    from app.core.security import hash_password, verify_password

    h = hash_password("pw")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "pw")
    assert not verify_password(h, "other")
