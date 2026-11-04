"""The library, file names, tags, AI suggestions and the review before a download."""
import base64
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from PIL import Image

from app import ai, config, db, downloads, files, locations, metadata, settings_store, tags
from tests.conftest import HAS_FFMPEG

pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
CODECS = {".m4a": ["-c:a", "aac"], ".mp3": ["-c:a", "libmp3lame"], ".opus": ["-c:a", "libopus"], ".flac": ["-c:a", "flac"],
          ".ogg": ["-ac", "2", "-c:a", "vorbis", "-strict", "-2"]}
_samples: dict[tuple, Path] = {}


def sample(ext: str, seconds: int = 1) -> Path:
    key = (ext, seconds)
    if key not in _samples:
        p = config.TMP_DIR / f"sample-{seconds}{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", str(seconds), *CODECS[ext], str(p)], check=True)
        _samples[key] = p
    return _samples[key]


def put(rel: str, seconds: int = 1, **meta) -> Path:
    """A song in the location; with `video_id` it looks like an Amplicon download."""
    dest = config.DOWNLOADS_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(sample(Path(rel).suffix, seconds), dest)
    if meta:
        vid = meta.pop("video_id", None)
        tags._apply(dest, meta, clear=True, video_id=vid)
    return dest


def jpeg(color="red", size=(64, 64)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def tidy():
    for p in config.DOWNLOADS_DIR.iterdir() if config.DOWNLOADS_DIR.exists() else []:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    yield
    db.execute("DELETE FROM library_files")
    db.execute("DELETE FROM duplicate_ignores")
    db.execute("DELETE FROM settings")
    db.execute("UPDATE locations SET rename_all = 0")


def scan(web) -> dict:
    files.scan()
    return web.get("/api/files?limit=100").json()


# ---------------------------------------------------------------- tags

@pytest.mark.parametrize("ext", [".m4a", ".mp3", ".opus", ".flac", ".ogg"])
def test_tags_round_trip_every_format(client, ext):
    p = put(f"round{ext}", title="T", artist="A, B", album="Al", albumartist="A", year="2001-03-07", genre="Electro",
            label="Lab", isrc="GBDUW0000053", track=3, track_total=12, disc=2, disc_total=2, video_id="abcdefghijk")
    t = tags.read(p)
    assert (t["title"], t["artist"], t["album"], t["albumartist"]) == ("T", "A, B", "Al", "A")
    assert (t["year"], t["genre"], t["label"], t["isrc"]) == ("2001-03-07", "Electro", "Lab", "GBDUW0000053")
    assert (t["track"], t["track_total"], t["disc"], t["disc_total"]) == (3, 12, 2, 2)
    assert t["video_id"] == "abcdefghijk" and t["duration"] and t["bitrate"]

    tags.update(p, {"title": "New", "genre": ""}, cover=jpeg())
    t = tags.read(p)
    assert t["title"] == "New" and t["genre"] is None and t["has_cover"]
    assert t["artist"] == "A, B" and t["track"] == 3 and t["track_total"] == 12, "other fields stay"
    assert t["video_id"] == "abcdefghijk", "the duplicate check depends on it"
    assert tags.cover_of(p)[:2] == b"\xff\xd8"
    tags.update(p, {"track": 4}, remove_cover=True)
    t = tags.read(p)
    assert t["track"] == 4 and t["track_total"] == 12 and not t["has_cover"]


def test_update_keeps_tags_amplicon_does_not_know(client):
    p = put("keep.flac", title="T", artist="A")
    f = FLAC(p)
    f["replaygain_track_gain"] = "-3.2 dB"
    f.save()
    tags.update(p, {"title": "New"})
    assert FLAC(p)["replaygain_track_gain"] == ["-3.2 dB"]


def test_failed_update_leaves_the_file_alone(client, monkeypatch):
    p = put("safe.m4a", title="T", artist="A")
    before = p.read_bytes()

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(tags, "_apply", broken)
    with pytest.raises(OSError):
        tags.update(p, {"title": "New"})
    assert p.read_bytes() == before
    assert [x.name for x in p.parent.iterdir() if x.name.startswith(".amplicon")] == []


def test_download_writes_new_fields(client):
    p = put("new.mp3")
    tags.write(p, {"video_id": "abcdefghijk", "title": "T", "artist": "A", "genre": "Pop", "label": "L", "isrc": "X"})
    t = tags.read(p)
    assert (t["genre"], t["label"], t["isrc"]) == ("Pop", "L", "X") and t["comment"].endswith("abcdefghijk")
    p = put("new.m4a")
    tags.write(p, {"video_id": "abcdefghijk", "title": "T", "artist": "A", "genre": "Pop"})
    assert MP4(p).tags["\xa9gen"] == ["Pop"]


# ---------------------------------------------------------------- scanning and filters

def test_scan_lists_all_files_and_filters(web):
    put("Daft Punk - One.m4a", title="One", artist="Daft Punk", album="Discovery", track=1, genre="Electro", year="2001", video_id="aaaaaaaaaaa")
    put("Rock/two.mp3", title="Two", artist="Queen", year="1975")
    put("three.flac")
    (config.DOWNLOADS_DIR / "notes.txt").write_text("not music")
    r = scan(web)
    assert r["total"] == 3

    def q(**kw):
        return [f["title"] for f in web.get("/api/files", params=kw).json()["items"]]
    assert q(q="daft discovery") == ["One"]
    assert q(format="mp3") == ["Two"]
    assert q(genre="electro") == ["One"]
    assert q(year="19") == ["Two"]
    assert q(source="amplicon") == ["One"] and len(q(source="other")) == 2
    assert sorted(x or "" for x in q(missing="album")) == ["", "Two"]
    assert q(missing="cover,genre,title") == [None]
    assert q(sort="year", dir="desc")[0] == "One", "newest first, empty ones last"
    assert web.get("/api/files?sort=1;DROP TABLE jobs").status_code == 200, "unknown sort falls back"
    f = web.get("/api/files/facets").json()
    assert f["files"] == 3 and f["missing"]["cover"] == 3 and {x["value"] for x in f["formats"]} == {"m4a", "mp3", "flac"}


def test_scan_notices_changes_and_removals(web):
    p = put("a.m4a", title="Old", artist="A")
    put("b.m4a", title="B", artist="B")
    scan(web)
    tags.update(p, {"title": "New"})
    (config.DOWNLOADS_DIR / "b.m4a").unlink()
    r = scan(web)
    assert [f["title"] for f in r["items"]] == ["New"]


def test_offline_location_keeps_its_index(web):
    p = put("a.m4a", title="A", artist="A")
    scan(web)
    moved = config.DOWNLOADS_DIR.with_name("unmounted")
    config.DOWNLOADS_DIR.rename(moved)
    try:
        assert scan(web)["total"] == 1, "a missing disk must not empty the library"
    finally:
        moved.rename(config.DOWNLOADS_DIR)
    assert p.exists()


def test_broken_file_shows_up(web):
    (config.DOWNLOADS_DIR / "broken.mp3").write_bytes(b"not audio at all")
    r = web.get("/api/files").json() if False else scan(web)
    f = r["items"][0]
    assert f["path"] == "broken.mp3"
    assert web.get("/api/files/facets").json()["files"] == 1


def test_download_lands_in_the_library(client, web, app_token, monkeypatch):
    async def no_cover(self, meta):
        return None
    monkeypatch.setattr(downloads.Manager, "_cover", no_cover)
    h = {"Authorization": f"Bearer {app_token['token']}"}
    job = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01"}, headers=h).json()
    end = time.time() + 20
    while client.get(f"/api/jobs/{job['id']}", headers=h).json()["status"] != "done" and time.time() < end:
        time.sleep(0.05)
    items = web.get("/api/files").json()["items"]
    assert [f["path"] for f in items] == ["Artist - Single ttt.m4a"]
    assert items[0]["video_id"] == "ttttttttt01" and not items[0]["misnamed"]


# ---------------------------------------------------------------- file names

def test_rule_only_touches_amplicon_files_unless_the_location_says_so(web):
    put("Old Folder/mine.m4a", title="Mine", artist="Me", video_id="aaaaaaaaaaa")
    theirs = put("My Collection/Album/01 song.flac", title="Song", artist="Band")
    scan(web)
    pv = web.get("/api/files/rename").json()
    assert pv["count"] == 1 and pv["samples"][0]["target_path"] == "Me - Mine.m4a"

    r = web.post("/api/files/rename", json={}).json()
    assert r["renamed"] == 1 and r["problems"] == []
    assert (config.DOWNLOADS_DIR / "Me - Mine.m4a").exists()
    assert not (config.DOWNLOADS_DIR / "Old Folder").exists(), "the empty folder goes too"
    assert theirs.exists()

    loc = db.row("SELECT * FROM locations ORDER BY id LIMIT 1")
    db.update("locations", loc["id"], {"rename_all": 1})
    files.refresh_targets()
    assert web.get("/api/files/rename").json()["count"] == 1
    web.post("/api/files/rename", json={})
    assert (config.DOWNLOADS_DIR / "Band - Song.flac").exists()


def test_files_without_tags_are_never_renamed(web):
    put("keep me.m4a", video_id="aaaaaaaaaaa")
    put("keep too.m4a", title="Only Title", video_id="bbbbbbbbbbb")
    scan(web)
    assert web.get("/api/files/rename").json()["count"] == 0


def test_rename_conflict_is_reported_not_overwritten(web):
    a = put("one/x.m4a", title="Same", artist="A", video_id="aaaaaaaaaaa")
    b = put("two/x.m4a", title="Same", artist="A", video_id="bbbbbbbbbbb")
    scan(web)
    r = web.post("/api/files/rename", json={}).json()
    assert r["renamed"] == 1 and r["problems"][0]["reason"] == "Another file already has that name"
    assert (config.DOWNLOADS_DIR / "A - Same.m4a").exists()
    assert a.exists() != b.exists(), "one moved, the other stayed"


def test_profile_change_offers_the_rename_and_moves_everything(web):
    put("Band - Song.m4a", title="Song", artist="Band", album="Al", track=2, video_id="aaaaaaaaaaa")
    (config.DOWNLOADS_DIR / "Band - Song.lrc").write_text("[00:01]la")
    (config.DOWNLOADS_DIR / "Playlists").mkdir()
    m3u = config.DOWNLOADS_DIR / "Playlists" / "Mix.m3u8"
    m3u.write_text("#EXTM3U\n#EXTINF:1,Band - Song\n../Band - Song.m4a\n")
    scan(web)
    jid = db.insert("jobs", {"kind": "song", "source_id": "x", "title": "x", "status": "done", "created_at": 1,
                             "location_id": db.scalar("SELECT id FROM locations LIMIT 1")})
    db.insert("job_items", {"job_id": jid, "position": 1, "video_id": "aaaaaaaaaaa", "title": "Song", "status": "done",
                            "file_path": "Band - Song.m4a", "updated_at": 1})
    p = web.get("/api/profiles").json()[0]
    old = dict(p)
    r = web.put(f"/api/profiles/{p['id']}", json={**p, "album_template": "{artist}/{album}/{track:02} {title}"}).json()
    assert r["rename"]["count"] == 1 and r["rename"]["samples"][0]["target_path"] == "Band/Al/02 Song.m4a"
    assert (config.DOWNLOADS_DIR / "Band - Song.m4a").exists(), "nothing moves before the confirmation"

    assert web.post("/api/files/rename", json={}).json()["renamed"] == 1
    assert (config.DOWNLOADS_DIR / "Band/Al/02 Song.m4a").exists()
    assert (config.DOWNLOADS_DIR / "Band/Al/02 Song.lrc").exists()
    assert "../Band/Al/02 Song.m4a" in m3u.read_text()
    assert db.scalar("SELECT file_path FROM job_items WHERE job_id = ?", [jid]) == "Band/Al/02 Song.m4a"
    web.put(f"/api/profiles/{p['id']}", json=old)
    db.execute("DELETE FROM jobs WHERE id = ?", [jid])


def test_edit_moves_the_file_when_the_rule_is_enforced(web):
    put("A - Old.m4a", title="Old", artist="A", video_id="aaaaaaaaaaa")
    fid = scan(web)["items"][0]["id"]
    r = web.post("/api/files/apply", json={"changes": [{"id": fid, "fields": {"title": "New", "track": "3", "genre": "Pop"}}]}).json()
    assert r["changed"] == 1 and r["renamed"] == 1
    d = web.get(f"/api/files/{fid}").json()
    assert d["path"] == "A - New.m4a" and d["tags"]["track"] == 3 and d["genre"] == "Pop"
    assert (config.DOWNLOADS_DIR / "A - New.m4a").exists()

    settings_store.update({"enforce_file_names": False})
    r = web.post("/api/files/apply", json={"changes": [{"id": fid, "fields": {"title": "Newer"}}]}).json()
    assert r["renamed"] == 0 and web.get(f"/api/files/{fid}").json()["misnamed"] is True


def test_edit_problems_do_not_stop_the_others(web):
    put("a.m4a", title="A", artist="A")
    put("b.m4a", title="B", artist="B")
    ids = [f["id"] for f in scan(web)["items"]]
    (config.DOWNLOADS_DIR / "b.m4a").unlink()
    r = web.post("/api/files/apply", json={"changes": [{"id": i, "fields": {"genre": "Pop"}} for i in ids]}).json()
    assert r["changed"] == 1 and len(r["problems"]) == 1


# ---------------------------------------------------------------- bulk tools

def test_bulk_preview_writes_nothing_then_applies(web):
    put("Daft Punk - One More Time.m4a", title="one more time (Official Video)", artist="Daft Punk")
    put("Queen - Bicycle Race.m4a", title="BICYCLE RACE [Lyrics]", artist="Queen")
    ids = [f["id"] for f in scan(web)["items"]]

    def run(ops):
        return web.post("/api/files/preview", json={"ids": ids, "ops": ops}).json()["proposals"]
    assert run({"set": {"genre": "Rock"}})[0]["changed"] == ["genre"]
    assert [p["after"]["title"] for p in run({"clean_title": True})] == ["one more time", "BICYCLE RACE"]
    assert run({"case": {"field": "title", "mode": "title"}})[1]["after"]["title"] == "Bicycle Race [lyrics]"
    assert run({"replace": {"field": "artist", "find": "queen", "with": "QUEEN"}})[1]["after"]["artist"] == "QUEEN"
    assert run({"replace": {"field": "title", "find": r"\s*[\[(].*", "with": "", "regex": True}})[0]["after"]["title"] == "one more time"
    assert run({"from_name": "{artist} - {title}"})[0]["after"]["title"] == "One More Time"
    assert [p["after"]["track"] for p in run({"number": {"start": 5}})] == [5, 6]
    assert web.post("/api/files/preview", json={"ids": ids, "ops": {"replace": {"field": "title", "find": "(", "regex": True}}}).status_code == 400
    assert web.get(f"/api/files/{ids[0]}").json()["tags"]["genre"] is None, "a preview writes nothing"


def test_cover_upload_and_delete(web):
    p = put("a.m4a", title="A", artist="A")
    fid = scan(web)["items"][0]["id"]
    assert web.get(f"/api/files/{fid}/cover").status_code == 404
    data = "data:image/jpeg;base64," + base64.b64encode(jpeg(size=(200, 100))).decode()
    assert web.post("/api/files/apply", json={"changes": [{"id": fid, "cover": {"data": data}}]}).status_code == 200
    assert Image.open(io.BytesIO(tags.cover_of(p))).size == (100, 100), "covers end up square"
    r = web.get(f"/api/files/{fid}/cover?size=48")
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (48, 48)
    assert web.get(f"/api/files").json()["items"][0]["has_cover"] is True
    web.post("/api/files/apply", json={"changes": [{"id": fid, "remove_cover": True}]})
    assert not tags.read(p)["has_cover"]
    bad = web.post("/api/files/apply", json={"changes": [{"id": fid, "cover": {"data": base64.b64encode(b"nope").decode()}}]})
    assert bad.status_code == 400
    assert web.post("/api/files/apply", json={"changes": [{"id": fid, "cover": {"url": "https://example.com/x.jpg"}}]}).status_code == 400
    assert web.post("/api/files/apply", json={"changes": [{"id": fid, "cover": {"url": "http://169.254.169.254/x"}}]}).status_code == 400


def test_audio_needs_a_session_and_supports_ranges(web, client):
    put("a.m4a", title="A", artist="A")
    fid = scan(web)["items"][0]["id"]
    r = web.get(f"/api/files/{fid}/audio", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get(f"/api/files/{fid}/audio").status_code == 401
    assert client.get("/api/files").status_code == 401
    client.cookies.update(cookies)


# ---------------------------------------------------------------- duplicates

def test_duplicates(web):
    same = put("a.m4a", title="Song", artist="A", video_id="aaaaaaaaaaa")
    shutil.copy(same, config.DOWNLOADS_DIR / "copy.m4a")
    put("v1.mp3", title="Hit", artist="B", video_id="bbbbbbbbbbb")
    put("v2.m4a", title="Other Name", artist="Else", video_id="bbbbbbbbbbb")
    put("s1.m4a", title="Tune (Official Video)", artist="C", album="X", genre="Pop", year="2000")
    put("s2.m4a", title="tune", artist="C, D")
    put("r1.m4a", title="Tune (Bob Remix)", artist="C")
    put("long.m4a", 8, title="Tune", artist="C")
    scan(web)
    groups = web.get("/api/files/duplicates").json()["groups"]
    kinds = sorted(g["kind"] for g in groups)
    assert kinds == ["file", "song", "video"], groups
    song = next(g for g in groups if g["kind"] == "song")
    paths = {f["path"] for f in song["files"]}
    assert paths == {"s1.m4a", "s2.m4a"}, "the remix and the longer recording are different songs"
    assert next(f for f in song["files"] if f["id"] == song["keep"])["path"] == "s1.m4a", "the better tagged one"

    web.post("/api/files/duplicates/ignore", json={"key": song["key"]})
    assert len(web.get("/api/files/duplicates").json()["groups"]) == 2
    assert len(web.get("/api/files/duplicates?ignored=true").json()["groups"]) == 3

    drop = next(g for g in groups if g["kind"] == "file")
    other = next(f["id"] for f in drop["files"] if f["id"] != drop["keep"])
    assert web.post("/api/files/delete", json={"ids": [other]}).json()["deleted"] == 1
    kept = next(f for f in drop["files"] if f["id"] == drop["keep"])
    assert (config.DOWNLOADS_DIR / kept["path"]).exists()
    assert len(web.get("/api/files/duplicates").json()["groups"]) == 1


# ---------------------------------------------------------------- matching

def test_cleaning_and_scoring():
    assert metadata.clean_title("Song (Official Music Video) [HD]") == "Song"
    assert metadata.clean_title("Song (feat. Someone)") == "Song"
    assert metadata.clean_title("Song feat. Someone") == "Song"
    assert metadata.clean_title("Song (Bob Remix) [Lyrics]") == "Song (Bob Remix)"
    assert metadata.clean_title("With or Without You") == "With or Without You"
    assert metadata.clean_title("Dancing with Myself") == "Dancing with Myself"
    assert metadata.clean_title("Song (with Bob)") == "Song"
    assert metadata.clean_title("Song feat. Someone (Bob Remix)") == "Song (Bob Remix)"
    assert metadata.split_featured("Get Lucky (feat. Pharrell Williams and Nile Rodgers)", "Daft Punk, Pharrell Williams, Nile Rodgers") == \
        ("Get Lucky", "Daft Punk, Pharrell Williams, Nile Rodgers")
    assert metadata.split_featured("Song (feat. A & B)", "C") == ("Song", "C, A, B"), "nobody gets lost"
    assert metadata.split_featured("Song (Bob Remix)", "C") == ("Song (Bob Remix)", "C")
    assert metadata.split_featured("With or Without You", "U2") == ("With or Without You", "U2")
    assert metadata.artists_of("Daft Punk - Topic") == ["Daft Punk"]
    assert metadata.artists_of("A, B & C feat. D") == ["A", "B", "C", "D"]
    song = {"title": "One More Time (Official Video)", "artist": "Daft Punk", "duration": 320}
    good = metadata.score(song, {"title": "One More Time", "artist": "Daft Punk", "duration": 320})
    wrong = metadata.score(song, {"title": "One More Time", "artist": "Britney Spears", "duration": 197})
    remix = metadata.score(song, {"title": "One More Time (Bob Remix)", "artist": "Daft Punk", "duration": 320})
    assert good >= metadata.CONFIDENT > wrong and remix < metadata.CONFIDENT


def test_covers_only_from_known_hosts():
    assert metadata.cover_allowed("https://cdn-images.dzcdn.net/images/cover/x/1000x1000.jpg")
    assert metadata.cover_allowed("https://lh3.googleusercontent.com/abc=w544-h544")
    assert metadata.cover_allowed("https://is1-ssl.mzstatic.com/image/x/1400x1400bb.jpg")
    for bad in ("http://cdn-images.dzcdn.net/x", "https://dzcdn.net.evil.com/x", "https://localhost/x", "file:///etc/passwd", "", None):
        assert not metadata.cover_allowed(bad)
    assert metadata.hires("https://lh3.googleusercontent.com/abc=w544-h544-l90-rj").endswith("=w1400-h1400-l90-rj")


# ---------------------------------------------------------------- review

DEEZER_TRACK = {"id": 1, "title": "Single ttt", "isrc": "GBDUW0000053", "duration": 61, "link": "https://deezer.com/track/1",
                "artist": {"name": "Artist"}, "album": {"id": 9, "title": "Real Album", "cover_xl": "https://cdn-images.dzcdn.net/c.jpg"}}


def deezer_answers(url, params):
    path = url.split("api.deezer.com/")[1]
    if path == "search":
        return {"data": [DEEZER_TRACK, {**DEEZER_TRACK, "id": 2, "title": "Unrelated", "artist": {"name": "Nobody"}, "duration": 300}]}
    if path == "search/album":
        return {"data": [{"id": 9, "title": "Album", "nb_tracks": 2, "artist": {"name": "Artist"}}]}
    if path.startswith("track/"):
        return {**DEEZER_TRACK, "track_position": 4, "disk_number": 2, "contributors": [{"name": "Artist"}, {"name": "Guest"}]}
    if path == "album/9":
        return {"id": 9, "title": "Real Album", "release_date": "2001-03-07", "label": "Label", "nb_tracks": 12, "artist": {"name": "Artist"},
                "genres": {"data": [{"name": "Electro"}]}, "cover_xl": "https://cdn-images.dzcdn.net/c.jpg"}
    if path == "album/9/tracks":
        return {"data": [{"id": 11, "title": "One", "track_position": 1, "disk_number": 1, "isrc": "I1", "duration": 61, "artist": {"name": "Artist"}},
                         {"id": 12, "title": "Two", "track_position": 2, "disk_number": 2, "isrc": "I2", "duration": 61, "artist": {"name": "Artist"}}]}
    raise AssertionError(path)


@pytest.fixture()
def deezer(monkeypatch):
    calls = []

    async def fake(url, params=None):
        calls.append(url)
        return deezer_answers(url, params)
    monkeypatch.setattr(metadata, "_get_json", fake)
    monkeypatch.setattr(metadata._deezer_gate, "gap", 0)
    metadata._albums.clear()
    return calls


@pytest.fixture(autouse=True)
def covers_stubbed(monkeypatch):
    seen = []

    async def cover(self, meta):
        seen.append(meta)
        return None
    monkeypatch.setattr(downloads.Manager, "_cover", cover)
    return seen


def wait_status(c, jid, h, want, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{jid}", headers=h).json()
        if j["status"] in want:
            return j
        time.sleep(0.03)
    raise AssertionError(j)


@pytest.fixture()
def h(app_token):
    return {"Authorization": f"Bearer {app_token['token']}"}


def test_review_off_or_old_app_downloads_straight_away(client, h, deezer):
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    assert wait_status(client, j["id"], h, ("done",))["review"] is False, "the setting is off"
    settings_store.update({"metadata_review": True})
    assert client.get("/api/app/info", headers=h).json()["metadata_review"] is True
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt02"}, headers=h).json()
    assert wait_status(client, j["id"], h, ("done",))["status"] == "done", "an app that can't review isn't stuck"
    assert deezer == []


def test_review_song_with_edits(client, h, deezer, covers_stubbed):
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    assert wait_status(client, j["id"], h, ("review",))
    time.sleep(0.1)
    assert not list(config.DOWNLOADS_DIR.rglob("*.m4a")), "nothing downloads before the confirmation"

    r = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()
    s = r["songs"][0]
    assert s["confident"] and s["original"]["album"] == "Album"
    assert s["suggested"]["album"] == "Real Album" and s["suggested"]["artist"] == "Artist, Guest"
    assert (s["suggested"]["genre"], s["suggested"]["label"], s["suggested"]["isrc"]) == ("Electro", "Label", "GBDUW0000053")
    assert (s["suggested"]["year"], s["suggested"]["disc"], s["suggested"]["track"]) == ("2001-03-07", 2, 4)
    assert s["path"] == "Artist, Guest - Single ttt.m4a" and s["exists"] is False
    assert {c["source"] for c in s["covers"]} == {"youtube", "deezer"}

    p = client.post(f"/api/jobs/{j['id']}/review/path", json={"item_id": s["item_id"], "fields": {**s["suggested"], "title": "Edited"}}, headers=h).json()
    assert p["path"] == "Artist, Guest - Edited.m4a"
    assert len(client.post(f"/api/jobs/{j['id']}/review/lookup", json={"item_id": s["item_id"], "query": "something else"}, headers=h).json()["candidates"]) == 2

    bad = client.post(f"/api/jobs/{j['id']}/confirm", json={"cover": {"url": "https://evil.example/x.jpg"}}, headers=h)
    assert bad.status_code == 400
    r = client.post(f"/api/jobs/{j['id']}/confirm", json={"songs": [{"item_id": s["item_id"], "fields": {"title": "Edited", "genre": "House"}}]}, headers=h)
    assert r.status_code == 200, r.text
    done = wait_status(client, j["id"], h, ("done", "failed"))
    assert done["status"] == "done", done
    assert done["items"][0]["title"] == "Edited" and done["items"][0]["file_path"] == "Artist, Guest - Edited.m4a"
    t = tags.read(config.DOWNLOADS_DIR / "Artist, Guest - Edited.m4a")
    assert (t["title"], t["genre"], t["album"], t["label"], t["disc"]) == ("Edited", "House", "Real Album", "Label", 2)
    assert t["video_id"] == "ttttttttt01"
    assert covers_stubbed[-1]["thumbnail"] == "https://cdn-images.dzcdn.net/c.jpg"
    assert client.post(f"/api/jobs/{j['id']}/confirm", json={}, headers=h).status_code == 400, "only once"


def test_review_low_confidence_keeps_youtube_data(client, h, monkeypatch):
    async def fake(url, params=None):
        if url.endswith("/search"):
            return {"data": [{**DEEZER_TRACK, "title": "Totally Different", "artist": {"name": "Nobody"}, "duration": 500}]}
        return deezer_answers(url, params)
    monkeypatch.setattr(metadata, "_get_json", fake)
    monkeypatch.setattr(metadata._deezer_gate, "gap", 0)
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    s = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]
    assert not s["confident"] and s["candidates"] and s["suggested"]["title"] == "Single ttt"
    client.post(f"/api/jobs/{j['id']}/cancel", headers=h)


def test_review_works_when_deezer_is_down(client, h, monkeypatch):
    async def down(url, params=None):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(metadata, "_get_json", down)
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    s = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]
    assert s["candidates"] == [] and s["suggested"]["title"] == "Single ttt"
    client.post(f"/api/jobs/{j['id']}/confirm", json={}, headers=h)
    assert wait_status(client, j["id"], h, ("done", "failed"))["status"] == "done"


def test_review_album_shared_fields_skip_and_cover_upload(client, h, deezer, covers_stubbed):
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "album", "id": "MPREalbum", "title": "Album", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    r = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()
    assert r["albums"][0]["title"] == "Real Album" and len(r["albums"][0]["songs"]) == 2
    one, two = r["songs"]
    data = base64.b64encode(jpeg()).decode()
    res = client.post(f"/api/jobs/{j['id']}/confirm", headers=h, json={
        "shared": {"album": "Shared", "year": "1999"}, "cover": {"data": data},
        "songs": [{"item_id": one["item_id"], "fields": {"title": "First"}}, {"item_id": two["item_id"], "skip": True}]})
    assert res.status_code == 200, res.text
    done = wait_status(client, j["id"], h, ("done", "failed"))
    assert done["status"] == "done" and done["total"] == 1
    t = tags.read(config.DOWNLOADS_DIR / done["items"][0]["file_path"])
    assert (t["title"], t["album"], t["year"]) == ("First", "Shared", "1999")
    assert covers_stubbed[-1]["cover_file"].endswith("-all.jpg")


def test_review_skipping_everything_cancels(client, h, deezer):
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    item = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]["item_id"]
    r = client.post(f"/api/jobs/{j['id']}/confirm", json={"songs": [{"item_id": item, "skip": True}]}, headers=h).json()
    assert r["status"] == "cancelled"


