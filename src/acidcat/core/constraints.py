"""The constraint core: the shared vocabulary that write, repair, and (soon)
validate/audit all speak.

A container is a set of fields, some free and some *derived* -- their correct
value is a function of other parts of the file. A derived field whose stored
value disagrees with that function is a ``Violation``. Every structural verb is
one move over violations:

    analyze(data)  -> the violations present now      (read-only: validate, audit)
    apply(data)    -> fix the witnessed ones, re-emit  (repair; write adds a mutation first)

A violation is repairable only when it has an independent *witness* -- a source of
truth other than the field itself (end-of-file for a master size, the target's
real position for an offset, the spec for a pad byte). Un-witnessed violations are
reported, never silently "fixed". The four derived-field kinds observed across the
formats acidcat models are SIZE, OFFSET, COUNT, and ZERO; a repairer declares which
kinds it speaks and how it derives and witnesses them.

This module owns the protocol and the registry. The per-format derivations live in
their own engines (``structure`` for the IFF size cascade, ``mp4repair`` for MP4
offset tables); each is wrapped here as a ``Repairer`` so the command layer, and
every future verb, is format-agnostic.
"""

from dataclasses import dataclass, field

# the four derived-field kinds
SIZE = "size"
OFFSET = "offset"
COUNT = "count"
ZERO = "zero"


@dataclass
class Violation:
    """One derived field whose stored value disagrees with its function."""
    kind: str            # SIZE / OFFSET / COUNT / ZERO
    path: str            # where it lives, e.g. "RIFF/data" or "stco"
    field: str           # the field name, e.g. "size", "chunk_offsets", "pad_byte"
    stored: object       # the value on disk
    computed: object     # the value its function yields
    witness: str = ""    # the independent source of truth ("" if un-witnessed)
    detail: str = ""     # human note

    @property
    def repairable(self):
        return bool(self.witness)

    def describe(self):
        if self.field == "pad_byte":
            return f"{self.path} pad byte: 0x{self.stored:02x} -> 0x{self.computed:02x}"
        if isinstance(self.stored, int) and isinstance(self.computed, int):
            return f"{self.path} {self.field}: {self.stored:,} -> {self.computed:,} bytes"
        return f"{self.path} {self.field}: {self.stored} -> {self.computed}"


@dataclass
class Report:
    """The result of analyzing (or repairing) one file."""
    label: str
    violations: list = field(default_factory=list)
    note: str = ""       # e.g. an out-of-scope reason ("multi-track ...")

    @property
    def repairable(self):
        return [v for v in self.violations if v.repairable]


class Repairer:
    """A format's constraint engine, expressed through the shared protocol.

    Subclasses implement ``applies`` (does this engine handle these bytes),
    ``analyze`` (read-only: what violations are present), and ``apply`` (produce
    corrected bytes for the witnessed violations, guarding the audio)."""

    label = "?"

    def applies(self, data):
        raise NotImplementedError

    def analyze(self, data, opts=None):
        """Return a Report; never mutates ``data``. ``opts`` carries verb flags
        (e.g. ``keep_pad``)."""
        raise NotImplementedError

    def apply(self, data, opts=None):
        """Return (new_bytes, Report). Must not alter any audio payload; a
        repairer that would is a bug and should raise."""
        raise NotImplementedError


# ── registry ───────────────────────────────────────────────────────
# import the concrete repairers lazily to avoid an import cycle (they import
# walkers that may import back here in future).

def _repairers():
    from acidcat.core.repairers import (FlacRepairer, IffRepairer,
                                        Mp4OffsetRepairer)
    return (IffRepairer(), Mp4OffsetRepairer(), FlacRepairer())


def repairer_for(data):
    """The first registered repairer that handles ``data``, or None."""
    for r in _repairers():
        if r.applies(data):
            return r
    return None


def analyze(data, opts=None):
    """Read-only: the Report for ``data`` (validate/audit entry point). Returns
    None when no repairer applies."""
    r = repairer_for(data)
    return r.analyze(data, opts) if r else None


def repair(data, opts=None):
    """Fix the witnessed violations in ``data``. Returns (new_bytes, Report), or
    None when no repairer applies."""
    r = repairer_for(data)
    return r.apply(data, opts) if r else None
