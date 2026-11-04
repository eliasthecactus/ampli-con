"""Files joining the library through an import, what a profile asks of them, fixing them, and files that disappear."""
import asyncio
import os
import shutil
import time

import pytest

from app import config, db, files, fixes, metadata, tags
from tests.conftest import HAS_FFMPEG
from tests.test_files import deezer, deezer_answers, h, jpeg, put, sample, tidy, wait_status  # noqa: F401

pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")


def profile_id() -> int:
    return db.scalar("SELECT id FROM profiles ORDER BY is_default DESC, id LIMIT 1")


def waiting(web) -> list[dict]:
    return web.get("/api/files/import").json()["folders"]


def library(web, **params) -> list[dict]:
    return web.get("/api/files", params={"limit": 500, **params}).json()["items"]


def import_all(web) -> None:
    r = web.post("/api/files/import", json={"profile_id": profile_id(), "folders": waiting(web)})
    assert r.status_code == 200, r.text


def second_location(tmp_path) -> dict:
    lid = db.insert("locations", {"name": "Other", "path": str(tmp_path), "created_at": 1})
    return db.row("SELECT * FROM locations WHERE id = ?", [lid])


@pytest.fixture(autouse=True)
def empty_queue():
    yield
    db.execute("DELETE FROM fix_items")
    db.execute("UPDATE profiles SET required_tags = 'title,artist', embed_cover = 1")


# ---------------------------------------------------------------- importing

def test_new_files_wait_for_an_import(web):
    put("Collection/Album/01 song.wav", title="Song", artist="Band")
    put("Collection/Album/02 other.flac", title="Other", artist="Band")
    put("loose.aac")
    (config.DOWNLOADS_DIR / "Collection" / "Album" / "notes.txt").write_text("not music")
    files.scan()
    assert library(web) == [], "nothing is in the library before an import"
    assert web.get("/api/files/facets").json()["files"] == 0
    assert web.get("/api/files/facets").json()["new"] == 3
    groups = {g["folder"]: g for g in waiting(web)}
    assert groups["Collection/Album"]["files"] == 2 and groups["Collection/Album"]["formats"] == ["flac", "wav"]
    assert groups[""]["files"] == 1
    inside = library(web, state="new", location_id=groups[""]["location_id"], folder="")
    assert [f["path"] for f in inside] == ["loose.aac"]


def test_import_changes_nothing_on_the_disk_and_shows_what_is_wrong(web):
    p = put("Collection/01 song.wav", title="Song", artist="Band")
    before = (p.read_bytes(), p.stat().st_mtime)
    files.scan()
    import_all(web)
    assert (p.read_bytes(), p.stat().st_mtime) == before
    f = library(web)[0]
    assert f["state"] == "imported" and f["profile_name"] and f["target_path"] == "Band - Song.wav", "the format stays"
    assert set(f["issues"]) == {"name", "cover"}
    counts = web.get("/api/files/facets").json()["issues"]
    assert (counts["any"], counts["name"], counts["cover"], counts["location"]) == (1, 1, 1, 0)
    assert [x["id"] for x in library(web, issue="name")] == [f["id"]]
    assert library(web, issue="location") == []
    assert waiting(web) == []


def test_import_needs_a_profile_and_a_selection(web):
    put("a.m4a", title="A", artist="A")
    files.scan()
    fid = db.scalar("SELECT id FROM library_files")
    assert web.post("/api/files/import", json={"profile_id": 9999, "ids": [fid]}).status_code == 400
    assert web.post("/api/files/import", json={"profile_id": profile_id(), "ids": []}).status_code == 400


def test_ignored_files_stay_ignored_until_they_are_brought_back(web):
    put("sample.wav", title="Kick", artist="Drum Kit")
    files.scan()
    folders = waiting(web)
    assert web.post("/api/files/ignore", json={"folders": folders}).json()["ignored"] == 1
    files.scan()
    assert waiting(web) == [] and web.get("/api/files/facets").json()["ignored"] == 1
    back = web.get("/api/files/import?state=ignored").json()["folders"]
    assert web.post("/api/files/restore", json={"folders": back, "state": "ignored"}).json()["restored"] == 1
    assert len(waiting(web)) == 1


