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

_SEVERITY = {"alert": 3, "warn": 2, "notice": 1}

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


def _declared_end(head):
    """The offset a conformant reader stops at, from the container's size field.
    None for formats without a simple total-size header."""
    if len(head) >= 8 and head[:4] in (b"RIFF", b"RF64"):
        return 8 + struct.unpack_from("<I", head, 4)[0]
    if len(head) >= 8 and head[:4] == b"FORM":
        return 8 + struct.unpack_from(">I", head, 4)[0]
    return None


def scan(filepath, fmt_label, chunks, warns):
    """Return a list of forensic findings for an already-walked file."""
    findings = []
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(16)

    # 1. surface the walker's structural lint (size overruns, forged counts, ...)
    for w in warns or []:
        findings.append({"severity": "warn", "offset": 0,
                         "rule": "structure", "message": w})
    for c in chunks:
        for w in c.get("warnings") or []:
            findings.append({"severity": "warn", "offset": c.get("offset", 0) or 0,
                             "rule": "structure",
                             "message": f"{str(c.get('id', '?')).strip()}: {w}"})

    # 2. trailing data past the DECLARED container end, and a tail magic scan.
    # Use the container's own size field, not the walker's parsed coverage (the
    # walker mis-reads appended bytes as a bogus chunk, a noisy boundary).
    end = _declared_end(head)
    if end is None:
        end = max((c["offset"] + c["size"] for c in chunks
                   if isinstance(c.get("offset"), int)
                   and isinstance(c.get("size"), int)), default=0)
    if isinstance(end, int) and 0 < end < size:
        findings.append({"severity": "notice", "offset": end, "rule": "trailing_data",
                         "message": f"{size - end:,} bytes past the declared "
                                    f"container end"})
        with open(filepath, "rb") as f:
            f.seek(end)
            tail = f.read(1 << 20)
        for magic, label in _MAGICS:
            if magic in tail:
                findings.append({"severity": "alert", "offset": end, "rule": "polyglot",
                                 "message": f"possible polyglot: {label} appended "
                                            f"after the container"})

    # 3. control bytes in decoded text fields (smuggling in "text")
    for c in chunks:
        for fl in c.get("fields") or []:
            v = fl.get("value")
            if isinstance(v, str) and v:
                ctrl = sum(1 for ch in v if ord(ch) < 9 or 13 < ord(ch) < 32)
                if ctrl >= 2:
                    findings.append({
                        "severity": "notice", "offset": fl.get("off") or 0,
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
                                        f"this region is spec'd to be ignorable"})

    # 6. FLAC APPLICATION block: 4-byte id + arbitrary freeform data
    for c in chunks:
        if str(c.get("id", "")).strip().upper() == "APPLICATION":
            findings.append({"severity": "notice", "offset": c.get("offset", 0) or 0,
                             "rule": "application_block",
                             "message": f"FLAC APPLICATION block "
                                        f"({c.get('size', 0):,} bytes of freeform data)"})

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
    if fmt_label and fmt_label.startswith("Ogg"):
        try:
            from acidcat.core import ogg as _ogg
            with open(filepath, "rb") as f:
                ogg_data = f.read(16 * 1024 * 1024)
            serials = {pg["serial"] for pg in _ogg.iter_pages(ogg_data)
                       if pg["header_type"] & 0x02}
            if len(serials) > 1:
                findings.append({"severity": "notice", "offset": 0,
                                 "rule": "ogg_multistream",
                                 "message": f"{len(serials)} logical bitstreams in "
                                            f"one Ogg; a single-codec player surfaces "
                                            f"only one (possible hidden stream)"})
        except Exception:
            pass

    # 9. MP4 mdat coverage gap: bytes inside mdat that no sample references. Sum
    # every stsz sample size across tracks and compare to the mdat payload; an
    # unreferenced run is a cavity (a payload appended at mdat's tail with only
    # the box size grown, so no chunk offset points at it and the audio is intact).
    if fmt_label and fmt_label.startswith("MP4"):
        try:
            from acidcat.core import mp4 as _mp4
            fsz = os.path.getsize(filepath)
            with open(filepath, "rb") as f:
                mdata = f.read(16 * 1024 * 1024)
            mdat_payload = sample_bytes = 0
            saw_stsz = False
            for b in _mp4.iter_boxes(mdata, file_size=fsz):
                if b["type"] == b"mdat" and not b["truncated"]:
                    mdat_payload += b["size"] - b["hdr"]
                elif b["type"] == b"stsz" and not b["beyond_cap"]:
                    p = b["offset"] + b["hdr"]
                    if p + 12 <= len(mdata):
                        saw_stsz = True
                        ssize = struct.unpack_from(">I", mdata, p + 4)[0]
                        scount = struct.unpack_from(">I", mdata, p + 8)[0]
                        if ssize:
                            sample_bytes += ssize * scount
                        else:
                            q = p + 12
                            for _ in range(min(scount, (len(mdata) - q) // 4)):
                                sample_bytes += struct.unpack_from(">I", mdata, q)[0]
                                q += 4
            gap = mdat_payload - sample_bytes
            # only a payload-sized unreferenced run is a plausible cavity; small
            # gaps are legit alignment/edit padding (thin real-MP4 calibration, so
            # keep this conservative, the real PoC payload was ~5.8 KB).
            if saw_stsz and mdat_payload > 0 and gap > 1024:
                findings.append({"severity": "notice", "offset": 0,
                                 "rule": "mp4_mdat_coverage",
                                 "message": f"{gap:,} bytes in mdat referenced by no "
                                            f"sample (stsz sums {sample_bytes:,} of "
                                            f"{mdat_payload:,}); possible cavity"})
        except Exception:
            pass

    # 10. ID3v2 non-zero padding: the region after the last frame up to the tag's
    # declared size is spec'd to be zero; content there is a cavity (not trailing
    # data, since it is inside the tag's own length).
    if fmt_label and fmt_label.startswith("MP3") and head[:3] == b"ID3":
        try:
            with open(filepath, "rb") as f:
                th = f.read(10)
                ver = th[3]
                tag_size = (((th[6] & 0x7F) << 21) | ((th[7] & 0x7F) << 14)
                            | ((th[8] & 0x7F) << 7) | (th[9] & 0x7F))
                body = f.read(tag_size)
            pos, pad_start = 0, tag_size
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
                if fsz < 0 or pos > len(body):
                    break
            pad = body[pad_start:]
            if any(pad):
                findings.append({"severity": "notice", "offset": 10 + pad_start,
                                 "rule": "id3_padding_nonzero",
                                 "message": f"non-zero bytes in ID3v2 padding "
                                            f"({len(pad):,} bytes after the last frame); "
                                            f"the padding region is spec'd to be zero"})
        except Exception:
            pass

    # 11. dual-endianness audio: 16-bit PCM engineered so BOTH the little-endian
    # and big-endian readings are structured audio (a WAV/AIFF twin that plays a
    # different sound each way). real audio is structured one endianness, noise
    # the other; both structured is the crafted-artifact tell.
    if fmt_label and (fmt_label.startswith("RIFF/WAVE") or "AIFF" in fmt_label):
        try:
            from acidcat.core import lsb as _lsb
            de = _lsb.dual_endian(filepath, fmt_label, chunks)
            if de and de["flagged"]:
                findings.append({"severity": "notice", "offset": 0,
                                 "rule": "dual_endianness",
                                 "message": f"both endian readings of the 16-bit PCM "
                                            f"are structured (LE autocorr {de['le']:.2f}, "
                                            f"BE {de['be']:.2f}); a cross-endian "
                                            f"audio+audio artifact"})
        except Exception:
            pass

    findings.sort(key=lambda x: (-_SEVERITY.get(x["severity"], 0), x["offset"]))
    return findings
