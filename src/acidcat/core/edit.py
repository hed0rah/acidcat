"""Editing: one front door (architecture-2.0 section 7).

    doc = acidcat.open("in.wav")
    patch = doc.edit({"RIFF/fmt_#sample_rate": 48000})
    patch.repair()                      # avg_bytes_per_sec follows the rate
    patch.verify()                      # re-reads; raises PatchError on a miss
    patch.commit("out.wav", backup=True)

    doc.edit({"title": "Kick"}).verify().commit()   # in place, _original kept

A patch either edits bytes in place or rewrites metadata through a profile,
not both: give those as two patches.

A key in `changes` is one of three things:

- an ADDR with `#` or `@`: a field (`RIFF/fmt_#sample_rate`) or a byte range
  (`@0x18+4`, `RIFF/fmt_[4:4]`). A field takes a value, encoded with its type
  and the inverse of its transform into bytes of the same length; any
  positioned range also takes raw `bytes` of exactly its length. A field whose
  type the normaliser only inferred is refused without `force=True`, because
  its type is a guess; only layer 0 can be edited, since a derived layer is a
  decoding, not bytes the file holds.
- `cover`: the front cover of a tagged file. Image bytes embed it; None
  removes it.
- anything else: a metadata field of the file's edit profile (wav, aiff,
  tagged, vital, ...), under the names `write --set` takes. None clears it.
  The `#`/`@` rule keeps a tag name from ever being read as a node name.

A Patch is the new file image plus a record per edit. `verify()` re-reads
every edit from the new bytes (a field through its type and transform, a tag
through its profile's own reader, the cover by extracting it), checks that the
bits a bit-field shares with its neighbours are kept, and that the re-walk
finds no defect the original did not have. `commit()` writes it the way every
write has: atomically, with a backup unless told not to.
"""

import os
import struct
import tempfile
from collections import Counter
from typing import Any, NamedTuple, Optional

from acidcat.core.infra import addr as addrmod
from acidcat.core.infra import contract, fieldcodec
from acidcat.core.write import edits, writer
from acidcat.core.write.edits import EditError


class PatchError(EditError):
    """verify() found an edit that does not read back, or a new defect."""


class Repair(NamedTuple):
    """One field repair() changed: its address, the value it held, the value
    it now holds, and what that value follows from."""
    addr: str
    old: Any
    new: Any
    follows: str


class Record(NamedTuple):
    """One edit. `kind` is field, bytes, meta or cover; `key` is what the
    caller wrote; `loc` is (layer, off, len) for a byte edit; `old` and `new`
    are the values before and after (for meta, as the profile reports them);
    `field` is the v1 field dict for a field edit."""
    kind: str
    key: str
    old: object
    new: object
    loc: Optional[tuple] = None
    field: Optional[dict] = None
    wanted: object = None


# ── types, written ─────────────────────────────────────────────────────

def _number(value, want_float=False):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if want_float else value
    text = str(value).strip()
    if want_float:
        return float(text)
    try:
        return int(text, 0)
    except ValueError:
        f = float(text)
        if f != int(f):
            raise EditError("%r is not a whole number" % value) from None
        return int(f)


def unxform(xform, value, old_stored, at=None, node_off=None):
    """The stored value that `xform` turns into `value`: the transform chain
    undone right to left. `mask` keeps the stored bits outside the mask as they
    were; `ascii-dec` cannot be undone and is refused."""
    steps = [s for s in (xform or "").split(";") if s]
    v = value
    for step in reversed(steps):
        m = contract._XFORM.fullmatch(step)
        if not m:
            raise EditError("unknown transform %r" % step)
        op, arg, bare = m.group(1), m.group(2), m.group(3)
        if bare == "neg":
            v = -v
        elif bare == "ascii-dec":
            raise EditError("an ascii-dec value cannot be written back")
        elif op == "add":
            v = v - int(arg)
        elif op == "mul":
            k = int(arg)
            if v % k:
                raise EditError("%r is not a multiple of %d" % (v, k))
            v = v // k
        elif op == "mask":
            k = int(arg, 16)
            if v & ~k:
                raise EditError("%r has bits outside the mask 0x%x" % (v, k))
            v = (int(old_stored) & ~k) | v
        elif op == "fixed":
            _m, n = (int(x) for x in arg.split("."))
            v = round(float(v) * (1 << n))
        elif op == "rel":
            v = v - (at if arg == "self" else node_off)
    return v