def test_review_shows_an_existing_file(client, h, deezer):
    put("Artist, Guest - Single ttt.m4a")
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    assert client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]["exists"] is True
    client.post(f"/api/jobs/{j['id']}/cancel", headers=h)


def test_review_survives_a_restart(client, h, deezer):
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    downloads.manager.running.clear()
    db.execute("UPDATE job_items SET status = 'queued' WHERE status = 'downloading'")
    assert client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]["suggested"]["album"] == "Real Album"
    assert j["id"] in [x["id"] for x in client.get("/api/jobs?active=true", headers=h).json()]
    client.post(f"/api/jobs/{j['id']}/cancel", headers=h)


# ---------------------------------------------------------------- AI

REAL_CLIENT = httpx.AsyncClient


class FakeOpenRouter:
    def __init__(self, reply=None, status=200):
        self.reply, self.status, self.bodies, self.headers = reply, status, [], []

    def install(self, monkeypatch):
        fake = self

        def handler(request):
            fake.bodies.append(json.loads(request.content))
            fake.headers.append(request.headers)
            if fake.status != 200:
                return httpx.Response(fake.status, json={"error": {"message": "nope"}})
            songs = json.loads(fake.bodies[-1]["messages"][1]["content"].split("Songs:\n", 1)[1])
            content = fake.reply(songs) if callable(fake.reply) else fake.reply
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
        return self


