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

from dataclasses import dataclass

from acidcat.core.walk.wav import _FORMAT_TAGS

# value-to-label tables a descriptor references by name, seeded from the
# walkers' own decode dicts -- reuse, never re-type.
TABLES = {"wave_format_tags": _FORMAT_TAGS}

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

    def length(self, payload=None, pos=None, ctx=None):
        return self.nbytes

    def decode(self, payload, pos, ctx):
        raw = int.from_bytes(payload[pos:pos + self.nbytes],
                             "big" if self.be else "little",
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

    def length(self, payload=None, pos=None, ctx=None):
        return self.base.nbytes

    def decode(self, payload, pos, ctx):
        raw = self.base.decode(payload, pos, ctx)[1]
        enc = (">" if self.base.be else "<") + _STRUCT_CODES[self.base.nbytes]
        return f"0x{raw:0{self.hexwidth}x}", raw, enc

    def note(self, raw):
        return TABLES[self.table].get(raw, f"unknown 0x{raw:0{self.hexwidth}x}")


class _NotBuilt(Type):
    """A type the engine does not implement yet: raises on construction so a
    descriptor can never carry an enc annotation that would not verify."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            f"{type(self).__name__} is not built yet (Phase 1/2)")


class Float(_NotBuilt):
    pass


class Bits(_NotBuilt):
    pass


class Codec(_NotBuilt):
    pass


class CString(_NotBuilt):
    pass