def write_type(typ, value, old):
    """Bytes of `typ` that store `value`, the same length as `old` (the bytes
    there now, which a bit-field keeps outside its own bits). Raises EditError
    when the value does not fit or the type is not writable."""
    if typ in contract._INT_TYPES:
        w, signed, order = contract._INT_TYPES[typ]
        v = _number(value)
        try:
            return int(v).to_bytes(w, order, signed=signed)
        except OverflowError:
            raise EditError("%r does not fit a %s" % (value, typ)) from None
    if typ in ("f32le", "f32be", "f64le", "f64be"):
        fmt = ("<" if typ.endswith("le") else ">") + ("f" if typ[1:3] == "32" else "d")
        try:
            return struct.pack(fmt, _number(value, want_float=True))
        except (struct.error, OverflowError) as e:
            raise EditError("%r does not fit a %s: %s" % (value, typ, e)) from None
    if typ in ("f80be", "synchsafe"):
        try:
            return fieldcodec.encode_value("float80" if typ == "f80be" else typ, str(value))
        except (ValueError, struct.error) as e:
            raise EditError("%r does not fit a %s: %s" % (value, typ, e)) from None
    if typ.startswith("bits:"):
        clen, bitpos, width = (int(x) for x in typ.split(":")[1:])
        v = _number(value)
        if not 0 <= v < (1 << width):
            raise EditError("%r does not fit %d bits" % (value, width))
        return fieldcodec.bitfield_apply(bytes(old), bitpos, width, 0, v)
    if typ in ("fourcc", "ascii"):
        b = str(value).encode("latin-1", "replace")
        if len(b) > len(old) or (typ == "fourcc" and len(b) != 4):
            raise EditError("%r does not fit %d bytes" % (value, len(old)))
        return b + b"\x00" * (len(old) - len(b))
    raise EditError("a %s field is not writable; give its bytes instead" % typ)


def _read_back(field, data):
    """The value a field's bytes hold, through its type and transform."""
    at = field["at"]
    b = data[at["off"]:at["off"] + at["len"]]
    stored = contract.read_type(field["type"], b)
    return contract.apply_xform(field.get("xform"), stored, at=at["off"],
                                node_off=field.get("_node_off"))


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            return False
        return abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))
    return a == b


# ── planning ───────────────────────────────────────────────────────────

def _is_addr(key):
    return "#" in key or "@" in key or key.endswith("]")


def _plan_byte_edit(raw_doc, key, value, force, data):
    t = addrmod.resolve(raw_doc, key)
    if t.off is None:
        raise EditError("%s has no byte range to write (it is unpositioned)" % key)
    if t.layer != 0:
        raise EditError("%s is in layer %d, a decoding; edit the bytes it comes "
                        "from in layer 0" % (key, t.layer))
    old = data[t.off:t.off + t.len]
    if isinstance(value, str) and value.startswith("hex:"):
        # raw bytes as text, for a command line: `@0x16+2=hex:0100`
        try:
            value = bytes.fromhex(value[4:])
        except ValueError:
            raise EditError("%s: %r is not hex bytes" % (key, value)) from None
    if isinstance(value, (bytes, bytearray)):
        if len(value) != t.len:
            raise EditError("%s is %d bytes; %d given" % (key, t.len, len(value)))
        return Record("bytes", key, bytes(old), bytes(value), (0, t.off, t.len),
                      wanted=bytes(value)), bytes(value)
    f = t.field
    if f is None:
        raise EditError("%s is a byte range; give it bytes (hex:0100 on the "
                        "command line)" % key)
    if f.get("type_source") == "inferred" and not force:
        raise EditError("%s has an inferred type (%s), which is a guess; "
                        "force it to write anyway" % (key, f.get("type")))
    if f.get("type_source") == "none" or f.get("type") == "display":
        raise EditError("%s has no known encoding; give its bytes instead" % key)
    field = dict(f, _node_off=(t.node.get("extent") or {}).get("off"))
    try:
        old_stored = contract.read_type(f["type"], old)
    except ValueError as e:
        raise EditError("%s: %s" % (key, e)) from None
    typ = f["type"]
    want = _number(value, want_float=typ.startswith("f"))
    stored = unxform(f.get("xform"), want, old_stored, at=t.off,
                     node_off=field["_node_off"])
    new = write_type(typ, stored, old)
    if len(new) != t.len:
        raise EditError("%s: encoded %d bytes for a %d-byte field"
                        % (key, len(new), t.len))
    return Record("field", key, f.get("value"), want, (0, t.off, t.len), field,
                  wanted=want), new