def test_ai_defaults():
    assert settings_store.DEFAULTS["openrouter_model"] == "deepseek/deepseek-v4.1-flash-1017"
    text = settings_store.DEFAULTS["ai_instruction"]
    assert text.startswith("Split artist and title") and text.endswith("(Name Cover).")


def test_ai_key_is_never_shown(web):
    assert web.get("/api/settings").json()["settings"]["openrouter_key_set"] is False
    s = web.put("/api/settings", json={"openrouter_key": "sk-secret"}).json()["settings"]
    assert s["openrouter_key_set"] is True and "sk-secret" not in json.dumps(s) and "openrouter_key_enc" not in s
    assert "sk-secret" not in db.scalar("SELECT value FROM settings WHERE key = 'openrouter_key_enc'")
    assert settings_store.openrouter_key() == "sk-secret"
    # Saving the page sends the settings back as they came.
    assert web.put("/api/settings", json=s).json()["settings"]["openrouter_key_set"] is True
    assert web.put("/api/settings", json={"openrouter_key": ""}).json()["settings"]["openrouter_key_set"] is False
    assert web.put("/api/settings", json={"openrouter_model": "", "ai_instruction": ""}).json()["settings"]["openrouter_model"].startswith("deepseek/")


def test_ai_needs_a_key(web):
    put("a.m4a", title="A", artist="A")
    fid = scan(web)["items"][0]["id"]
    r = web.post("/api/files/ai", json={"ids": [fid]})
    assert r.status_code == 400 and "OpenRouter" in r.json()["detail"]


