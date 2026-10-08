"""Ableton Live container primitives.

Two unrelated shapes ship under the Live umbrella:

* ``.asd`` -- the binary per-sample analysis sidecar. Reverse-engineered here
  against 8,196 real specimens; see ``parse_asd_header``.
* ``.als`` / ``.alc`` / ``.adg`` / ``.adv`` / ``.agr`` -- gzipped XML, with the
  document type given by the root element's first child. Live has more of
  these than are mapped here (``.ams`` Operator meta sound, ``.abl`` and
  ``.ablbundle`` Note, ``.ask`` theme); they share the shape, so an unmapped
  one is still recognised as Ableton XML and its root child is reported rather
  than guessed at. ``.alp`` is gzip but an archive, not a document.

``.amxd`` (Max for Live) is a third, chunked ``ampf`` container.

Nothing here reads a whole file into memory unbounded: the XML side is a
gzip stream that expands ~50x in the wild, so every decompression is capped.
"""

import re
import struct
import zlib

from acidcat.core.infra.source import gzip_open, input_name, open_input

# byte 0 is always 0x06; byte 1 is the TIFF-style byte-order mark, 'I' for
# Intel (little-endian) and 'M' for Motorola (big-endian). Measured over 8,196
# specimens: 7,748 little, 319 big, and the big ones parse only big-endian.
ASD_MAGIC_LE = b"\x06\x49"
ASD_MAGIC_BE = b"\x06\x4d"
ASD_MAGICS = (ASD_MAGIC_LE, ASD_MAGIC_BE)

# an AppleDouble resource stub named "._foo.wav.asd" is not a .asd at all. 129
# of the 8,196 files carrying the extension were these. Recognising them stops
# a corpus sweep reporting them as corrupt Ableton files.
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"

# the frame-position table steps by at most 30 ms of audio, so the largest step
# pins the sample rate. Ordered so the first grain >= the observed step wins.
_STANDARD_RATES = (8000, 11025, 16000, 22050, 32000, 44100, 48000,
                   88200, 96000, 176400, 192000)
ASD_GRAIN_SECONDS = 0.03

# a Live Set decompresses to several MB of XML; a hostile or merely huge one
# should not be able to force an unbounded allocation.
XML_DECOMPRESS_CAP = 64 * 1024 * 1024

# field names are stored as u32 character-count + UTF-16LE. 69 distinct names
# were recovered across the specimen set.
_UTF16_RUN = re.compile(rb"(?:[ -~]\x00){3,}")

# fields that mark what the analysis actually contains, for the summary line
NOTABLE_FIELDS = (
    "WarpMarkers", "IsWarped", "WarpMode", "LoopStart", "LoopEnd",
    "HiddenLoopStart", "SampleOffset", "Numerator", "Denominator",
    "OriginalFileSize", "InitialBPM", "Bpms", "UnbiasedTempoEstimate",
    "OnSets", "UserOnsets", "PitchMarks", "Tonalities", "BeatTrackState",
    "OverViewLevels", "AufTaktData", "SamplesPerBinLog2",
)


class AbletonError(Exception):
    """Malformed enough that no structure can be reported."""


def is_appledouble(head):
    return head[:4] == APPLEDOUBLE_MAGIC


def asd_byte_order(head):
    """'<' or '>' for a .asd header, else None."""
    if head[:2] == ASD_MAGIC_LE:
        return "<"
    if head[:2] == ASD_MAGIC_BE:
        return ">"
    return None


def looks_like_asd(head, size=None):
    """Cheap structural check used by the sniffer.

    Two magic bytes alone would be far too weak, so the reserved u32 at offset
    6 (zero in all 8,067 real specimens) and a plausible table length carry
    most of the confidence.
    """
    order = asd_byte_order(head)
    if order is None or len(head) < 10:
        return False
    if struct.unpack_from(order + "I", head, 6)[0] != 0:
        return False
    count = struct.unpack_from(order + "I", head, 2)[0]
    if not 1 <= count < 10_000_000:
        return False
    return size is None or 10 + 4 * (count - 1) <= size