def plan(data, name, changes, raw_doc=None, force=False):
    """A Patch for `changes` against the file image `data` (named `name`,
    for the profiles that go by extension). `raw_doc` is its v1 dict when the
    caller has walked it; it is walked here when an address needs it."""
    data = bytes(data)
    records, spans = [], []
    new = bytearray(data)
    meta = {}
    cover = None
    for key, value in changes.items():
        key = str(key)
        if key == "cover":
            cover = (key, value)
        elif _is_addr(key):
            if raw_doc is None:
                raw_doc = _walk_bytes(data, name)
                if raw_doc is None:
                    raise EditError("%s: the file is not one acidcat reads, so "
                                    "no address resolves in it" % key)
            rec, b = _plan_byte_edit(raw_doc, key, value, force, data)
            _l, off, n = rec.loc
            if any(off < e and s < off + n for s, e in spans):
                raise EditError("%s overlaps another edit in the same patch" % key)
            spans.append((off, off + n))
            new[off:off + n] = b
            records.append(rec)
        else:
            meta[key] = value
    fmt = None
    notes = []
    out = bytes(new)
    if meta:
        if spans:
            raise EditError("a patch either edits bytes in place or rewrites "
                            "metadata, not both: give them as two edits")
        fmt, out, applied = edits.edit_metadata_data(out, name, meta, notes)
        for field, old, newv in applied:
            records.append(Record("meta", field, old, newv, wanted=meta.get(field)))
    if cover is not None:
        out, rec = _cover_edit(out, name, cover[1])
        records.append(rec)
    if fmt is None and raw_doc is not None:
        fmt = raw_doc["format"].get("label")    # a field or byte-range patch
    return Patch(data, out, records, name, fmt, raw_doc, force, notes)


def _cover_edit(data, name, image):
    from acidcat.core.formats import cover as covermod
    suffix = os.path.splitext(name or "")[1] or ".mp3"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="acidcat_cover_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        before = covermod.extract(tmp)
        if image is None:
            removed = covermod.remove_cover(tmp)
            rec = Record("cover", "cover", before and before[1],
                         None, wanted=None, field={"removed": removed})
        else:
            covermod.set_cover(tmp, bytes(image))
            rec = Record("cover", "cover", before and before[1], bytes(image),
                         wanted=bytes(image))
        with open(tmp, "rb") as fh:
            new = fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        edits._verify_audio_preserved(data, new)
    except EditError as e:
        raise covermod.CoverError(str(e))
    return new, rec


def _walk_bytes(data, name):
    from acidcat.core.infra.source import BytesSource
    from acidcat.core.walk.base import Unsupported
    try:
        return contract.walk(BytesSource(data, name=name))
    except Unsupported:
        return None


# ── the patch ──────────────────────────────────────────────────────────