def test_a_file_can_go_back_to_the_import_tab(web):
    p = put("x/a.m4a", title="A", artist="A")
    files.scan()
    import_all(web)
    fid = library(web)[0]["id"]
    assert web.post("/api/files/restore", json={"ids": [fid]}).json()["restored"] == 1
    assert library(web) == [] and len(waiting(web)) == 1
    assert web.get("/api/files/rename").json()["count"] == 0 and p.exists()


def test_downloads_are_imported_on_their_own(client, web, app_token, monkeypatch):
    from app import downloads

    async def no_cover(self, meta):
        return None
    monkeypatch.setattr(downloads.Manager, "_cover", no_cover)
    h = {"Authorization": f"Bearer {app_token['token']}"}
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01"}, headers=h).json()
    assert wait_status(client, j["id"], h, ("done",))["status"] == "done"
    f = library(web)[0]
    assert f["state"] == "imported" and f["profile_id"] == profile_id() and "name" not in f["issues"] and waiting(web) == []


def test_a_download_saved_to_another_location_on_purpose_stays_there(web, tmp_path):
    loc = second_location(tmp_path)
    shutil.copy(sample(".m4a"), tmp_path / "Band - Song.m4a")
    tags._apply(tmp_path / "Band - Song.m4a", {"title": "Song", "artist": "Band"}, clear=True, video_id="aaaaaaaaaaa", cover=jpeg())
    files.index_file(loc, "Band - Song.m4a", profile_id())
    assert library(web)[0]["issues"] == []


# ---------------------------------------------------------------- what a profile asks

def test_required_tags_come_from_the_profile(web):
    put("Band - Song.m4a", title="Song", artist="Band")
    files.scan()
    import_all(web)
    assert "tag:album" not in library(web)[0]["issues"]
    p = web.get("/api/profiles").json()[0]
    assert p["required_tags"] == ["title", "artist"]
    r = web.put(f"/api/profiles/{p['id']}", json={**p, "required_tags": ["album", "year", "nonsense"]}).json()
    assert r["required_tags"] == ["title", "artist", "album", "year"], "the file name needs the first two"
    assert {"tag:album", "tag:year"} <= set(library(web)[0]["issues"])
    assert [f["path"] for f in library(web, issue="tags")] == ["Band - Song.m4a"]


def test_a_file_without_tags_keeps_its_name_but_counts_as_wrong(web):
    put("mystery.m4a")
    files.scan()
    import_all(web)
    f = library(web)[0]
    assert {"tag:title", "tag:artist"} <= set(f["issues"]) and "name" not in f["issues"]
    assert web.get("/api/files/rename").json()["count"] == 0


def test_a_file_outside_its_profile_moves_there(web, tmp_path):
    loc = second_location(tmp_path)
    src = tmp_path / "Old" / "song.m4a"
    src.parent.mkdir()
    shutil.copy(sample(".m4a"), src)
    tags._apply(src, {"title": "Song", "artist": "Band"}, clear=True, cover=jpeg())
    (src.with_suffix(".lrc")).write_text("[00:01]la")
    files.scan()
    import_all(web)
    f = library(web)[0]
    assert {"location", "name"} <= set(f["issues"]) and f["location_id"] == loc["id"]
    assert web.get("/api/files/facets").json()["issues"]["location"] == 1
    plan = web.get("/api/files/rename").json()
    assert plan["count"] == 1 and plan["samples"][0]["target_location"] != "Other"

    r = web.post("/api/files/rename", json={}).json()
    assert r["renamed"] == 1 and r["problems"] == []
    dest = config.DOWNLOADS_DIR / "Band - Song.m4a"
    assert dest.exists() and dest.with_suffix(".lrc").exists() and not src.exists() and not src.parent.exists()
    f = library(web)[0]
    assert f["issues"] == [] and f["location_id"] != loc["id"]
    files.scan()
    assert [x["deleted_at"] for x in library(web)] == [None], "the scan doesn't mistake it for a deleted file"


