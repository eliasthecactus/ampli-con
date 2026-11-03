import httpx
import pytest

from app import stream
from tests.conftest import COOKIES


@pytest.fixture()
def h(app_token):
    return {"Authorization": f"Bearer {app_token['token']}"}


def test_search_and_browse(client, h):
    r = client.get("/api/search", params={"q": "daft"}, headers=h).json()
    assert r["songs"][0] == {**r["songs"][0], "id": "sssssssssss", "album_id": "MPREalbum", "duration": 61}
    assert r["albums"][0]["thumbnail"].endswith("=w544-h544-l90-rj")
    assert r["playlists"][0]["id"] == "PLpublic" and r["playlists"][0]["track_count"] == 3
    assert r["artists"][0]["id"] == "UCartist" and r["top"]["type"] == "song" and r["top"]["item"]["id"] == "sssssssssss"

    album = client.get("/api/albums/MPREalbum", headers=h).json()
    assert [t["track"] for t in album["tracks"]] == [1, 2, 3]
    assert album["tracks"][0]["id"] == "ttttttttt01", "the audio track, not the music video"
    assert not album["tracks"][0]["is_video"]
    assert album["tracks"][2]["available"] is False
    assert all(t["album"] == "Album" for t in album["tracks"])

    artist = client.get("/api/artists/UCartist", headers=h).json()
    assert artist["songs"][0]["id"] == "sssssssssss" and artist["albums"][0]["id"] == "MPREalbum"


def test_account_playlists_need_a_youtube_account(client, h):
    r = client.get("/api/library/playlists", headers=h)
    assert r.status_code == 409 and "No YouTube account" in r.json()["detail"]


def test_connect_youtube_and_edit_playlists(web, h):
    r = web.put("/api/youtube", json={"cookies": "garbage"})
    assert r.status_code == 400
    r = web.put("/api/youtube", json={"cookies": COOKIES})
    assert r.status_code == 200, r.text
    assert r.json()["account"]["name"] == "Test Person"
    # also a pasted Cookie header
    assert web.put("/api/youtube", json={"cookies": "cookie: SAPISID=abc; LOGIN_INFO=x"}).status_code == 200

    pls = web.get("/api/library/playlists", headers=h).json()
    assert [p["id"] for p in pls] == ["LM", "PLmine"]

    new = web.post("/api/library/playlists", json={"title": "Road", "video_ids": ["aaaaaaaaaaa"]}, headers=h).json()["id"]
    web.post(f"/api/library/playlists/{new}/items", json={"video_ids": ["bbbbbbbbbbb"]}, headers=h)
    assert len(web.fake.playlists[new]["tracks"]) == 2
    assert web.post(f"/api/library/playlists/{new}/items", json={"video_ids": ["../etc"]}, headers=h).status_code == 400

    mine = web.get("/api/playlists/PLmine", headers=h).json()
    assert mine["editable"] and mine["tracks"][0]["set_video_id"] == "S1"
    web.post("/api/library/playlists/PLmine/remove", json={"items": [{"id": "aaaaaaaaaaa", "set_video_id": "S1"}]}, headers=h)
    assert [t["videoId"] for t in web.fake.playlists["PLmine"]["tracks"]] == ["bbbbbbbbbbb"]
    r = web.post("/api/library/playlists/PLmine/remove", json={"items": [{"id": "x"}]}, headers=h)
    assert r.status_code == 502

    web.post("/api/library/playlists/PLmine/move", json={"set_video_id": "S2", "before_set_video_id": None}, headers=h)
    assert web.fake.calls[-1] == ("edit", "PLmine", None, "S2")
    web.patch("/api/library/playlists/PLmine", json={"title": "Renamed"}, headers=h)
    assert web.fake.playlists["PLmine"]["title"] == "Renamed"
    assert web.patch("/api/library/playlists/LM", json={"title": "x"}, headers=h).status_code == 400
    web.post("/api/library/playlists/LM/items", json={"video_ids": ["ccccccccccc"]}, headers=h)
    assert web.fake.calls[-1] == ("rate", "ccccccccccc", "LIKE")
    web.delete(f"/api/library/playlists/{new}", headers=h)
    assert new not in web.fake.playlists
    web.delete("/api/youtube")