class Patch:
    """A planned edit: the file image it makes (`data`), one Record per edit
    (`records`), the edits reported as (field, old, new) (`applied`), and a
    format label (`format`: the profile's for a metadata edit, the walked
    file's for a field or byte-range edit). `notes` says what
    was stored other than as asked (a mode the acid chunk cannot hold)."""

    def __init__(self, before, data, records, name, fmt=None, raw_doc=None,
                 force=False, notes=()):
        self.before = before
        self.data = data
        self.records = list(records)
        self.name = name
        self.format = fmt
        self._raw_doc = raw_doc
        self.force = force
        self.notes = list(notes)
        self.repairs = []       # Repair records, one per field repair() set
        self.verified = False
        self.path = None        # the file it was made from, when there is one

    @property
    def applied(self):
        return [(r.key, r.old, r.new) for r in self.records]

    @property
    def changed(self):
        return self.data != self.before

    def spans(self):
        """(off, old bytes, new bytes) for each in-place byte edit."""
        return [(r.loc[1], r.old if r.kind == "bytes" else
                 self.before[r.loc[1]:r.loc[1] + r.loc[2]],
                 self.data[r.loc[1]:r.loc[1] + r.loc[2]])
                for r in self.records if r.loc is not None]

    def verify(self):
        """Re-read every edit from the new bytes and re-walk them. Returns the
        Patch; raises PatchError naming each edit that does not read back and
        each defect the original did not have."""
        problems = []
        for r in self.records:
            try:
                problems.extend(self._check(r))
            except (ValueError, struct.error) as e:
                problems.append("%s does not read back: %s" % (r.key, e))
        problems.extend(self._new_defects())
        if problems:
            raise PatchError("the patch does not verify: " + "; ".join(problems))
        self.verified = True
        return self

    def _check(self, r):
        if r.kind == "bytes":
            _l, off, n = r.loc
            if self.data[off:off + n] != r.wanted:
                return ["%s: the bytes written are not the bytes given" % r.key]
            return []
        if r.kind == "field":
            got = _read_back(r.field, self.data)
            out = []
            if not _same(got, r.wanted):
                out.append("%s reads back %r, not %r" % (r.key, got, r.wanted))
            typ = r.field["type"]
            if typ.startswith("bits:"):
                clen, bitpos, width = (int(x) for x in typ.split(":")[1:])
                _l, off, n = r.loc
                # bitpos counts from the container's most significant bit
                mask = ((1 << width) - 1) << (n * 8 - bitpos - width)
                a = int.from_bytes(self.before[off:off + n], "big") & ~mask
                b = int.from_bytes(self.data[off:off + n], "big") & ~mask
                if a != b:
                    out.append("%s changed the bits beside it" % r.key)
            return out
        if r.kind == "meta":
            # the profile's own reader: applying the same change again reports
            # what the new bytes hold as `old`
            _fmt, _d, again = edits.edit_metadata_data(
                self.data, self.name, {r.key: r.wanted})
            held = {f: old for f, old, _n in again}
            if r.key not in held:
                return ["%s is not reported by its profile after the edit" % r.key]
            got, want = held[r.key], r.new
            if not (_same(got, want) or str(got or "") == str(want or "")):
                return ["%s reads back %r, not %r" % (r.key, got, want)]
            return []
        if r.kind == "strip":
            return []                   # what a strip keeps is checked by _new_defects
        if r.kind == "cover":
            from acidcat.core.formats import cover as covermod
            got = _extract_cover(self.data, self.name, covermod)
            if r.wanted is None:
                return [] if got is None else ["the cover is still there"]
            if got is None or got[1] != r.wanted:
                return ["the cover does not read back"]
            return []
        return ["%s: unknown edit kind %s" % (r.key, r.kind)]

    def _new_defects(self):
        before = self._raw_doc or _walk_bytes(self.before, self.name)
        after = _walk_bytes(self.data, self.name)
        if after is None:
            return [] if before is None else ["the edited file no longer walks"]
        if before is None:
            return []
        count = lambda d: Counter(f["code"] for f in d["findings"]
                                  if f["kind"] == "defect")
        grown = count(after) - count(before)
        return ["a new %s defect (%d)" % (c, n) for c, n in sorted(grown.items())]

    def repair(self):
        """Bring back into line what this patch put out of step: a size, an
        offset, or a field that follows from another (a WAV's
        avg_bytes_per_sec after its sample_rate), through the constraint
        engine `check --fix` uses (core/write/constraints.py). Only the
        violations the patched bytes have and the original did not (or whose
        right value the edit moved) are this patch's to fix; if the original
        already had one the engine would also rewrite, that is refused, since repairing it is a decision
        about the file, not about this edit (`acidcat check --fix` makes it).
        A violation with no witness cannot be repaired and is refused too.
        Returns the Patch, with `repairs` listing what changed (Repair:
        addr, old, new, follows); call verify() after it."""
        from acidcat.core.write import constraints

        def violations(data):
            rep = constraints.analyze(data)
            return {} if rep is None else {(v.path, v.field): v
                                           for v in rep.violations}
        before, after = violations(self.before), violations(self.data)
        # the patch's: new ones, and ones whose right value the edit moved
        # (a byte rate that was already wrong, under a new sample rate)
        new = [v for k, v in after.items()
               if k not in before or before[k].computed != v.computed]
        if not new:
            return self
        blind = [v for v in new if not v.repairable]
        if blind:
            raise PatchError("repair() cannot fix what this patch broke: "
                             + "; ".join(v.describe() for v in blind))
        standing = [v for k, v in after.items() if v.repairable and v not in new]
        if standing:
            raise PatchError(
                "the original already has %d violation(s) repair would also "
                "rewrite (%s); run `acidcat check --fix` on it first"
                % (len(standing), "; ".join(v.describe() for v in standing)))
        fixed, _report = constraints.repair(self.data)
        self.data = fixed
        self.repairs = [Repair("%s#%s" % (v.path, v.field), v.stored,
                               v.computed, v.witness) for v in new]
        self.verified = False
        return self

    def commit(self, out=None, backup=True, path=None):
        """Write the patch, atomically: to `out`, leaving the file it was made
        from untouched, or in place over that file (or `path`, when the patch
        was made from bytes). An in-place commit first copies the file to
        `<name>_original` unless `backup=False` (an existing `_original` is
        never overwritten). Returns (written, backup or None)."""
        path = path or self.path
        if path is None and out is None:
            raise EditError("the patch was not made from a file; give out=")
        if path is None:
            writer.atomic_write(out, self.data)
            return out, None
        return writer.commit(path, self.data, out=out, overwrite=not backup)

    def __repr__(self):
        return "Patch(%d edit(s), %s)" % (
            len(self.records), "verified" if self.verified else "not verified")


def _extract_cover(data, name, covermod):
    suffix = os.path.splitext(name or "")[1] or ".mp3"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="acidcat_cover_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return covermod.extract(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def strip_patch(before, after, name, removed, fmt):
    """A Patch for a metadata strip: `after` is the stripped image, `removed`
    what went. verify() holds it to the rule every patch has: the stripped
    file walks with no defect the original did not have."""
    rec = [Record("strip", r, r, None) for r in removed]
    return Patch(before, after, rec, name, fmt)


def edit_path(path, changes, force=False):
    """A Patch for the file at `path` (walked only if an address needs it)."""
    with open(path, "rb") as fh:
        data = fh.read()
    patch = plan(data, path, changes, force=force)
    patch.path = path
    return patch