def test_deleting_a_profile_shows_which_files_lost_it(web):
    put("Band - Song.m4a", title="Song", artist="Band")
    other = db.insert("profiles", {"name": "Second", "location_id": db.scalar("SELECT MIN(id) FROM locations"), "format": "m4a", "quality": "320",
                                   "album_template": "{artist} - {title}", "single_template": "{artist} - {title}",
                                   "playlist_template": "{title}", "playlist_layout": "album", "on_exists": "skip", "write_m3u": 1,
                                   "embed_cover": 1, "embed_lyrics": 0, "sponsorblock": 1, "is_default": 0, "created_at": 1})
    files.scan()
    web.post("/api/files/import", json={"profile_id": other, "folders": waiting(web)})
    assert web.delete(f"/api/profiles/{other}").status_code == 200
    assert library(web)[0]["issues"] == ["profile"]
    fid = library(web)[0]["id"]
    assert web.post("/api/files/import", json={"profile_id": profile_id(), "ids": [fid], "state": "imported"}).status_code == 200
    assert "profile" not in library(web)[0]["issues"]


# ---------------------------------------------------------------- deleted files

def test_a_deleted_file_stays_listed_and_comes_back(web):
    p = put("Band - Song.m4a", title="Song", artist="Band")
    keep = p.read_bytes()
    files.scan()
    import_all(web)
    fid = library(web)[0]["id"]
    p.unlink()
    files.scan()
    f = library(web)[0]
    assert f["id"] == fid and f["deleted_at"] and f["issues"] == [] and f["title"] == "Song"
    facets = web.get("/api/files/facets").json()
    assert facets["deleted"] == 1 and facets["files"] == 0 and facets["issues"]["any"] == 0
    assert [x["id"] for x in library(web, deleted="only")] == [fid]
    assert library(web, missing="cover") == [], "deleted files don't count as missing something"
    assert web.get(f"/api/files/{fid}").json()["tags"]["title"] == "Song"
    assert web.get(f"/api/files/{fid}/audio").status_code == 404
    r = web.post("/api/files/apply", json={"changes": [{"id": fid, "fields": {"title": "New"}}]}).json()
    assert r["changed"] == 0 and r["problems"][0]["reason"] == "This file was deleted"
    assert web.get("/api/files/rename").json()["count"] == 0

    p.write_bytes(keep)
    files.scan()
    f = library(web)[0]
    assert f["id"] == fid and f["deleted_at"] is None and f["profile_id"] == profile_id()


def test_a_deleted_file_can_be_taken_off_the_list(web):
    p = put("gone.m4a", title="A", artist="B")
    q = put("here.m4a", title="C", artist="D")
    files.scan()
    import_all(web)
    ids = {f["path"]: f["id"] for f in library(web)}
    p.unlink()
    files.scan()
    assert web.post("/api/files/forget", json={"ids": list(ids.values())}).json()["forgotten"] == 1
    assert [f["path"] for f in library(web)] == ["here.m4a"] and q.exists(), "a file that's still there is left alone"


def test_a_new_file_that_disappears_just_goes(web):
    p = put("a.m4a", title="A", artist="B")
    files.scan()
    p.unlink()
    files.scan()
    assert waiting(web) == [] and db.scalar("SELECT COUNT(*) FROM library_files") == 0


def test_a_missing_disk_marks_nothing(web):
    put("a.m4a", title="A", artist="A")
    files.scan()
    import_all(web)
    moved = config.DOWNLOADS_DIR.with_name("unmounted")
    config.DOWNLOADS_DIR.rename(moved)
    try:
        files.scan()
        assert library(web)[0]["deleted_at"] is None
    finally:
        moved.rename(config.DOWNLOADS_DIR)


def test_a_file_moved_by_hand_keeps_its_profile(web):
    p = put("Old/Band - Song.m4a", title="Song", artist="Band")
    files.scan()
    import_all(web)
    fid = library(web)[0]["id"]
    new = config.DOWNLOADS_DIR / "Elsewhere" / "renamed.m4a"
    new.parent.mkdir()
    p.rename(new)
    files.scan()
    f = library(web)[0]
    assert (f["id"], f["path"], f["deleted_at"], f["profile_id"]) == (fid, "Elsewhere/renamed.m4a", None, profile_id())
    assert waiting(web) == []


