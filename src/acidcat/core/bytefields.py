"""Typed byte-field decoding and anchored-offset resolution -- the surgical-RE
engine behind `carve`'s typed modes.

Two jobs, both format-agnostic:

  * decode a byte range as a typed value (u8..i64 / f32 / f64 / fixed string /
    null-terminated string), little- or big-endian, so `carve` can hand back a
    decoded value instead of raw bytes;
  * resolve an anchored offset expression so you don't hand-count -- an absolute
    address, one relative to the declared end, to a found pattern, or to a named
    chunk from the walker.

Read-only: nothing here writes. `carve` is a knife.
"""

import struct

# base type -> (struct code, byte size). endianness is applied separately.
_INT = {"u8": ("B", 1), "i8": ("b", 1), "u16": ("H", 2), "i16": ("h", 2),
        "u32": ("I", 4), "i32": ("i", 4), "u64": ("Q", 8), "i64": ("q", 8),
        "f32": ("f", 4), "f64": ("d", 8)}


class FieldError(ValueError):
    """A bad type spec or unresolvable offset; message is user-facing."""


def parse_type(spec, default_endian=">"):
    """Parse a type spec into (kind, code, size, endian).

    kind is 'num' | 'str' | 'cstr'. A trailing 'be'/'le' on the spec overrides
    default_endian (needed inside a --struct where fields differ). Examples:
    u32, u32be, i16le, f32, 4s (fixed 4-byte string), cstr (null-terminated)."""
    endian = default_endian
    s = spec.strip()
    if s.endswith("be"):
        endian, s = ">", s[:-2]
    elif s.endswith("le"):
        endian, s = "<", s[:-2]
    if s in _INT:
        code, size = _INT[s]
        return ("num", code, size, endian)
    if s == "cstr":
        return ("cstr", None, None, endian)
    if len(s) > 1 and s.endswith("s") and s[:-1].isdigit():
        return ("str", None, int(s[:-1]), endian)
    raise FieldError(f"unknown type {spec!r} (u8..i64, f32/f64, Ns, cstr; "
                     f"optional be/le suffix)")


def type_size(parsed, raw=b""):
    """Byte size a parsed type consumes. For cstr it depends on the data (up to
    and including the terminator, if present)."""
    kind, _code, size, _endian = parsed
    if kind == "cstr":
        i = raw.find(b"\x00")
        return len(raw) if i < 0 else i + 1
    return size


def decode(raw, parsed):
    """Decode `raw` bytes as the parsed type. Returns the value."""
    kind, code, size, endian = parsed
    if kind == "num":
        if len(raw) < size:
            raise FieldError(f"need {size} bytes, have {len(raw)}")
        return struct.unpack_from(endian + code, raw)[0]
    if kind == "str":
        return raw[:size].split(b"\x00", 1)[0].decode("latin1", "replace")
    # cstr
    return raw.split(b"\x00", 1)[0].decode("latin1", "replace")


def decode_both_endian(raw, spec):
    """Decode a numeric spec both ways -> {'be': v, 'le': v} (the endian guess).
    Non-numeric specs just return the single decode under {'value': v}."""
    kind, code, size, _e = parse_type(spec)
    if kind != "num":
        return {"value": decode(raw, (kind, code, size, ">"))}
    return {"be": struct.unpack_from(">" + code, raw)[0],
            "le": struct.unpack_from("<" + code, raw)[0]}


def _split_delta(expr):
    """Split a trailing +N / -N (hex or decimal) off an anchor expression."""
    for i in range(len(expr) - 1, 0, -1):
        if expr[i] in "+-":
            head, sign, rest = expr[:i], expr[i], expr[i + 1:]
            # only treat it as a delta if the tail parses as a number
            try:
                return head, (int(rest, 0) if sign == "+" else -int(rest, 0))
            except ValueError:
                return expr, 0
    return expr, 0


def resolve_offset(expr, filepath, size):
    """Resolve an --at expression to an absolute byte offset.

    Forms (each may carry a trailing +N / -N, hex or decimal):
        0x1c / 28            absolute address
        end                  the file's declared/actual end
        find:BFDi            offset of the first ASCII match
        find:0x42464469      offset of the first hex-byte match
        chunk:fmt            offset of a named chunk (via the walker)
    """
    anchor, delta = _split_delta(expr.strip())

    if anchor == "end":
        base = size
    elif anchor.startswith("find:"):
        pat = anchor[5:]
        needle = bytes.fromhex(pat[2:]) if pat.lower().startswith("0x") else pat.encode("latin1")
        if not needle:
            raise FieldError("empty --at find: pattern")
        with open(filepath, "rb") as f:
            data = f.read()
        pos = data.find(needle)
        if pos < 0:
            raise FieldError(f"pattern {pat!r} not found")
        base = pos
    elif anchor.startswith("chunk:"):
        base = _chunk_offset(filepath, anchor[6:])
    else:
        try:
            base = int(anchor, 0)
        except ValueError:
            raise FieldError(f"cannot resolve --at {expr!r}")

    off = base + delta
    if off < 0:
        raise FieldError(f"--at {expr!r} resolves before the file start")
    return off


def _chunk_offset(filepath, chunk_id):
    """Offset of a named chunk (where its id begins), via the walker. Works for
    any walked chunked format, not just RIFF/AIFF."""
    from acidcat.core.walk import walk_file, Unsupported
    want = chunk_id.strip()
    try:
        _label, chunks, _warns = walk_file(filepath)
    except Unsupported as e:
        raise FieldError(f"chunk:{chunk_id}: {e}")
    for c in chunks:
        if c.get("id", "").strip() == want:
            return c["offset"]
    raise FieldError(f"chunk {chunk_id!r} not found in {filepath}")


def flatten_fields(chunks):
    """Yield (chunk_id, field_name, value) for every decoded field the walker
    produced, so a caller can look a field up by name."""
    for c in chunks:
        for fld in c.get("fields", []):
            name = fld.get("name")
            if name:
                yield c.get("id", ""), name, fld.get("value")
