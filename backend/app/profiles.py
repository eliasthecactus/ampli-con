"""Download profiles: format, where the files go and how they're named (like the quality profiles of the *arrs)."""
import re
import unicodedata

from . import db, locations

FORMATS = {
    "m4a": "M4A (AAC, as YouTube has it, no re-encoding)",
    "opus": "Opus (as YouTube has it, no re-encoding)",
    "mp3": "MP3 (re-encoded)",
}
MP3_QUALITIES = ("320", "256", "192", "V0", "V2")
LAYOUTS = ("album", "playlist")
ON_EXISTS = ("skip", "replace")

DEFAULT_ALBUM = "{artist} - {title}"
DEFAULT_SINGLE = "{artist} - {title}"
DEFAULT_PLAYLIST = "Playlists/{playlist}/{artist} - {title}"
FIELDS = ("artist", "albumartist", "album", "title", "track", "disc", "year", "genre", "label", "playlist", "position",
          "video_id")

_FIELD = re.compile(r"\{(\w+)(?::(\d+)d?)?\}")
_OPTIONAL = re.compile(r"\[([^\[\]]*)\]")


def clean(part: str) -> str:
    """Safe for every file system: no slashes, no reserved characters, no trailing dots, sane length."""
    part = unicodedata.normalize("NFC", part)
    part = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part).strip()
    part = re.sub(r"\s+", " ", part).rstrip(". ")
    if part in ("", ".", ".."):
        part = "_"
    return part[:120]


def _fill(text: str, values: dict, keep_empty: bool) -> str | None:
    missing = False

    def rep(m: re.Match) -> str:
        nonlocal missing
        name, width = m.group(1), m.group(2)
        if name not in FIELDS:
            raise ValueError(f"Unknown field {{{name}}}. Use {', '.join('{' + f + '}' for f in FIELDS)}")
        v = values.get(name)
        if v in (None, "", 0) and name in ("track", "disc", "position", "year"):
            v = None
        if v is None or v == "":
            missing = True
            return ""
        if width and str(v).isdigit():
            return str(v).zfill(int(width))
        return clean(str(v)).replace("/", "_")

    out = _FIELD.sub(rep, text)
    if missing and not keep_empty:
        return None
    return out


def render(template: str, values: dict) -> str:
    """'{albumartist}/{album}[ ({year})]/{track:02} {title}' → a relative path without extension.
    [optional parts] disappear when a field inside is empty; every path part is cleaned."""
    template = template.strip().strip("/")
    if not template:
        raise ValueError("The template is empty")
    if ".." in template.split("/"):
        raise ValueError("The template can't go up a folder")
    disc_total = values.get("disc_total") or 1
    values = {**values}
    if values.get("year"):
        values["year"] = str(values["year"])[:4]  # the tag can hold a full date
    if int(disc_total) <= 1:
        values["disc"] = None  # "[{disc}-]" only shows on multi-disc albums
    text = _OPTIONAL.sub(lambda m: _fill(m.group(1), values, False) or "", template)
    text = _fill(text, values, True)
    parts = [clean(p) for p in text.split("/") if p.strip()]
    if not parts:
        raise ValueError("The template gives an empty file name")
    return "/".join(parts)


SAMPLE = {"artist": "Daft Punk", "albumartist": "Daft Punk", "album": "Discovery", "title": "One More Time",
          "track": 1, "disc": 1, "disc_total": 1, "year": "2001", "genre": "Electro", "label": "Daft Life",
          "playlist": "Road Trip", "position": 7, "video_id": "FGBhQbmPwH8"}


def preview(p: dict) -> dict:
    ext = {"m4a": "m4a", "opus": "opus", "mp3": "mp3"}.get(p.get("format") or "m4a", "m4a")
    out = {}
    for key in ("album_template", "single_template", "playlist_template"):
        try:
            out[key] = render(p.get(key) or "", SAMPLE) + "." + ext
        except ValueError as e:
            out[key] = f"⚠ {e}"
    return out