def test_a_rename_can_take_the_name_of_a_deleted_file(web):
    gone = put("Band - Song.m4a", title="Song", artist="Band", album="A")
    put("x/other.m4a", seconds=2, title="Song", artist="Band", album="B")
    files.scan()
    import_all(web)
    gone.unlink()
    files.scan()
    assert sorted((f["path"], bool(f["deleted_at"])) for f in library(web)) == [("Band - Song.m4a", True), ("x/other.m4a", False)]
    r = web.post("/api/files/rename", json={}).json()
    assert r["renamed"] == 1 and r["problems"] == []
    assert [(f["path"], f["album"], f["deleted_at"]) for f in library(web)] == [("Band - Song.m4a", "B", None)]
    assert (config.DOWNLOADS_DIR / "Band - Song.m4a").exists()


def test_files_still_being_copied_wait_for_the_next_scan(web):
    p = put("copying.wav", title="A", artist="B")
    files.SETTLE_SECONDS = 30
    files.scan()
    assert waiting(web) == [] and files.state["settling"] == 1
    old = time.time() - 60
    os.utime(p, (old, old))
    files.scan()
    assert len(waiting(web)) == 1 and files.state["settling"] == 0


# ---------------------------------------------------------------- fixing

def run_fixes() -> None:
    asyncio.run(fixes.manager.drain())


@pytest.fixture()
def no_network_covers(monkeypatch):
    async def art(self, fid, p):
        return jpeg() if p.get("cover") or p.get("folder_cover") else None
    monkeypatch.setattr(fixes.Fixer, "_cover", art)


def imported(rel: str, **meta):
    p = put(rel, **meta)
    os.utime(p, (time.time() - 60, time.time() - 60))
    files.scan()
    ids = [r["id"] for r in db.rows("SELECT id FROM library_files WHERE state = 'new'")]
    files.import_files(ids, profile_id())
    return p, ids[0]


def fix_state(web, group="confident") -> dict:
    return web.get("/api/files/fix", params={"group": group}).json()


def test_fix_fills_what_is_missing_and_keeps_the_rest(web, deezer, no_network_covers):
    p, fid = imported("x/song.m4a", title="Single ttt", artist="Artist", genre="My Genre")
    assert web.post("/api/files/fix", json={}).json()["queued"] == 1
    run_fixes()
    s = fix_state(web)
    assert s["counts"]["confident"] == 1
    item = s["items"][0]
    assert item["after"]["album"] == "Real Album" and item["after"]["genre"] == "My Genre", "tags that are there stay"
    assert item["after"]["title"] == "Single ttt" and item["cover"]
    assert tags.read(p)["album"] is None, "nothing is written before it's accepted"

    assert web.post("/api/files/fix/apply", json={"group": "confident"}).json()["accepted"] == 1
    run_fixes()
    assert fix_state(web)["counts"]["confident"] == 0
    f = library(web)[0]
    assert f["album"] == "Real Album" and f["has_cover"] and "cover" not in f["issues"]
    assert not p.exists() and (config.DOWNLOADS_DIR / f["path"]).exists(), "the name follows the new tags"


def test_fix_can_replace_tags_when_asked(web, deezer, no_network_covers):
    imported("x/song.m4a", title="Single ttt", artist="Artist", genre="My Genre")
    web.post("/api/files/fix", json={"mode": "all", "ids": [library(web)[0]["id"]]})
    run_fixes()
    assert fix_state(web)["items"][0]["after"]["genre"] == "Electro"


def test_fix_reads_a_file_without_tags_from_its_name_and_folders(web, monkeypatch, no_network_covers):
    async def nothing(song, details=3, query=None):
        return []
    monkeypatch.setattr(metadata, "lookup", nothing)
    imported("Queen/Jazz/03 - Queen - Bicycle Race.flac")
    web.post("/api/files/fix", json={})
    run_fixes()
    s = fix_state(web, "check")
    assert s["counts"]["check"] == 1
    item = s["items"][0]
    assert (item["after"]["artist"], item["after"]["title"], item["after"]["album"]) == ("Queen", "Bicycle Race", None)
    assert item["source"] == "file name"
    web.post("/api/files/fix/apply", json={"ids": [item["id"]]})
    run_fixes()
    assert library(web)[0]["path"] == "Queen - Bicycle Race.flac"


