"""Writes tags and the cover into downloaded files (M4A, MP3, Opus)."""
import base64
import io
import logging
from pathlib import Path

from mutagen.flac import Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from PIL import Image

log = logging.getLogger("amplicon.tags")


def square_jpeg(data: bytes, size: int = 1000) -> bytes:
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


def write(path: Path, meta: dict, cover: bytes | None = None, lyrics: str | None = None) -> None:
    """meta: title, artist, albumartist, album, track, track_total, disc, disc_total, year, video_id"""
    ext = path.suffix.lower()
    url = f"https://music.youtube.com/watch?v={meta['video_id']}"
    if ext in (".m4a", ".mp4"):
        f = MP4(path)
        t = f.tags if f.tags is not None else f.add_tags() or f.tags
        t.clear()
        t["\xa9nam"] = meta["title"]
        t["\xa9ART"] = meta.get("artist") or ""
        if meta.get("albumartist"):
            t["aART"] = meta["albumartist"]
        if meta.get("album"):
            t["\xa9alb"] = meta["album"]
        if meta.get("year"):
            t["\xa9day"] = str(meta["year"])
        if meta.get("track"):
            t["trkn"] = [(int(meta["track"]), int(meta.get("track_total") or 0))]
        if meta.get("disc"):
            t["disk"] = [(int(meta["disc"]), int(meta.get("disc_total") or 0))]
        t["\xa9cmt"] = url
        t["----:com.apple.iTunes:YOUTUBE_ID"] = MP4FreeForm(meta["video_id"].encode())
        if lyrics:
            t["\xa9lyr"] = lyrics
        if cover:
            t["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
    elif ext == ".mp3":
        try:
            t = ID3(path)
            t.delete(path)
        except ID3NoHeaderError:
            pass
        t = ID3()
        t.add(TIT2(encoding=3, text=meta["title"]))
        t.add(TPE1(encoding=3, text=meta.get("artist") or ""))
        if meta.get("albumartist"):
            t.add(TPE2(encoding=3, text=meta["albumartist"]))
        if meta.get("album"):
            t.add(TALB(encoding=3, text=meta["album"]))
        if meta.get("year"):
            t.add(TDRC(encoding=3, text=str(meta["year"])))
        if meta.get("track"):
            t.add(TRCK(encoding=3, text=f"{meta['track']}/{meta['track_total']}" if meta.get("track_total") else str(meta["track"])))
        if meta.get("disc"):
            t.add(TPOS(encoding=3, text=f"{meta['disc']}/{meta['disc_total']}" if meta.get("disc_total") else str(meta["disc"])))
        if meta.get("genre"):
            t.add(TCON(encoding=3, text=meta["genre"]))
        t.add(COMM(encoding=3, lang="eng", desc="", text=url))
        t.add(TXXX(encoding=3, desc="YOUTUBE_ID", text=meta["video_id"]))
        if lyrics:
            t.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        if cover:
            t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        t.save(path, v2_version=4)
    elif ext in (".opus", ".ogg"):
        f = OggOpus(path)
        f.delete()
        f["title"] = meta["title"]
        f["artist"] = meta.get("artist") or ""
        if meta.get("albumartist"):
            f["albumartist"] = meta["albumartist"]
        if meta.get("album"):
            f["album"] = meta["album"]
        if meta.get("year"):
            f["date"] = str(meta["year"])
        if meta.get("track"):
            f["tracknumber"] = str(meta["track"])
            if meta.get("track_total"):
                f["tracktotal"] = str(meta["track_total"])
        if meta.get("disc"):
            f["discnumber"] = str(meta["disc"])
        f["comment"] = url
        f["youtube_id"] = meta["video_id"]
        if lyrics:
            f["lyrics"] = lyrics
        if cover:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, "image/jpeg", "Cover", cover
            f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        f.save()
    else:
        log.warning("no tagging for %s", ext)


def video_id_of(path: Path) -> str | None:
    """The YouTube id Amplicon wrote into a file, so existing downloads are recognised."""
    try:
        ext = path.suffix.lower()
        if ext in (".m4a", ".mp4"):
            v = (MP4(path).tags or {}).get("----:com.apple.iTunes:YOUTUBE_ID")
            return bytes(v[0]).decode() if v else None
        if ext == ".mp3":
            v = ID3(path).getall("TXXX:YOUTUBE_ID")
            return str(v[0].text[0]) if v else None
        if ext in (".opus", ".ogg"):
            v = OggOpus(path).get("youtube_id")
            return v[0] if v else None
    except Exception:
        return None
    return None
