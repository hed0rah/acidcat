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

DEFECT = "defect"            # the file has something to answer for
COVERAGE = "coverage"        # our walk stopped early; not a claim about the file
ENVIRONMENT = "environment"  # something outside the file: a sibling, an extra
INFO = "info"                # worth knowing, not wrong
ERROR = "error"              # acidcat failed (a walker bug), not the file

KINDS = (DEFECT, COVERAGE, ENVIRONMENT, INFO, ERROR)


class Note(str):
    """A warning string that also carries its kind, and its finding code.

    `code` is the stable id a consumer keys on (core/infra/findings.py); a
    note without one is reported as `legacy`. Made through
    `acidcat.core.infra.findings` (and `limits.hit` for coverage), which check
    the code is registered."""

    __slots__ = ("kind", "cap", "code")

    def __new__(cls, text, kind=DEFECT, cap=None, code=None):
        if kind not in KINDS:
            raise ValueError(f"unknown warning kind {kind!r}; expected one of "
                             f"{KINDS}")
        if (kind == COVERAGE) != (cap is not None):
            raise ValueError("a coverage note names the limit it hit, and only "
                             "a coverage note does")
        obj = super().__new__(cls, text)
        obj.kind = kind
        obj.cap = cap
        obj.code = code
        return obj

    def __repr__(self):
        return (f"Note({str.__repr__(self)}, kind={self.kind!r}, "
                f"cap={self.cap!r}, code={self.code!r})")

    # A Note must survive a round trip through copy/pickle with its kind, or it
    # silently downgrades to a defect wherever a structure is copied.
    def __reduce__(self):
        return (Note, (str(self), self.kind, self.cap, self.code))


def kind_of(warning):
    """The kind of any warning, including plain strings.

    Plain strings are defects. That is the safe default: an unclassified
    warning keeps the behaviour it has today rather than quietly dropping out
    of the findings a user relies on.
    """
    return getattr(warning, "kind", DEFECT)


def is_coverage(warning):
    return kind_of(warning) == COVERAGE


def code_of(warning):
    """The finding code a warning carries, or None for a plain string."""
    return getattr(warning, "code", None)


# ── across a JSON boundary ───────────────────────────────────────────────
#
# The sandboxed walk returns its result as JSON, where a Note is only its text
# and would come back a plain string, i.e. a defect. These carry the kind, cap
# and code across and restore the Note on the other side.

_WIRE = "__note__"


def to_wire(x):
    """`x` with every Note replaced by a JSON-safe dict that remembers it."""
    if isinstance(x, Note):
        return {_WIRE: str(x), "kind": x.kind, "cap": x.cap, "code": x.code}
    if isinstance(x, dict):
        return {k: to_wire(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_wire(v) for v in x]
    return x


def from_wire(x):
    """The inverse of `to_wire`, on its JSON-decoded result."""
    if isinstance(x, dict):
        if _WIRE in x:
            return Note(x[_WIRE], x["kind"], cap=x["cap"], code=x["code"])
        return {k: from_wire(v) for k, v in x.items()}
    if isinstance(x, list):
        return [from_wire(v) for v in x]
    return x
