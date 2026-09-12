"""Chunks Apple writes, in whatever container they turn up in.

An `AFAn` typedstream, a Logic `ResU` analysis and a CoreAudio channel layout
are not RIFF chunks or AIFF chunks or CAF chunks. They are Apple structures
that three containers happen to carry, and reading them in three places is how
the same tag ends up decoded in one file and reported as unparsed bytes in the
next. A library of 4,015 AIFFs showed it plainly: acidcat named an `AFAn` in a
WAV and called the same bytes unparsed in an AIFF, 241 times.

So they live here, and the walkers call in.
"""

import struct

from acidcat.core.infra.vocab import (
    AUDIO_CHANNEL_LAYOUT_TAGS as _LAYOUT_TAGS,
    WAV_SPEAKER_POSITIONS as _SPEAKER_POSITIONS,
)
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import _f

# ResU is a few hundred bytes that inflate to a few thousand. The cap is far
# above any real one and exists so a crafted chunk cannot inflate without bound.
_RESU_INFLATE_CAP = 4 * 1024 * 1024
# How far into a typedstream to scan for class names, and how many to list.
# A real AFAn is a few KB.
_APPLE_SCAN_CAP = 64 * 1024
_APPLE_CLASS_CAP = 12

_TYPEDSTREAM = b"streamtyped"


def _parse_chan(b, _ctx):
    """AudioChannelLayout: a tag, a bitmap, and optional per-channel entries.

    The same structure in a CAF `chan` chunk and an AIFF-C `CHAN` chunk, which
    is why this is the reader for both. Logic writes the AIFF one as 32 bytes:
    the 12-byte header followed by a single all-zero channel description that
    the header says is not there, because the C struct declares
    `AudioChannelDescription mChannelDescriptions[1]` and the writer emits the
    struct rather than the fields.
    """
    fields, warns = [], []
    if len(b) < 12:
        return "truncated", fields, [
            f"chan payload is {len(b)} bytes, the header alone is 12"]
    tag, bitmap, n = struct.unpack_from(">III", b, 0)
    layout, count = tag >> 16, tag & 0xFFFF
    name = _LAYOUT_TAGS.get(layout)
    note = (f"layout {layout}" if name is None else name)
    if layout >= 100:
        note += f", {count} channel(s)"
    fields.append(_f(0x00, 4, "layout_tag", f"0x{tag:08x}", note,
                     enc=">I", raw=tag))
    fields.append(_f(0x04, 4, "channel_bitmap", f"0x{bitmap:08x}",
                     _channel_bitmap_names(bitmap)))
    fields.append(_f(0x08, 4, "descriptions", n))
    need = 12 + n * 20
    if need > len(b):
        warns.append(
            f"declares {n} channel descriptions ({need} bytes), payload is "
            f"{len(b)}")
    return (f"{note}, {n} description(s)"), fields, warns


def _channel_bitmap_names(bitmap):
    """The bitmap is the same speaker order the WAVEFORMATEXTENSIBLE channel
    mask uses, which is why it reuses that table rather than a second copy."""
    if not bitmap:
        return ""
    names = [nm for i, nm in enumerate(_SPEAKER_POSITIONS) if bitmap & (1 << i)]
    return ", ".join(names) if names else "no named position"


