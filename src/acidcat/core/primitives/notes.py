"""Walker warnings that say what KIND of thing they are.

A walker appends a warning for two very different reasons, and until now they
were the same object -- a bare string:

  the FILE is wrong        a size field overruns, a count is forged, a required
                           chunk is missing. The file has something to answer
                           for.

  our WALK stopped early   a read cap, a listing cap, a decompression bound.
                           The file has answered for nothing; we simply did not
                           look at all of it.

`anomalies.scan` turned every walker warning into a `structure` finding, and
findings drive `audit`'s exit code, so crossing one of our own internal limits
made a structurally perfect file exit 1. A script doing `audit f || quarantine f`
quarantined a clean file for being large.

Telling the two apart by matching the text of the message was the obvious fix
and the wrong one: it makes the wording of a human-readable string load-bearing
across a module boundary, which is the exact defect fixed elsewhere in 1.0 (the
anomaly checks dispatching on a display label). The kind travels with the
warning instead.

`Note` is a `str` subclass, so all 427 existing warning sites and every consumer
keep working untouched -- it renders, compares, sorts, and JSON-serialises as
the string it is. Only code that wants to know the kind has to ask.

    warns.append(hit("work_steps", CAP, n, f"stopped at the {CAP}-chunk cap"))
    warns.append("size field overruns the file")        # still a defect

A coverage note always names the limit it hit (`cap`), so it is made with
`acidcat.core.infra.limits.hit`; `Note` refuses a coverage kind without one.

One caveat worth knowing: string operations return plain `str`, so
`f"{note}"`, `note.strip()` and `prefix + note` all drop the kind. Classify
before reformatting, never after.
"""

DEFECT = "defect"        # the file has something to answer for
COVERAGE = "coverage"    # our walk stopped early; not a claim about the file

KINDS = (DEFECT, COVERAGE)


class Note(str):
    """A warning string that also carries its kind."""

    __slots__ = ("kind", "cap")

    def __new__(cls, text, kind=DEFECT, cap=None):
        if kind not in KINDS:
            raise ValueError(f"unknown warning kind {kind!r}; expected one of "
                             f"{KINDS}")
        if (kind == COVERAGE) != (cap is not None):
            raise ValueError("a coverage note names the limit it hit, and only "
                             "a coverage note does")
        obj = super().__new__(cls, text)
        obj.kind = kind
        obj.cap = cap
        return obj

    def __repr__(self):
        return f"Note({str.__repr__(self)}, kind={self.kind!r}, cap={self.cap!r})"

    # A Note must survive a round trip through copy/pickle with its kind, or it
    # silently downgrades to a defect wherever a structure is copied.
    def __reduce__(self):
        return (Note, (str(self), self.kind, self.cap))


def kind_of(warning):
    """The kind of any warning, including plain strings.

    Plain strings are defects. That is the safe default: an unclassified
    warning keeps the behaviour it has today rather than quietly dropping out
    of the findings a user relies on.
    """
    return getattr(warning, "kind", DEFECT)


def is_coverage(warning):
    return kind_of(warning) == COVERAGE
