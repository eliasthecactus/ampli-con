import time
from pathlib import Path

import httpx
import pytest
from mutagen.mp4 import MP4

from app import config, db, downloads, profiles, stream, tags
from tests.conftest import HAS_FFMPEG

pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")


@pytest.fixture()
def h(app_token):
    return {"Authorization": f"Bearer {app_token['token']}"}


@pytest.fixture(autouse=True)
def no_network_covers(monkeypatch):
    async def no_cover(self, meta):
        return None
    monkeypatch.setattr(downloads.Manager, "_cover", no_cover)


def wait_for(client, job_id, h, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}", headers=h).json()
        if j["status"] not in ("preparing", "queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} didn't finish: {j}")


def clean_downloads():
    import shutil
    for p in config.DOWNLOADS_DIR.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()


def test_template_rendering():
    v = {**profiles.SAMPLE}
    assert profiles.render(profiles.DEFAULT_ALBUM, v) == "Daft Punk/Discovery (2001)/01 One More Time"
    assert profiles.render(profiles.DEFAULT_ALBUM, {**v, "disc_total": 2, "disc": 2}) == "Daft Punk/Discovery (2001)/2-01 One More Time"
    assert profiles.render("{artist}/{title}", {"artist": "AC/DC", "title": 'What? "Why"'}) == "AC_DC/What_ _Why_"
    with pytest.raises(ValueError, match="Unknown field"):
        profiles.render("{nope}", v)
    with pytest.raises(ValueError):
        profiles.render("../{title}", v)


def test_song_download_tags_and_skips(client, h):
    clean_downloads()
    job = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01"}, headers=h).json()
    assert job["requested_by"] == "Test iPhone"
    j = wait_for(client, job["id"], h)
    assert j["status"] == "done", j
    path = config.DOWNLOADS_DIR / j["items"][0]["file_path"]
    # The song is on an album on YouTube Music, so it's filed like an album track.
    assert j["items"][0]["file_path"] == "Artist/Album (2020)/01 Single ttt.m4a"
    t = MP4(path).tags
    assert t["\xa9nam"] == ["Single ttt"] and t["\xa9alb"] == ["Album"] and t["trkn"] == [(1, 3)]
    assert tags.video_id_of(path) == "ttttttttt01"

    again = wait_for(client, client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01"}, headers=h).json()["id"], h)
    assert again["status"] == "done" and again["skipped"] == 1

    # Not on an album track list: filed as a single.
    j = wait_for(client, client.post("/api/downloads", json={"kind": "song", "id": "abcdefghijk"}, headers=h).json()["id"], h)
    assert j["items"][0]["file_path"] == "Artist/Album/Single abc.m4a"


def test_album_download(client, h):
    clean_downloads()
    job = client.post("/api/downloads", json={"kind": "album", "id": "MPREalbum", "title": "Album"}, headers=h).json()
    j = wait_for(client, job["id"], h)
    assert j["status"] == "done" and j["total"] == 2, "the unavailable track is left out"
    files = sorted(p.relative_to(config.DOWNLOADS_DIR).as_posix() for p in config.DOWNLOADS_DIR.rglob("*.m4a"))
    assert files == ["Artist/Album (2020)/01 One.m4a", "Artist/Album (2020)/02 Two.m4a"]


def test_playlist_download_with_playlist_layout_and_m3u(client, web, h):
    clean_downloads()
    loc = db.row("SELECT id FROM locations ORDER BY id LIMIT 1")["id"]
    p = web.post("/api/profiles", json={"name": "Playlists MP3", "location_id": loc, "format": "mp3", "quality": "V0",
                                        "playlist_layout": "playlist"}).json()
    assert p["preview"]["playlist_template"] == "Playlists/Road Trip/007 Daft Punk - One More Time.mp3"
    job = client.post("/api/downloads", json={"kind": "playlist", "id": "PLpublic", "profile_id": p["id"]}, headers=h).json()
    j = wait_for(client, job["id"], h)
    assert j["status"] == "done", j
    assert [i["file_path"] for i in j["items"]] == ["Playlists/Public/001 Artist - P One.mp3", "Playlists/Public/002 Artist - P Two.mp3"]
    m3u = (config.DOWNLOADS_DIR / "Playlists" / "Public.m3u8").read_text()
    assert "Public/001 Artist - P One.mp3" in m3u
    log = (config.CONFIG_DIR / "fake-ytdlp.log").read_text().splitlines()[-1]
    assert '"--audio-quality", "0"' in log and "music_offtopic" in log
    web.delete(f"/api/profiles/{p['id']}")


def test_failures_cancel_and_retry(client, h):
    clean_downloads()
    assert client.post("/api/downloads", json={"kind": "song", "id": "../../etc"}, headers=h).status_code == 400
    j = wait_for(client, client.post("/api/downloads", json={"kind": "song", "id": "unavailable"}, headers=h).json()["id"], h)
    assert j["status"] == "failed" and "Video unavailable" in j["items"][0]["error"]
    assert client.post(f"/api/jobs/{j['id']}/retry", headers=h).status_code == 200
    j = wait_for(client, j["id"], h)
    assert j["status"] == "failed" and j["failed"] == 1
    # A missing location is reported at once.
    loc = db.row("SELECT * FROM locations ORDER BY id LIMIT 1")
    db.update("locations", loc["id"], {"path": "/nonexistent-amplicon"})
    r = client.post("/api/downloads", json={"kind": "song", "id": "abcdefghijk"}, headers=h)
    assert r.status_code == 400 and "isn't there" in r.json()["detail"]
    db.update("locations", loc["id"], {"path": loc["path"]})

    jobs = client.get("/api/jobs", headers=h).json()
    assert jobs and all(j["progress"] == 1.0 for j in jobs if j["status"] == "done")
    assert client.get("/api/jobs", params={"active": True}, headers=h).json() == []
    assert client.delete("/api/jobs", headers=h).json()["removed"] >= 1


def test_profiles_and_locations_admin(web):
    locs = web.get("/api/locations").json()
    assert locs[0]["name"] == "Downloads" and locs[0]["online"]
    r = web.post("/api/locations", json={"name": "Outside", "path": "/etc"})
    assert r.status_code == 400
    sub = web.post("/api/locations", json={"name": "Kids", "path": str(config.DOWNLOADS_DIR / "kids"),
                                          "scan_kind": "navidrome", "scan_url": "http://navidrome:4533/", "scan_user": "u",
                                          "scan_secret": "p"}).json()
    assert sub["scan_url"] == "http://navidrome:4533" and sub["has_scan_secret"] and "scan_secret_enc" not in sub
    p = web.post("/api/profiles", json={"name": "Kids", "location_id": sub["id"], "format": "opus", "is_default": True}).json()
    assert p["is_default"] and p["location_name"] == "Kids"
    assert sum(x["is_default"] for x in web.get("/api/app/info").json()["profiles"]) == 1
    assert web.delete(f"/api/locations/{sub['id']}").status_code == 400, "a profile still uses it"
    assert web.post("/api/profiles", json={"name": "Bad", "location_id": sub["id"], "album_template": "{x}"}).status_code == 400
    web.delete(f"/api/profiles/{p['id']}")
    assert web.delete(f"/api/locations/{sub['id']}").status_code == 200
    assert any(x["is_default"] for x in web.get("/api/app/info").json()["profiles"])
