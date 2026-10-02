"""AppleDouble / AppleSingle walker. The layout is in
core/formats/appledouble.py."""

import datetime
import plistlib
import struct

from acidcat.core.formats import appledouble as admod
from acidcat.core.infra.findings import defect
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported, _f, _name, _open, _size

# a sidecar is metadata plus, at most, a resource fork
_AD_READ_CAP = 16 * 1024 * 1024
_AD_ENTRY_CAP = 64
_AD_ATTR_CAP = 256
# an attribute value decoded as a property list
_AD_PLIST_MAX = 1024 * 1024

_MAC_EPOCH = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)


def inspect_appledouble(filepath):
    size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(size, _AD_READ_CAP))
    kind = admod.kind(data)
    if kind is None or not admod.looks_like(data):
        raise Unsupported("not an AppleDouble or AppleSingle file")
    warns = []
    if size > _AD_READ_CAP:
        warns.append(hit("read_bytes", _AD_READ_CAP, size,
                         f"the file is {size:,} bytes; the first "
                         f"{_AD_READ_CAP // (1 << 20)} MB were read"))
    ents, claimed = admod.entries(data, _AD_ENTRY_CAP)
    if claimed > _AD_ENTRY_CAP:
        warns.append(hit("list_rows", _AD_ENTRY_CAP, claimed,
                         f"{claimed:,} entries; the first {_AD_ENTRY_CAP} were read"))
    name = _name(filepath).replace("\\", "/").rsplit("/", 1)[-1]
    head = {"id": "header", "offset": 0, "size": 26 + 12 * len(ents),
            "summary": f"{kind} header, {claimed} entr{'y' if claimed == 1 else 'ies'}",
            "fields": [_f(0, 4, "magic", data[:4].hex(" ")),
                       _f(4, 4, "version", f"0x{struct.unpack_from('>I', data, 4)[0]:08x}"),
                       _f(8, 16, "filler", data[8:24].decode("latin-1").rstrip("\0 ") or "(blank)"),
                       _f(24, 2, "entries", claimed)],
            "warnings": [], "payload_base": 0}
    if kind == "AppleDouble" and name.startswith("._") and len(name) > 2:
        head["fields"].append(_f(None, 0, "describes", name[2:],
                                 "the file this sidecar carries metadata for"))
    chunks = [head]
    for eid, off, ln, at in ents:
        label = admod.ENTRY_NAMES.get(eid, f"entry {eid}")
        head["fields"].append(_f(at - 0, 12, f"entry {eid}", f"{label} at {off:,}, {ln:,} bytes"))
        cw = []
        if off + ln > size:
            cw.append(defect("size.overrun", f"the {label} entry claims {ln:,} bytes at "
                                             f"{off:,} and the file is {size:,}"))
        chunk = {"id": label.replace(" ", "_").lower(), "offset": off,
                 "size": max(0, min(ln, size - off)), "summary": label,
                 "fields": [], "warnings": cw, "payload_base": off}
        body = data[off:off + ln]
        if eid == 9:
            _finder_info(data, off, ln, chunk, warns)
        elif eid == 8 and len(body) >= 16:
            for i, lab in enumerate(("created", "modified", "backed_up", "accessed")):
                secs = struct.unpack_from(">i", body, 4 * i)[0]
                chunk["fields"].append(_f(4 * i, 4, lab, secs, _mac_date(secs)))
        elif eid in (3, 4, 13):
            chunk["fields"].append(_f(0, len(body), "text", body.decode("utf-8", "replace")))
        elif eid == 2:
            blank = b"This resource fork intentionally left blank" in body
            chunk["summary"] = ("resource fork (the placeholder macOS writes, "
                                "left blank)" if blank else f"resource fork, {ln:,} bytes")
        chunks.append(chunk)
    return chunks, warns