def test_ai_suggests_and_writes_only_after_apply(web, monkeypatch):
    p = put("x.m4a", title="DaftPunkVEVO - One More Time (Official Video)", artist="DaftPunkVEVO", video_id="aaaaaaaaaaa")
    fid = scan(web)["items"][0]["id"]
    settings_store.update({"openrouter_key": "sk-test"})
    fake = FakeOpenRouter(lambda songs: json.dumps({"tracks": [
        {"id": songs[0]["id"], "title": "One More Time", "artist": "Daft Punk", "isrc": "NOT-ALLOWED"},
        {"id": 999999, "title": "Someone else's song"}]})).install(monkeypatch)
    r = web.post("/api/files/ai", json={"ids": [fid]}).json()
    body = fake.bodies[0]
    assert body["model"] == "deepseek/deepseek-v4.1-flash-1017" and body["response_format"] == {"type": "json_object"}
    assert "Split artist and title" in body["messages"][1]["content"]
    assert fake.headers[0]["authorization"] == "Bearer sk-test"
    pr = r["proposals"][0]
    assert sorted(pr["changed"]) == ["artist", "title"] and pr["after"]["isrc"] is None
    assert len(r["proposals"]) == 1
    assert tags.read(p)["artist"] == "DaftPunkVEVO", "nothing is written until a person agrees"
    web.post("/api/files/apply", json={"changes": [{"id": fid, "fields": {k: pr["after"][k] for k in pr["changed"]}}]})
    assert (config.DOWNLOADS_DIR / "Daft Punk - One More Time.m4a").exists()