def infer_sample_rate(max_step):
    """(rate, exact) inferred from the largest step in the frame table.

    Steps are capped at 30 ms of audio, so the true rate is the smallest
    standard rate whose grain is not smaller than the largest observed step.
    `exact` is True only when the cap was actually reached -- a short or very
    steady file may never hit it, and then the rate is a lower bound rather
    than a reading. Returns (None, False) when no standard rate is big enough.
    """
    if max_step <= 0:
        return None, False
    for rate in _STANDARD_RATES:
        grain = round(rate * ASD_GRAIN_SECONDS)
        if grain >= max_step:
            return rate, grain == max_step
    return None, False


def parse_asd_header(raw):
    """Decode the fixed head of a .asd.

    Layout, confirmed against the source audio for 22 specimens and
    structurally against 8,067:

        0   u8[2]  magic 06 'I' | 06 'M' (byte order)
        2   u32    count N
        6   u32    reserved, zero
        10  u32[N-1] frame positions, strictly increasing, last == total
                     frames of the source audio, step <= 30 ms of audio
    """
    order = asd_byte_order(raw)
    if order is None:
        raise AbletonError("not an Ableton .asd (bad magic)")
    if len(raw) < 10:
        raise AbletonError("truncated before the .asd header")
    count = struct.unpack_from(order + "I", raw, 2)[0]
    reserved = struct.unpack_from(order + "I", raw, 6)[0]
    n = max(count - 1, 0)
    avail = (len(raw) - 10) // 4
    truncated = n > avail
    n = min(n, avail)
    frames = list(struct.unpack_from(f"{order}{n}I", raw, 10)) if n else []

    steps = [b - a for a, b in zip(frames, frames[1:])]
    monotonic = all(s > 0 for s in steps)
    max_step = max(steps) if steps else 0
    rate, exact = infer_sample_rate(max_step)
    total = frames[-1] if frames else 0
    return {
        "byte_order": "little" if order == "<" else "big",
        "order": order,
        "count": count,
        "reserved": reserved,
        "frames": frames,
        "table_end": 10 + 4 * n,
        "total_frames": total,
        "max_step": max_step,
        "sample_rate": rate,
        "rate_exact": exact,
        "duration": (total / rate) if (rate and total) else None,
        "monotonic": monotonic,
        "truncated": truncated,
    }


# the field-name text follows the file's declared byte order too, so a
# big-endian .asd stores UTF-16BE with a big-endian count. Parsing those files
# as little-endian finds zero of their 64 field names -- and the walker then
# reports "no analysis fields", which reads as a fact about the file rather
# than a fault in the reader.
_UTF16_RUN_BE = re.compile(rb"(?:\x00[ -~]){3,}")


def field_names(raw, start=0, order="<"):
    """[(offset, name)] for every length-prefixed UTF-16 field name.

    These are the serialised object-tree field names. They are interleaved with
    their data rather than gathered in a header, so they double as a map of
    what the file records. The length prefix is what separates a real name from
    an accidental run of ASCII-range UTF-16.
    """
    big = order == ">"
    pattern = _UTF16_RUN_BE if big else _UTF16_RUN
    codec = "utf-16be" if big else "utf-16le"
    out = []
    for m in pattern.finditer(raw, start):
        off = m.start()
        if off < 4:
            continue
        try:
            declared = struct.unpack_from(order + "I", raw, off - 4)[0]
        except struct.error:
            continue
        text = m.group().decode(codec)
        if declared == len(text):
            out.append((off - 4, text))
    return out


def class_names(raw, start=0):
    """[(offset, name)] for the length-prefixed ASCII class names."""
    out = []
    for m in re.finditer(rb"[A-Za-z][A-Za-z0-9_<>&]{3,40}", raw[start:]):
        off = start + m.start()
        if off >= 1 and raw[off - 1] == len(m.group()):
            out.append((off - 1, m.group().decode("ascii")))
    return out


# ── the serialised object tree ────────────────────────────────────────────
#
# The object section opens with a self-describing type dictionary:
#
#     CLASS = u8 len + ASCII name + u32
#     FIELD = u32 char_count + UTF-16LE name + u8 type_tag
#
# The tag meanings were pinned by harvesting (field name -> tag) over 1,200
# specimens: `IsSet` and `IsVolatile` are always 0x10, `Version` and
# `ChannelCount` always 0x11, and `Value` takes 0x10/0x11/0x12 according to
# whether its parent is RemoteableBool / RemoteableInt / RemoteableDouble --
# which is the confirmation, since that one field's type varies by wrapper.
# Every 0x40 field has a plural name (Bpms, Positions, Tonalities, OnSets).
#
# The high nibble is the family: 0x1x scalar, 0x3x blob, 0x4x list.
TYPE_TAGS = {
    0x10: "bool",
    0x11: "int32",
    0x12: "double",
    0x14: "scalar",        # family known, exact width unconfirmed
    0x15: "scalar",
    0x17: "double",
    0x31: "blob",
    0x32: "blob",
    0x35: "list",
    0x40: "list",
}