def _parse_resu(b, ctx):
    """Logic Pro's analysis result: zlib-compressed JSON, and it holds a tempo.

    The payload opens with a zlib header and inflates to a JSON document that
    records what Logic's Flex analysis decided about the file -- its tempo, its
    time signature, and the beat positions it found, each with a confidence.

    Worth reading rather than skipping: it is a DAW's own opinion about the
    material, written into the file, and on a loop library it is the tempo
    someone actually worked to.
    """
    fields, warns = [], []
    if len(b) < 2 or b[0] != 0x78:
        return (f"unrecognized, {len(b):,} bytes"), fields, [
            "ResU does not open with a zlib header"]
    import json
    import zlib
    try:
        raw = zlib.decompressobj().decompress(b, _RESU_INFLATE_CAP)
    except zlib.error as e:
        return "undecodable", fields, [f"ResU did not inflate ({e})"]
    if len(raw) >= _RESU_INFLATE_CAP:
        warns.append(coverage(f"ResU inflated to the {_RESU_INFLATE_CAP >> 10} KB "
                              f"cap; the document may continue"))
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        return "undecodable", fields, [f"ResU inflated but did not parse as JSON "
                                       f"({e.__class__.__name__})"]
    if not isinstance(doc, dict):
        return "undecodable", fields, ["ResU JSON is not an object"]

    fields.append(_f(None, 0, "compressed", f"{len(b):,} bytes",
                     f"inflates to {len(raw):,}"))
    ctxd = doc.get("rec_ctx") if isinstance(doc.get("rec_ctx"), dict) else doc
    bits = []

    tempo = ctxd.get("Tempo")
    if isinstance(tempo, list) and tempo and isinstance(tempo[0], dict):
        value = tempo[0].get("tempo")
        if value is not None:
            fields.append(_f(None, 0, "tempo", f"{value:g} BPM",
                             "as Logic's analysis recorded it"))
            bits.append(f"{value:g} BPM")
    sig = ctxd.get("time_signatures")
    if isinstance(sig, list) and sig and isinstance(sig[0], dict):
        value = sig[0].get("signature")
        if value:
            fields.append(_f(None, 0, "time_signature", str(value)[:16]))
            bits.append(str(value))
    dur = ctxd.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        fields.append(_f(None, 0, "analysed_duration", f"{dur:.3f} s"))
    beats = ctxd.get("beats")
    if isinstance(beats, list) and beats:
        onsets = sum(1 for x in beats if isinstance(x, dict) and x.get("onset"))
        fields.append(_f(None, 0, "beats", f"{len(beats):,}",
                         f"{onsets:,} marked as onsets" if onsets else ""))
        bits.append(f"{len(beats)} beat marker(s)")
    for key, label in (("UserEdited", "user edited"),
                       ("MusicDetectionWasPerformed", "music detection run"),
                       ("ResultWasCreatedFromLogicTempoMap", "from Logic's tempo map")):
        if ctxd.get(key):
            fields.append(_f(None, 0, label.replace(" ", "_"), "yes"))
    return ("Logic analysis: " + ", ".join(bits)) if bits else \
           f"Logic analysis, {len(raw):,} bytes of JSON", fields, warns


def _parse_apple_meta(b, ctx):
    """AFAn / AFmd: Apple metadata, as a NeXTSTEP typedstream archive.

    NAMED, not decoded. The payload is a real serialised object graph -- an
    NSMutableDictionary -- and reading it properly means implementing
    typedstream, which is a format of its own rather than a field or two. What
    is honest here is to say what the bytes ARE, show the class names the
    archive references, and leave the values to a reader that implements the
    format.
    """
    fields, warns = [], []
    if _TYPEDSTREAM not in b[:32]:
        return (f"unrecognized, {len(b):,} bytes"), fields, [
            "AFAn/AFmd does not open with an Apple typedstream header"]
    fields.append(_f(None, 0, "container", "Apple typedstream (NSArchiver)"))
    fields.append(_f(None, 0, "bytes", f"{len(b):,}"))
    # the class names are length-prefixed ASCII in the clear; listing them says
    # what the archive holds without claiming to have decoded its values
    classes, i = [], 0
    window = b[:_APPLE_SCAN_CAP]
    while i < len(window) - 2:
        n = window[i]
        if 3 <= n <= 60 and i + 1 + n <= len(window):
            chunk = window[i + 1:i + 1 + n]
            if chunk[:2] == b"NS" and all(32 <= c < 127 for c in chunk):
                name = chunk.decode("ascii")
                if name not in classes:
                    classes.append(name)
                i += 1 + n
                continue
        i += 1
    for name in classes[:_APPLE_CLASS_CAP]:
        fields.append(_f(None, 0, "class", name))
    if len(classes) > _APPLE_CLASS_CAP:
        warns.append(coverage(f"listing the first {_APPLE_CLASS_CAP} of "
                     f"{len(classes)} archived class names"))
    summary = f"Apple typedstream, {len(b):,} bytes"
    if classes:
        summary += " -- " + ", ".join(classes[:3])
    return summary, fields, warns


# Apple Loops transient table geometry, measured on 3,200 files and holding on
# every one of them: a 76-byte header whose last four bytes are the record
# count, then fixed 24-byte records.
_TRNS_HEADER = 76
_TRNS_RECORD = 24
_TRNS_COUNT_OFF = 0x48
# How many transient positions to list. A four-bar 16th-note loop is 64; the
# largest in that library is 129.
_TRNS_LIST_CAP = 64
# Apple Loops category labels sit on a 50-byte grid of NUL-padded ASCII.
_CATE_SLOT = 50
_CATE_LABEL_CAP = 24


