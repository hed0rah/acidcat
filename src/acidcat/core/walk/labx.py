"""Arturia Analog Lab .labx bank walker.

A .labx is a plain ZIP of STORED entries, one per preset, each path
`<Engine>/User|Factory/<Bank>/<PresetName>` with no file extension, plus an
optional top-level cover image. Each preset entry is a Boost C++ text-
serialization archive (ASCII, length-prefixed strings) that opens
`22 serialization::archive ...` and carries the preset metadata: name, bank,
author, comment, unix save time, engine version, a Characteristics/Genres/Styles
tag blob, and keyed `Type`/`Subtype` fields.

This surfaces the bank census and, per preset, the path-derived identity plus
the archive metadata. Each preset chunk is a real byte region (STORED entry, so
a carve of it is the literal Boost archive, replayable into Analog Lab).
"""

import os
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone

from acidcat.core.walk.base import _f

_PRESET_CAP = 48                          # cap chunks like multisample's _ZONE_CAP
_META_CAP = 8192                          # metadata sits in the first ~1 KB
_TS_RE = re.compile(rb"(?<![\d.])(1\d{9})(?![\d.])")   # 10-digit unix save time
_VER_RE = re.compile(rb"\d+\.\d+\.\d+\.\d+")           # engine version N.N.N.NNNN
_ARCHIVE_MAGIC = b"serialization::archive"


def _data_offset(z, zi):
    """Absolute file offset of a zip entry's data (past the local file header),
    leaving z.fp positioned there. ZipInfo.header_offset points at the PK local
    header, not the payload, so a carve region must start here to be the literal
    entry bytes -- for a STORED entry, the archive itself."""
    z.fp.seek(zi.header_offset)
    h = z.fp.read(30)
    n = int.from_bytes(h[26:28], "little")
    m = int.from_bytes(h[28:30], "little")
    z.fp.read(n + m)                       # skip filename + extra field
    return zi.header_offset + 30 + n + m


def _read_head(z, zi, cap=_META_CAP):
    """(data_offset, head_bytes) for a zip entry. For a STORED entry the head is
    the raw archive bytes; otherwise fall back to a capped decompressed read."""
    doff = _data_offset(z, zi)
    if zi.compress_type == zipfile.ZIP_STORED:
        return doff, z.fp.read(min(zi.file_size, cap))
    try:
        return doff, z.read(zi.filename)[:cap]
    except Exception:
        return doff, b""


def _printable(b):
    return bool(b) and not any(c < 0x20 and c not in (9, 10, 13) for c in b)


def _lps_at(b, j):
    """Read the length-prefixed string '<n> <n bytes>' starting at b[j]."""
    sp = b.find(b" ", j)
    if sp < 0 or not b[j:sp].isdigit():
        return ""
    n = int(b[j:sp])
    return b[sp + 1:sp + 1 + n].decode("utf-8", "replace")


def _kv_after(b, key):
    """Value of a Boost 'name value' string pair: find '<len(key)> <key> ' and
    read the following length-prefixed string."""
    needle = f"{len(key)} {key} ".encode()
    i = b.find(needle)
    return _lps_at(b, i + len(needle)) if i >= 0 else ""


def _first_lps_after(b, start):
    """Skip bare-int flag tokens from `start` and return the first real length-
    prefixed string -- the one whose byte count lands on a space/end boundary. A
    flag int tried as a length prefix lands mid-token, which rejects it."""
    i, end = start, len(b)
    while i < end:
        while i < end and b[i] == 0x20:
            i += 1
        j = i
        while j < end and b[j] != 0x20:
            j += 1
        if not b[i:j].isdigit():
            return ""
        n = int(b[i:j])
        k = j + 1
        if n >= 1 and b[k + n:k + n + 1] in (b" ", b"") and _printable(b[k:k + n]):
            return b[k:k + n].decode("utf-8", "replace")
        i = j                            # a flag int, not a length prefix
    return ""


def _lps_before(b, pos):
    """The length-prefixed string whose value ends just before b[pos] (b[pos-1]
    is its trailing space). Walks back word by word until the length prefix
    matches, so a value containing spaces is recovered whole."""
    end = pos - 1
    if end <= 0:
        return ""
    s = end
    for _ in range(64):
        s = b.rfind(b" ", 0, s)
        if s < 0:
            return ""
        cand = b[s + 1:end]
        s2 = b.rfind(b" ", 0, s)
        tok = b[s2 + 1:s]
        if tok.isdigit() and int(tok) == len(cand):
            return cand.decode("utf-8", "replace")
    return ""


def _tag_blob(b):
    """The Characteristics values from the tag blob as 'A|B|C'. The value token
    starts 'Characteristics,' (comma); the key token 'Characteristics ' (space)
    does not, which distinguishes them."""
    i = b.find(b"Characteristics,")
    if i < 1:
        return ""
    k = b.rfind(b" ", 0, i - 1)          # space before the length prefix
    if k < 0 or not b[k + 1:i - 1].isdigit():
        return ""
    blob = b[i:i + int(b[k + 1:i - 1])].decode("utf-8", "replace")
    for part in blob.split(";"):
        if part.startswith("Characteristics,"):
            return part[len("Characteristics,"):]
    return ""


