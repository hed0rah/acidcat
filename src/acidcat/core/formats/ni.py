"""Native Instruments preset reader (NISound 'hsin' container).

Massive (.nmsv), Absynth (.nabs), FM8, Reaktor, and modern Kontakt (.nki/.nkm)
share the NISound container: an EBML-like tree of size-prefixed frames headed
by the 'hsin' magic at offset 0x0C. Each frame's data is one item, a stack of
nested records (read_stack), and each record's own data has the layout its item
id gives it (decode_item). The metadata (the SoundInfoItem: preset name,
author, bank chain, attributes, properties) is plain UTF-16LE and needs no
decompression; the synth state is a subtree, FastLZ-compressed in Massive and
Absynth and stored as it is in Reaktor, whose innermost state is the synth's
own and is left opaque here.

Implemented from byte-level facts only (verified against real Massive and
Absynth presets), not from any third-party source.
"""

import re
import struct
import zlib

MAGIC_OFFSET = 0x0C
MAGIC = b"hsin"
KSD_MAGIC = b"-in-"

# app names as they appear (UTF-16LE) in the SoundInfoItem
_APP_NAMES = {"Massive", "Massive X", "Absynth", "FM8", "Reaktor", "Kontakt",
              "Guitar Rig", "Battery"}
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")


def is_ni_hsin(data):
    return len(data) >= 0x30 and data[MAGIC_OFFSET:MAGIC_OFFSET + 4] == MAGIC


def _u16le_pascals(data, limit=1 << 20):
    """Collect (offset, text) for u32-count-prefixed UTF-16LE strings in the
    metadata region. Every length is bounds-checked."""
    out = []
    i, n = 0x30, min(len(data), limit)
    while i + 4 <= n:
        c = struct.unpack_from("<I", data, i)[0]
        if 1 <= c <= 256 and i + 4 + c * 2 <= len(data):
            raw = data[i + 4:i + 4 + c * 2]
            try:
                s = raw.decode("utf-16-le")
            except UnicodeDecodeError:
                i += 1
                continue
            # require mostly-ASCII printable text, so binary noise that happens
            # to decode as valid UTF-16LE (e.g. a stray CJK codepoint) is not
            # mistaken for a metadata string.
            printable = sum(ch.isascii() and ch.isprintable() for ch in s)
            if len(s) >= 2 and printable >= len(s) * 0.7:
                out.append((i, s))
                i += 4 + c * 2
                continue
        i += 1
    return out