def _is_identifier(s):
    """Field names are C++ member names, so they are plain identifiers.

    This matters because the object section is scanned in full: the overview
    pyramid is high-entropy bytes, and by chance some of it decodes as a
    length-prefixed UTF-16 run. Sample titles embedded nearby do too -- a real
    specimen yielded "MOONBOYS ASS SNARE HIGH" as a "field". Requiring an
    identifier costs nothing and keeps that noise out of the declared count.
    """
    return bool(s) and (s[0].isalpha() or s[0] == "_") and \
        all(c.isalnum() or c == "_" for c in s)


def type_dictionary(raw, start, end, order="<"):
    """Walk the type dictionary. Yields ('class', name, u32) and
    ('field', name, tag) in file order.

    Scans to `end`, which callers should set to the end of the file rather
    than a fixed window. A window looked safe -- the declarations usually open
    the object section -- but in 4.8% of a 2,456-file sample the overview
    pyramid comes FIRST and the dictionary sits 10-25 KB deep, so a window
    dropped their entire object tree and the walker then reported "no analysis
    fields", blaming the file for the reader's bound.

    Class names are ASCII and so byte-order agnostic; field names are UTF-16 in
    the file's declared order.
    """
    big = order == ">"
    lo, hi = (1, 0) if big else (0, 1)   # text byte / zero byte within each pair
    out = []
    o = start
    end = min(end, len(raw))
    while o < end:
        n = raw[o]
        if 3 <= n <= 48 and o + 1 + n + 4 <= end:
            b = raw[o + 1:o + 1 + n]
            if all(0x20 <= c < 0x7F for c in b):
                out.append(("class", b.decode("ascii"),
                            struct.unpack_from(order + "I", raw, o + 1 + n)[0]))
                o += 1 + n + 4
                continue
        if o + 4 <= end:
            c = struct.unpack_from(order + "I", raw, o)[0]
            if 1 <= c <= 64 and o + 4 + 2 * c + 1 <= end:
                bb = raw[o + 4:o + 4 + 2 * c]
                if (all(bb[i + hi] == 0 for i in range(0, len(bb), 2))
                        and all(0x20 <= bb[i + lo] < 0x7F
                                for i in range(0, len(bb), 2))):
                    name = bb[lo::2].decode("ascii")
                    if _is_identifier(name):
                        out.append(("field", name, raw[o + 4 + 2 * c]))
                        o += 4 + 2 * c + 1
                        continue
        o += 1
    return out


# A fixed sentinel closing the overview section. The trailer before it is what
# can actually be read: ChannelCount at sentinel-8 matched the source audio on
# 419 of 419 files that had one, and bytes-per-bin at sentinel-26 was always
# ChannelCount * 2.
#
# The sentinel is NOT byte-order dependent, and the reason is worth writing
# down because it looks like an oversight otherwise. Measured over 1,500
# specimens: every big-endian file is the older generation, and no big-endian
# file contains an overview block at all -- neither this sentinel nor its
# byte-swapped form appears in one. The overview pyramid postdates the
# PowerPC era, so the two never co-occur.
#
# The block is also OPTIONAL within the newer generation: only 171 of 1,031
# new-generation files carried one. Its absence is ordinary, not a defect.
OVERVIEW_SENTINEL = bytes.fromhex("ab1e5678")
OVERVIEW_MARK = b"\x13SampleOverViewLevel"


# Each warp marker carries its own class name inline, u8-length-prefixed, the
# same convention the type dictionary uses. That makes them self-locating: no
# count to find, no offset to derive, no heuristic. The record is 32 bytes:
#
#     u8   0x0A            length of the name
#     char "WarpMarker"
#     u32  Id              matches the XML's Id attribute
#     f64  SecTime         seconds into the audio
#     f64  BeatTime        position in beats
#
# Confirmed by taking the exact values a Live Set states for a clip and finding
# them, bit for bit, in that clip's sidecar.
WARP_MARKER_NAME = b"\x0aWarpMarker"
WARP_MARKER_SIZE = 1 + 10 + 4 + 8 + 8