def _parse_trns(b, ctx):
    """Apple Loops transient table: where the slice points are.

    The header is 76 bytes -- a version, two constants, a timestamp, and at
    0x48 the record count -- then one 24-byte record per transient holding a
    flag and a position in sample frames.

    The positions are frames rather than bytes, and the evidence is external to
    acidcat: on 3,194 files carrying a tempo in their own filename, the median
    gap between transients is a sixteenth note at that tempo. 2,550 land on it
    exactly and the rest inside one percent.
    """
    fields, warns = [], []
    if len(b) < _TRNS_HEADER:
        return "truncated", fields, [
            f"trns payload is {len(b)} bytes, the header alone is "
            f"{_TRNS_HEADER}"]
    version, a, c = struct.unpack_from(">HHH", b, 0)
    stamp = struct.unpack_from(">I", b, 8)[0]
    count = struct.unpack_from(">I", b, _TRNS_COUNT_OFF)[0]
    fields.append(_f(0x00, 2, "version", version))
    fields.append(_f(0x02, 4, "constants", f"{a}, {c}",
                     "the same in every file measured; meaning unknown"))
    fields.append(_f(0x08, 4, "timestamp", f"0x{stamp:08x}",
                     _unix_date(stamp), enc=">I", raw=stamp))
    fields.append(_f(_TRNS_COUNT_OFF, 4, "transients", count))

    have = (len(b) - _TRNS_HEADER) // _TRNS_RECORD
    if count > have:
        warns.append(f"declares {count:,} transients, the payload holds "
                     f"{have:,}")
        count = have

    rate = ctx.get("sample_rate") or 0
    frames = ctx.get("frames") or 0
    # every record is READ -- the listing is what the cap bounds, not the check
    # that the table stays inside its own audio
    positions, beyond = [], 0
    for i in range(count):
        off = _TRNS_HEADER + i * _TRNS_RECORD + 4
        pos = struct.unpack_from(">I", b, off)[0]
        if frames and pos > frames:
            beyond += 1
        if i < _TRNS_LIST_CAP:
            positions.append(pos)
            note = f"{pos / rate:.3f} s" if rate else ""
            fields.append(_f(off, 4, f"transient[{i}]", f"frame {pos:,}", note))
    if beyond:
        warns.append(f"{beyond} transient(s) fall past the {frames:,} frames "
                     f"COMM declares")
    if count > _TRNS_LIST_CAP:
        warns.append(coverage(f"listing the first {_TRNS_LIST_CAP} of "
                              f"{count:,} transients"))

    summary = f"{count:,} transient(s)"
    if rate and len(positions) > 2:
        gaps = sorted(y - x for x, y in zip(positions, positions[1:]) if y > x)
        if gaps:
            med = gaps[len(gaps) // 2]
            summary += f", every {med / rate:.3f} s"
    return summary, fields, warns


def _unix_date(v):
    """The field reads as a Unix timestamp and nothing in the file says so, so
    the reading is shown rather than asserted. Across 447 distinct values in a
    real library it spans 2012 to 2017, which is when those files were made;
    the same numbers read against the Mac epoch land in the 1940s."""
    import datetime
    if not 0 < v < (1 << 31):
        return ""
    try:
        d = datetime.datetime.fromtimestamp(v, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return f"reads as Unix time: {d.date().isoformat()}"


def _parse_cate(b, _ctx):
    """Apple Loops category labels: what the loop says it is.

    A count, then NUL-padded ASCII on a 50-byte grid. Only the labels are
    decoded. The chunk is 222 bytes in almost every file and 572 in one, and
    the second shape puts a gap where a flat array of slots would not -- so the
    record structure around the labels is not claimed, only the labels.
    """
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["cate payload is under 4 bytes"]
    fields.append(_f(0x00, 4, "count", struct.unpack_from(">I", b, 0)[0]))
    labels = []
    for off in range(4, len(b) - 1, _CATE_SLOT):
        slot = b[off:off + _CATE_SLOT].split(b"\x00")[0]
        if len(slot) >= 2 and all(32 <= ch < 127 for ch in slot):
            labels.append((off, slot.decode("ascii")))
    for off, text in labels[:_CATE_LABEL_CAP]:
        fields.append(_f(off, _CATE_SLOT, "label", text))
    if len(labels) > _CATE_LABEL_CAP:
        warns.append(coverage(f"listing the first {_CATE_LABEL_CAP} of "
                              f"{len(labels)} category labels"))
    if not labels:
        return f"apple loops category data, {len(b):,} bytes", fields, warns
    return " / ".join(t for _o, t in labels[:4]), fields, warns