def _ranged(audio: bytes, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        seen.append(rng)
        start, end = (int(x) for x in rng.removeprefix("bytes=").split("-"))
        body = audio[start:end + 1]
        return httpx.Response(206, content=body, headers={"Content-Range": f"bytes {start}-{end}/{len(audio)}",
                                                          "Content-Length": str(len(body))})
    return handler


def test_stream_proxy_passes_ranges(client, h, monkeypatch):
    audio = bytes(range(256)) * 40
    seen = []
    monkeypatch.setattr(stream, "_http", httpx.AsyncClient(transport=httpx.MockTransport(_ranged(audio, seen))))
    monkeypatch.setattr(stream, "_http_loop", None)
    monkeypatch.setattr(stream, "_cache_limit", lambda: 0)
    monkeypatch.setattr(stream, "CHUNK", 3000)
    monkeypatch.setattr(stream, "_clen", lambda url: len(audio))
    stream._resolved.clear()
    r = client.get("/api/stream/zzzzzzzzzzz", headers={**h, "Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == audio[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(audio)}"
    assert r.headers["content-type"] == "audio/mp4"
    seen.clear()
    r = client.get("/api/stream/zzzzzzzzzzz", headers=h)
    assert r.status_code == 200 and r.content == audio
    assert seen == ["bytes=0-2999", "bytes=3000-5999", "bytes=6000-8999", "bytes=9000-10239"], "fetched in chunks"
    r = client.get("/api/stream/zzzzzzzzzzz", headers={**h, "Range": "bytes=-10"})
    assert r.content == audio[-10:]
    assert client.get("/api/stream/zzzzzzzzzzz", headers={**h, "Range": f"bytes={len(audio)}-"}).status_code == 416
    assert client.get("/api/stream/not-an-id", headers=h).status_code == 400
    assert client.get("/api/stream/zzzzzzzzzzz").status_code == 401


def test_fresh_links_are_retried_until_youtube_accepts_them(client, h, monkeypatch):
    audio = b"x" * 500
    calls = {"n": 0}
    inner = _ranged(audio, [])

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403) if calls["n"] <= 2 else inner(request)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(stream, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(stream, "_http_loop", None)
    monkeypatch.setattr(stream, "_cache_limit", lambda: 0)
    monkeypatch.setattr(stream, "_clen", lambda url: len(audio))
    monkeypatch.setattr(stream.asyncio, "sleep", no_sleep)
    stream._resolved.clear()
    r = client.get("/api/stream/yyyyyyyyyyy", headers=h)
    assert r.status_code == 200 and r.content == audio and calls["n"] == 3


def test_stream_cache_fills_in_chunks(client, h, monkeypatch):
    audio = bytes(range(256)) * 30
    seen = []
    monkeypatch.setattr(stream, "_http", httpx.AsyncClient(transport=httpx.MockTransport(_ranged(audio, seen))))
    monkeypatch.setattr(stream, "_http_loop", None)
    monkeypatch.setattr(stream, "_cache_limit", lambda: 10_000_000)
    monkeypatch.setattr(stream, "CHUNK", 2000)
    monkeypatch.setattr(stream, "_clen", lambda url: len(audio))
    stream._resolved.clear()
    import time as _t
    client.get("/api/stream/xxxxxxxxxxx", headers={**h, "Range": "bytes=0-1"})
    for _ in range(50):
        if (stream.config.STREAM_CACHE_DIR / "xxxxxxxxxxx.m4a").exists():
            break
        _t.sleep(0.05)
    assert (stream.config.STREAM_CACHE_DIR / "xxxxxxxxxxx.m4a").read_bytes() == audio
    r = client.get("/api/stream/xxxxxxxxxxx", headers={**h, "Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == audio[10:20]
    stream.clear_cache()