def warp_markers(raw, order="<", limit=400):
    """[{id, sec, beat, at}] for every warp marker, in file order; `at` is
    the offset of its 20-byte record (u32 id, then two doubles).

    Live positions audio by mapping seconds to beats: each marker pins one
    instant of the recording to one position in the bar. Two markers are enough
    to state a tempo, and the tempo Live shows is DERIVED from consecutive
    pairs rather than stored -- which is why searching the file for a BPM value
    finds nothing.
    """
    def sane(x):
        # a real time is zero or an ordinary magnitude. The class DECLARATION
        # in the type dictionary carries the same "WarpMarker" literal, and the
        # bytes after it decode as denormals around 1e-307 -- which slipped
        # through an "is it finite" test and added a phantom marker to every
        # file.
        return x == 0.0 or 1e-9 < abs(x) < 1e6

    out = []
    o = raw.find(WARP_MARKER_NAME)
    while o != -1 and len(out) < limit:
        rec = o + 11
        if rec + 20 > len(raw):
            break
        mid = struct.unpack_from(order + "I", raw, rec)[0]
        sec, beat = struct.unpack_from(order + "2d", raw, rec + 4)
        if mid <= 100_000 and sane(sec) and sane(beat):
            out.append({"id": mid, "sec": sec, "beat": beat, "at": rec})
        o = raw.find(WARP_MARKER_NAME, o + 1)
    # instances are written in id order; anything else is not the array
    out.sort(key=lambda m: m["id"])
    return [m for i, m in enumerate(out) if m["id"] == i]


def derived_tempos(markers):
    """BPM over each consecutive warp-marker span, in order.

    Live stores no tempo number; it stores this mapping. Between two markers,
    (beats / seconds) * 60 is the tempo over that span. Spans that do not cover
    real time are skipped, which is the ordinary case for an unwarped one-shot.

    A list, because warp markers exist precisely to encode tempo that MOVES. A
    clip warped 120 / 60 / 200 has three answers and reporting the first as
    "the" tempo is a confident wrong one.
    """
    out = []
    for a, b in zip(markers, markers[1:]):
        dt, db = b["sec"] - a["sec"], b["beat"] - a["beat"]
        if dt > 1e-9 and db > 1e-9:
            out.append(round(db / dt * 60.0, 4))
    return out


def derived_tempo(markers):
    """The first span's BPM, or None. Kept for callers wanting a single number;
    prefer derived_tempos() and say so when the spans disagree."""
    spans = derived_tempos(markers)
    return spans[0] if spans else None


def references_size(raw, size, order="<"):
    """True when the sidecar contains `size` as a u32.

    Live records the source audio's byte size (`OriginalFileSize`, present in
    every specimen) and re-analyses when it no longer matches. Reading it
    positionally is not yet possible -- its offset is not fixed, and it sits at
    EOF-8 in only 9% of files -- but the question that matters can be answered
    without knowing where it lives: does this sidecar reference this audio's
    current size at all?

    Measured on 1,200 sidecars sitting beside their actual audio: 96% yes.
    A "no" means the audio changed after the analysis was written, so the
    sidecar describes a version of the file that no longer exists.
    """
    if not 0 < size < 2 ** 32:
        return False
    return raw.find(struct.pack(order + "I", size)) >= 0