def fastlz_decompress(src, max_out=32 * 1024 * 1024):
    """FastLZ decompression, levels 1 and 2 (pure Python). NI compresses the
    hsin subtree payload (item 115) with level 1; Kontakt 4.2 patch bodies use
    both. The level is the top three bits of the first byte. Returns the
    decompressed bytes, or None if the output would exceed max_out (a
    decompression-bomb guard). A malformed stream stops early, so callers
    compare the length with the one they expected."""
    dst = bytearray()
    ip, n = 0, len(src)
    if n == 0:
        return b""
    level2 = (src[0] >> 5) == 1
    ctrl = src[ip] & 0x1F if level2 else src[ip]
    ip += 1
    while True:
        if ctrl >= 32:  # back-reference
            length = ctrl >> 5
            ofs = (ctrl & 0x1F) << 8
            if length == 7:
                if ip >= n:
                    break
                if level2:
                    # level 2 extends the length with bytes until one is not 255
                    while ip < n and src[ip] == 255:
                        length += 255
                        ip += 1
                    if ip >= n:
                        break
                length += src[ip]
                ip += 1
            if ip >= n:
                break
            code = src[ip]
            ofs += code
            ip += 1
            length += 2
            if level2 and code == 255 and ofs == (31 << 8) + 255:
                # a 16-bit far distance follows, counted beyond the near window
                if ip + 2 > n:
                    break
                ofs = ((src[ip] << 8) | src[ip + 1]) + 8191
                ip += 2
            ref = len(dst) - ofs - 1
            if ref < 0:
                break
            if ref + length <= len(dst):
                dst += dst[ref:ref + length]
            else:
                # overlapping: the copy reads bytes it is writing, which makes
                # it the last (len - ref) bytes repeated. Build it as a repeat,
                # not byte by byte: a long run is one slice, not a Python loop.
                period = bytes(dst[ref:])
                dst += (period * (length // len(period) + 1))[:length]
        else:  # literal run of ctrl+1 bytes
            length = ctrl + 1
            dst.extend(src[ip:ip + length])
            ip += length
        if len(dst) > max_out:
            return None
        if ip < n:
            ctrl = src[ip]
            ip += 1
        else:
            break
    return bytes(dst)


_SUBTREE_TOTAL_CAP = 64 * 1024 * 1024


def decompress_subtree(data, max_attempts=64, total=None):
    """Locate the FastLZ-compressed subtree (item 115) in an hsin preset and
    return its decompressed inner container, or None. The payload header is
    u32=1, u8=1, u32 uncompressed_size, u32 compressed_size, then FastLZ."""
    attempts = 0
    # every attempt inflates a candidate that may prove wrong; bound them all
    # together, not each alone, or 64 wrong candidates cost 64 times the cap
    budget = _SUBTREE_TOTAL_CAP if total is None else total
    # locate the sentinel with a C-level find (not a Python byte-by-byte scan,
    # which a crafted sentinel-free file could stretch to seconds).
    m = data.find(b"\x01\x00\x00\x00\x01", 0x30)
    while 0 <= m <= len(data) - 13:
        uncomp = struct.unpack_from("<I", data, m + 5)[0]
        comp = struct.unpack_from("<I", data, m + 9)[0]
        if (16 < comp < len(data) and 16 < uncomp < 64 * 1024 * 1024
                and m + 13 + comp <= len(data)):
            attempts += 1
            if attempts > max_attempts:
                break
            if uncomp + 16 > budget:
                break
            out = fastlz_decompress(data[m + 13:m + 13 + comp], uncomp + 16)
            if out is not None and len(out) == uncomp:
                return out
            budget -= len(out) if out is not None else uncomp + 16
        m = data.find(b"\x01\x00\x00\x00\x01", m + 1)
    return None


def is_ni_ksd(data):
    return data[:4] == KSD_MAGIC


# ── Reaktor instruments (.ism) ──

# the class record: a 0x5b tag, u32 length 7, "KSModul". In 497 real .ism it
# sits at 46-49 behind an `NRKT` header, or at 5 or 13 in the older layout
# that opens 0x3a; it is also in Reaktor's .mdl and .rcc, and in nothing else
# across a 64,743-file synth library
REAKTOR_MODULE_CLASS = b"\x5b\x07\x00\x00\x00KSModul"
REAKTOR_MAGIC = b"NRKT"
_REAKTOR_CLASS_WINDOW = 128


def reaktor_module_at(head):
    """Offset of the KSModul class record in a Reaktor instrument's first
    bytes, or None when the head is not one."""
    if head[:4] != REAKTOR_MAGIC and head[:1] != b"\x3a":
        return None
    at = bytes(head[:_REAKTOR_CLASS_WINDOW]).find(REAKTOR_MODULE_CLASS)
    return at if at >= 0 else None


# ── hsin frame tree, read for its structure ──

FRAME_HEADER = 0x30
# the reference ahead of a child frame: u32, a domain tag, u32 item id
CHILD_REF = 12
# the item ids the anatomy page names (docs/formats/ni-anatomy.html)
ITEM_NAMES = {1: "end of the item stack", 101: "Authoring Application",
              108: "SoundInfoItem", 115: "SubtreeItem", 116: "EncryptionItem",
              118: "RepositoryRoot", 121: "ControllerAssignments"}


def _domain(tag):
    return len(tag) == 4 and tag.isalnum()


def read_frames(read_at, off, end, max_frames, max_depth=128):
    """The hsin frame tree at `off`, depth first, read through read_at(off, n)
    so a large frame need not be in memory. Unlike the editor's walk, any
    four-letter domain tag is accepted: Reaktor writes RTKR beside DSIN.

    Returns (frames, problem). A frame is {at, size, data_size, depth, ref,
    ref_fields, items, stack_problem, header}: `ref` is where the 12-byte
    reference before a child frame starts and `ref_fields` its (u32, domain,
    item id), both None for the root; `items` the item records of its data
    section, from read_stack, and `stack_problem` why they stop short of
    item 1, or None; `header` the six header words. `problem` is None when
    the tree fills its root exactly, "frames" or "depth" when a bound stopped
    the walk, else why the bytes stop being a frame tree. An unreadable root
    is ([], reason)."""
    frames = []
    # (frame offset, reference offset, depth, end of the parent frame, reference)
    todo = [(off, None, 0, end, None)]
    while todo:
        at, ref, depth, limit, ref_bytes = todo.pop()
        if len(frames) >= max_frames:
            return frames, "frames"
        if depth > max_depth:
            return frames, "depth"
        head = read_at(at, FRAME_HEADER)
        if len(head) < FRAME_HEADER or head[12:16] != MAGIC:
            return frames, f"no hsin frame at {at:#x}"
        fs, hv = struct.unpack_from("<QI", head, 0)
        c1, c2 = struct.unpack_from("<II", head, 16)
        ds = struct.unpack_from("<Q", head, 0x28)[0]
        if fs < FRAME_HEADER or FRAME_HEADER + ds > fs:
            return frames, f"the frame at {at:#x} has sizes that do not nest"
        if at + fs > limit:
            return frames, (f"the frame at {at:#x} claims {fs:,} bytes and "
                            f"{limit - at:,} remain")
        data_start, data_end, frame_end = at + FRAME_HEADER, at + FRAME_HEADER + ds, at + fs
        items, stack_problem = read_stack(read_at, data_start, data_end)
        frames.append({"at": at, "size": fs, "data_size": ds, "depth": depth,
                       "ref": ref, "items": items, "stack_problem": stack_problem,
                       "header": (fs, hv, c1, c2, bytes(head[24:40]), ds),
                       "ref_fields": None if ref_bytes is None else (
                           struct.unpack_from("<I", ref_bytes, 0)[0],
                           ref_bytes[4:8].decode("ascii"),
                           struct.unpack_from("<I", ref_bytes, 8)[0])})
        kids, pos = [], data_end
        while pos < frame_end:
            r = read_at(pos, CHILD_REF + 8)
            if len(r) < CHILD_REF + 8 or not _domain(r[4:8]):
                return frames, f"no child frame reference at {pos:#x}"
            cfs = struct.unpack_from("<Q", r, CHILD_REF)[0]
            if cfs < FRAME_HEADER or pos + CHILD_REF + cfs > frame_end:
                return frames, f"the child frame at {pos + CHILD_REF:#x} overruns its parent"
            kids.append((pos + CHILD_REF, pos, depth + 1, frame_end, bytes(r[:CHILD_REF])))
            pos += CHILD_REF + cfs
        # depth first, in file order
        todo.extend(reversed(kids))
    return frames, None


# records in one item stack; the deepest real one is three
_STACK_CAP = 64
_RECORD = 12          # domain tag, u32 item id, u32 version


def _nests(head, pos, lim):
    """Whether the record whose first 20 bytes are `head` is followed by a
    size that fits a nested record."""
    if len(head) < _RECORD + 8:
        return False
    n = struct.unpack_from("<Q", head, _RECORD)[0]
    return n >= 8 + _RECORD and pos + _RECORD + n <= lim


def read_stack(read_at, start, end):
    """The item a frame's data section [start, end) holds, as its stack of
    records, the outermost first.

    A record is a domain tag, a u32 item id and a u32 version. Every record
    but item 1's is followed by the record nested in it, which opens with its
    own size as a u64 that counts those 8 bytes too (the outermost record's
    size is the frame header's data_size, which does not count itself). The
    nested record ends before its parent does, and the bytes between are the
    parent's own data, so the records' data reads innermost first: item 1's,
    then each enclosing record's, the outermost's last.

    Returns (records, problem). A record is {at, domain, item_id, version,
    size_at, own}: `size_at` is where the nested record's u64 size sits (None
    for item 1, which nests nothing), `own` the (start, end) of the record's
    own data. `problem` is None when the stack ends on item 1, else why the
    records stop; the records read before it keep their own data."""
    recs, pos, lim = [], start, end
    while True:
        if len(recs) >= _STACK_CAP:
            return recs, f"more than {_STACK_CAP} nested item records"
        head = read_at(pos, _RECORD + 8) if pos + _RECORD <= lim else b""
        if len(head) < _RECORD or not _domain(head[:4]):
            return recs, f"no item record at {pos:#x}"
        iid, ver = struct.unpack_from("<II", head, 4)
        dom = head[:4].decode("ascii")
        rec = {"at": pos, "domain": dom, "item_id": iid,
               "version": ver, "size_at": None, "own": (pos + _RECORD, lim)}
        if iid == 1 and (dom == "DSIN" or not _nests(head, pos, lim)):
            # item 1 of DSIN nests nothing. Reaktor's RTKR item 1 is another
            # class, and in 266 of its frames across 60 real ensembles a
            # DSIN record is nested in it
            recs.append(rec)
            return recs, None
        if len(head) < _RECORD + 8 or pos + _RECORD + 8 > lim:
            return recs, f"the record at {pos:#x} has no room for the size of the one inside it"
        n = struct.unpack_from("<Q", head, _RECORD)[0]
        size_at = pos + _RECORD
        if n < 8 + _RECORD or size_at + n > lim:
            return recs, (f"the record nested at {size_at:#x} claims {n:,} bytes "
                          f"and {lim - size_at:,} remain")
        rec["size_at"], rec["own"] = size_at, (size_at + n, lim)
        recs.append(rec)
        pos, lim = size_at + 8, size_at + n


# ── an item record's own data ──

class _Short(ValueError):
    """The item's data ends inside a value."""


class _Fields:
    """Reads an item's own data front to back, keeping each value it reads as
    (offset, length, name, value, note, enc) relative to the data."""

    def __init__(self, b):
        self.b, self.pos, self.out = b, 0, []

    def _take(self, n):
        if self.pos + n > len(self.b):
            raise _Short(f"{n} bytes at {self.pos} run past the item's "
                         f"{len(self.b)}")
        at = self.pos
        self.pos += n
        return at

    def num(self, fmt, name, note=""):
        at = self._take(struct.calcsize(fmt))
        v = struct.unpack_from(fmt, self.b, at)[0]
        self.out.append((at, struct.calcsize(fmt), name, v, note, fmt))
        return v

    def text(self, name, note=""):
        at = self._take(4)
        n = struct.unpack_from("<I", self.b, at)[0]
        if n > _HSIN_STR_MAX:
            raise _Short(f"a {n:,}-character string at {at}")
        self.pos = at
        self._take(4 + 2 * n)
        v = self.b[at + 4:at + 4 + 2 * n].decode("utf-16-le", errors="replace")
        self.out.append((at, 4 + 2 * n, name, v, note, None))
        return v

    def raw(self, n, name, note=""):
        at = self._take(n)
        v = bytes(self.b[at:at + n])
        shown = v.hex(" ") if n <= 24 else f"{n:,} bytes"
        self.out.append((at, n, name, shown, note, None))
        return v

    def note(self, text):
        """Set the note of the value read last."""
        self.out[-1] = self.out[-1][:4] + (text,) + self.out[-1][5:]

    def rest(self, name="rest", note="uninterpreted"):
        if self.pos < len(self.b):
            self.raw(len(self.b) - self.pos, name, note)


_SEEN = "uninterpreted; the same in every preset measured"
# the SoundInfoItem's device flags: 2 on 121 of 536 real Absynth presets, all
# 121 in effect banks with delay, reverb and distortion attributes; 1 on the
# instruments (980 of the 1,114 Massive and Absynth measured), 0 on 13 old
# Massive presets with no attributes
_DEVICE_FLAGS = {1: "Instrument", 2: "Effect"}


def _item_root(r, meta):
    # item 1 nests nothing; 4 bytes under another record, 12 at a tree's top
    r.num("<I", "version", "1 in every item measured")
    r.rest()


def _item_versioned(r, meta):
    # an item whose data is a version word and words not interpreted (106:
    # 28 bytes under a RepositoryRoot, 8 under an Authoring Application)
    r.num("<I", "version", "1 in every item measured")
    r.rest()


def _item_118(r, meta):
    """The RepositoryRoot: the tree's top item."""
    r.num("<I", "version", "1 in every item measured")
    w = r.num("<I", "version_word")
    # read as major << 20 | minor << 12 | patch it is the SoundInfoItem's
    # @verl property in all 568 of 1,114 real presets whose @verl is 1.7.3 or
    # later; the rest give 1.3.0 beside an @verl of 1.7.2 (288) or 1.2.0 with
    # no @verl (258)
    r.note(f"{w >> 20}.{(w >> 12) & 0xFF}.{w & 0xFFF}, read as major << 20 | "
           f"minor << 12 | patch")
    r.raw(24, "words", "uninterpreted")
    r.text("text", "uninterpreted; \"0\" or \"000\" in every preset measured")
    r.rest()


def _item_101(r, meta):
    r.num("<I", "version", "1 in every item measured")
    r.num("<B", "flag", "uninterpreted")
    app = r.num("<I", "app_id", "the writing product: 7 Massive, 6 Absynth")
    r.num("<I", "word", _SEEN)
    ver = r.text("app_version", "the writing product's version")
    if ver:
        meta["version"] = ver
    meta["app_id"] = app
    r.rest()


def _item_108(r, meta):
    """The SoundInfoItem: the preset's browser card."""
    r.num("<I", "version", "1 in every item measured")
    for name in ("word", "word", "word"):
        r.num("<I", name, _SEEN)
    for name in _HSIN_INFO:
        v = r.text(name)
        if v.strip():
            meta[name] = v.strip()
    r.text("text", "uninterpreted; empty in every preset measured")
    r.raw(24, "words", "uninterpreted")
    flags = r.num("<I", "device_flags",
                  "1 an instrument, 2 an effect; equals the @devicetypeflags "
                  "property wherever both are set")
    meta["device_flags"] = flags
    if flags in _DEVICE_FLAGS:
        meta["device_type"] = _DEVICE_FLAGS[flags]
    r.num("<I", "word", _SEEN)
    bank = [r.text(f"bank {i}") for i in range(r.num("<I", "bank_levels"))]
    attrs = [r.text(f"attribute {i}") for i in range(r.num("<I", "attributes"))]
    r.num("<I", "word", _SEEN)
    props = {}
    for i in range(r.num("<I", "properties")):
        k = r.text(f"property {i}")
        props[k] = r.text(k)
    bank = [b.strip() for b in bank if b.strip()]
    if bank:
        meta["bankchain"] = bank
    attrs = [a.strip() for a in attrs if a.strip()]
    if attrs:
        meta["attributes"] = attrs
    if props:
        meta["properties"] = props
    r.rest()


def _item_115(r, meta):
    """The SubtreeItem: a whole hsin tree, FastLZ-compressed or as it is."""
    r.num("<I", "version", "1 in every item measured")
    packed = r.num("<B", "compressed", "1: FastLZ, as every Massive and Absynth "
                                       "preset measured; 0: the tree as it is, "
                                       "as every Reaktor ensemble")
    if packed == 0:
        # the inner tree's own frame follows, its u64 size first
        meta["subtree"] = {"compressed": 0, "tree_at": r.pos}
        return
    full = r.num("<I", "uncompressed_size")
    comp = r.num("<I", "compressed_size")
    meta["subtree"] = {"compressed": packed, "size": full, "stream_at": r.pos,
                       "stream_len": comp}
    # the stream is the walker's to read; whatever follows it is not measured
    if r.pos + comp <= len(r.b):
        r.pos += comp
        r.rest()


def _item_116(r, meta):
    r.num("<I", "version", "1 in every item measured")
    r.num("<B", "flag", "uninterpreted; 0 in every Massive and Absynth preset "
                        "measured, whose subtree then reads as plain FastLZ; "
                        "Reaktor ensembles write 0, 1 and other values")
    r.rest()


def _item_121(r, meta):
    """ControllerAssignments: pages of named controls, each slot a label, a
    range and the synth parameter it drives."""
    r.num("<I", "version", "1 in every item measured")
    r.num("<I", "word", _SEEN)
    pages = []
    for _ in range(r.num("<I", "pages")):
        start = r.pos
        sub = _Fields(r.b)
        sub.pos = r.pos
        sub.num("<I", "word")
        slots = sub.num("<I", "slots")
        name = sub.text("page")
        controls = []
        for _s in range(slots):
            sub.num("<I", "word")
            label = sub.text("label")
            lo = sub.num("<f", "min")
            hi = sub.num("<f", "max")
            param = sub.num("<I", "parameter")
            pos = sub.num("<I", "position")
            controls.append((label, lo, hi, param, pos))
        r.pos = sub.pos
        pages.append((start, r.pos - start, name, controls))
        r.out.append((start, r.pos - start, f"page {len(pages) - 1}",
                      f"{name}: {len(controls)} control(s)", "", None))
    if pages:
        meta["pages"] = pages
    r.rest()


def _item_109(r, meta):
    """The synth's own state, inside the subtree."""
    r.num("<I", "version", "1 in every item measured")
    r.num("<I", "word", _SEEN)
    r.num("<I", "word", _SEEN)
    n = r.num("<Q", "state_size", "bytes of plugin state that follow")
    # the synth's parameter state, its layout the synth's own (a float array
    # in Massive, a tagged chunk list in Absynth, megabytes in Reaktor): the
    # walker's to place; whatever follows it is not measured
    meta["state"] = (r.pos, n)
    if r.pos + n <= len(r.b):
        r.pos += n
        r.rest()


def _item_104(r, meta):
    """A Reaktor ensemble's module: 98% of the bytes of 40 real ensembles."""
    r.num("<I", "version", "1 in every item measured")
    r.num("<I", "word", _SEEN)
    n = r.num("<Q", "module_size", "bytes of Reaktor module that follow")
    lead = bytes(r.b[r.pos:r.pos + 4])
    # it opens NRKT or 0x3a, as an .ism instrument does
    meta["module"] = (r.pos, n, "NRKT" if lead == REAKTOR_MAGIC else lead[:1].hex())
    # the module is the walker's to place; whatever follows it is not measured
    if r.pos + n <= len(r.b):
        r.pos += n
        r.rest()


ITEM_DECODERS = {1: _item_root, 101: _item_101, 104: _item_104, 106: _item_versioned,
                 108: _item_108, 109: _item_109, 115: _item_115, 116: _item_116,
                 118: _item_118, 121: _item_121}


def item_name(domain, item_id, default=None):
    """The name of an item, which only DSIN's ids have here."""
    name = ITEM_NAMES.get(item_id) if domain == "DSIN" else None
    return name or (f"item {item_id}" if default is None else default)


def decodes(domain, item_id):
    """Whether an item's own data has a layout here: DSIN's items only, as
    Reaktor's RTKR numbers its own from 1 too."""
    return domain == "DSIN" and item_id in ITEM_DECODERS


def decode_item(item_id, b):
    """An item record's own data read as its item id lays it out: (fields,
    meta, problem). `fields` are (offset, length, name, value, note, enc)
    relative to the data, `meta` what the item says about the preset, and
    `problem` None when the layout read to the end, else why it stopped (the
    fields before it stand). An item with no layout here is (None, {}, None)."""
    fn = ITEM_DECODERS.get(item_id)
    if fn is None:
        return None, {}, None
    r, meta = _Fields(b), {}
    try:
        fn(r, meta)
    except (_Short, struct.error) as e:
        return r.out, meta, str(e)
    return r.out, meta, None


class NotHeld(ValueError):
    """A field this preset cannot take: not one its container's editor maps,
    or (an in-place edit) not present in this file. A bad --set, not a
    refused edit."""


class Unmapped(ValueError):
    """The container is a layout the editor does not map (an hsin frame
    tree it cannot follow): there is no editor for this variant."""


# ── hsin writing (frame-size cascade; verified against Massive + Absynth) ──

# the child-reference domain tags, each a reversed 4CC. 2SAM ('MAS2') is
# Massive X's: a .mxsnd whose first child carries it walks exactly with it
# accepted -- every frame filled, the SoundInfoItem where the others put it
_HSIN_DOMAINS = (b"DSIN", b"4KIN", b"NISD", b"2SAM")
# field -> index of the UTF-16LE pascal string in the SoundInfoItem(108) payload.
# `artist` is the canonical spelling edit_metadata folds author and creator
# to before dispatch; without it no author edit could reach this map
_HSIN_EDIT = {"name": 0, "title": 0, "author": 1, "creator": 1, "artist": 1,
              "vendor": 2, "comment": 3, "description": 3}
_HSIN_INFO = ("name", "author", "vendor", "description")
_HSIN_STR_MAX = 0x10000          # UTF-16 units; the writer's own bound


def _hsin_walk(data, off, fields, depth=0):
    """Walk the hsin frame at off, recording every size field as
    (field_offset, width, span_start, span_end). Returns
    [(item_id, frame_off, data_start, data_end, payload_start), ...]."""
    if depth > 128:
        raise Unmapped("hsin nesting too deep")
    frames = []
    if off + 0x30 > len(data):
        raise Unmapped(f"bad hsin frame at {off:#x}")
    fs = struct.unpack_from("<Q", data, off)[0]
    if data[off + 12:off + 16] != b"hsin" or off + fs > len(data):
        raise Unmapped(f"bad hsin frame at {off:#x}")
    ds = struct.unpack_from("<Q", data, off + 0x28)[0]
    data_start, data_end, frame_end = off + 0x30, off + 0x30 + ds, off + fs
    if data_end > frame_end:
        raise Unmapped("data section overruns frame")
    fields.append((off, 8, off, frame_end))               # frame_size (inclusive)
    fields.append((off + 0x28, 8, data_start, data_end))  # data_size (exclusive)
    # the item records (read_stack): the record nested in another opens with
    # its own u64 size, which counts itself; the outermost record's own data
    # starts where the record nested in it ends
    item_id, payload_start, pos, lim = None, None, data_start, data_end
    while pos + 12 <= lim and data[pos:pos + 4] in _HSIN_DOMAINS:
        iid = struct.unpack_from("<I", data, pos + 4)[0]
        if item_id is None:
            item_id = iid
        if iid == 1:
            if payload_start is None:
                payload_start = pos + 12
            break
        if pos + 20 > lim:
            raise Unmapped("stack record has no room for its nested size")
        inner = struct.unpack_from("<Q", data, pos + 12)[0]
        inner_start, inner_end = pos + 12, pos + 12 + inner
        if inner < 20 or inner_end > lim:
            raise Unmapped("stack inner_size overruns data")
        fields.append((pos + 12, 8, inner_start, inner_end))
        if payload_start is None:
            payload_start = inner_end
        pos, lim = inner_start + 8, inner_end
    if payload_start is None:
        payload_start = data_start
    frames.append((item_id, off, data_start, data_end, payload_start))
    pos = data_end
    while pos < frame_end:
        if pos + 20 > frame_end or data[pos + 4:pos + 8] not in _HSIN_DOMAINS:
            raise Unmapped(f"bad child prefix {bytes(data[pos + 4:pos + 8])!r} "
                           f"at {pos:#x}")
        child_off = pos + 12
        cfs = struct.unpack_from("<Q", data, child_off)[0]
        frames.extend(_hsin_walk(data, child_off, fields, depth + 1))
        pos = child_off + cfs
    if pos != frame_end:
        raise Unmapped("children do not fill frame")
    return frames


def _hsin_info(data, fields=None, bounds=None):
    """The SoundInfoItem(108) strings, located through the frame walk:
    [(offset, count), ...] for name, author, vendor and description, as many
    as the item holds. `fields`, when a list, collects the walk's size fields;
    `bounds`, when a list, gets the (start, end) of the item's data appended.
    Raises Unmapped when the tree or the item is not the mapped layout."""
    frames = _hsin_walk(data, 0, [] if fields is None else fields)
    info = [f for f in frames if f[0] == 108]
    if len(info) != 1:
        raise Unmapped(f"expected one SoundInfoItem(108), found {len(info)}")
    _, _, _, d_end, payload = info[0]
    if bounds is not None:
        bounds.append((payload, d_end))
    # the item's own data opens u32 1, 2, 1, 0 (decode_item reads it whole)
    if payload + 16 > d_end or struct.unpack_from("<I", data, payload + 8)[0] != 1:
        raise Unmapped("unexpected SoundInfoItem payload")
    out, off = [], payload + 16
    while len(out) < len(_HSIN_INFO) and off + 4 <= d_end:
        count = struct.unpack_from("<I", data, off)[0]
        if count > _HSIN_STR_MAX or off + 4 + count * 2 > d_end:
            raise Unmapped("info string overruns SoundInfoItem")
        out.append((off, count))
        off += 4 + count * 2
    return out


def read_hsin_info(data):
    """{name, author, vendor, description, bank, attributes} read exactly out
    of the SoundInfoItem, the bytes the editor writes, or None when the frame
    tree is not the mapped layout. Empty strings are left out; `bank` is the
    bank chain joined with " / ", `attributes` a list of the sound's tags."""
    bounds = []
    try:
        strings = _hsin_info(data, None, bounds)
    except (Unmapped, struct.error):
        return None
    meta = {}
    for label, (off, count) in zip(_HSIN_INFO, strings):
        val = data[off + 4:off + 4 + count * 2].decode(
            "utf-16-le", errors="replace").strip()
        if val:
            meta[label] = val
    # the rest of the item (bank chain, attributes, properties) as the walk
    # reads it, so the two readers cannot drift apart
    start, end = bounds[0]
    _fields, item, _why = decode_item(108, data[start:end])
    if item.get("bankchain"):
        meta["bank"] = " / ".join(item["bankchain"])
    if item.get("attributes"):
        meta["attributes"] = list(item["attributes"])
    return meta


def _edit_hsin_string(data, index, new_value):
    """Replace the index-th SoundInfoItem string (0 name, 1 author, 2 vendor,
    3 description) and bump every enclosing size field. Returns (new_bytes, old)."""
    fields = []
    strings = _hsin_info(data, fields)
    if index >= len(strings):
        raise Unmapped("info string index out of range")
    off, count = strings[index]
    str_end = off + 4 + count * 2
    old = data[off + 4:str_end].decode("utf-16-le", "replace")
    enc = new_value.encode("utf-16-le")
    if len(enc) // 2 > _HSIN_STR_MAX:
        raise NotHeld(f"{_HSIN_INFO[index]} holds at most {_HSIN_STR_MAX:,} "
                      f"characters; got {len(enc) // 2:,}")
    new_field = struct.pack("<I", len(enc) // 2) + enc
    delta = len(new_field) - (4 + count * 2)
    out = bytearray(data)
    out[off:str_end] = new_field
    for foff, width, s0, s1 in fields:
        if s0 <= off and str_end <= s1:
            v = struct.unpack_from("<Q", data, foff)[0]
            struct.pack_into("<Q", out, foff, v + delta)
    return bytes(out), old


def edit_hsin(data, changes):
    """Edit hsin (Massive/Absynth) preset metadata: name, author, vendor,
    description. Cascades the enclosing frame-size fields. Returns
    (new_bytes, applied). EXPERIMENTAL: confirm reload in the app."""
    if not is_ni_hsin(data):
        raise ValueError("not an hsin preset")
    out, applied = bytes(data), []
    for field, value in changes.items():
        idx = _HSIN_EDIT.get(field.lower())
        if idx is None:
            raise NotHeld(f"hsin preset has no editable field {field!r}")
        out, old = _edit_hsin_string(out, idx, "" if value is None else str(value))
        applied.append((field, old, value))
    return out, applied


def is_ni_nksf(data):
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"NIKS"


# ── minimal MessagePack decoder (only what the NISI metadata map uses) ──


def _mp_str(b, pos, n):
    if pos + n > len(b):
        raise ValueError("mp str overruns")
    return b[pos:pos + n].decode("utf-8", errors="replace"), pos + n


def _mp_decode(b, pos=0, depth=0):
    if depth > 32 or pos >= len(b):
        return None, pos
    t = b[pos]
    pos += 1
    if t < 0x80:
        return t, pos                      # positive fixint
    if t >= 0xe0:
        return t - 256, pos                # negative fixint
    if 0x80 <= t <= 0x8f:
        return _mp_map(b, pos, t & 0x0F, depth)
    if 0x90 <= t <= 0x9f:
        return _mp_array(b, pos, t & 0x0F, depth)
    if 0xa0 <= t <= 0xbf:
        return _mp_str(b, pos, t & 0x1F)
    if t == 0xc0:
        return None, pos                   # nil
    if t == 0xc2:
        return False, pos
    if t == 0xc3:
        return True, pos
    if t == 0xd9:                          # str8
        return _mp_str(b, pos + 1, b[pos])
    if t == 0xda:                          # str16
        return _mp_str(b, pos + 2, struct.unpack_from(">H", b, pos)[0])
    if t == 0xdb:                          # str32
        return _mp_str(b, pos + 4, struct.unpack_from(">I", b, pos)[0])
    if t == 0xde:                          # map16
        return _mp_map(b, pos + 2, struct.unpack_from(">H", b, pos)[0], depth)
    if t == 0xdf:                          # map32
        return _mp_map(b, pos + 4, struct.unpack_from(">I", b, pos)[0], depth)
    if t == 0xdc:                          # array16
        return _mp_array(b, pos + 2, struct.unpack_from(">H", b, pos)[0], depth)
    if t == 0xdd:                          # array32
        return _mp_array(b, pos + 4, struct.unpack_from(">I", b, pos)[0], depth)
    if t == 0xcc:                          # uint8
        return b[pos], pos + 1
    if t == 0xcd:                          # uint16
        return struct.unpack_from(">H", b, pos)[0], pos + 2
    if t == 0xce:                          # uint32
        return struct.unpack_from(">I", b, pos)[0], pos + 4
    if t == 0xcf:                          # uint64
        return struct.unpack_from(">Q", b, pos)[0], pos + 8
    if t == 0xd0:                          # int8
        return struct.unpack_from(">b", b, pos)[0], pos + 1
    if t == 0xd1:                          # int16
        return struct.unpack_from(">h", b, pos)[0], pos + 2
    if t == 0xd2:                          # int32
        return struct.unpack_from(">i", b, pos)[0], pos + 4
    if t == 0xd3:                          # int64
        return struct.unpack_from(">q", b, pos)[0], pos + 8
    if t == 0xca:                          # float32
        return struct.unpack_from(">f", b, pos)[0], pos + 4
    if t == 0xcb:                          # float64
        return struct.unpack_from(">d", b, pos)[0], pos + 8
    if t in (0xc4, 0xc5, 0xc6):            # bin8 / bin16 / bin32
        w = {0xc4: 1, 0xc5: 2, 0xc6: 4}[t]
        n = int.from_bytes(b[pos:pos + w], "big")
        return bytes(b[pos + w:pos + w + n]), pos + w + n
    raise ValueError(f"unsupported msgpack type 0x{t:02x}")


def _mp_map(b, pos, count, depth):
    # every entry is at least two bytes (one per key/value), so a count larger
    # than the remaining payload is forged; without this check a crafted map32
    # header spins ~4 billion iterations (pos stops advancing at EOF)
    if count * 2 > len(b) - pos:
        raise ValueError("mp map count overruns")
    out = {}
    for _ in range(count):
        k, pos = _mp_decode(b, pos, depth + 1)
        v, pos = _mp_decode(b, pos, depth + 1)
        # MessagePack lets a key be any type, including a map or an array;
        # Python dict keys must be hashable, so `out[k] = v` raised TypeError
        # straight out of the walker on one flipped byte. NKS metadata is
        # string-keyed, so a document with a composite key is not NKS -- reject
        # it the way every other malformed shape here is rejected, which
        # parse_nksf already turns into a clean None.
        if not isinstance(k, (str, bytes, int, float, bool, type(None))):
            raise ValueError(f"mp map key is {type(k).__name__}, not a scalar")
        out[k] = v
    return out, pos


def _mp_array(b, pos, count, depth):
    # every element is at least one byte; see _mp_map
    if count > len(b) - pos:
        raise ValueError("mp array count overruns")
    out = []
    for _ in range(count):
        v, pos = _mp_decode(b, pos, depth + 1)
        out.append(v)
    return out, pos


def _mp_encode(obj):
    """Minimal MessagePack encoder (inverse of _mp_decode): dict/str/list/int/
    nil/bool. Any valid encoding decodes to the same values, so re-encoding the
    NISI map is spec-safe."""
    out = bytearray()

    def enc(o):
        if o is None:
            out.append(0xC0)
        elif o is True:
            out.append(0xC3)
        elif o is False:
            out.append(0xC2)
        elif isinstance(o, int):
            if 0 <= o < 128:
                out.append(o)
            elif -32 <= o < 0:
                out.append(o & 0xFF)
            elif 0 <= o < 256:
                out.extend(b"\xcc" + struct.pack(">B", o))
            elif -128 <= o < 128:
                out.extend(b"\xd0" + struct.pack(">b", o))
            elif 0 <= o < 65536:
                out.extend(b"\xcd" + struct.pack(">H", o))
            elif -32768 <= o < 32768:
                out.extend(b"\xd1" + struct.pack(">h", o))
            elif 0 <= o < 2 ** 32:
                out.extend(b"\xce" + struct.pack(">I", o))
            elif -2 ** 31 <= o < 2 ** 31:
                out.extend(b"\xd2" + struct.pack(">i", o))
            elif 0 <= o < 2 ** 64:
                out.extend(b"\xcf" + struct.pack(">Q", o))
            else:
                out.extend(b"\xd3" + struct.pack(">q", o))
        elif isinstance(o, float):
            out.extend(b"\xcb" + struct.pack(">d", o))
        elif isinstance(o, str):
            b = o.encode("utf-8")
            if len(b) < 32:
                out.append(0xA0 | len(b))
            elif len(b) < 256:
                out.extend((0xD9, len(b)))
            elif len(b) < 65536:
                out.extend(b"\xda" + struct.pack(">H", len(b)))
            else:
                out.extend(b"\xdb" + struct.pack(">I", len(b)))
            out.extend(b)
        elif isinstance(o, list):
            if len(o) < 16:
                out.append(0x90 | len(o))
            else:
                out.extend(b"\xdc" + struct.pack(">H", len(o)))
            for x in o:
                enc(x)
        elif isinstance(o, dict):
            if len(o) < 16:
                out.append(0x80 | len(o))
            else:
                out.extend(b"\xde" + struct.pack(">H", len(o)))
            for k, v in o.items():
                enc(k)
                enc(v)
        else:
            raise ValueError(f"cannot encode {type(o).__name__}")
    enc(obj)
    return bytes(out)


_NKSF_EDIT = {
    "name": "name", "title": "name",
    "author": "author", "creator": "author", "artist": "author",
    "vendor": "vendor",
    "comment": "comment", "description": "comment",
}


def edit_nksf(data, changes):
    """Edit the NISI MessagePack metadata in a .nksf (RIFF/NIKS): decode the
    full map, edit fields, re-encode, rewrite the RIFF with correct sizes.
    Returns (new_bytes, applied)."""
    if not is_ni_nksf(data):
        raise ValueError("not a .nksf preset")
    n = len(data)
    pos, chunks = 12, []
    while pos + 8 <= n:
        cid = data[pos:pos + 4]
        sz = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + sz > n:
            raise ValueError(f"nksf chunk {cid!r} overruns file")
        chunks.append([cid, data[pos + 8:pos + 8 + sz]])
        pos += 8 + sz + (sz & 1)
    nisi = next((c for c in chunks if c[0] == b"NISI"), None)
    if nisi is None:
        raise ValueError("no NISI chunk to edit")
    ver = struct.unpack_from("<I", nisi[1], 0)[0]
    obj, _ = _mp_decode(nisi[1][4:])
    if not isinstance(obj, dict):
        raise ValueError("NISI payload is not a map")
    applied = []
    for field, value in changes.items():
        key = _NKSF_EDIT.get(field.lower())
        if key is None:
            raise NotHeld(f"nksf has no editable field {field!r}")
        old = obj.get(key)
        obj[key] = "" if value is None else str(value)
        applied.append((field, old, obj[key]))
    nisi[1] = struct.pack("<I", ver) + _mp_encode(obj)
    out = bytearray(b"RIFF\x00\x00\x00\x00NIKS")
    for cid, payload in chunks:
        out += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            out += b"\x00"
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out), applied


def parse_nksf(data):
    """RIFF/NIKS (.nksf): decode the NISI MessagePack metadata (name, author,
    vendor, comment, device type, bankchain, and the types and characters
    as attributes), or None if not a .nksf."""
    if not is_ni_nksf(data):
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        sz = struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"NISI" and pos + 8 + sz <= len(data):
            mp = data[pos + 12:pos + 8 + sz]  # after the u32 version
            try:
                obj, _ = _mp_decode(mp)
            except (ValueError, IndexError, struct.error):
                return None
            if not isinstance(obj, dict):
                return None
            meta = {}
            for src, dst in (("name", "name"), ("author", "author"),
                             ("vendor", "vendor"), ("comment", "comment"),
                             ("deviceType", "device_type")):
                v = obj.get(src)
                if v:
                    meta[dst] = v
            bc = obj.get("bankchain")
            if isinstance(bc, list):
                bc = [str(x) for x in bc if x]
                if bc:
                    meta["bank"] = " / ".join(bc)
            # the browser's tags: `types` a list of [type, subtype] pairs,
            # `characters` a list (105 and 104 of 230 real presets carry them)
            tags = []
            types, chars = obj.get("types"), obj.get("characters")
            for t in types if isinstance(types, list) else []:
                tags += [str(x) for x in (t if isinstance(t, list) else [t]) if x]
            tags += [str(x) for x in (chars if isinstance(chars, list) else []) if x]
            if tags:
                meta["attributes"] = list(dict.fromkeys(tags))
            return meta or None
        pos += 8 + sz + (sz & 1)
    return None


_MAX_INFLATE = 8 * 1024 * 1024      # zlib output cap (decompression-bomb guard)


def _safe_inflate(chunk, maxlen=_MAX_INFLATE):
    """zlib-inflate with an output cap so a decompression bomb cannot exhaust
    memory. Returns the bytes, or None on error or if the stream exceeds the
    cap."""
    try:
        d = zlib.decompressobj()
        out = d.decompress(chunk, maxlen)
    except (zlib.error, MemoryError):
        return None
    if d.unconsumed_tail:  # more than the cap remained: refuse
        return None
    return out


# .ksd (old Absynth / KORE): the doc header is a zlib-compressed XML blob.
_KSD_FIELDS = [
    ("name", r"<doc_name>(.*?)</doc_name>"),
    ("author", r"<Author>(.*?)</Author>"),
    ("vendor", r"<Vendor>(.*?)</Vendor>"),
    ("bank", r"<Bankname>(.*?)</Bankname>"),
    ("comment", r"<Comment>(.*?)</Comment>"),
    ("plugin", r"<Plugin>(.*?)</Plugin>"),
    ("device_type", r'<DeviceType A="(.*?)"'),
    ("tempo", r'<MusicalAttr Tempo="(.*?)"'),
    ("genre", r"<Genre>(.*?)</Genre>"),
    ("key", r"<Key>(.*?)</Key>"),
]


# field -> (xml tag) for .ksd NI_DOC_HEADER editing
_KSD_EDIT = {
    "name": "doc_name", "title": "doc_name",
    "author": "Author", "creator": "Author", "artist": "Author",
    "vendor": "Vendor",
    "bank": "Bankname",
    "comment": "Comment", "description": "Comment",
}


def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def edit_ksd(data, changes):
    """Edit a .ksd (KORE/Absynth): decompress the NI_DOC_HEADER XML blob, patch
    the requested tags, re-compress, and update the cascading size fields
    (internal XML length, uncompSize, compSize). Returns (new_bytes, applied)."""
    if not is_ni_ksd(data):
        raise ValueError("not a .ksd preset")
    z = blob = comp_len = None
    attempts = 0
    for m in re.finditer(rb'\x78[\x01\x9c\xda]', data):
        attempts += 1
        if attempts > 32:
            break
        d = zlib.decompressobj()
        try:
            dec = d.decompress(data[m.start():], _MAX_INFLATE)
        except zlib.error:
            continue
        if d.unconsumed_tail:           # exceeded the cap: refuse (bomb guard)
            continue
        if b"NI_DOC_HEADER" in dec:
            z, blob = m.start(), dec
            comp_len = len(data[m.start():]) - len(d.unused_data)
            break
    if z is None:
        raise ValueError("no NI_DOC_HEADER blob found")
    xstart = blob.find(b"<?xml")
    if xstart < 0:
        raise ValueError("no XML in the doc header")
    header = blob[:xstart]
    xml = blob[xstart:].decode("utf-8", "replace")
    applied = []
    for field, value in changes.items():
        tag = _KSD_EDIT.get(field.lower())
        if tag is None:
            raise NotHeld(f".ksd has no editable field {field!r}")
        pat = re.compile(rf"(<{tag}>)(.*?)(</{tag}>)", re.S)
        mo = pat.search(xml)
        if mo is None:
            # edited in place: a tag is replaced where it stands, and where a
            # missing one would go is not mapped
            raise NotHeld(f"this .ksd has no <{tag}> tag for {field!r}; the "
                          f"editor changes a tag in place and cannot add one")
        old = mo.group(2)
        repl = _xml_escape("" if value is None else str(value))
        xml = pat.sub(lambda m2: m2.group(1) + repl + m2.group(3), xml, count=1)
        applied.append((field, old, value))
    xml_b = xml.encode("utf-8")
    new_header = bytearray(header)
    if header[:4] == b" LMX" and len(header) >= 12:  # reversed 'XML ' + ver + len
        struct.pack_into("<I", new_header, 8, len(xml_b))
    new_blob = bytes(new_header) + xml_b
    new_zlib = zlib.compress(new_blob, 9)
    out = bytearray(data)
    out[z:z + comp_len] = new_zlib
    struct.pack_into("<I", out, z - 8, len(new_zlib))   # compSize
    struct.pack_into("<I", out, z - 4, len(new_blob))   # uncompSize
    return bytes(out), applied


def parse_ksd(data):
    """Extract the metadata from a .ksd (name, author, vendor, bank, comment,
    plugin, device type, tempo/genre/key), or None if not a .ksd. The XML is
    read with regex (no XML parser, so no entity-expansion DoS)."""
    if not is_ni_ksd(data):
        return None
    xml = None
    attempts = 0
    for m in re.finditer(rb'\x78[\x01\x9c\xda]', data):  # zlib stream markers
        attempts += 1
        if attempts > 32:  # bound inflate cost on a marker-flooded file
            break
        dec = _safe_inflate(data[m.start():])
        if dec and b"NI_DOC_HEADER" in dec:
            s = dec.find(b"<?xml")
            if s >= 0:
                xml = dec[s:].decode("utf-8", errors="replace")
                break
    if xml is None:
        return None
    meta = {}
    for label, pat in _KSD_FIELDS:
        mm = re.search(pat, xml, re.S)
        if mm:
            v = mm.group(1).strip()
            if v and v.lower() != "not set" and not (label == "tempo" and v == "0"):
                meta[label] = v
    return meta or None


# the app ids item 101 names, each checked against the product the preset's
# own bank chain starts with
_APP_IDS = {6: "Absynth", 7: "Massive"}


def _hex_float(s):
    """A float written as its eight IEEE-754 hex digits, `42F00000` for 120,
    or None."""
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", s or ""):
        return None
    return struct.unpack(">f", bytes.fromhex(s))[0]


def hsin_meta(items):
    """The preset's metadata from its decoded items ({item id: meta}, as
    decode_item gives them): name, author, vendor, description, product,
    version, bank, attributes, tempo, device type, and the names of a
    Reaktor ensemble's snapshots. Empty when no item says anything."""
    info, app = items.get(108, {}), items.get(101, {})
    meta = {k: info[k] for k in _HSIN_INFO if info.get(k)}
    chain = info.get("bankchain") or []
    if chain and chain[0] in _APP_NAMES:
        meta["product"] = chain[0]
    elif app.get("app_id") in _APP_IDS:
        meta["product"] = _APP_IDS[app["app_id"]]
    if app.get("version"):
        meta["version"] = app["version"]
    if chain:
        meta["bank"] = " / ".join(chain)
        meta["bankchain"] = list(chain)
    if info.get("attributes"):
        meta["attributes"] = list(info["attributes"])
    if info.get("device_type"):
        meta["device_type"] = info["device_type"]
    tempo = _hex_float((info.get("properties") or {}).get("\\@tempo"))
    if tempo:
        meta["tempo"] = f"{tempo:g}"
    if items.get("snapshots"):
        meta["snapshots"] = list(items["snapshots"])
    return meta


# a SoundInfoItem this shallow describes the preset: root > Authoring
# Application > SoundInfoItem in every Massive and Absynth preset measured.
# A Reaktor ensemble has none there; it holds one per snapshot, five or more
# frames down (4,821 of them in 139 of 223 ensembles, named "Untitled
# Snapshot 1", "snap 1b", "INIT"), and the first is not the ensemble's name
_PRESET_INFO_DEPTH = 2


def collect_meta(items, item_id, depth, meta):
    """File one decoded item's meta into `items` ({item id: meta}): the first
    of each item id, except that a SoundInfoItem below _PRESET_INFO_DEPTH adds
    its name to items["snapshots"] instead."""
    if item_id == 108 and depth > _PRESET_INFO_DEPTH:
        if meta.get("name"):
            items.setdefault("snapshots", []).append(meta["name"])
        return
    items.setdefault(item_id, meta)


def _tree_items(data):
    """{item id: meta} for the items of the hsin tree in `data` that carry
    metadata, or None when the tree does not read. `data` may be the first
    bytes of a longer file: a tree its end cuts short gives what it holds."""
    root = struct.unpack_from("<Q", data, 0)[0] if len(data) >= 8 else 0
    end = max(len(data), root)
    frames, problem = read_frames(lambda o, n: data[o:o + n], 0, end, 1 << 16)
    if not frames or (problem is not None and end == len(data)):
        return None
    items = {}
    for fr in frames:
        for rec in fr["items"]:
            iid = rec["item_id"]
            s, e = rec["own"]
            if iid in (101, 108) and rec["domain"] == "DSIN" and e <= len(data):
                _fields, meta, _why = decode_item(iid, data[s:e])
                collect_meta(items, iid, fr["depth"], meta)
    return items


def parse_hsin(data):
    """The preset metadata of an hsin container ({name, author, vendor,
    description, product, version, bank, attributes, tempo}), or None if the
    bytes are not one. A tree that reads whole is read item by item, and one
    with no SoundInfoItem has no metadata: {}. Only a tree that does not read
    is scanned for its strings."""
    if not is_ni_hsin(data):
        return None
    items = _tree_items(data)
    if items is not None:
        # a whole tree says what it holds; the string scan below took a
        # Reaktor ensemble's own bytes for a name, author and version
        return hsin_meta(items)
    meta = {}
    strings = _u16le_pascals(data)
    app_off = version_off = None
    for off, s in strings:
        if s in _APP_NAMES:
            meta["product"], app_off = s, off
            break
    for off, s in strings:
        if _VERSION_RE.match(s):
            meta["version"], version_off = s, off
            break
    # the SoundInfoItem read exactly, through the same frame walk the editor
    # uses, when the tree is one it maps. The scan below caps a string at 256
    # units to keep noise out, so a longer value the editor wrote (500
    # characters of description) was not shown -- and the scan then took a
    # run of the item's own bytes for the name
    exact = read_hsin_info(data)
    if exact is not None:
        meta.update(exact)
        return meta
    # the SoundInfoItem name is the FIRST alphabetic, non-version pascal string
    # after the version; author/vendor/description are the fields right after it
    # (positional, so they read correctly even when the name field is populated).
    name_end = None
    for off, s in strings:
        if version_off is not None and off <= version_off:
            continue
        if app_off is not None and off >= app_off:
            break
        if s == meta.get("version") or s in _APP_NAMES:
            continue
        if any(ch.isalpha() for ch in s):
            meta["name"] = s
            name_end = off + 4 + len(s) * 2
            break
    if name_end is not None:
        pos = name_end
        for label in ("author", "vendor", "description"):
            if (app_off is not None and pos >= app_off) or pos + 4 > len(data):
                break
            c = struct.unpack_from("<I", data, pos)[0]
            if c > 256 or pos + 4 + c * 2 > len(data):
                break
            val = data[pos + 4:pos + 4 + c * 2].decode(
                "utf-16-le", errors="replace").strip()
            if val:
                meta[label] = val
            pos += 4 + c * 2
    return meta
