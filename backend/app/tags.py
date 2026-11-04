"""Reads and writes tags and covers: M4A, MP3, Opus, Ogg Vorbis and FLAC.

`write` tags a fresh download. `update` changes single fields of a file that's already in the library: everything
Amplicon doesn't know about stays, and the file is only replaced once the new version is complete.
"""
import base64
import io
import logging
import os
import shutil
import uuid
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TPUB, TRCK, TSRC, TXXX, USLT,
                         ID3NoHeaderError)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from PIL import Image

log = logging.getLogger("amplicon.tags")

MP4_EXT = (".m4a", ".m4b", ".mp4")
OGG_EXT = (".opus", ".ogg", ".oga")
SUPPORTED = (*MP4_EXT, ".mp3", *OGG_EXT, ".flac")

TEXT_FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "label", "isrc", "comment", "lyrics")
NUMBER_FIELDS = ("track", "track_total", "disc", "disc_total")
FIELDS = (*TEXT_FIELDS, *NUMBER_FIELDS)

MP4_TEXT = {"title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "year": "\xa9day",
            "genre": "\xa9gen", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}
MP4_FREE = {"label": "----:com.apple.iTunes:LABEL", "isrc": "----:com.apple.iTunes:ISRC"}
MP4_VIDEO_ID = "----:com.apple.iTunes:YOUTUBE_ID"
ID3_TEXT = {"title": ("TIT2", TIT2), "artist": ("TPE1", TPE1), "albumartist": ("TPE2", TPE2), "album": ("TALB", TALB),
            "year": ("TDRC", TDRC), "genre": ("TCON", TCON), "label": ("TPUB", TPUB), "isrc": ("TSRC", TSRC)}
VORBIS_TEXT = {"title": "title", "artist": "artist", "albumartist": "albumartist", "album": "album", "year": "date",
               "genre": "genre", "label": "label", "isrc": "isrc", "comment": "comment", "lyrics": "lyrics"}


