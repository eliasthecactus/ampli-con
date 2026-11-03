import time

import pytest

from app import auth, cli, config, db


def test_first_start_creates_a_user_and_prints_the_password(capsys, monkeypatch):
    db.init()
    db.execute("DELETE FROM users")
    monkeypatch.setattr(config, "ENV_USERNAME", "")
    monkeypatch.setattr(config, "ENV_PASSWORD", "")
    password = auth.bootstrap()
    out = capsys.readouterr().out
    assert password and password in out and "Username: admin" in out
    assert "docker exec -it amplicon amplicon reset-password" in out
    assert auth.verify_password(auth.the_user()["password_hash"], password)
    # Later starts leave it alone and print nothing.
    assert auth.bootstrap() is None
    assert capsys.readouterr().out == ""


def test_environment_user_wins_on_every_start(monkeypatch):
    db.init()
    auth.set_credentials("admin", "something-else", sign_out=True)
    monkeypatch.setattr(config, "ENV_USERNAME", "elias")
    monkeypatch.setattr(config, "ENV_PASSWORD", "from-compose-123")
    d = auth.create_device("Phone", None)
    auth.bootstrap()
    u = auth.the_user()
    assert u["username"] == "elias" and auth.verify_password(u["password_hash"], "from-compose-123")
    assert auth.device_for_token(d["token"]) is None, "changing the password signs apps out"
    assert db.scalar("SELECT COUNT(*) FROM users") == 1


def test_environment_password_is_validated(monkeypatch):
    db.init()
    monkeypatch.setattr(config, "ENV_USERNAME", "elias")
    monkeypatch.setattr(config, "ENV_PASSWORD", "short")
    with pytest.raises(SystemExit):
        auth.bootstrap()


def test_reset_password_command(capsys, monkeypatch):
    db.init()
    monkeypatch.setattr(config, "ENV_USERNAME", "")
    monkeypatch.setattr(config, "ENV_PASSWORD", "")
    auth.set_credentials("admin", "old-password-1", sign_out=True)
    d = auth.create_device("Phone", None)
    assert cli.main(["reset-password"]) == 0
    out = capsys.readouterr().out
    new = out.split("Password: ")[1].split("\n")[0]
    assert auth.verify_password(auth.the_user()["password_hash"], new)
    assert auth.device_for_token(d["token"]) is None
    assert cli.main(["reset-password", "--username", "boss", "--password", "chosen-password"]) == 0
    assert auth.the_user()["username"] == "boss"
    assert cli.main(["reset-password", "--password", "short"]) == 1


def test_reset_warns_when_compose_sets_the_user(capsys, monkeypatch):
    db.init()
    monkeypatch.setattr(config, "ENV_USERNAME", "elias")
    monkeypatch.setattr(config, "ENV_PASSWORD", "from-compose-123")
    cli.main(["reset-password"])
    assert "replace this password on the next restart" in capsys.readouterr().err


def test_web_login_and_csrf(client):
    assert client.get("/api/locations").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"}).status_code == 200
    assert client.get("/api/locations").status_code == 200
    # writes need the custom header
    assert client.post("/api/profiles/preview", json={}).status_code == 403
    assert client.post("/api/profiles/preview", json={}, headers={"X-Requested-With": "amplicon"}).status_code == 200


def test_app_login_and_bearer(client, app_token):
    assert app_token["token"] and app_token["secret"] and app_token["name"] == "Amplicon"
    h = {"Authorization": f"Bearer {app_token['token']}"}
    r = client.get("/api/app/info", headers=h)
    assert r.status_code == 200 and r.json()["profiles"][0]["is_default"]
    # app tokens can't reach the admin API
    assert client.get("/api/locations", headers=h).status_code == 401
    assert client.get("/api/app/info", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_rate_limit(client):
    auth._attempts.clear()
    for _ in range(auth.MAX_ATTEMPTS):
        client.post("/api/app/login", json={"username": "admin", "password": "bad"})
    assert client.post("/api/app/login", json={"username": "admin", "password": "correct-horse"}).status_code == 429
    auth._attempts.clear()


def test_change_password(web):
    r = web.post("/api/auth/change", json={"current": "correct-horse", "new": "new-password-9"})
    assert r.status_code == 200
    assert web.get("/api/locations").status_code == 200, "the browser that changed it stays signed in"
    assert web.post("/api/auth/change", json={"current": "wrong", "new": "x" * 9}).status_code == 400


def test_signed_links(client, app_token):
    exp = int(time.time()) + 60
    path = "/api/stream/aaaaaaaaaaa"
    sig = auth.sign(app_token["secret"], path, exp)
    (config.STREAM_CACHE_DIR / "aaaaaaaaaaa.m4a").write_bytes(b"x" * 1000)
    r = client.get(f"{path}?d={app_token['device_id']}&exp={exp}&sig={sig}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and len(r.content) == 10
    assert client.get(f"{path}?d={app_token['device_id']}&exp={exp}&sig={'0' * 64}").status_code == 401
    old = int(time.time()) - 1
    assert client.get(f"{path}?d={app_token['device_id']}&exp={old}&sig={auth.sign(app_token['secret'], path, old)}").status_code == 401
    (config.STREAM_CACHE_DIR / "aaaaaaaaaaa.m4a").unlink()


def test_signing_in_again_replaces_the_device(client):
    from app import db
    body = {"username": "admin", "password": "correct-horse", "device": "Ampli on iPhone", "install_id": "abc"}
    first = client.post("/api/app/login", json=body).json()
    second = client.post("/api/app/login", json=body).json()
    assert db.scalar("SELECT COUNT(*) FROM devices WHERE install_id = 'abc'") == 1
    assert client.get("/api/app/info", headers={"Authorization": f"Bearer {first['token']}"}).status_code == 401
    assert client.get("/api/app/info", headers={"Authorization": f"Bearer {second['token']}"}).status_code == 200
