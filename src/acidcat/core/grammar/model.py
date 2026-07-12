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

from dataclasses import dataclass

from acidcat.core.vocab import CTX_KEYS


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


@dataclass
class Format:
    """A file format as data: a container strategy + per-region specs."""

    name: str             # display label when the strategy offers none
    container: str        # strategy id in grammar.strategies.STRATEGIES
    regions: dict         # exact region id -> Region
