"""The Type layer: a friendly surface over the enc-language.

A Type decodes payload bytes at a position into the walker field triple
(display value, raw value, enc string) and knows its on-disk length. The
fidelity contract is exact walker parity:

- a plain Int field's value IS the int and it carries NO enc/raw keys
  (fieldcodec.infer_enc covers plain ints downstream);
- an Enum field displays hex ("0x0001"), carries enc + raw for the editor,
  and its label ("PCM") lands in the field NOTE, placed by the interpreter.

enc/raw is the sharp edge: a wrong annotation must never verify, so these
types refuse layouts they cannot re-encode exactly instead of guessing, and
the not-yet-built types raise on construction rather than mis-encode.
"""

import struct
from dataclasses import dataclass

# value->label tables live in the core-owned vocab module, not in a walker,
# so the grammar layer no longer depends on a walker's internals.
from acidcat.core.vocab import TABLES

_STRUCT_CODES = {1: "B", 2: "H", 4: "I", 8: "Q"}


class Type:
    """Contract: length() in bytes; decode() -> (display, raw, enc|None)."""

    def length(self, payload, pos, ctx):
        raise NotImplementedError

    def decode(self, payload, pos, ctx):
        raise NotImplementedError


@dataclass
class Int(Type):
    """A plain integer; value == raw, no enc annotation (walker parity)."""

    nbytes: int
    signed: bool = False
    be: bool = False

    def __post_init__(self):
        # a plain Int emits no enc and relies on downstream inference, which
        # only round-trips 1/2/4/8-byte struct widths; an odd width (e.g. 3)
        # would silently lose editability, so force it through an explicit
        # Codec (u24be) instead of decoding into a dead end.
        if self.nbytes not in _STRUCT_CODES:
            raise ValueError(f"Int width {self.nbytes} has no struct code; "
                             "use a Codec for odd widths (e.g. u24be)")

    def length(self, payload=None, pos=None, ctx=None):
        return self.nbytes

    def decode(self, payload, pos, ctx):
        b = payload[pos:pos + self.nbytes]
        # the interpreter bounds-checks before calling, but assert the contract
        # here too so future call sites (Switch cases, repeat elements) cannot
        # emit an enc for bytes that are not on disk
        if len(b) != self.nbytes:
            raise ValueError("short read: decode called past the payload end")
        raw = int.from_bytes(b, "big" if self.be else "little",
                             signed=self.signed)
        return raw, raw, None


@dataclass
class Enum(Type):
    """An integer with a named value->label table: hex display, label note."""

    base: Int
    table: str
    hexwidth: int = 4

    def __post_init__(self):
        if self.base.signed or self.base.nbytes not in _STRUCT_CODES:
            raise ValueError("Enum base must be an unsigned 1/2/4/8-byte Int")
        # validate the table name at construction so a descriptor typo fails
        # loudly here (trusted code) instead of raising KeyError mid-interpret,
        # where the file-parse contract is degrade-never-raise
        if self.table not in TABLES:
            raise ValueError(f"unknown enum table {self.table!r}")

    def length(self, payload=None, pos=None, ctx=None):
        return self.base.nbytes

    def decode(self, payload, pos, ctx):
        raw = self.base.decode(payload, pos, ctx)[1]
        enc = (">" if self.base.be else "<") + _STRUCT_CODES[self.base.nbytes]
        return f"0x{raw:0{self.hexwidth}x}", raw, enc

    def note(self, raw):
        return TABLES[self.table].get(raw, f"unknown 0x{raw:0{self.hexwidth}x}")


@dataclass
class Hex(Type):
    """Hex display with enc; the note comes from the Field's note-source
    (NoteLookup/NoteFlags), not the type. ``pad`` zero-pads the display to N hex
    digits (channel_mask/mp3_flags are unpadded; acid type_flags is 08x); None =
    unpadded."""

    nbytes: int
    pad: int = None
    be: bool = False

    def __post_init__(self):
        if self.nbytes not in _STRUCT_CODES:
            raise ValueError(f"Hex width {self.nbytes} has no struct code")

    def length(self, payload=None, pos=None, ctx=None):
        return self.nbytes

    def decode(self, payload, pos, ctx):
        b = payload[pos:pos + self.nbytes]
        if len(b) != self.nbytes:
            raise ValueError("short read: decode called past the payload end")
        raw = int.from_bytes(b, "big" if self.be else "little")
        enc = (">" if self.be else "<") + _STRUCT_CODES[self.nbytes]
        disp = f"0x{raw:0{self.pad}x}" if self.pad else f"0x{raw:x}"
        return disp, raw, enc


class _NotBuilt(Type):
    """A type the engine does not implement yet: raises on construction so a
    descriptor can never carry an enc annotation that would not verify."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            f"{type(self).__name__} is not built yet (Phase 1/2)")


@dataclass
class Float(Type):
    """An IEEE float; display is the value rounded to ``round`` digits (the
    walker rounds for readability). No enc: a rounded display cannot re-encode
    to the exact bytes, so the field carries neither enc nor raw."""

    nbytes: int          # 4 (<f) | 8 (<d)
    round: int = None    # display rounding; None = the raw float
    be: bool = False

    def __post_init__(self):
        if self.nbytes not in (4, 8):
            raise ValueError("Float must be 4 or 8 bytes")

    def length(self, payload=None, pos=None, ctx=None):
        return self.nbytes

    def decode(self, payload, pos, ctx):
        code = ("f" if self.nbytes == 4 else "d")
        raw = struct.unpack_from((">" if self.be else "<") + code, payload, pos)[0]
        disp = round(raw, self.round) if self.round is not None else raw
        return disp, raw, None


class Bits(_NotBuilt):
    pass


class Codec(_NotBuilt):
    pass


class CString(_NotBuilt):
    pass