def test_ai_own_instruction_and_errors(web, monkeypatch):
    put("x.m4a", title="T", artist="A")
    fid = scan(web)["items"][0]["id"]
    settings_store.update({"openrouter_key": "sk-test"})
    fake = FakeOpenRouter("not json at all").install(monkeypatch)
    r = web.post("/api/files/ai", json={"ids": [fid], "instruction": "Only fix the case"}).json()
    assert r["errors"] and r["proposals"][0]["changed"] == []
    assert "Only fix the case" in fake.bodies[0]["messages"][1]["content"]
    FakeOpenRouter(status=402).install(monkeypatch)
    assert "OpenRouter error 402" in web.post("/api/files/ai", json={"ids": [fid]}).json()["errors"][0]


def test_ai_batches(web, monkeypatch):
    for i in range(5):
        put(f"{i}.m4a", title=f"T{i}", artist="A")
    ids = [f["id"] for f in scan(web)["items"]]
    settings_store.update({"openrouter_key": "sk-test", "ai_batch_size": 2})
    fake = FakeOpenRouter(lambda songs: json.dumps({"tracks": [{"id": s["id"], "title": s["title"] + "!"} for s in songs]})).install(monkeypatch)
    r = web.post("/api/files/ai", json={"ids": ids}).json()
    assert len(fake.bodies) == 3 and all(p["changed"] == ["title"] for p in r["proposals"])


