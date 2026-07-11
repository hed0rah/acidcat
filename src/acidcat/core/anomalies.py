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
    if end is None and head[:4] in (b"RF64", b"BW64"):
        # RF64/BW64 put 0xFFFFFFFF in the RIFF size and the true 64-bit size in
        # the ds64 chunk (offset 12: 'ds64' + u32 size + riffSize u64 at 20).
        # Without this the trailing/polyglot scan never runs on any RF64 file
        # (and the parsed-coverage fallback is poisoned by the mis-parsed
        # appended bytes, which the walker reads as a giant bogus chunk).
        end = _rf64_end(filepath)
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
            # clamped: read(N) pre-allocates N bytes (see core/midi.py)
            tail = f.read(min(1 << 20, size - end))
        for magic, label in _MAGICS:
            at = tail.find(magic)
            if at >= 0:
                findings.append({"severity": "alert", "offset": end + at,
                                 "rule": "polyglot",
                                 "message": f"possible polyglot: {label} magic at "
                                            f"0x{end + at:08x}, past the container end"})

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
                    size, hlen = struct.unpack(">I", hdr[:4])[0], 8
                    if size == 1:
                        ext = f.read(8)
                        if len(ext) < 8:
                            break
                        size, hlen = struct.unpack(">Q", ext)[0], 16
                    elif size == 0:
                        size = fsz - pos
                    if size < hlen or pos + size > fsz:
                        break
                    if hdr[4:8] == b"mdat":
                        mdat_payload += size - hlen
                    elif hdr[4:8] == b"moov":
                        moov_off, moov_size = pos, size
                    pos += size
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
        except Exception:
            pass

    # 10. ID3v2 non-zero padding: the region after the last frame up to the tag's
    # declared size is spec'd to be zero; content there is a cavity (not trailing
    # data, since it is inside the tag's own length).
    if fmt_label and fmt_label.startswith("MP3") and head[:3] == b"ID3":
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

    # 12. RIFF/AIFF odd-chunk pad byte non-zero. The spec requires a single $00
    # pad after any odd-sized chunk; the walker skips it without checking. A
    # non-zero pad after chunks is a low-bandwidth covert channel invisible to
    # every conformant reader (it lands in nobody's payload).
    if fmt_label and (fmt_label.startswith("RIFF") or "AIFF" in fmt_label
                      or "RF64" in fmt_label or fmt_label.startswith("RMID")):
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
    if size >= 32 and not (fmt_label and fmt_label.startswith("MP3")):
        with open(filepath, "rb") as f:
            f.seek(size - 32)
            if f.read(8) == b"APETAGEX":
                findings.append({"severity": "notice", "offset": size - 32,
                                 "rule": "wrong_format_tag",
                                 "message": "an APEv2 tag sits on a non-MP3 file "
                                            "(unusual metadata carrier)"})

    findings.sort(key=lambda x: (-_SEVERITY.get(x["severity"], 0), x["offset"]))
    return findings
