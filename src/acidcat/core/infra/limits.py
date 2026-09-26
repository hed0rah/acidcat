"""The limits a walk runs under, and the one way to say one was hit.

`Limits` is what a caller passes: how much to read, list, inflate and decode.
The Document records it, so two Documents can be compared knowing they were
made the same way (node-v1.md section 9.1).

`hit()` is how a walker says it stopped early. It returns a coverage note that
names the limit, the bound it applied and how much the file asked for, and it
is the only way to make one: `Note` refuses a coverage kind without a cap.
So a cap hit cannot be reported as a defect by forgetting to classify it, and
the Document's `limits.hit` is read off the notes rather than kept in step by
hand.

    warns.append(hit("list_rows", _PRESET_LIST_CAP, len(presets),
                     f"listing the first {_PRESET_LIST_CAP} of {len(presets)} presets"))

Each walker's bound is still its own module constant (the per-format value the
cap ledger in tests/test_cap_announcements.py sweeps); `hit` carries it as
`limit`. `used` is how much the file asked for as far as the walk saw: the
total when it is known, else the count reached, which is then a lower bound.
"""

from dataclasses import asdict, dataclass, fields
from typing import Optional

from acidcat.core.primitives.notes import COVERAGE, Note


@dataclass(frozen=True)
class Limits:
    read_bytes: int = 64 << 20       # default read window per format
    chunk_payload: int = 64 << 10    # bytes of a chunk payload kept for display
    inflate_bytes: int = 64 << 20    # output of any one decompression
    work_steps: int = 4_000_000      # chunks, objects, commands, sectors walked
    list_rows: Optional[int] = None  # None: each walker's own display default
    frame_rows: int = 100_000        # --frames listings
    depth: int = 32                  # nested layers and explore
    decode: bool = False             # the "extra decoding work" half of deep

    @classmethod
    def for_deep(cls, deep):
        """What `deep=True` has always meant: do the extra decoding work."""
        return cls(decode=bool(deep))

    def record(self, hit=()):
        """The Document's `limits` object: these values, and which were hit."""
        return dict(asdict(self), hit=sorted(set(hit)))


NAMES = tuple(f.name for f in fields(Limits) if f.name != "decode")

# the finding code each limit's coverage note carries (node-v1.md section 9)
CODES = {"read_bytes": "cap.read", "chunk_payload": "cap.payload",
         "inflate_bytes": "cap.inflate", "work_steps": "cap.steps",
         "list_rows": "cap.list", "frame_rows": "cap.frames",
         "depth": "cap.depth"}


def hit(name, limit, used, message):
    """A coverage note: the walk stopped at `limit` of `name`; the file asked
    for `used`. Not a statement about the file."""
    if name not in NAMES:
        raise ValueError(f"unknown limit {name!r}; expected one of {NAMES}")
    return Note(message, COVERAGE, code=CODES[name],
                cap={"name": name, "limit": int(limit), "used": int(used)})
