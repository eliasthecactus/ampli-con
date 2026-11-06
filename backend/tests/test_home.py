"""The For you feed: YouTube Music's home page, for the web interface and the app."""
import pytest

from app import db, youtube


@pytest.fixture()
def h(app_token):
    return {"Authorization": f"Bearer {app_token['token']}"}


def connect() -> None:
    db.execute("INSERT INTO youtube_account (id, cookies_enc, status, updated_at) VALUES (1, 'x', 'valid', 1)")
    youtube.reset_clients()


def test_sections_say_what_each_entry_is(client, h):
    r = client.get("/api/home", headers=h).json()
    assert r["personalized"] is False and r["fetched_at"] > 0
    assert [s["title"] for s in r["sections"]] == ["Quick picks", "Listen again"], "nothing to show, no section"
    assert [i["item"]["id"] for i in r["sections"][0]["items"]] == ["hhhhhhhhh01"]
    assert r["sections"][0]["items"][0]["item"]["album_id"] == "MPREalbum"
    mixed = {i["type"]: i["item"] for i in r["sections"][1]["items"]}
    assert set(mixed) == {"song", "album", "playlist", "artist"}
    assert mixed["album"]["type"] == "Single" and mixed["album"]["artist"] == "Artist"
    assert mixed["playlist"]["id"] == "RDCLAKmix" and mixed["playlist"]["track_count"] == 20
    assert mixed["artist"] == {"id": "UCartist", "name": "Artist", "thumbnail": None}


def test_entries_look_like_search_results(client, h):
    """The app decodes both with the same models."""
    found = client.get("/api/search", params={"q": "x"}, headers=h).json()
    home = client.get("/api/home", headers=h).json()
    mixed = {i["type"]: i["item"] for i in home["sections"][1]["items"]}
    assert set(mixed["song"]) == set(found["songs"][0])
    assert set(mixed["album"]) == set(found["albums"][0])
    assert set(mixed["playlist"]) == set(found["playlists"][0])
    assert set(mixed["artist"]) == set(found["artists"][0])


def test_needs_a_sign_in(client):
    assert client.get("/api/home").status_code == 401


def test_it_is_kept_for_a_while(client, h):
    client.get("/api/home", headers=h)
    client.get("/api/home", headers=h)
    assert client.fake.calls.count(("get_home", youtube.HOME_SECTIONS)) == 1
    client.get("/api/home", params={"refresh": True}, headers=h)
    assert client.fake.calls.count(("get_home", youtube.HOME_SECTIONS)) == 2


def test_the_account_gets_its_own_feed(client, h):
    try:
        client.get("/api/home", headers=h)
        connect()
        assert client.get("/api/home", headers=h).json()["personalized"] is True
    finally:
        db.execute("DELETE FROM youtube_account")


def test_a_broken_sign_in_still_shows_the_general_page(client, h, monkeypatch):
    connect()
    used = []
    real = youtube.call

    async def refused(fn, authenticated=None):
        used.append(authenticated)
        if authenticated:
            raise youtube.YouTubeError("The request was refused")
        return await real(fn, authenticated)

    monkeypatch.setattr(youtube, "call", refused)
    try:
        r = client.get("/api/home", headers=h).json()
        assert r["personalized"] is False and r["sections"] and used == [True, False]
    finally:
        db.execute("DELETE FROM youtube_account")


def test_an_expired_account_is_not_tried(client, h):
    connect()
    db.execute("UPDATE youtube_account SET status = 'expired'")
    try:
        assert client.get("/api/home", headers=h).json()["personalized"] is False
    finally:
        db.execute("DELETE FROM youtube_account")


def test_an_old_copy_beats_an_error(client, h):
    first = client.get("/api/home", headers=h).json()
    client.fake.home_error = "boom"
    assert client.get("/api/home", params={"refresh": True}, headers=h).json() == first


def test_youtube_down_without_a_copy_is_an_error(client, h):
    client.fake.home_error = "boom"
    assert client.get("/api/home", headers=h).status_code == 502