def public(r: dict) -> dict:
    loc = locations.get(r.get("location_id"))
    out = {**r, "location_name": loc["name"] if loc else None, "location_path": loc["path"] if loc else None}
    for k in ("write_m3u", "embed_cover", "embed_lyrics", "sponsorblock", "is_default"):
        out[k] = bool(r[k])
    out["preview"] = preview(r)
    return out


def list_all() -> list[dict]:
    return [public(r) for r in db.rows("SELECT * FROM profiles ORDER BY is_default DESC, name COLLATE NOCASE")]


def get(profile_id: int | None) -> dict | None:
    if profile_id:
        r = db.row("SELECT * FROM profiles WHERE id = ?", [profile_id])
        if r:
            return r
    return db.row("SELECT * FROM profiles ORDER BY is_default DESC, id LIMIT 1")


def save(data: dict, profile_id: int | None = None) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Give the profile a name")
    fmt = data.get("format") or "m4a"
    if fmt not in FORMATS:
        raise ValueError("Unknown format")
    quality = str(data.get("quality") or "320")
    if fmt == "mp3" and quality not in MP3_QUALITIES:
        raise ValueError("Unknown MP3 quality")
    loc = locations.get(data.get("location_id"))
    if not loc:
        raise ValueError("Pick a location")
    layout = data.get("playlist_layout") or "album"
    if layout not in LAYOUTS:
        raise ValueError("Unknown playlist layout")
    on_exists = data.get("on_exists") or "skip"
    if on_exists not in ON_EXISTS:
        raise ValueError("Unknown choice for existing files")
    fields = {
        "name": name, "location_id": loc["id"], "format": fmt, "quality": quality,
        "album_template": (data.get("album_template") or DEFAULT_ALBUM).strip(),
        "single_template": (data.get("single_template") or DEFAULT_SINGLE).strip(),
        "playlist_template": (data.get("playlist_template") or DEFAULT_PLAYLIST).strip(),
        "playlist_layout": layout, "on_exists": on_exists,
        "write_m3u": int(bool(data.get("write_m3u", True))), "embed_cover": int(bool(data.get("embed_cover", True))),
        "embed_lyrics": int(bool(data.get("embed_lyrics", False))),
        "sponsorblock": int(bool(data.get("sponsorblock", True))),
    }
    for key in ("album_template", "single_template", "playlist_template"):
        render(fields[key], SAMPLE)  # raises ValueError with a readable message
    make_default = bool(data.get("is_default"))
    with db.tx() as c:
        if profile_id:
            cols = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE profiles SET {cols} WHERE id = ?", [*fields.values(), profile_id])
        else:
            first = c.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
            make_default = make_default or first
            cols = ", ".join([*fields, "created_at"])
            marks = ", ".join("?" for _ in range(len(fields) + 1))
            profile_id = c.execute(f"INSERT INTO profiles ({cols}) VALUES ({marks})",
                                   [*fields.values(), db.now()]).lastrowid
        if make_default:
            c.execute("UPDATE profiles SET is_default = (id = ?)", [profile_id])
    return public(get(profile_id))


def delete(profile_id: int) -> None:
    if db.scalar("SELECT COUNT(*) FROM profiles") <= 1:
        raise ValueError("Keep at least one profile")
    was_default = db.scalar("SELECT is_default FROM profiles WHERE id = ?", [profile_id])
    db.execute("DELETE FROM profiles WHERE id = ?", [profile_id])
    if was_default:
        db.execute("UPDATE profiles SET is_default = 1 WHERE id = (SELECT id FROM profiles ORDER BY id LIMIT 1)")


def ensure_default() -> None:
    if db.scalar("SELECT COUNT(*) FROM profiles"):
        return
    loc = db.row("SELECT id FROM locations ORDER BY id LIMIT 1")
    if not loc:
        return
    save({"name": "Standard", "location_id": loc["id"], "format": "m4a", "is_default": True})
