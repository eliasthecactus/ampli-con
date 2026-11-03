import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Test files import this module again as tests.conftest; keep the first temp dir so the environment stays put.
ROOT = Path(os.environ.get("AMPLICON_TEST_ROOT") or tempfile.mkdtemp(prefix="amplicon-test-"))
os.environ["AMPLICON_TEST_ROOT"] = str(ROOT)
os.environ.update(
    CONFIG_DIR=str(ROOT / "config"),
    DOWNLOADS_DIR=str(ROOT / "downloads"),
    AMPLICON_DISABLE_BACKGROUND="1",
    STATIC_DIR=str(ROOT / "static"),
    BUNDLED_YTDLP=str(ROOT / "no-bundled-ytdlp"),
)
os.environ.pop("AMPLICON_USERNAME", None)
os.environ.pop("AMPLICON_PASSWORD", None)
(ROOT / "static").mkdir(parents=True, exist_ok=True)
(ROOT / "static" / "index.html").write_text("<!doctype html><title>Amplicon</title>")

HAS_FFMPEG = shutil.which("ffmpeg") is not None

FAKE_YTDLP = r'''#!PYTHON
"""Stand-in for yt-dlp: resolves to a local URL, "downloads" by generating a short audio file with ffmpeg."""
import json, os, subprocess, sys
from pathlib import Path
a = sys.argv[1:]
if "--version" in a:
    print("2026.01.01"); sys.exit(0)
url = a[a.index("--") + 1] if "--" in a else a[-1]
vid = url.split("v=")[1][:11]
if vid.startswith("unavailable"):
    print(f"ERROR: [youtube] {vid}: Video unavailable. This video has been removed", file=sys.stderr); sys.exit(1)
log = Path(os.environ["CONFIG_DIR"]) / "fake-ytdlp.log"
with open(log, "a") as f:
    f.write(json.dumps(a) + "\n")
if "-J" in a:
    print(json.dumps({"id": vid, "url": os.environ.get("FAKE_STREAM_URL", "http://stream.test/") + vid,
                      "ext": "m4a", "acodec": "mp4a.40.2", "http_headers": {"User-Agent": "fake"}, "duration": 1}))
    sys.exit(0)
home = next(x.split(":", 1)[1] for i, x in enumerate(a) if a[i - 1] == "-P" and x.startswith("home:"))
fmt = a[a.index("--audio-format") + 1]
codec = {"m4a": ["-c:a", "aac"], "mp3": ["-c:a", "libmp3lame"], "opus": ["-c:a", "libopus"]}[fmt]
out = Path(home) / f"{vid}.{fmt}"
print('ACPROG {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100}', flush=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1",
                *codec, str(out)], check=True)
print("ACDONE " + json.dumps({"filepath": str(out)}), flush=True)
'''


def install_fake_ytdlp(config_dir: Path) -> None:
    bin_dir = config_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / "yt-dlp-fake"
    p.write_text(FAKE_YTDLP.replace("#!PYTHON", "#!" + sys.executable))
    p.chmod(0o755)
    (bin_dir / "current").write_text(p.name)


install_fake_ytdlp(ROOT / "config")


def track(vid, title, artist="Artist", album=None, album_id=None, n=None, set_id=None):
    t = {"videoId": vid, "title": title, "artists": [{"name": artist, "id": "UCartist"}], "duration_seconds": 61,
         "thumbnails": [{"url": f"https://i.ytimg.com/vi/{vid}/hq.jpg", "width": 480}], "isAvailable": True}
    if album:
        t["album"] = {"name": album, "id": album_id}
    if n:
        t["trackNumber"] = n
    if set_id:
        t["setVideoId"] = set_id
    return t


