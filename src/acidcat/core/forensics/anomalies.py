"""Structural forensic checks for `inspect --anomalies`.

Pure header math on what the walker already decoded, plus a bounded tail scan.
Reports: the walker's own lint warnings, trailing data past the declared
container end, an appended second-format magic (polyglot detection), and control
bytes smuggled into text fields. No sample-data analysis (that is the deferred
`deep` tier). Findings are {severity, offset, rule, message}; severity is one of
alert > warn > notice.

Framed as detection, not exploitation: acidcat says what looks off and where.
"""

import os
import struct

from acidcat.core.infra.fieldcodec import _field_abs
from acidcat.core.primitives.signal import byte_entropy
from acidcat.core.primitives.notes import is_coverage

# second-format magics worth flagging when appended after an audio container
_MAGICS = [
    (b"PK\x03\x04", "ZIP local header"),
    (b"PK\x05\x06", "ZIP end-of-central-directory"),
    (b"%PDF", "PDF"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\x7fELF", "ELF"),
    (b"Rar!\x1a\x07", "RAR"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip"),
    (b"\xfd7zXZ\x00", "XZ"),
]

# info ranks below notice on purpose: it carries the coverage notes, which say
# what this run did not look at rather than anything about the file, so they
# belong at the bottom of a findings list. Stated rather than left to the
# .get(..., 0) default every sort site happens to use.
_SEVERITY = {"alert": 3, "warn": 2, "notice": 1, "info": 0}

# ID3 frames that legitimately repeat (so duplicates are not suspicious), plus
# the synthetic header fields inspect emits for the tag itself.
_ID3_REPEATABLE = {"TXXX", "WXXX", "APIC", "PIC", "PRIV", "GEOB", "COMM", "UFID",
                   "USLT", "SYLT", "WCOM", "WOAR", "WXXX", "version", "flags",
                   "tag_size"}

# spec-ignorable regions: content there is a classic smuggling spot
_CAVITY = {"PADDING": "FLAC PADDING", "FREE": "MP4 free box", "SKIP": "MP4 skip box",
           "JUNK": "RIFF JUNK chunk", "PAD": "RIFF PAD chunk"}

# cavities whose reported size is the payload length (RIFF/FLAC), vs MP4 boxes
# whose size includes the 8-byte header
_CAVITY_PAYLOAD_SIZE = {"PADDING", "JUNK", "PAD"}

# Complete audio containers, for the embedded-media check. Deliberately NOT
# images: cover art inside a tag is ordinary and flagging it would train people
# to ignore the rule. An audio file inside an audio file is not ordinary.
_EMBEDDED_MEDIA = [
    (b"RIFF", 8, b"WAVE", "WAV"),
    (b"FORM", 8, b"AIFF", "AIFF"),
    (b"FORM", 8, b"AIFC", "AIFF-C"),
    (b"fLaC", 0, b"", "FLAC"),
    (b"OggS", 0, b"", "Ogg"),
]

# Top-level keys a JSON-backed preset is known to carry. A key outside this set
# is either somewhere to hide bytes or a sign the format moved and this decoder
# did not -- both worth saying out loud, neither worth an alert.
# keyed by sniff id, not by display label, for the reason given at _OGG_IDS
_JSON_PRESET_KEYS = {
    "vital": {"author", "comments", "macro1", "macro2", "macro3", "macro4",
              "preset_name", "preset_style", "settings", "synth_version"},
}


def _entropy_note(blob):
    """A short characterization for a suspicious blob, or '' when unremarkable.
    Only fires on a payload-sized region so a few random bytes don't cry wolf."""
    if len(blob) < 64:
        return ""
    h = byte_entropy(blob)
    if h >= 7.2:
        return f"; entropy {h:.1f}/8 (encrypted or compressed payload)"
    return ""


def _json_object_end(data):
    """Index just past the top-level JSON object, or None.

    Brace counting has to respect strings and escapes: a preset whose comment
    contains a brace would otherwise report the object ending early, and every
    byte after it as trailing data.
    """
    start = data.find(b"{")
    if start < 0:
        return None
    depth, i, instr, esc = 0, start, False, False
    while i < len(data):
        ch = data[i]
        if instr:
            if esc:
                esc = False
            elif ch == 0x5C:          # backslash
                esc = True
            elif ch == 0x22:          # closing quote
                instr = False
        elif ch == 0x22:
            instr = True
        elif ch == 0x7B:              # {
            depth += 1
        elif ch == 0x7D:              # }
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _json_top_level_keys(data, end):
    """Top-level keys of the object, without parsing the whole document.

    json.loads on a 100 MB preset builds the entire tree to answer a question
    about its first level, and a forged one is exactly where that is a bad
    trade.
    """
    keys = []
    depth, i, instr, esc, cur, in_key = 0, 0, False, False, [], False
    while i < end and i < len(data):
        ch = data[i]
        if instr:
            if esc:
                esc = False
            elif ch == 0x5C:
                esc = True
            elif ch == 0x22:
                instr = False
                if in_key and depth == 1:
                    keys.append(bytes(cur).decode("utf-8", "replace"))
                in_key = False
                cur = []
            elif in_key:
                cur.append(ch)
        elif ch == 0x22:
            instr = True
            in_key = depth == 1
            cur = []
        elif ch in (0x7B, 0x5B):      # { [
            depth += 1
        elif ch in (0x7D, 0x5D):      # } ]
            depth -= 1
        i += 1
    # a value string at depth 1 is captured too; keep only those a colon follows
    out = []
    for k in keys:
        probe = b'"' + k.encode("utf-8", "replace") + b'"'
        at = data.find(probe)
        while at >= 0:
            j = at + len(probe)
            while j < len(data) and data[j] in (0x20, 0x09, 0x0A, 0x0D):
                j += 1
            if j < len(data) and data[j] == 0x3A:     # colon
                out.append(k)
                break
            at = data.find(probe, at + 1)
    return out


def _find_embedded(filepath, candidates, own, window=1 << 20):
    """{magic: offset} for each complete container found inside a file.

    One pass for every magic, not one pass each: the I/O is the cost, and
    searching a handful of short patterns inside a window already in memory is
    free next to reading the file again. A clean file -- no embedded container,
    the common case -- used to be read once per magic.

    Windowed rather than slurped. The first version read 16 MB, which put
    `audit`'s peak over the bound its memory test enforces and stopped scanning
    at the cap without saying so. The overlap is the longest thing a match
    needs, so a container straddling a boundary is still seen.
    """
    pending = list(candidates)
    keep = max((len(m) + fa + 4) for m, fa, _f, _n in pending) if pending else 0
    out = {}
    with open(filepath, "rb") as fh:
        base = 0
        prev = b""
        while pending:
            chunk = fh.read(window)
            if not chunk:
                break
            buf = prev + chunk
            start_abs = base - len(prev)
            still = []
            for magic, form_at, form, name in pending:
                at = _match_in(buf, start_abs, magic, form_at, form, own)
                if at is None:
                    still.append((magic, form_at, form, name))
                else:
                    out[magic] = at
            pending = still
            base += len(chunk)
            prev = buf[-keep:] if len(buf) > keep else buf
    return out


def _match_in(buf, start_abs, magic, form_at, form, own):
    """Absolute offset of the first real match in this window, or None."""
    off = 0
    while True:
        at = buf.find(magic, off)
        if at < 0:
            return None
        abs_at = start_abs + at
        off = at + 1
        if abs_at <= 0:                       # the file's own header
            continue
        if magic == own and abs_at < 16:
            continue
        if form:
            if at + form_at + 4 > len(buf):
                return None                    # resolve on the next window
            if buf[at + form_at:at + form_at + 4] != form:
                continue
        return abs_at


def _declared_end(head):
    """The offset a conformant reader stops at, from the container's size field.
    None when the header does not give a usable total size, so the caller falls
    back to the walker's parsed coverage. RF64/BW64 store 0xFFFFFFFF in the RIFF
    size field as a sentinel (the real size lives in ds64, which the walker
    already resolves into the chunk sizes); a plain RIFF can carry the same
    sentinel for a streamed file -- either way, trust parsed coverage there or
    the trailing-data and appended-magic scans silently never run."""
    if len(head) >= 8 and head[:4] in (b"RIFF", b"RF64"):
        n = struct.unpack_from("<I", head, 4)[0]
        return None if n == 0xFFFFFFFF else 8 + n
    if len(head) >= 8 and head[:4] == b"FORM":
        return 8 + struct.unpack_from(">I", head, 4)[0]
    return None


def _rf64_end(filepath):
    """True container end for an RF64/BW64 file from the ds64 chunk's 64-bit
    riffSize, or None if ds64 isn't at its spec position. 8 + riffSize."""
    try:
        with open(filepath, "rb") as f:
            head = f.read(28)
        if len(head) >= 28 and head[12:16] == b"ds64":
            return 8 + struct.unpack_from("<Q", head, 20)[0]
    except OSError:
        pass
    return None


_MAGIC_BLOCK = 1 << 20            # bytes held at once; the region may be huge


def _scan_magics(filepath, start, size):
    """Yield (magic, label, absolute_offset) for every known magic in
    [start, size), reading in blocks.

    This used to read the first megabyte and search that, while the
    accompanying finding announced the FULL trailing size -- so a PDF appended
    3 MB past the container end was reported as 3 MB of trailing data and no
    polyglot, with nothing saying the search had stopped at 1 MB. The region
    can be arbitrarily large, so it is streamed rather than either truncated or
    slurped; blocks overlap by the longest magic so one straddling a boundary
    is still found.
    """
    overlap = max(len(m) for m, _ in _MAGICS) - 1
    seen = set()
    with open(filepath, "rb") as f:
        f.seek(start)
        pos = start
        carry = b""
        while pos < size:
            block = f.read(_MAGIC_BLOCK)
            if not block:
                break
            window = carry + block
            base = pos - len(carry)
            for magic, label in _MAGICS:
                at = window.find(magic)
                while at >= 0:
                    absolute = base + at
                    if (magic, absolute) not in seen:
                        seen.add((magic, absolute))
                        yield magic, label, absolute
                    at = window.find(magic, at + 1)
            pos += len(block)
            carry = window[-overlap:] if overlap else b""


# Format families, keyed by sniff id rather than by the walker's display label.
#
# These checks used to branch on the prose label -- fmt_label.startswith("Ogg"),
# "AIFF" in fmt_label -- which made the wording of a display string
# load-bearing: renaming a label would silently switch detection off, with no
# test to notice and nothing in the label's own definition hinting that anything
# depended on it. Ids are the stable name; labels are for humans.
_OGG_IDS = frozenset({"ogg", "oga", "opus", "spx"})
_MP4_IDS = frozenset({"mp4", "m4a", "m4b", "mov"})
_RIFF_IDS = frozenset({"wav", "rf64", "bwf", "w64", "rmid", "wave"})
_IFF_IDS = frozenset({"aiff", "aifc", "aif", "8svx"})
_CHUNKED_IDS = _RIFF_IDS | _IFF_IDS


def scan(filepath, fmt_label=None, chunks=None, warns=None):
    """Return a list of forensic findings for a file.

    Pass just the path and the file is walked for you. Pass `chunks` (and
    `warns`) when you have already walked it, to avoid a second walk. A file
    acidcat cannot structurally parse is still scanned for the byte-level tells
    (appended data, polyglot magic) against empty chunks rather than refused.

    `fmt_label` is accepted for compatibility and ignored: dispatch below uses
    the sniff id instead -- see the note on the id sets above.
    """
    if chunks is None:
        from acidcat.core.walk import Unsupported, walk_file
        try:
            _label, chunks, warns = walk_file(filepath)
        except Unsupported:
            chunks, warns = [], []
    from acidcat.core.infra import sniff as _sniff
    try:
        fmt_id = _sniff.sniff(filepath)
    except Exception:
        fmt_id = None

    findings = []
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(16)

    # 1. surface the walker's structural lint (size overruns, forged counts, ...)
    #
    # A warning saying our own walk stopped early is not lint. It is reported,
    # because a partial answer presented as a whole one is the defect this
    # release is named for -- but as `info`/`coverage`, so it does not read as
    # the file having something to answer for and does not drive an exit code.
    # The kind travels on the warning; matching the message text would make its
    # wording load-bearing across a module boundary.
    #
    # Note the classification happens BEFORE the chunk id is prefixed below:
    # string formatting returns a plain str and drops the kind.
    for w in warns or []:
        cov = is_coverage(w)
        findings.append({"severity": "info" if cov else "warn", "offset": 0,
                         "rule": "coverage" if cov else "structure",
                         "message": w})
    for c in chunks:
        for w in c.get("warnings") or []:
            cov = is_coverage(w)
            findings.append({"severity": "info" if cov else "warn",
                             "offset": c.get("offset", 0) or 0,
                             "rule": "coverage" if cov else "structure",
                             "message": f"{str(c.get('id', '?')).strip()}: {w}"})

    # 2. trailing data past the DECLARED container end, and a tail magic scan.
    # Use the container's own size field, not the walker's parsed coverage (the
    # walker mis-reads appended bytes as a bogus chunk, a noisy boundary).
    end = _declared_end(head)
    if end is None and head[:4] in (b"RF64", b"BW64"):
        # RF64/BW64 put 0xFFFFFFFF in the RIFF size and the true 64-bit size in
        # the ds64 chunk (offset 12: 'ds64' + u32 size + riffSize u64 at 20).
        # Without this the trailing/polyglot scan never runs on any RF64 file
        # (and the parsed-coverage fallback is poisoned by the mis-parsed
        # appended bytes, which the walker reads as a giant bogus chunk).
        end = _rf64_end(filepath)
    if end is None:
        # The EXTENT, not offset+size. `size` is the payload, so for any walker
        # that states a payload_base this lands short by the header of the last
        # chunk and invents trailing data that is really the chunk's own header.
        # Measured on a 96-byte amxd: offset+size says the container ends at 88.
        #
        # Read through geometry.extent_of rather than re-deriving the rule, for
        # the reason the geometry module's own tests give: a consumer carrying
        # its own copy keeps disagreeing after the copy it disagrees with is
        # fixed. This module predates the extent/payload split and had never
        # been moved onto it.
        from acidcat.core.infra import geometry as _geom

        ends = []
        for c in chunks:
            if not isinstance(c.get("offset"), int):
                continue
            eoff, elen = _geom.extent_of(c)
            if isinstance(eoff, int) and isinstance(elen, int):
                ends.append(eoff + elen)
            elif isinstance(c.get("size"), int):
                ends.append(c["offset"] + c["size"])
        end = max(ends, default=0)
    if isinstance(end, int) and 0 < end < size:
        findings.append({"severity": "notice", "offset": end, "rule": "trailing_data",
                         "message": f"{size - end:,} bytes past the declared "
                                    f"container end"})
        for magic, label, at in _scan_magics(filepath, end, size):
            findings.append({"severity": "alert", "offset": at,
                             "rule": "polyglot",
                             "message": f"possible polyglot: {label} magic at "
                                        f"0x{at:08x}, past the container end"})

    # 3. control bytes in decoded text fields (smuggling in "text")
    for c in chunks:
        for fl in c.get("fields") or []:
            v = fl.get("value")
            if isinstance(v, str) and v:
                ctrl = sum(1 for ch in v if ord(ch) < 9 or 13 < ord(ch) < 32)
                if ctrl >= 2:
                    # a finding's offset is absolute; the field's is relative
                    # to its chunk's payload
                    at = _field_abs(c, fl)
                    findings.append({
                        "severity": "notice", "offset": at if at is not None else 0,
                        "rule": "nonprintable_text",
                        "message": f"{str(c.get('id', '?')).strip()}/{fl.get('name')}: "
                                   f"{ctrl} control bytes in a text field"})

    # 4. duplicate non-repeatable ID3 frames (a tag-smuggling / bad-tooling tell)
    for c in chunks:
        if "ID3" not in str(c.get("id", "")):
            continue
        counts = {}
        for fl in c.get("fields") or []:
            nm = fl.get("name")
            if nm and nm not in _ID3_REPEATABLE:
                counts[nm] = counts.get(nm, 0) + 1
        for nm, k in counts.items():
            if k > 1:
                findings.append({"severity": "notice", "offset": c.get("offset", 0) or 0,
                                 "rule": "duplicate_frame",
                                 "message": f"ID3 frame {nm} appears {k} times "
                                            f"(should be unique)"})

    # 5. non-zero content in a spec-ignorable padding / free region (dead space
    # that is supposed to be zeros: content there is a hiding spot)
    for c in chunks:
        cid = str(c.get("id", "")).strip().upper()
        label = _CAVITY.get(cid)
        if not label:
            continue
        base = c.get("payload_base")
        if base is None and cid in ("JUNK", "PAD"):
            # RIFF chunk data always starts 8 bytes past the chunk header
            base = (c.get("offset") or 0) + 8
        if base is None:
            continue
        if cid in _CAVITY_PAYLOAD_SIZE:
            clen = c.get("size") or 0
        else:  # MP4 box: size includes the header, base is past it
            clen = (c.get("size") or 0) - (base - (c.get("offset") or 0))
        if clen <= 0:
            continue
        # small non-zero JUNK/PAD is routine DAW metadata (cue points, timestamps);
        # calibrated on 2328 real WAVs it topped out at 641 bytes, so only a
        # payload-sized run (>= 1 KB) is a plausible cavity worth flagging.
        if cid in ("JUNK", "PAD") and clen < 1024:
            continue
        with open(filepath, "rb") as f:
            f.seek(base)
            blob = f.read(min(clen, 1 << 20))
        if any(blob):
            findings.append({"severity": "notice", "offset": base, "rule": "cavity_content",
                             "message": f"non-zero bytes in {label} ({clen:,} bytes); "
                                        f"this region is spec'd to be ignorable"
                                        f"{_entropy_note(blob)}"})

    # 6. FLAC APPLICATION block: 4-byte id + arbitrary freeform data
    for c in chunks:
        if str(c.get("id", "")).strip().upper() == "APPLICATION":
            note = ""
            base = c.get("payload_base")
            if isinstance(base, int):
                with open(filepath, "rb") as f:
                    f.seek(base + 4)             # past the 4-byte application id
                    note = _entropy_note(f.read(min((c.get("size", 0) or 0) - 4,
                                                    1 << 20)))
            findings.append({"severity": "notice", "offset": c.get("offset", 0) or 0,
                             "rule": "application_block",
                             "message": f"FLAC APPLICATION block "
                                        f"({c.get('size', 0):,} bytes of freeform data)"
                                        f"{note}"})

    # 7. universal appended-ZIP scan: a ZIP end-of-central-directory near EOF
    # means an archive was appended, even to formats with no total-size header
    # (mp3/flac/ogg polyglots, where the audio run absorbs the tail so the
    # size-based check above cannot see it). Scans the last 64K+ from the end.
    if not any(f["rule"] == "polyglot" for f in findings):
        with open(filepath, "rb") as f:
            f.seek(max(0, size - 66000))
            tail = f.read()
        idx = tail.rfind(b"PK\x05\x06")
        if idx >= 0 and len(tail) - idx >= 22:
            findings.append({"severity": "alert", "offset": (size - len(tail)) + idx,
                             "rule": "polyglot",
                             "message": "possible polyglot: appended ZIP archive "
                                        "(end-of-central-directory record near EOF)"})

    # 8. Ogg: multiple logical bitstreams. A conformant single-track Ogg has one
    # BOS page; more than one distinct BOS serial means several logical streams
    # multiplexed into one file, a place to carry content most players never
    # surface (a Vorbis-only player hears only its stream, etc.).
    if fmt_id in _OGG_IDS:
        try:
            from acidcat.core.formats import ogg as _ogg
            with open(filepath, "rb") as f:
                # clamped: read(N) pre-allocates N bytes (see core/midi.py)
                ogg_data = f.read(min(16 * 1024 * 1024,
                                      os.path.getsize(filepath)))
            serials = {pg["serial"] for pg in _ogg.iter_pages(ogg_data)
                       if pg["header_type"] & 0x02}
            if len(serials) > 1:
                findings.append({"severity": "notice", "offset": 0,
                                 "rule": "ogg_multistream",
                                 "message": f"{len(serials)} logical bitstreams in "
                                            f"one Ogg; a single-codec player surfaces "
                                            f"only one (possible hidden stream)"})
        except Exception as e:
            # a rule that crashed is not a rule that found nothing:
            # swallowing this made a malformed structure look clean
            findings.append({"severity": "warn", "offset": None,
                             "rule": "check_failed",
                             "message": f"the ogg_multistream check could not run ({type(e).__name__}); this file was NOT screened for it"})

    # 9. MP4 mdat coverage gap: bytes inside mdat that no sample references. Sum
    # every stsz sample size across tracks and compare to the mdat payload; an
    # unreferenced run is a cavity (a payload appended at mdat's tail with only
    # the box size grown, so no chunk offset points at it and the audio is intact).
    if fmt_id in _MP4_IDS:
        try:
            from acidcat.core.formats import mp4 as _mp4
            fsz = os.path.getsize(filepath)
            # header-only top-level scan: total mdat payload, and locate moov,
            # which on non-faststart files (most ffmpeg/Apple output) sits at EOF,
            # past any fixed read window. reads 8-16 bytes per top-level box.
            mdat_payload = 0
            moov_off = moov_size = None
            with open(filepath, "rb") as f:
                pos = 0
                while pos + 8 <= fsz:
                    f.seek(pos)
                    hdr = f.read(8)
                    if len(hdr) < 8:
                        break
                    # `box` and not `size`: the enclosing scope already binds
                    # `size` to the FILE's length, and this loop was assigning
                    # a box header to it. Nothing downstream read it until a
                    # rule was added after this one, which then accounted a
                    # 254 KB file against a 3.5 KB box and reported that the
                    # structure explained 1% of it. A loop variable that
                    # shadows the file size is a trap armed for whoever comes
                    # next.
                    box, hlen = struct.unpack(">I", hdr[:4])[0], 8
                    if box == 1:
                        ext = f.read(8)
                        if len(ext) < 8:
                            break
                        box, hlen = struct.unpack(">Q", ext)[0], 16
                    elif box == 0:
                        box = fsz - pos
                    if box < hlen or pos + box > fsz:
                        break
                    if hdr[4:8] == b"mdat":
                        mdat_payload += box - hlen
                    elif hdr[4:8] == b"moov":
                        moov_off, moov_size = pos, box
                    pos += box
            if moov_off is not None and mdat_payload > 0:
                with open(filepath, "rb") as f:
                    f.seek(moov_off)
                    moov = f.read(min(moov_size, 16 * 1024 * 1024))
                sample_bytes = total_count = 0
                for b in _mp4.iter_boxes(moov):
                    if b["type"] == b"stsz" and not b["truncated"] and not b["beyond_cap"]:
                        p = b["offset"] + b["hdr"]
                        if p + 12 <= len(moov):
                            ssize = struct.unpack_from(">I", moov, p + 4)[0]
                            scount = struct.unpack_from(">I", moov, p + 8)[0]
                            total_count += scount
                            if ssize:
                                sample_bytes += ssize * scount
                            else:
                                q = p + 12
                                for _ in range(min(scount, (len(moov) - q) // 4)):
                                    sample_bytes += struct.unpack_from(">I", moov, q)[0]
                                    q += 4
                gap = mdat_payload - sample_bytes
                # require samples actually described (fragmented/DASH stsz has
                # count 0 with the samples in moof/trun, not a cavity), and only a
                # payload-sized run (small gaps are legit alignment/edit padding).
                if total_count > 0 and gap > 1024:
                    findings.append({"severity": "notice", "offset": 0,
                                     "rule": "mp4_mdat_coverage",
                                     "message": f"{gap:,} bytes in mdat referenced by no "
                                                f"sample (stsz sums {sample_bytes:,} of "
                                                f"{mdat_payload:,}); possible cavity"})
        except Exception as e:
            # a rule that crashed is not a rule that found nothing:
            # swallowing this made a malformed structure look clean
            findings.append({"severity": "warn", "offset": None,
                             "rule": "check_failed",
                             "message": f"the mp4_mdat_coverage check could not run ({type(e).__name__}); this file was NOT screened for it"})

    # 10. ID3v2 non-zero padding: the region after the last frame up to the tag's
    # declared size is spec'd to be zero; content there is a cavity (not trailing
    # data, since it is inside the tag's own length).
    if fmt_id == "mp3" and head[:3] == b"ID3":
        try:
            with open(filepath, "rb") as f:
                th = f.read(10)
                ver, flags = th[3], th[5]
                tag_size = (((th[6] & 0x7F) << 21) | ((th[7] & 0x7F) << 14)
                            | ((th[8] & 0x7F) << 7) | (th[9] & 0x7F))
                body = f.read(tag_size)
            # whole-tag unsynchronisation (v2.2/2.3) escapes $FF00; de-escape
            # before reading sizes.
            if flags & 0x80 and ver != 4:
                body = body.replace(b"\xff\x00", b"\xff")
            pos, pad_start = 0, tag_size
            # skip the extended header (flag 0x40); its zero size bytes would
            # otherwise be misread as the start of padding at offset 0.
            if flags & 0x40 and ver != 2 and len(body) >= 4:
                if ver == 4:
                    ext = (((body[0] & 0x7F) << 21) | ((body[1] & 0x7F) << 14)
                           | ((body[2] & 0x7F) << 7) | (body[3] & 0x7F))
                else:
                    ext = struct.unpack(">I", body[0:4])[0] + 4
                if 0 < ext <= len(body):
                    pos = ext
            while pos + 10 <= len(body):
                if body[pos] == 0:                      # a null frame id = padding
                    pad_start = pos
                    break
                if ver == 4:                            # v2.4 syncsafe frame size
                    fsz = (((body[pos + 4] & 0x7F) << 21) | ((body[pos + 5] & 0x7F) << 14)
                           | ((body[pos + 6] & 0x7F) << 7) | (body[pos + 7] & 0x7F))
                else:
                    fsz = struct.unpack_from(">I", body, pos + 4)[0]
                pos += 10 + fsz
                if pos > len(body):
                    break
            pad = body[pad_start:]
            if any(pad):
                findings.append({"severity": "notice", "offset": 10 + pad_start,
                                 "rule": "id3_padding_nonzero",
                                 "message": f"non-zero bytes in ID3v2 padding "
                                            f"({len(pad):,} bytes after the last frame); "
                                            f"the padding region is spec'd to be zero"
                                            f"{_entropy_note(pad)}"})
        except Exception as e:
            # a rule that crashed is not a rule that found nothing:
            # swallowing this made a malformed structure look clean
            findings.append({"severity": "warn", "offset": None,
                             "rule": "check_failed",
                             "message": f"the id3_padding check could not run ({type(e).__name__}); this file was NOT screened for it"})

    # 11. dual-endianness audio: 16-bit PCM engineered so BOTH the little-endian
    # and big-endian readings are structured audio (a WAV/AIFF twin that plays a
    # different sound each way). real audio is structured one endianness, noise
    # the other; both structured is the crafted-artifact tell.
    if fmt_id in ({"wav", "rf64", "bwf", "wave"} | _IFF_IDS):
        try:
            from acidcat.core.forensics import lsb as _lsb
            de = _lsb.dual_endian(filepath, fmt_label, chunks)
            if de and de["flagged"]:
                findings.append({"severity": "notice", "offset": 0,
                                 "rule": "dual_endianness",
                                 "message": f"both endian readings of the 16-bit PCM "
                                            f"are structured (LE autocorr {de['le']:.2f}, "
                                            f"BE {de['be']:.2f}); a cross-endian "
                                            f"audio+audio artifact"})
        except Exception as e:
            # a rule that crashed is not a rule that found nothing:
            # swallowing this made a malformed structure look clean
            findings.append({"severity": "warn", "offset": None,
                             "rule": "check_failed",
                             "message": f"the dual_endian_pcm check could not run ({type(e).__name__}); this file was NOT screened for it"})

    # 12. RIFF/AIFF odd-chunk pad byte non-zero. The spec requires a single $00
    # pad after any odd-sized chunk; the walker skips it without checking. A
    # non-zero pad after chunks is a low-bandwidth covert channel invisible to
    # every conformant reader (it lands in nobody's payload).
    if fmt_id in _CHUNKED_IDS:
        stego = []
        with open(filepath, "rb") as f:
            for c in chunks:
                csz = c.get("size")
                coff = c.get("offset")
                if not isinstance(csz, int) or not isinstance(coff, int) or csz % 2 == 0:
                    continue
                pad_off = coff + 8 + csz
                if pad_off >= size:
                    continue
                f.seek(pad_off)
                if f.read(1) not in (b"\x00", b""):
                    stego.append(pad_off)
        if stego:
            findings.append({"severity": "warn", "offset": stego[0],
                             "rule": "nonzero_pad",
                             "message": f"non-zero pad byte after {len(stego)} "
                                        f"odd-sized chunk(s); the alignment pad is "
                                        f"spec'd to be zero (covert-channel tell)"})

    # 13. duplicate structural chunks. A second fmt/data/smpl/ds64/COMM/SSND is a
    # parser-ambiguity / smuggling tell (readers disagree on which one wins).
    # LIST/JUNK/labl and the like legitimately repeat, so only the unique-by-spec
    # ids are checked.
    _UNIQUE_CHUNKS = {"fmt ", "data", "smpl", "fact", "acid", "ds64", "cue ",
                      "COMM", "SSND", "FVER", "INST", "MARK"}
    seen_ids = {}
    for c in chunks:
        cid = str(c.get("id", ""))
        if cid in _UNIQUE_CHUNKS:
            seen_ids[cid] = seen_ids.get(cid, 0) + 1
    for cid, k in seen_ids.items():
        if k > 1:
            findings.append({"severity": "warn", "offset": 0,
                             "rule": "duplicate_chunk",
                             "message": f"chunk {cid.strip()!r} appears {k} times; "
                                        f"spec allows one (reader-ambiguity tell)"})

    # 14. an APEv2 tag on a non-MP3 file: an unusual metadata carrier. (A
    # prepended ID3v2 wrapping a non-MP3 is handled by inspect's dispatch, which
    # reports it before the walk; the walker refuses such files, so the scanner
    # never sees them.)
    if size >= 32 and fmt_id != "mp3":
        with open(filepath, "rb") as f:
            f.seek(size - 32)
            if f.read(8) == b"APETAGEX":
                findings.append({"severity": "notice", "offset": size - 32,
                                 "rule": "wrong_format_tag",
                                 "message": "an APEv2 tag sits on a non-MP3 file "
                                            "(unusual metadata carrier)"})

    # 15. a complete audio container living INSIDE another one. Distinct from
    # polyglot (appended past the declared end) and cavity_content (bytes in a
    # spec-ignorable region): here the region is legitimately occupied and
    # nothing about the size or the structure looks wrong, which is what makes
    # it worth saying. Images are excluded on purpose -- cover art inside a tag
    # is ordinary, and flagging it teaches people to ignore the rule.
    try:
        with open(filepath, "rb") as f:
            own = f.read(4)
        # Ogg stamps OggS on EVERY page, so page 2 of any Ogg looks like an
        # embedded Ogg -- six of six real Ogg/Opus files tripped this before the
        # guard. The genuine case, several logical bitstreams in one file, is
        # what ogg_multistream above is for.
        # the id alone decides. "fmt_label and" was left over from when this
        # branched on the label, and it made the guard depend on the label being
        # non-empty: a walker that returned "" got a spurious embedded-Ogg
        # finding on every ordinary Ogg file.
        wanted = [c for c in _EMBEDDED_MEDIA
                  if not (c[0] == b"OggS" and fmt_id in _OGG_IDS)]
        hits = _find_embedded(filepath, wanted, own) if wanted else {}
        for magic, _form_at, _form, name in wanted:
            at = hits.get(magic)
            if at is None:
                continue
            holder = None
            for c in chunks:
                base = c.get("offset") or 0
                if base <= at < base + (c.get("size") or 0) + 8:
                    holder = str(c.get("id", "?")).strip()
                    break
            where = f" inside {holder!r}" if holder else ""
            findings.append({"severity": "notice", "offset": at,
                             "rule": "embedded_standalone_media",
                             "message": f"a complete {name} container sits "
                                        f"{at:,} bytes in{where}; a whole file "
                                        f"inside a file that still parses"})
    except Exception as e:
        findings.append({"severity": "warn", "offset": None,
                         "rule": "check_failed",
                         "message": f"the embedded_standalone_media check could not run "
                                    f"({type(e).__name__}); this file was NOT screened for it"})

    # 16/17. JSON-backed presets. The object IS the format -- no length field,
    # no chunk table, no padding -- so the only two places to put something are
    # a key nobody reads and the bytes after the closing brace.
    known = _JSON_PRESET_KEYS.get(fmt_id)
    if known is not None:
        try:
            with open(filepath, "rb") as f:
                doc = f.read(min(size, 64 * 1024 * 1024))
            end = _json_object_end(doc)
            if end is None:
                findings.append({"severity": "warn", "offset": 0,
                                 "rule": "structure",
                                 "message": "the top-level JSON object never closes"})
            else:
                tail = doc[end:].strip()
                if tail:
                    findings.append({"severity": "alert", "offset": end,
                                     "rule": "json_trailing_data",
                                     "message": f"{len(tail):,} bytes follow the "
                                                f"top-level object; a parser stops at "
                                                f"the closing brace and never sees them"
                                                f"{_entropy_note(tail)}"})
                extra = [k for k in _json_top_level_keys(doc, end) if k not in known]
                for k in sorted(set(extra)):
                    findings.append({"severity": "notice", "offset": 0,
                                     "rule": "json_unknown_key",
                                     "message": f"top-level key {k!r} is not part of "
                                                f"the known {fmt_label} schema "
                                                f"(carrier, or a format this decoder "
                                                f"has not caught up with)"})
        except Exception as e:
            findings.append({"severity": "warn", "offset": None,
                             "rule": "check_failed",
                             "message": f"the json preset checks could not run "
                                        f"({type(e).__name__}); this file was NOT screened for them"})

    # offset is deliberately None on the four "check_failed" rules -- the ones
    # whose whole job is to say "this rule could not run, the file was NOT
    # screened for it". Sorting on it directly compared None against an int the
    # moment such a finding shared a severity with a positioned one, and
    # `audit` died with a TypeError traceback. A 3-byte b"ID3" was the smallest
    # trigger, but any ordinary corrupt file that crashes one rule while
    # another warns hits it. File-global findings sort ahead of positioned ones.
    # Bytes no structure explains at all. Distinct from rule 5 above, which
    # reports a spec-ignorable region CARRYING something: this reports regions
    # nothing accounts for, and the coverage figure that goes with them. A file
    # the walker barely understood and a file with nothing hidden in it both
    # produce no findings, and only the fraction accounted for tells them apart.
    try:
        from acidcat.core.forensics import cavity as _cavity
        report = _cavity.account(filepath, fmt_label, chunks, size=size)
        for region in report["regions"]:
            if region["kind"] != "unaccounted":
                continue            # rule 5 owns the ignorable-region case
            findings.append({
                "severity": "notice", "offset": region["offset"],
                "rule": "unaccounted_bytes",
                "message": "%s bytes no chunk accounts for; the structure "
                           "explains %.1f%% of this file"
                           % (format(region["length"], ","),
                              100.0 * report["coverage"])})
    except Exception:
        pass                        # an accounting is never worth a traceback

    findings.sort(key=lambda x: (-_SEVERITY.get(x["severity"], 0),
                                 x["offset"] if x["offset"] is not None else -1))
    return findings
