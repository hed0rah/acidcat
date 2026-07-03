"""MP4 box-walk: a large box read only in part is beyond_cap, not truncated."""
import struct

from acidcat.core import mp4


def _boxes(data, **kw):
    return list(mp4.iter_boxes(data, **kw))


def test_large_mdat_beyond_cap_not_truncated():
    ftyp = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    mdat = struct.pack(">I", 1000) + b"mdat" + b"\x00" * 992
    full = ftyp + mdat
    # only the first 100 bytes were read, but file_size is the true length
    boxes = _boxes(full[:100], file_size=len(full))
    md = [b for b in boxes if b["type"] == b"mdat"]
    assert md, "mdat should still be enumerated from its header"
    assert md[0]["truncated"] is False
    assert md[0]["beyond_cap"] is True


def test_mdat_overrunning_the_file_is_truncated():
    ftyp = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    # mdat claims 9999 bytes but the file only has room for far less
    mdat = struct.pack(">I", 9999) + b"mdat" + b"\x00" * 20
    full = ftyp + mdat
    boxes = _boxes(full, file_size=len(full))
    md = [b for b in boxes if b["type"] == b"mdat"]
    assert md and md[0]["truncated"] is True
