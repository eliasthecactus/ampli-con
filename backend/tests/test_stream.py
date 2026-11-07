import struct

from app import stream


def box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def mdhd(version: int, duration: int) -> bytes:
    if version == 0:
        body = struct.pack(">B3xIIII", 0, 1, 2, 44100, duration)
    else:
        body = struct.pack(">B3xQQIQ", 1, 1, 2, 44100, duration)
    return box(b"mdhd", body + b"\x55\xc4\x00\x00")


def mp4(version: int = 0, duration: int = 10_967_040, fragmented: bool = True) -> bytes:
    trak = box(b"trak", box(b"tkhd", bytes(84)) + box(b"mdia", mdhd(version, duration) + box(b"hdlr", bytes(25))))
    moov = box(b"moov", box(b"mvhd", bytes(100)) + (box(b"mvex", box(b"trex", bytes(24))) if fragmented else b"") + trak)
    return box(b"ftyp", b"dash\0\0\0\0iso6mp41") + moov + box(b"sidx", bytes(24)) + box(b"moof", bytes(16)) + box(b"mdat", bytes(64))


def duration_of(buf: bytes, version: int) -> int:
    at = buf.index(b"mdhd") + 4
    return struct.unpack(">I", buf[at + 16:at + 20])[0] if version == 0 else struct.unpack(">Q", buf[at + 24:at + 32])[0]


def test_fragmented_mdhd_duration_is_zeroed():
    for version in (0, 1):
        raw = mp4(version)
        patches = stream.header_patches(raw)
        assert len(patches) == 1
        out = stream.apply_patches(raw, 0, patches)
        assert len(out) == len(raw)
        assert duration_of(out, version) == 0
        # everything else stays as it was, the timescale too
        at, n = patches[0]
        assert out[:at] == raw[:at] and out[at + n:] == raw[at + n:]
        assert stream.header_patches(out) == []


def test_patch_across_chunk_boundaries():
    raw = mp4()
    patches = stream.header_patches(raw)
    whole = stream.apply_patches(raw, 0, patches)
    for size in (1, 3, 7, 50):
        assert b"".join(stream.apply_patches(raw[i:i + size], i, patches) for i in range(0, len(raw), size)) == whole
    # a range that starts after the header passes through unchanged
    tail = raw[len(raw) - 40:]
    assert stream.apply_patches(tail, len(raw) - 40, patches) is tail


def test_regular_mp4_is_left_alone():
    assert stream.header_patches(mp4(fragmented=False)) == []
    assert stream.header_patches(b"not an mp4 at all") == []
    assert stream.header_patches(mp4()[:60]) == []  # header cut short


def test_cached_file_is_fixed_once(tmp_path):
    p = tmp_path / "abcdefghijk.m4a"
    p.write_bytes(mp4())
    stream._fix_file(p)
    assert duration_of(p.read_bytes(), 0) == 0
    assert len(p.read_bytes()) == len(mp4())