def test_fix_searches_with_the_folders_but_never_writes_them(web, monkeypatch):
    asked = []

    async def nothing(song, details=3, query=None):
        asked.append(song)
        return []
    monkeypatch.setattr(metadata, "lookup", nothing)
    imported("Collection/Misc/noise.flac")
    web.post("/api/files/fix", json={})
    run_fixes()
    assert (asked[0]["artist"], asked[0]["album"], asked[0]["title"]) == ("Collection", "Misc", "noise")
    s = fix_state(web, "none")
    assert s["counts"]["none"] == 1 and s["counts"]["check"] == 0, "a folder called Collection is no artist"


def test_a_lookup_prefers_the_album_the_file_names(monkeypatch):
    track = {"title": "Bicycle Race", "artist": {"name": "Queen"}, "duration": 181}

    async def fake(url, params=None):
        return {"data": [{**track, "id": 1, "album": {"title": "Greatest Hits"}}, {**track, "id": 2, "album": {"title": "Jazz"}}]}
    monkeypatch.setattr(metadata, "_get_json", fake)
    monkeypatch.setattr(metadata._deezer_gate, "gap", 0)

    async def full(c, t):
        return c
    monkeypatch.setattr(metadata, "_full", full)
    found = asyncio.run(metadata.lookup({"title": "Bicycle Race", "artist": "Queen", "album": "Jazz", "duration": 181}, 1))
    assert found[0]["album"] == "Jazz"
    found = asyncio.run(metadata.lookup({"title": "Bicycle Race", "artist": "Queen", "album": "Misc", "duration": 181}, 1))
    assert found[0]["album"] == "Greatest Hits", "a folder name that fits nothing changes nothing"


def test_fix_uses_the_cover_of_the_folder(web, monkeypatch):
    async def nothing(song, details=3, query=None):
        return []
    monkeypatch.setattr(metadata, "lookup", nothing)
    p, fid = imported("Album/Band - Song.m4a", title="Song", artist="Band")
    (p.parent / "Folder.JPG").write_bytes(jpeg(size=(50, 50)))
    web.post("/api/files/fix", json={})
    run_fixes()
    item = fix_state(web, "check")["items"][0]
    assert item["folder_cover"] is True and item["cover"] is None
    assert web.get(f"/api/files/{fid}/folder-cover").status_code == 200
    web.post("/api/files/fix/apply", json={"ids": [fid]})
    run_fixes()
    assert library(web)[0]["has_cover"] is True


def test_fix_reports_what_it_could_not_find_and_discards(web, monkeypatch):
    async def nothing(song, details=3, query=None):
        return []
    monkeypatch.setattr(metadata, "lookup", nothing)
    imported("Band - Song.m4a", title="Song", artist="Band")
    web.post("/api/files/fix", json={})
    run_fixes()
    assert fix_state(web, "none")["counts"]["none"] == 1
    web.post("/api/files/fix/discard", json={})
    assert sum(fix_state(web)["counts"].values()) == 0


def test_fix_forgets_a_compliant_file_the_match_has_nothing_for(web, monkeypatch):
    async def same(song, details=3, query=None):
        return [{"title": "Song", "artist": "Band", "score": 1.0, "source": "Deezer"}]
    monkeypatch.setattr(metadata, "lookup", same)
    _, fid = imported("Band - Song.m4a", title="Song", artist="Band")
    web.post("/api/files/fix", json={"ids": [fid]})
    run_fixes()
    assert fix_state(web, "none")["counts"]["none"] == 1, "it still lacks a cover and the match had none"

    db.execute("UPDATE library_files SET has_cover = 1, issues = '' WHERE id = ?", [fid])
    web.post("/api/files/fix", json={"ids": [fid]})
    run_fixes()
    assert sum(fix_state(web)["counts"].values()) == 0, "found, nothing to change and nothing missing"


def test_fix_survives_a_restart_and_a_broken_file(web, deezer, no_network_covers):
    p, fid = imported("x/song.m4a", title="Single ttt", artist="Artist")
    q, other = imported("y/other.m4a", title="Single ttt", artist="Artist")
    web.post("/api/files/fix", json={})
    assert db.scalar("SELECT COUNT(*) FROM fix_items WHERE status = 'queued'") == 2, "the queue is in the database"
    q.write_bytes(b"not audio any more")
    run_fixes()
    assert sum(1 for f in db.rows("SELECT status FROM fix_items") if f["status"] in ("ready", "failed")) == 2
    p.unlink()
    files.scan()
    web.post("/api/files/fix", json={"ids": [fid]})
    run_fixes()
    assert db.scalar("SELECT COUNT(*) FROM fix_items WHERE file_id = ?", [fid]) == 0, "a deleted file drops out"


