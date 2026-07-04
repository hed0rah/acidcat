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
_CAVITY = {"PADDING": "FLAC PADDING", "FREE": "MP4 free box", "SKIP": "MP4 skip box"}


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
        base = c.get("payload_base")
        if not label or base is None:
            continue
        if cid == "PADDING":
            clen = c.get("size") or 0
        else:  # MP4 box: size includes the header, base is past it
            clen = (c.get("size") or 0) - (base - (c.get("offset") or 0))
        if clen <= 0:
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

    findings.sort(key=lambda x: (-_SEVERITY.get(x["severity"], 0), x["offset"]))
    return findings