class FakeYT:
    """The part of ytmusicapi.YTMusic Amplicon uses, in memory."""

    def __init__(self):
        self.playlists = {"PLmine": {"title": "Mine", "owned": True,
                                     "tracks": [track("aaaaaaaaaaa", "First", set_id="S1"), track("bbbbbbbbbbb", "Second", set_id="S2")]}}
        self.calls = []

    def search(self, q, filter=None, limit=20):
        return [
            {"resultType": "song", "category": "Top result", **track("sssssssssss", f"{q} song", album="Album", album_id="MPREalbum")},
            {"resultType": "album", "browseId": "MPREalbum", "title": "Album", "artists": [{"name": "Artist", "id": "UCartist"}],
             "year": "2020", "type": "Album", "thumbnails": [{"url": "https://lh3.googleusercontent.com/x=w120-h120-l90-rj", "width": 120}]},
            {"resultType": "playlist", "browseId": "VLPLpublic", "title": "Public", "author": "Someone", "itemCount": "3 songs"},
            {"resultType": "artist", "browseId": "UCartist", "artist": "Artist"},
        ]

    def get_album(self, browse_id):
        return {"title": "Album", "type": "Album", "year": "2020", "artists": [{"name": "Artist", "id": "UCartist"}],
                "audioPlaylistId": "OLAKalbum", "thumbnails": [{"url": "https://lh3.googleusercontent.com/x=w60-h60", "width": 60}],
                "tracks": [{**track("vvvvvvvvv01", "One", n=1), "videoType": "MUSIC_VIDEO_TYPE_OMV"},
                           track("ttttttttt02", "Two", n=2),
                           {**track("unavailable", "Gone", n=3), "isAvailable": False}]}

    def get_playlist(self, pid, limit=None):
        if pid == "OLAKalbum":  # the album's audio tracks
            return {"id": pid, "title": "Album", "tracks": [
                {**track("ttttttttt01", "One"), "videoType": "MUSIC_VIDEO_TYPE_ATV"},
                {**track("ttttttttt02", "Two"), "videoType": "MUSIC_VIDEO_TYPE_ATV"},
                {**track("unavailable", "Gone"), "isAvailable": False}]}
        p = self.playlists.get(pid) or {"title": "Public", "tracks": [track("ppppppppp01", "P One", album="Album", album_id="MPREalbum"),
                                                                        track("ppppppppp02", "P Two")]}
        return {"id": pid, "title": p["title"], "owned": p.get("owned", False), "trackCount": len(p["tracks"]),
                "author": {"name": "Me"}, "tracks": p["tracks"]}

    def get_liked_songs(self, limit=None):
        return {"title": "Liked Music", "tracks": [track("lllllllllll", "Liked")]}

    def get_watch_playlist(self, videoId=None, limit=None):
        return {"tracks": [track(videoId, "Single " + videoId[:3], album="Album", album_id="MPREalbum")], "lyrics": "MPLYt"}

    def get_lyrics(self, browse_id):
        return {"lyrics": "la la la"}

    def get_artist(self, cid):
        return {"name": "Artist", "songs": {"browseId": "VLOLAKsongs", "results": [track("sssssssssss", "Hit")]},
                "albums": {"results": [{"browseId": "MPREalbum", "title": "Album", "year": "2020"}]}}

    def get_account_info(self):
        return {"accountName": "Test Person", "channelHandle": "@test", "accountPhotoUrl": "https://example.com/p.jpg"}

    def get_library_playlists(self, limit=None):
        return [{"playlistId": k, "title": v["title"], "count": len(v["tracks"])} for k, v in self.playlists.items()]

    def create_playlist(self, title, description, privacy_status="PRIVATE", video_ids=None):
        pid = f"PLnew{len(self.playlists)}"
        self.playlists[pid] = {"title": title, "owned": True, "tracks": [track(v, v) for v in video_ids or []]}
        return pid

    def edit_playlist(self, pid, title=None, moveItem=None):
        self.calls.append(("edit", pid, title, moveItem))
        if title:
            self.playlists[pid]["title"] = title
        return "STATUS_SUCCEEDED"

    def delete_playlist(self, pid):
        self.playlists.pop(pid)

    def add_playlist_items(self, pid, ids, duplicates=False):
        self.playlists[pid]["tracks"] += [track(v, v, set_id="N" + v) for v in ids]
        return {"status": "STATUS_SUCCEEDED"}

    def remove_playlist_items(self, pid, videos):
        sets = {v["setVideoId"] for v in videos}
        self.playlists[pid]["tracks"] = [t for t in self.playlists[pid]["tracks"] if t.get("setVideoId") not in sets]

    def rate_song(self, vid, rating):
        self.calls.append(("rate", vid, rating))


@pytest.fixture()
def fake_yt(monkeypatch):
    from app import youtube
    fake = FakeYT()
    monkeypatch.setattr(youtube, "_make", lambda authenticated: fake)
    youtube.reset_clients()
    youtube._album_cache.clear()
    yield fake
    youtube.reset_clients()


COOKIES = "\n".join([
    "# Netscape HTTP Cookie File",
    ".youtube.com\tTRUE\t/\tTRUE\t2000000000\tSAPISID\tabc",
    ".youtube.com\tTRUE\t/\tTRUE\t2000000000\t__Secure-3PAPISID\tabc",
    ".youtube.com\tTRUE\t/\tTRUE\t2000000000\tLOGIN_INFO\txyz",
])


@pytest.fixture()
def client(fake_yt):
    from fastapi.testclient import TestClient
    from app import auth, db
    from app.main import app
    with TestClient(app) as c:
        auth.set_credentials("admin", "correct-horse", sign_out=True)
        c.fake = fake_yt
        yield c
        db.execute("DELETE FROM jobs")


@pytest.fixture()
def web(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    assert r.status_code == 200, r.text
    client.headers["X-Requested-With"] = "amplicon"
    return client


@pytest.fixture()
def app_token(client):
    r = client.post("/api/app/login", json={"username": "admin", "password": "correct-horse", "device": "Test iPhone"})
    assert r.status_code == 200, r.text
    return r.json()
