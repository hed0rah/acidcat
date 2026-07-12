"""Descriptor model: a format as pure data.

A Format names its container strategy and maps region ids (chunk/block/box
ids) to Region specs. Ids are matched EXACTLY -- "fmt " keeps its trailing
space -- because the walkers' parser registries match exactly; a forgiving
lookup would parse chunks the walker leaves unparsed. A Region is a flat
struct (an ordered tuple of Fields) or an opaque payload. A Field pairs a
name with a Type (grammar/types.py) that knows its length, its decode, and
its enc string.

The v1 vocabulary is deliberately tiny (struct/payload regions, two types).
The later constructs -- structured guards, switch dispatch, valid ranges,
repeat-over-records, format-level rules -- are additive keyword fields, so
existing descriptors never break as the vocabulary grows.
"""

import operator
from dataclasses import dataclass

from acidcat.core.vocab import CTX_KEYS

# guards are structured atoms, not an expression language: a field-vs-constant
# comparison and a remaining-bytes check over a FIXED operator set. Anything
# richer routes to a named helper (grammar/helpers.py), never grows here.
_OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt,
        "<=": operator.le, ">": operator.gt, ">=": operator.ge}


@dataclass
class Cmp:
    """Guard: an already-parsed same-region field compared to a constant."""

    field: str
    op: str
    const: object

    def holds(self, local, payload, pos):
        return self.field in local and _OPS[self.op](local[self.field], self.const)


@dataclass
class Remaining:
    """Guard: the bytes remaining from the current parse position vs a constant."""

    op: str
    const: int

    def holds(self, local, payload, pos):
        return _OPS[self.op](len(payload) - pos, self.const)


@dataclass
class Field:
    """One named field inside a struct region."""

    name: str
    type: object          # a grammar.types.Type
    note: str = ""        # static display note (e.g. "Hz"); when empty, an
                          # Enum type supplies its label note dynamically
    ctx: str = None       # file-global ctx key to publish the raw value
                          # under, using the walker's SEMANTIC names ("bits",
                          # not "bits_per_sample"); None = unpublished
    when: tuple = ()      # guards (Cmp/Remaining); ALL must hold or the field is
                          # skipped (e.g. cb_size only when format_tag != 0xFFFE)

    def __post_init__(self):
        # validate the ctx key against the sanctioned semantic vocabulary at
        # construction, so a descriptor typo fails loudly in trusted code
        # instead of silently missing an index column at parse time
        if self.ctx is not None and self.ctx not in CTX_KEYS:
            raise ValueError(
                f"unknown ctx key {self.ctx!r} on field {self.name!r}; "
                "add it to core.vocab.CTX_KEYS if it is a real semantic key")


@dataclass
class Region:
    """How to parse one container region (chunk/block/box)."""

    kind: str = "struct"  # "struct" (ordered fields) | "payload" (opaque)
    fields: tuple = ()
    min_len: int = 0      # below this payload length the region degrades to a
    min_len_msg: str = "" # "truncated" summary + this warning, 0 fields (the
                          # walkers' all-or-nothing convention, e.g. fmt < 16)


@dataclass
class Format:
    """A file format as data: a container strategy + per-region specs."""

    name: str             # display label when the strategy offers none
    container: str        # strategy id in grammar.strategies.STRATEGIES
    regions: dict         # exact region id -> Region
