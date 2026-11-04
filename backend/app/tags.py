"""Reads and writes tags and covers of every audio format the library lists.

`write` tags a fresh download. `update` changes single fields of a file that's already in the library: everything
Amplicon doesn't know about stays, and the file is only replaced once the new version is complete.
"""
import base64
import io
import logging
import os
import shutil
import struct
import uuid
from pathlib import Path

import mutagen
from mutagen.aac import AAC
from mutagen.apev2 import BINARY, APEValue
from mutagen.asf import ASF, ASFByteArrayAttribute
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
ID3_CHUNK_EXT = (".wav", ".aif", ".aiff", ".dsf")  # the ID3 tag is a part of the container
ID3_PLAIN_EXT = (".mp3", ".aac", ".tta")  # the ID3 tag sits in front of the audio
ID3_EXT = (*ID3_PLAIN_EXT, *ID3_CHUNK_EXT)
APE_EXT = (".ape", ".wv", ".mpc", ".tak")
ASF_EXT = (".wma",)
SUPPORTED = (*MP4_EXT, *OGG_EXT, ".flac", *ID3_EXT, *APE_EXT, *ASF_EXT)

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


APE_TEXT = {"title": "Title", "artist": "Artist", "albumartist": "Album Artist", "album": "Album", "year": "Year",
            "genre": "Genre", "label": "Label", "isrc": "ISRC", "comment": "Comment", "lyrics": "Lyrics"}
APE_COVER = "Cover Art (Front)"
ASF_TEXT = {"title": "Title", "artist": "Author", "albumartist": "WM/AlbumArtist", "album": "WM/AlbumTitle",
            "year": "WM/Year", "genre": "WM/Genre", "label": "WM/Publisher", "isrc": "WM/ISRC", "comment": "Description",
            "lyrics": "WM/Lyrics"}
ASF_COVER = "WM/Picture"
VIDEO_ID_KEY = "YOUTUBE_ID"


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


def _id3_of(path: Path) -> tuple:
    """(the file, its ID3 tag or None)."""
    if path.suffix.lower() == ".aac":  # mutagen only reads the stream of these
        f = AAC(path)
        try:
            return f, ID3(path)
        except ID3NoHeaderError:
            return f, None
    f = mutagen.File(path)
    return f, (f.tags if f is not None else None)


def _asf_cover(value: bytes) -> bytes:
    """WM/Picture: type, size, then the mime type and description as UTF-16 ending in a double zero, then the image."""
    size = struct.unpack_from("<I", value, 1)[0]
    pos = 5
    for _ in range(2):
        while value[pos:pos + 2] != b"\0\0":
            pos += 2
        pos += 2
    return value[pos:pos + size]


def _asf_value(t, key: str):
    v = t.get(key)
    return v[0].value if v else None


def _asf_drop(t, key: str) -> None:
    if key in t:
        del t[key]


def _ape_cover(value: bytes) -> bytes:
    """The image comes after its file name and a zero."""
    return value.split(b"\0", 1)[1] if b"\0" in value else value


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
    elif ext in ID3_EXT:
        f, t = _id3_of(path)
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
        out["format"] = "aiff" if ext == ".aif" else ext[1:]
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
    elif ext in APE_EXT:
        f = mutagen.File(path)
        if f is None:
            raise ValueError("Not an audio file")
        t = f.tags or {}
        for field, key in APE_TEXT.items():
            if key in t:
                out[field] = str(t[key]) or None
        out["track"], out["track_total"] = _pair(t["Track"]) if "Track" in t else (None, None)
        out["disc"], out["disc_total"] = _pair(t["Disc"]) if "Disc" in t else (None, None)
        out["video_id"] = str(t[VIDEO_ID_KEY]) if VIDEO_ID_KEY in t else None
        out["has_cover"] = APE_COVER in t
        out["format"] = ext[1:]
    elif ext in ASF_EXT:
        f = ASF(path)
        t = f.tags
        for field, key in ASF_TEXT.items():
            if (v := _asf_value(t, key)) not in (None, ""):
                out[field] = str(v)
        out["track"], out["track_total"] = _pair(_asf_value(t, "WM/TrackNumber"))
        out["track_total"] = out["track_total"] or _int(_asf_value(t, "TotalTracks"))
        out["disc"], out["disc_total"] = _pair(_asf_value(t, "WM/PartOfSet"))
        out["video_id"] = _asf_value(t, VIDEO_ID_KEY)
        out["has_cover"] = bool(t.get(ASF_COVER))
        out["format"] = "wma"
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
        if ext in ID3_EXT:
            _f, t = _id3_of(path)
            pics = t.getall("APIC") if t is not None else []
            return pics[0].data if pics else None
        if ext in APE_EXT:
            t = mutagen.File(path).tags or {}
            return _ape_cover(t[APE_COVER].value) if APE_COVER in t else None
        if ext in ASF_EXT:
            v = _asf_value(ASF(path).tags, ASF_COVER)
            return _asf_cover(v) if v else None
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
    elif ext in ID3_EXT:
        owner = None
        if ext in ID3_CHUNK_EXT:
            owner = mutagen.File(path)
            if owner.tags is None:
                owner.add_tags()
            t = owner.tags
        else:
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
        if owner is not None:
            owner.save()
        else:
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
    elif ext in APE_EXT:
        f = mutagen.File(path)
        if f.tags is None:
            f.add_tags()
        t = f.tags
        if clear:
            t.clear()
        for field, value in text.items():
            t.pop(APE_TEXT[field], None)
            if value:
                t[APE_TEXT[field]] = value
        if has_numbers:
            for key, n, total in (("Track", numbers["track"], numbers["track_total"]),
                                  ("Disc", numbers["disc"], numbers["disc_total"])):
                t.pop(key, None)
                if n:
                    t[key] = f"{n}/{total}" if total else str(n)
        if video_id:
            t[VIDEO_ID_KEY] = video_id
        if cover or remove_cover:
            t.pop(APE_COVER, None)
        if cover:
            t[APE_COVER] = APEValue(b"cover.jpg\0" + cover, BINARY)
        f.save()
    elif ext in ASF_EXT:
        f = ASF(path)
        t = f.tags
        if clear:
            t.clear()
        for field, value in text.items():
            _asf_drop(t, ASF_TEXT[field])
            if value:
                t[ASF_TEXT[field]] = value
        if has_numbers:
            for key in ("WM/TrackNumber", "TotalTracks", "WM/PartOfSet"):
                _asf_drop(t, key)
            if numbers["track"]:
                t["WM/TrackNumber"] = str(numbers["track"])
                if numbers["track_total"]:
                    t["TotalTracks"] = str(numbers["track_total"])
            if numbers["disc"]:
                total = numbers["disc_total"]
                t["WM/PartOfSet"] = f"{numbers['disc']}/{total}" if total else str(numbers["disc"])
        if video_id:
            t[VIDEO_ID_KEY] = video_id
        if cover or remove_cover:
            _asf_drop(t, ASF_COVER)
        if cover:
            head = struct.pack("<bI", 3, len(cover)) + "image/jpeg".encode("utf-16-le") + b"\0\0" + b"\0\0"
            t[ASF_COVER] = [ASFByteArrayAttribute(head + cover)]
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