def onsets(raw, total_frames, start, order="<", limit=200_000):
    """Live's detected transients: (positions, energies), or None.

    The `.als` XML and the `.asd` serialise the same object model -- the XML
    carries the very field names the binary type dictionary declares -- so the
    XML says what this structure is. `OnSets` holds `Positions` and
    `TransitionEnergies`, and on disk that is two length-prefixed arrays back
    to back:

        u32 n | u32 positions[n] (frame offsets) | u32 n | f32 energies[n]

    Stepped byte by byte, not by four: the arrays are not aligned to the end
    of the frame grid, so a word-stepped scan walks straight past them.

    Anchored structurally rather than on the clip-parameter block that precedes
    it, because those parameters are defaults (granularity 30/65/25) and any
    user who changes one would move the anchor.

    The signature is specific enough to trust: the same count twice, positions
    strictly increasing, and the last one inside the file's own frame count --
    which the grid already gave us independently.
    """
    pack = order + "I"
    end = min(len(raw), start + limit)
    o = start
    best = None
    while o + 8 <= end:
        n = struct.unpack_from(pack, raw, o)[0]
        # n == 1 is not admissible: with a single position "strictly
        # increasing" constrains nothing, so any stray pair of equal u32
        # qualifies. That cost 7 of 14 files a wrong answer. A one-shot with a
        # single genuine transient is therefore reported as UNKNOWN rather
        # than guessed at -- see the note in the walker.
        if not 2 <= n <= 100_000 or o + 8 + 8 * n > len(raw):
            o += 1
            continue
        pos = struct.unpack_from(f"{order}{n}I", raw, o + 4)
        after = o + 4 + 4 * n
        if (struct.unpack_from(pack, raw, after)[0] == n
                and all(b > a for a, b in zip(pos, pos[1:]))
                and (not total_frames or pos[-1] <= total_frames)):
            energies = struct.unpack_from(f"{order}{n}f", raw, after + 4)
            # With n == 1 "strictly increasing" constrains nothing, so a stray
            # pair of equal u32 anywhere would qualify. The energies are the
            # second opinion: real ones are ordinary positive magnitudes, while
            # random bytes read as denormals, infinities or NaN.
            if all(0.0 < e < 1e7 for e in energies):
                cand = {"count": n, "positions": list(pos),
                        "energies": [round(e, 4) for e in energies],
                        "offset": o, "end": after + 4 + 4 * n}
                # keep scanning: take the richest structure, not the first
                # coincidence to satisfy the predicate
                if best is None or n > best["count"]:
                    best = cand
        o += 1
    return best


# The clip parameters, in the order the Live Set XML declares them. Types are
# mixed -- TransientResolution and TransientLoopMode are integers, the rest
# floats -- which is why they do not read as one float run.
CLIP_PARAMS = (
    ("TransientResolution", "u32"),
    ("GranularityTones", "f32"),
    ("GranularityTexture", "f32"),
    ("FluctuationTexture", "f32"),
    ("TransientLoopMode", "u32"),
    ("TransientEnvelope", "f32"),
    ("ComplexProFormants", "f32"),
    ("ComplexProEnvelope", "f32"),
)


def clip_params(raw, onset_offset, order="<"):
    """The clip-parameter block that sits just before the onset arrays.

    Read backwards from the onsets, which are structurally anchored, so this
    does not depend on any parameter holding its default.
    """
    size = sum(4 for _ in CLIP_PARAMS)
    # the block ends a little before the onset count; find it by matching the
    # two integer fields, which are small and distinctive among the floats
    for back in range(16, 140, 4):
        o = onset_offset - back - size
        if o < 0:
            continue
        vals, ok = {}, True
        for i, (name, kind) in enumerate(CLIP_PARAMS):
            at = o + 4 * i
            if at + 4 > len(raw):
                ok = False
                break
            if kind == "u32":
                v = struct.unpack_from(order + "I", raw, at)[0]
                if v > 64:
                    ok = False
                    break
            else:
                v = round(struct.unpack_from(order + "f", raw, at)[0], 4)
                if not 0.0 <= v <= 1000.0:
                    ok = False
                    break
            vals[name] = v
        if ok and vals.get("GranularityTones") is not None:
            return {"offset": o, "values": vals}
    return None


def overview_trailer(raw, order="<"):
    """{channels, bytes_per_bin, ...} from the overview trailer, or None.

    Anchored on the sentinel rather than on the class name: the trailer's
    length varies with the number of levels, so anchoring on the name agrees
    only ~79% of the time while the sentinel is exact.
    """
    mark = raw.rfind(OVERVIEW_MARK)
    if mark < 0:
        return None
    s = raw.find(OVERVIEW_SENTINEL, mark)
    if s < 0 or s < 26:
        return None
    channels = struct.unpack_from(order + "I", raw, s - 8)[0]
    per_bin = struct.unpack_from(order + "I", raw, s - 26)[0]
    log2 = struct.unpack_from(order + "I", raw, s - 12)[0]
    if not 1 <= channels <= 32:
        return None
    # SamplesPerBinLog2 is READ, not inferred. An earlier version inferred 64
    # from blob sizes and was wrong: the declared value is 7 and the measured
    # geometry is 128 frames per bin, agreeing exactly on every specimen with
    # an overview. The inference had divided by a span that included several
    # KB of unrelated structure.
    bin_samples = 1 << log2 if 0 < log2 < 32 else None
    return {
        "channels": channels,
        "bytes_per_bin": per_bin,
        "consistent": per_bin == channels * 2,
        "sentinel_at": s,
        "samples_per_bin_log2": log2,
        "bin_samples": bin_samples,
    }