def _iso(ts):
    try:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return str(ts)


def _preset_fields(head, engine, name, bank):
    """Archive-sourced fields for one preset, plus the summary suffix."""
    fields = [_f(None, 0, "engine", engine), _f(None, 0, "name", name)]
    typ, sub = _kv_after(head, "Type"), _kv_after(head, "Subtype")
    author = ""
    bank_lps = f"{len(bank)} {bank} ".encode()
    bi = head.find(bank_lps)
    if bi >= 0:
        author = _first_lps_after(head, bi + len(bank_lps))
    comment = saved = ver = ""
    m = _TS_RE.search(head)
    if m:
        comment = _lps_before(head, m.start())
        saved = _iso(int(m.group(1)))
        vm = _VER_RE.search(head, m.end())
        if vm:
            ver = vm.group(0).decode()
    for label, val in (("type", typ), ("subtype", sub), ("author", author)):
        if val:
            fields.append(_f(None, 0, label, val))
    if comment:
        fields.append(_f(None, 0, "comment", comment[:80]))
    if saved:
        fields.append(_f(None, 0, "saved", saved))
    if ver:
        fields.append(_f(None, 0, "engine_version", ver))
    tags = _tag_blob(head)
    if tags:
        fields.append(_f(None, 0, "tags", tags[:120]))
    suffix = f"  ({typ or '?'}/{sub or '?'})" if (typ or sub) else ""
    return fields, suffix


def inspect_labx(filepath):
    size = os.path.getsize(filepath)
    try:
        z = zipfile.ZipFile(filepath)
    except zipfile.BadZipFile:
        return ([{"id": "labx", "offset": 0, "size": size,
                  "summary": "not a valid zip archive", "fields": [],
                  "warnings": ["not a zip archive"], "payload_base": 0}],
                ["not a zip archive"])

    warns = []
    with z:
        presets, assets = [], []
        for zi in z.infolist():
            if zi.is_dir():
                continue
            parts = zi.filename.split("/")
            (presets if len(parts) >= 3 else assets).append((zi, parts))

        if not presets:
            warns.append("zip does not follow the <Engine>/User/<Bank>/<Preset> "
                         "layout; listing raw entries")
            chunks = [{"id": "labx", "offset": 0, "size": size, "payload_base": 0,
                       "summary": f"zip archive, {len(assets)} entries "
                                  "(not Analog Lab layout)",
                       "fields": [_f(None, 0, "entries", len(assets))],
                       "warnings": warns}]
            for zi, _ in assets[:_PRESET_CAP]:
                doff = _data_offset(z, zi)
                chunks.append({"id": "asset", "offset": doff,
                               "size": zi.compress_size, "summary": zi.filename,
                               "fields": [_f(None, 0, "size", f"{zi.file_size:,} bytes")],
                               "warnings": [], "payload_base": doff})
            return chunks, warns

        banks = Counter(p[2] for _, p in presets)
        engines = Counter(p[0] for _, p in presets)
        eng_str = ", ".join(f"{e} {c}" for e, c in engines.most_common())
        top_bank = banks.most_common(1)[0][0]
        if len(banks) == 1:
            summary = (f"'{top_bank}': {len(presets)} presets across "
                       f"{len(engines)} engines ({eng_str})")
        else:
            summary = f"{len(banks)} banks: " + ", ".join(
                f"'{b}' ({c})" for b, c in banks.most_common())

        bfields = [_f(None, 0, "bank_name", top_bank),
                   _f(None, 0, "presets", len(presets)),
                   _f(None, 0, "engines", eng_str)]
        cover = next((zi for zi, _ in assets
                      if zi.filename.lower().endswith((".png", ".jpg", ".jpeg"))), None)
        if cover is not None:
            bfields.append(_f(None, 0, "cover_image", cover.filename))
        chunks = [{"id": "bank", "offset": 0, "size": size, "payload_base": 0,
                   "summary": summary, "fields": bfields, "warnings": []}]

        for zi, parts in presets[:_PRESET_CAP]:
            engine, name = parts[0], parts[-1]
            bank = parts[2] if len(parts) >= 4 else parts[-2]
            doff, head = _read_head(z, zi)
            pwarn = []
            if _ARCHIVE_MAGIC in head[:64]:
                fields, suffix = _preset_fields(head, engine, name, bank)
            else:
                fields = [_f(None, 0, "engine", engine), _f(None, 0, "name", name)]
                suffix = ""
                pwarn.append("entry is not a boost text archive")
            chunks.append({"id": "preset", "offset": doff,
                           "size": zi.compress_size,
                           "summary": f"{engine}: {name}{suffix}",
                           "fields": fields, "warnings": pwarn,
                           "payload_base": doff})
        if len(presets) > _PRESET_CAP:
            chunks.append({"id": "preset", "offset": 0, "size": 0,
                           "summary": f"... {len(presets) - _PRESET_CAP} more preset(s)",
                           "fields": [], "warnings": [], "payload_base": 0})
    return chunks, warns
