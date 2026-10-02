"""AppleDouble sidecars (`._name`). Fixtures built here from RFC 1740 and the
macOS extended-attribute block."""

import struct

from acidcat.core.formats import appledouble as admod
from acidcat.core.infra.sniff import sniff_bytes
from acidcat.core.walk import walk_file


def _attr_block(attrs, block_at):
    """The 'ATTR' header and entries, then the values; offsets are absolute."""
    entries = b""
    names = []
    for name, _v in attrs:
        n = name.encode() + b"\0"
        names.append(n)
    head_len = 36
    ent_len = sum((11 + len(n) + 3) & ~3 for n in names)
    data_at = block_at + head_len + ent_len
    values = b""
    for (name, v), n in zip(attrs, names):
        e = struct.pack(">IIHB", data_at + len(values), len(v), 0, len(n)) + n
        entries += e + b"\0" * (((len(e) + 3) & ~3) - len(e))
        values += v
    total = data_at + len(values)
    head = (b"ATTR" + struct.pack(">IIII", 0, total, block_at + head_len + ent_len, len(values))
            + bytes(12) + struct.pack(">HH", 0, len(attrs)))
    return head + entries + values


def sidecar(ftype=b"WAVE", creator=b"PTul", attrs=(
        ("com.apple.quarantine", b"0081;675f97b6;The\\x20Unarchiver;"),
        ("com.apple.metadata:kMDItemWhereFroms",
         b"bplist00\xa1\x01_\x10\x13https://example.com\x08\x0a\x00\x00\x00\x00"
         b"\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00\x00"
         b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x20"),
        ("com.example.text", b"plain words"))):
    n_entries = 2
    finder_at = 26 + 12 * n_entries
    finder = ftype + creator + bytes(24) + b"\0\0"
    finder += _attr_block(list(attrs), finder_at + len(finder))
    fork_at = finder_at + len(finder)
    fork = bytes(256)
    head = (admod.DOUBLE + b"\x00\x02\x00\x00" + b"Mac OS X        "
            + struct.pack(">H", n_entries)
            + struct.pack(">III", 9, finder_at, len(finder))
            + struct.pack(">III", 2, fork_at, len(fork)))
    return head + finder + fork


def _walk(tmp_path, data, name="._kick.wav"):
    p = tmp_path / name
    p.write_bytes(data)
    return walk_file(str(p))


def _f(c):
    return {f["name"]: f["value"] for f in c["fields"]}


def test_sniff_needs_the_magic_and_a_version():
    assert sniff_bytes(sidecar()[:64]) == "appledouble"
    assert sniff_bytes(admod.DOUBLE + b"\x00\x09\x00\x00" + bytes(56)) != "appledouble"


def test_a_sidecar_reads_type_creator_and_attributes(tmp_path):
    label, chunks, warns = _walk(tmp_path, sidecar())
    assert label == "AppleDouble metadata sidecar"
    head = _f(chunks[0])
    assert head["describes"] == "kick.wav" and head["entries"] == 2
    fi = _f(chunks[1])
    assert (fi["file_type"], fi["creator"]) == ("WAVE", "PTul")
    assert fi["attributes"] == 3
    assert fi["com.apple.quarantine"] == "downloaded by The Unarchiver, 2024-12-16 03:00:06 UTC"
    assert fi["com.apple.metadata:kMDItemWhereFroms"] == "https://example.com"
    assert fi["com.example.text"] == "plain words"
    assert not warns and not any(c["warnings"] for c in chunks)


def test_an_entry_past_the_end_is_an_overrun(tmp_path):
    data = sidecar()
    _l, chunks, _w = _walk(tmp_path, data[:-100])
    codes = [getattr(w, "code", None) for c in chunks for w in c["warnings"]]
    assert "size.overrun" in codes


def test_finder_info_without_attributes(tmp_path):
    _l, chunks, _w = _walk(tmp_path, sidecar(attrs=()))
    assert _f(chunks[1])["file_type"] == "WAVE"


def test_quarantine_agent_escapes_are_decoded():
    assert admod.quarantine(b"0081;675f97b6;Google\\x20Chrome;uuid") == ("Google Chrome", 0x675f97b6)
    assert admod.quarantine(b"garbage") is None


def test_an_out_of_range_quarantine_time_is_named_not_raised(tmp_path):
    # the hex time is read from the file unchecked; datetime refuses most of it
    data = sidecar(attrs=(("com.apple.quarantine", b"0081;ffffffffffffff;Safari;"),))
    _l, chunks, warns = _walk(tmp_path, data)
    assert "out of range" in _f(chunks[1])["com.apple.quarantine"]
    assert not warns


def _dag_plist(depth):
    """A binary plist whose array k is [k-1, k-1]: tiny on disk, and its full
    text doubles with every level, because the objects are shared."""
    objs = [b"\x51x"] + [bytes([0xA2, k - 1, k - 1]) for k in range(1, depth + 1)]
    body = b"bplist00"
    offsets = []
    for o in objs:
        offsets.append(len(body))
        body += o
    table = bytes(offsets)
    trailer = (bytes(6) + bytes([1, 1]) + (len(objs)).to_bytes(8, "big")
               + depth.to_bytes(8, "big") + len(body).to_bytes(8, "big"))
    return body + table + trailer


def test_a_shared_reference_plist_renders_in_bounded_time(tmp_path):
    import time
    data = sidecar(attrs=(("com.apple.metadata:kMDItemWhereFroms", _dag_plist(40)),))
    t = time.perf_counter()
    _l, chunks, _w = _walk(tmp_path, data)
    assert time.perf_counter() - t < 2.0
    text = _f(chunks[1])["com.apple.metadata:kMDItemWhereFroms"]
    assert text.endswith("...") and len(text) < 450