# ── the gzipped-XML family ────────────────────────────────────────────────

# The element directly inside <Ableton> names the document type -- with one
# ambiguity that cannot be resolved from content: a Live Set (.als) and a Live
# Clip (.alc) BOTH use <LiveSet>, so only the extension separates them. A
# device preset (.adv) puts the device's own class there (<Operator>,
# <Wavetable>, ...), so it is the default rather than an enumerated case.
XML_ROOT_CHILDREN = {
    "LiveSet": "als",               # or alc -- see sniff_gzip_ableton
    "GroupDevicePreset": "adg",
    "Groove": "agr",
}
_XML_LABELS = {
    "als": "Live Set",
    "alc": "Live Clip",
    "adg": "device group / rack",
    "adv": "device preset",
    "agr": "groove",
}


def xml_label(fmt_id):
    return _XML_LABELS.get(fmt_id, "Live document")


def gunzip_capped(path, cap=XML_DECOMPRESS_CAP):
    """Decompress at most `cap` bytes. Returns (data, truncated).

    A Live Set expands roughly fifty-fold, so an uncapped read is a memory
    hazard on hostile input and merely wasteful on ordinary input.
    """
    data = bytearray()
    with open_input(path) as fh:
        dec = zlib.decompressobj(zlib.MAX_WBITS | 16)
        while len(data) < cap:
            block = fh.read(65536)
            if not block:
                break
            try:
                data += dec.decompress(block, cap - len(data))
            except zlib.error as exc:
                raise AbletonError(f"gzip stream is damaged: {exc}") from exc
            if dec.eof:
                break
    return bytes(data[:cap]), len(data) >= cap


_ROOT_CHILD = re.compile(rb"<Ableton\b[^>]*>\s*<(\w+)")


def sniff_gzip_ableton(path):
    """Format id for a gzipped Ableton document, or None.

    Only the first block is decompressed -- enough to see the XML declaration
    and the root's first child without paying for a multi-megabyte set.

    A Live Pack (.alp) is also gzip but is an archive, not an XML document; it
    has no <Ableton> element and so returns None here rather than being
    mislabelled as a set.
    """
    try:
        with gzip_open(path) as fh:
            head = fh.read(4096)
    except (OSError, EOFError, zlib.error):
        return None
    if b"<Ableton" not in head:
        return None
    m = _ROOT_CHILD.search(head)
    child = m.group(1).decode("ascii") if m else ""
    # Live has more document types than this maps -- .ams (Operator meta
    # sound), .abl / .ablbundle (Note), .ask (theme). They are the same gzip +
    # <Ableton> XML shape, so they land here as "adv" and the walker names the
    # root child it actually found rather than asserting a device preset.
    fid = XML_ROOT_CHILDREN.get(child, "adv")
    # <LiveSet> means set or clip; nothing in the content distinguishes them
    if fid == "als" and input_name(path).lower().endswith(".alc"):
        return "alc"
    return fid


_ATTR = re.compile(rb'(\w+)="([^"]*)"')


def root_child(xml_head):
    """The element directly inside <Ableton>, which names the document type."""
    m = _ROOT_CHILD.search(xml_head)
    return m.group(1).decode("ascii") if m else None


def header_attributes(xml_head):
    """Attributes of the root <Ableton> element, or None if there is no such
    element.

    None and {} are different answers. This returned {} for both "no <Ableton>
    element" and "the element is there and carries no attributes", so the
    caller warned "no <Ableton> root element found" about documents whose root
    element it had just parsed the child of -- the same output both reporting
    and denying the element's existence.
    """
    m = re.search(rb"<Ableton\b([^>]*)>", xml_head)
    if not m:
        return None
    return {k.decode("ascii"): v.decode("utf-8", "replace")
            for k, v in _ATTR.findall(m.group(1))}