def square_jpeg(data: bytes, size: int = 1400) -> bytes:
    """YouTube video thumbnails are 16:9 with bars; album art is square already. Crop to the centre square."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
    if side > size:
        img = img.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def _int(v) -> int | None:
    try:
        n = int(str(v).split("/")[0].strip())
    except (TypeError, ValueError):
        return None
    return n or None


def _pair(text) -> tuple[int | None, int | None]:
    """'3/12' → (3, 12)."""
    parts = str(text or "").split("/")
    return _int(parts[0]), _int(parts[1]) if len(parts) > 1 else None


def _first(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if str(x)) if v else ""
    return str(v) if v is not None else ""


def _vorbis(path: Path):
    ext = path.suffix.lower()
    if ext == ".flac":
        return FLAC(path)
    if ext == ".opus":
        return OggOpus(path)
    try:
        return OggVorbis(path)
    except mutagen.MutagenError:
        return OggOpus(path)  # .ogg is only the container


# ---------------------------------------------------------------- reading

def read(path: Path) -> dict:
    """Every field Amplicon knows, plus format, duration, bitrate, has_cover and the YouTube id. Raises on a broken file."""
    ext = path.suffix.lower()
    out: dict = {k: None for k in FIELDS}
    out["video_id"] = None
    out["has_cover"] = False
    if ext in MP4_EXT:
        f = MP4(path)
        t = f.tags or {}
        for field, key in MP4_TEXT.items():
            if key in t:
                out[field] = _first(t[key])
        for field, key in MP4_FREE.items():
            if key in t:
                out[field] = bytes(t[key][0]).decode("utf-8", "replace")
        if "trkn" in t and t["trkn"]:
            out["track"], out["track_total"] = (t["trkn"][0][0] or None), (t["trkn"][0][1] or None)
        if "disk" in t and t["disk"]:
            out["disc"], out["disc_total"] = (t["disk"][0][0] or None), (t["disk"][0][1] or None)
        if MP4_VIDEO_ID in t:
            out["video_id"] = bytes(t[MP4_VIDEO_ID][0]).decode("utf-8", "replace")
        out["has_cover"] = bool(t.get("covr"))
        out["format"] = "m4a"
    elif ext == ".mp3":
        f = mutagen.File(path)
        t = f.tags if f is not None and f.tags is not None else None
        if t is not None:
            for field, (key, _cls) in ID3_TEXT.items():
                frame = t.get(key)
                if frame is not None:
                    out[field] = _first([str(x) for x in frame.text])
            out["track"], out["track_total"] = _pair(t["TRCK"].text[0]) if "TRCK" in t else (None, None)
            out["disc"], out["disc_total"] = _pair(t["TPOS"].text[0]) if "TPOS" in t else (None, None)
            lyr = t.getall("USLT")
            out["lyrics"] = lyr[0].text if lyr else None
            com = t.getall("COMM")
            out["comment"] = _first(com[0].text) if com else None
            vid = t.getall("TXXX:YOUTUBE_ID")
            out["video_id"] = str(vid[0].text[0]) if vid else None
            out["has_cover"] = bool(t.getall("APIC"))
        out["format"] = "mp3"
    elif ext in (*OGG_EXT, ".flac"):
        f = _vorbis(path)
        for field, key in VORBIS_TEXT.items():
            if f.get(key):
                out[field] = _first(f[key])
        for field, key, total in (("track", "tracknumber", "tracktotal"), ("disc", "discnumber", "disctotal")):
            n, m = _pair(_first(f.get(key)))
            out[field] = n
            out[f"{field}_total"] = _int(_first(f.get(total))) or m
        out["video_id"] = _first(f.get("youtube_id")) or None
        out["has_cover"] = bool(f.get("metadata_block_picture")) or bool(getattr(f, "pictures", None))
        out["format"] = "flac" if ext == ".flac" else ("opus" if isinstance(f, OggOpus) else "ogg")
    else:
        raise ValueError(f"Can't read {ext} files")
    info = f.info if f is not None else None
    out["duration"] = round(getattr(info, "length", 0) or 0, 2) or None
    bitrate = getattr(info, "bitrate", 0) or 0
    if not bitrate and out["duration"]:
        bitrate = int(path.stat().st_size * 8 / out["duration"])
    out["bitrate"] = int(bitrate / 1000) or None
    return out


def cover_of(path: Path) -> bytes | None:
    ext = path.suffix.lower()
    try:
        if ext in MP4_EXT:
            c = (MP4(path).tags or {}).get("covr")
            return bytes(c[0]) if c else None
        if ext == ".mp3":
            pics = ID3(path).getall("APIC")
            return pics[0].data if pics else None
        f = _vorbis(path)
        if getattr(f, "pictures", None):
            return f.pictures[0].data
        raw = f.get("metadata_block_picture")
        return Picture(base64.b64decode(raw[0])).data if raw else None
    except Exception:  # noqa: BLE001 - a broken tag only means no cover to show
        return None


def video_id_of(path: Path) -> str | None:
    """The YouTube id Amplicon wrote into a file, so existing downloads are recognised."""
    try:
        return read(path)["video_id"]
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- writing

def _vorbis_picture(f, cover: bytes | None) -> None:
    pic = Picture()
    pic.type, pic.mime, pic.desc, pic.data = 3, "image/jpeg", "Cover", cover or b""
    if isinstance(f, FLAC):
        f.clear_pictures()
        if cover:
            f.add_picture(pic)
    elif cover:
        f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
    elif "metadata_block_picture" in f:
        del f["metadata_block_picture"]


def _apply(path: Path, values: dict, cover: bytes | None = None, remove_cover: bool = False, clear: bool = False,
           video_id: str | None = None) -> None:
    """values holds only what changes; an empty value removes the field."""
    ext = path.suffix.lower()
    current = {} if clear else read(path)
    numbers = {k: values.get(k, current.get(k)) for k in NUMBER_FIELDS}
    has_numbers = any(k in values for k in NUMBER_FIELDS)
    text = {k: ("" if values[k] is None else str(values[k])) for k in TEXT_FIELDS if k in values}

    if ext in MP4_EXT:
        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        t = f.tags
        if clear:
            t.clear()
        for field, value in text.items():
            key = MP4_TEXT.get(field) or MP4_FREE[field]
            t.pop(key, None)
            if value:
                t[key] = MP4FreeForm(value.encode()) if field in MP4_FREE else value
        if has_numbers:
            t.pop("trkn", None)
            t.pop("disk", None)
            if numbers["track"]:
                t["trkn"] = [(int(numbers["track"]), int(numbers["track_total"] or 0))]
            if numbers["disc"]:
                t["disk"] = [(int(numbers["disc"]), int(numbers["disc_total"] or 0))]
        if video_id:
            t[MP4_VIDEO_ID] = MP4FreeForm(video_id.encode())
        if cover:
            t["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        elif remove_cover:
            t.pop("covr", None)
        f.save()
    elif ext == ".mp3":
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        if clear:
            t.clear()
        for field, value in text.items():
            if field in ID3_TEXT:
                key, cls = ID3_TEXT[field]
                t.delall(key)
                if value:
                    t.add(cls(encoding=3, text=value))
        if "comment" in text:
            t.delall("COMM")
            if text["comment"]:
                t.add(COMM(encoding=3, lang="eng", desc="", text=text["comment"]))
        if "lyrics" in text:
            t.delall("USLT")
            if text["lyrics"]:
                t.add(USLT(encoding=3, lang="eng", desc="", text=text["lyrics"]))
        if has_numbers:
            t.delall("TRCK")
            t.delall("TPOS")
            if numbers["track"]:
                total = numbers["track_total"]
                t.add(TRCK(encoding=3, text=f"{numbers['track']}/{total}" if total else str(numbers["track"])))
            if numbers["disc"]:
                total = numbers["disc_total"]
                t.add(TPOS(encoding=3, text=f"{numbers['disc']}/{total}" if total else str(numbers["disc"])))
        if video_id:
            t.delall("TXXX:YOUTUBE_ID")
            t.add(TXXX(encoding=3, desc="YOUTUBE_ID", text=video_id))
        if cover or remove_cover:
            t.delall("APIC")
        if cover:
            t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        t.save(path, v2_version=4)
    elif ext in (*OGG_EXT, ".flac"):
        f = _vorbis(path)
        if clear:
            f.delete()
        for field, value in text.items():
            key = VORBIS_TEXT[field]
            if key in f:
                del f[key]
            if value:
                f[key] = value
        if has_numbers:
            for key, n in (("tracknumber", numbers["track"]), ("tracktotal", numbers["track_total"]),
                           ("discnumber", numbers["disc"]), ("disctotal", numbers["disc_total"])):
                if key in f:
                    del f[key]
                if n:
                    f[key] = str(n)
        if video_id:
            f["youtube_id"] = video_id
        if cover or remove_cover:
            _vorbis_picture(f, cover)
        f.save()
    else:
        raise ValueError(f"Can't tag {ext} files")


def write(path: Path, meta: dict, cover: bytes | None = None, lyrics: str | None = None) -> None:
    """A fresh download. meta: title, artist, albumartist, album, track, track_total, disc, disc_total, year, genre,
    label, isrc, video_id"""
    url = f"https://music.youtube.com/watch?v={meta['video_id']}"
    values = {k: meta.get(k) for k in ("title", "artist", "albumartist", "album", "genre", "label", "isrc", "track",
                                       "track_total", "disc", "disc_total", "year")}
    values["comment"] = url
    values["lyrics"] = lyrics
    if path.suffix.lower() not in SUPPORTED:
        log.warning("no tagging for %s", path.suffix)
        return
    _apply(path, values, cover=cover, clear=True, video_id=meta["video_id"])


def update(path: Path, changes: dict, cover: bytes | None = None, remove_cover: bool = False) -> None:
    """Changes a file in the library. Works on a copy, so a crash or a full disk never leaves a half written song."""
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unknown field {sorted(unknown)[0]}")
    work = path.with_name(f".amplicon-{uuid.uuid4().hex[:8]}{path.suffix}")
    shutil.copy2(path, work)
    try:
        _apply(work, changes, cover=cover, remove_cover=remove_cover)
        os.replace(work, path)
    finally:
        work.unlink(missing_ok=True)