def _mac_date(secs):
    try:
        return (_MAC_EPOCH + datetime.timedelta(seconds=secs)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except OverflowError:
        return "out of range"


def _finder_info(data, off, ln, chunk, warns):
    fi = data[off:off + admod.FINDER_INFO_LEN]
    if len(fi) >= 8:
        ftype, creator = fi[:4], fi[4:8]
        chunk["fields"].append(_f(0, 4, "file_type",
                                  ftype.decode("latin-1") if ftype.strip(b"\0") else "(none)"))
        chunk["fields"].append(_f(4, 4, "creator",
                                  creator.decode("latin-1") if creator.strip(b"\0") else "(none)"))
    attrs, block = admod.attributes(data, off, ln, _AD_ATTR_CAP)
    if block is None:
        return
    count = struct.unpack_from(">H", data, block + 34)[0]
    if count > _AD_ATTR_CAP:
        warns.append(hit("list_rows", _AD_ATTR_CAP, count,
                         f"{count:,} extended attributes; the first "
                         f"{_AD_ATTR_CAP} were read"))
    chunk["fields"].append(_f(block - off + 34, 2, "attributes", count))
    names = []
    for name, voff, vlen, _at in attrs:
        names.append(name)
        value = data[voff:voff + vlen]
        if voff + vlen > len(data):
            chunk["warnings"].append(defect("size.overrun", f"attribute {name!r} claims "
                                                            f"bytes past the end"))
            continue
        chunk["fields"].append(_f(voff - off, vlen, name, _attr_text(name, value)))
    chunk["summary"] = ("Finder info and extended attributes: " + ", ".join(names)
                        if names else "Finder info")


_PLIST_TEXT = 400        # characters of a decoded property list shown


def _unix_date(ts):
    """A Unix time as text. The quarantine record's hex time is read from the
    file unchecked, so it can be any size; datetime refuses most of them."""
    if ts is None:
        return "no time"
    try:
        return (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S UTC"))
    except (OverflowError, OSError, ValueError):
        return f"time {ts:#x} (out of range)"


def _brief(obj, room):
    """At most `room` characters describing a decoded property list.

    str() is not safe here: plistlib shares objects that the file references
    more than once, so a few hundred bytes can describe a tree whose full text
    doubles with every level. This stops writing at `room`, so its cost is
    bounded by the output, not by the shape of the tree."""
    out = []
    left = [room]

    def put(text):
        if left[0] <= 0:
            return False
        out.append(text[:left[0]])
        left[0] -= len(text)
        return left[0] > 0

    def walk(x):
        if isinstance(x, list):
            if not put("["):
                return False
            for i, v in enumerate(x):
                if (i and not put(", ")) or not walk(v):
                    return False
            return put("]")
        if isinstance(x, dict):
            if not put("{"):
                return False
            for i, (k, v) in enumerate(x.items()):
                if (i and not put(", ")) or not put(f"{k}: ") or not walk(v):
                    return False
            return put("}")
        if isinstance(x, (bytes, bytearray)):
            return put(x[:16].hex(" ") + (" ..." if len(x) > 16 else ""))
        return put(str(x)[:room])

    done = walk(obj)
    text = "".join(out)
    return text if done else text + " ..."


def _attr_text(name, value):
    if name == "com.apple.quarantine":
        q = admod.quarantine(value)
        if q:
            agent, ts = q
            return f"downloaded by {agent or '(unnamed agent)'}, {_unix_date(ts)}"
    if value.startswith(b"bplist00") and len(value) <= _AD_PLIST_MAX:
        try:
            obj = plistlib.loads(value)
        except Exception:
            return f"binary property list, {len(value):,} bytes, unreadable"
        if isinstance(obj, list):
            # a WhereFroms list reads best as its items, not as a list literal
            return _brief(obj, _PLIST_TEXT)[1:].rstrip("]")
        return _brief(obj, _PLIST_TEXT)
    text = value.rstrip(b"\0")
    if text and all(32 <= b < 127 or b in (9, 10, 13) for b in text):
        return text.decode("ascii")
    return f"{len(value):,} bytes: {value[:16].hex(' ')}" + (" ..." if len(value) > 16 else "")