# ---------------------------------------------------------------- what's already there

def test_in_library_finds_the_video_then_the_song(web):
    put("Collection/Daft Punk/One More Time.flac", seconds=2, title="One More Time (Official Video)", artist="Daft Punk")
    put("Collection/x.m4a", title="Something", artist="Else", video_id="aaaaaaaaaaa")
    files.scan()
    assert files.in_library("aaaaaaaaaaa", "Other", "Name")["match"] == "video", "waiting for an import still counts"
    hit = files.in_library("zzzzzzzzzzz", "One More Time", "Daft Punk, Romanthony", 2)
    assert hit["match"] == "song" and hit["path"].endswith(".flac") and hit["format"] == "flac"
    assert files.in_library("zzzzzzzzzzz", "One More Time", "Daft Punk", 24)["match"] == "song", "a video edit is the same song"
    assert files.in_library("zzzzzzzzzzz", "One More Time", "Daft Punk", 200) is None, "this far off it's another recording"
    assert files.in_library("zzzzzzzzzzz", "One More Time (Remix)", "Daft Punk", 2) is None
    assert files.in_library(None, None, None) is None


def test_deleted_and_ignored_files_are_not_in_the_library(web):
    p = put("a.m4a", title="Song", artist="Band", video_id="aaaaaaaaaaa")
    put("b.wav", title="Drum", artist="Kit", video_id="bbbbbbbbbbb")
    files.scan()
    web.post("/api/files/ignore", json={"ids": [db.scalar("SELECT id FROM library_files WHERE path = 'b.wav'")]})
    assert files.in_library("bbbbbbbbbbb", "Drum", "Kit") is None
    import_all(web)
    p.unlink()
    files.scan()
    assert files.in_library("aaaaaaaaaaa", "Song", "Band") is None


def test_a_copied_download_is_not_downloaded_again(client, web, app_token):
    put("Collection/some name.m4a", title="Song", artist="Band", video_id="ttttttttt01")
    files.scan()
    h = {"Authorization": f"Bearer {app_token['token']}"}
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01"}, headers=h).json()
    done = wait_status(client, j["id"], h, ("done", "failed"))
    assert done["skipped"] == 1 and done["items"][0]["file_path"] == "Collection/some name.m4a"


def test_a_new_download_replaces_the_entry_of_the_deleted_one(client, web, app_token, monkeypatch):
    from app import downloads

    async def no_cover(self, meta):
        return None
    monkeypatch.setattr(downloads.Manager, "_cover", no_cover)
    p = put("Old Name.m4a", title="Song", artist="Band", video_id="ttttttttt01")
    files.scan()
    import_all(web)
    p.unlink()
    files.scan()
    h = {"Authorization": f"Bearer {app_token['token']}"}
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01"}, headers=h).json()
    assert wait_status(client, j["id"], h, ("done", "failed"))["status"] == "done"
    assert [(f["path"], f["deleted_at"]) for f in library(web)] == [("Artist - Single ttt.m4a", None)]


def test_the_review_says_what_is_already_there(client, web, h, deezer):
    from app import settings_store
    put("Collection/x.flac", seconds=61, title="Single ttt", artist="Artist")
    files.scan()
    settings_store.update({"metadata_review": True})
    j = client.post("/api/downloads", json={"kind": "song", "id": "ttttttttt01", "review": True}, headers=h).json()
    assert wait_status(client, j["id"], h, ("review",))
    s = client.get(f"/api/jobs/{j['id']}/review", headers=h).json()["songs"][0]
    assert s["in_library"]["match"] == "song" and s["in_library"]["path"] == "Collection/x.flac"


# ---------------------------------------------------------------- playing

def test_formats_a_browser_can_not_play_are_converted(web):
    put("a.wma", title="A", artist="B")
    put("c.wav", title="C", artist="D")
    files.scan()
    import_all(web)
    ids = {f["format"]: f["id"] for f in library(web)}
    r = web.get(f"/api/files/{ids['wma']}/audio")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and len(r.content) > 500
    assert web.get(f"/api/files/{ids['wav']}/audio").headers["content-type"].startswith("audio/")