def test_review_ai(client, h, deezer, monkeypatch):
    settings_store.update({"metadata_review": True, "openrouter_key": "sk-test"})
    assert client.get("/api/app/info", headers=h).json()["ai_available"] is True
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    wait_status(client, j["id"], h, ("review",))
    s = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]
    FakeOpenRouter(lambda songs: json.dumps({"tracks": [{"id": songs[0]["id"], "title": "Clean"}]})).install(monkeypatch)
    r = client.post(f"/api/jobs/{j['id']}/review/ai", json={"songs": [{"id": s["item_id"], "title": "Dirty"}]}, headers=h).json()
    assert r["suggestions"][str(s["item_id"])] == {"title": "Clean"}
    client.post(f"/api/jobs/{j['id']}/cancel", headers=h)


# ---------------------------------------------------------------- upgrading

def test_upgrade_from_0_1_keeps_custom_templates_and_moves_old_defaults(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.executescript("".join(db.MIGRATIONS[:3]))
    conn.execute("INSERT INTO locations (id, name, path, scan_kind, created_at) VALUES (1, 'Music', '/music', 'none', 1)")
    old = ("{albumartist}/{album}[ ({year})]/[{disc}-]{track:02} {title}", "{artist}/{album}/{title}",
           "Playlists/{playlist}/{position:03} {artist} - {title}")
    cols = ("INSERT INTO profiles (name, location_id, format, quality, album_template, single_template, playlist_template, "
            "playlist_layout, on_exists, write_m3u, embed_cover, embed_lyrics, sponsorblock, is_default, created_at) "
            "VALUES (?, 1, 'm4a', '320', ?, ?, ?, 'album', 'skip', 1, 1, 0, 1, 0, 1)")
    conn.execute(cols, ["Old", *old])
    conn.execute(cols, ["Mine", "{album}/{title}", "Singles/{title}", old[2]])
    conn.execute("INSERT INTO jobs (kind, source_id, title, created_at) VALUES ('song', 'x', 'kept', 1)")
    conn.executescript("BEGIN;" + db.MIGRATIONS[3] + ";COMMIT;")
    rows = conn.execute("SELECT name, album_template, single_template, playlist_template FROM profiles ORDER BY id").fetchall()
    assert rows[0] == ("Old", "{artist} - {title}", "{artist} - {title}", "Playlists/{playlist}/{artist} - {title}")
    assert rows[1][1:3] == ("{album}/{title}", "Singles/{title}"), "a template someone wrote stays"
    assert conn.execute("SELECT title, review FROM jobs").fetchone() == ("kept", 0)
    assert conn.execute("SELECT rename_all FROM locations").fetchone() == (0,)
    conn.close()
