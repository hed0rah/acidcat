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

from acidcat.core.vocab import CTX_KEYS, FLAGS, TABLES

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

    def __post_init__(self):
        if self.op not in _OPS:  # fail loud in trusted code, not KeyError mid-parse
            raise ValueError(f"unknown guard op {self.op!r}")

    def holds(self, local, payload, pos):
        return self.field in local and _OPS[self.op](local[self.field], self.const)


@dataclass
class Remaining:
    """Guard: the bytes remaining from the current parse position vs a constant."""

    op: str
    const: int

    def __post_init__(self):
        if self.op not in _OPS:
            raise ValueError(f"unknown guard op {self.op!r}")

    def holds(self, local, payload, pos):
        return _OPS[self.op](len(payload) - pos, self.const)


@dataclass
class NoteLookup:
    """Field note = a value->label table lookup on the raw (optionally masked),
    e.g. mp3_id, mp3_flags. The table is shared with the walker via core/vocab."""

    table: str
    mask: int = None
    default: str = ""

    def __post_init__(self):
        if self.table not in TABLES:
            raise ValueError(f"unknown note table {self.table!r}")

    def resolve(self, raw):
        return TABLES[self.table].get(
            raw & self.mask if self.mask is not None else raw, self.default)


@dataclass
class NoteFlags:
    """Field note = flag decomposition of the raw over a bit->name list (the
    walk/base._flag_names pattern), e.g. channel_mask -> speaker positions."""

    table: str

    def __post_init__(self):
        if self.table not in FLAGS:
            raise ValueError(f"unknown flags table {self.table!r}")

    def resolve(self, raw):
        names = [n for i, n in enumerate(FLAGS[self.table]) if raw & (1 << i)]
        return ", ".join(names) if names else "none"


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
class Case:
    """One arm of a Switch, emitted ALL-OR-NOTHING: only when the available
    window is at least ``min_window`` bytes (the walker's ``len(ext) >= N``
    guard), so a short region never yields a partial group."""

    min_window: int
    fields: tuple


@dataclass
class Switch:
    """A tagged-union entry in a region's ordered fields tuple: dispatch on a
    parsed field's value to one Case's fields. Case parsing is bounded by
    ``window`` (an earlier field, e.g. cb_size) clamped to the remaining
    payload, or by the remaining payload alone when window is None (the
    EXTENSIBLE branch, which the walker reads at fixed offsets)."""

    on: str               # earlier field whose raw value selects the case
    cases: dict           # const -> Case
    window: str = None    # earlier field bounding case parsing; None = unwindowed
    default: tuple = ()   # entries when no case matches (usually empty)


@dataclass
class Helper:
    """A named decode helper (grammar.helpers._HELPERS): the budgeted escape
    hatch for irregular decode. Counts against the measurable helper budget."""

    name: str


def _validate_entries(region_id, entries, declared):
    """Every guard/switch reference must point at an earlier-declared field in
    the same scope, so a typo'd field name fails at Format construction instead
    of silently omitting a guarded field forever. Cases validate against a copy
    (a name declared in one arm does not leak to a sibling)."""
    for e in entries:
        if isinstance(e, Switch):
            for ref in (e.on, e.window):
                if ref is not None and ref not in declared:
                    raise ValueError(f"region {region_id!r}: Switch references "
                                     f"undeclared field {ref!r}")
            for case in e.cases.values():
                _validate_entries(region_id, case.fields, set(declared))
        elif isinstance(e, Helper):
            continue
        else:  # Field
            for g in e.when:
                ref = getattr(g, "field", None)
                if ref is not None and ref not in declared:
                    raise ValueError(f"region {region_id!r}: guard on {e.name!r} "
                                     f"references undeclared field {ref!r}")
            declared.add(e.name)


@dataclass
class Format:
    """A file format as data: a container strategy + per-region specs."""

    name: str             # display label when the strategy offers none
    container: str        # strategy id in grammar.strategies.STRATEGIES
    regions: dict         # exact region id -> Region

    def __post_init__(self):
        for rid, region in self.regions.items():
            _validate_entries(rid, getattr(region, "fields", ()), set())
