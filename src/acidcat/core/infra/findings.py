"""Finding codes: the stable ids consumers key on, and the notes that carry them.

A finding is one record for a walker warning and a forensic observation alike
(node-v1.md section 9): `kind`, `code`, `severity`, `message`. Consumers select
by kind and code, never by message text, so the wording of a message can change
without breaking a script, the TUI or `audit`.

A walker says what it found with the helper for its kind:

    warns.append(defect("size.overrun", f"chunk {cid} claims {n} bytes ..."))
    warns.append(environment("sibling.missing", f"{lib} is not beside the file"))

Each returns a `Note`, the same `str` every consumer already handles, carrying
its kind and code. A coverage note is made by `limits.hit`, which gives it a
`cap.*` code. A warning still appended as a plain string is a defect with code
`legacy`; the Document counts them (`typing.findings_legacy`) and
tests/test_findings.py holds the number of such sites in the walkers to a
ratchet that only falls.
"""

from acidcat.core.infra.limits import CODES as _CAP_CODES
from acidcat.core.primitives.notes import (
    COVERAGE, DEFECT, ENVIRONMENT, ERROR, INFO, Note,
)

# code -> (kind, default severity, what it means)
REGISTRY = {
    # the file breaks its format
    "size.overrun": (DEFECT, "warn",
                     "a declared size or count runs past the file or its parent"),
    "pointer.dangling": (DEFECT, "warn",
                         "an offset in the file points outside it"),
    "magic.mismatch": (DEFECT, "warn",
                       "a required magic number or signature is absent or wrong"),
    "header.truncated": (DEFECT, "warn",
                         "the file ends inside a fixed-size header"),
    "chunk.short": (DEFECT, "warn",
                    "a chunk is shorter than the fixed layout the spec gives it"),
    "length.misaligned": (DEFECT, "notice",
                          "a length is not a whole number of its units"),
    "checksum.mismatch": (DEFECT, "warn",
                          "a checksum the file carries does not match its bytes"),
    "reserved.nonzero": (DEFECT, "notice",
                         "a field the spec reserves is not zero"),
    "parse.failed": (DEFECT, "warn",
                     "an embedded document (JSON, XML, a nested file) does not parse"),
    "geometry.invalid": (DEFECT, "warn",
                         "a chunk's declared extent is impossible"),
    "legacy": (DEFECT, "warn",
               "a walker warning not yet given a code"),
    # outside the file
    "sibling.missing": (ENVIRONMENT, "notice",
                        "a file this one names is not beside it"),
    "sibling.unchecked": (ENVIRONMENT, "info",
                          "a file this one names could not be looked for "
                          "(the input has no directory)"),
    # worth knowing
    "triage.generic": (INFO, "info",
                       "no format-specific walker; the generic triage ran"),
    "encoding.unknown": (INFO, "notice",
                         "the sample encoding could not be determined, so the "
                         "geometry is a guess"),
    # acidcat failed
    "walker.error": (ERROR, "alert", "the walker raised; a bug in acidcat"),
    "geometry.error": (ERROR, "alert", "normalising the walk raised; a bug in acidcat"),
}
REGISTRY.update({code: (COVERAGE, "info", f"the walk stopped at its {name} limit")
                 for name, code in _CAP_CODES.items()})

# the forensic scan's rules (core/forensics/anomalies.py), as codes; "structure"
# and "coverage" are the walker's own notes and keep the note's code
ANOMALY_RULES = (
    "trailing_data", "polyglot", "nonprintable_text", "duplicate_frame",
    "cavity_content", "application_block", "ogg_multistream", "mp4_mdat_coverage",
    "id3_padding_nonzero", "dual_endianness", "nonzero_pad", "duplicate_chunk",
    "wrong_format_tag", "embedded_standalone_media", "json_trailing_data",
    "json_unknown_key", "unaccounted_bytes", "lsb_entropy",
)
REGISTRY.update({f"anomaly.{r}": (DEFECT, "notice", f"forensic rule {r}")
                 for r in ANOMALY_RULES})
REGISTRY["anomaly.check_failed"] = (ERROR, "notice",
                                    "a forensic rule raised and was not applied")


def kind_of_code(code):
    return REGISTRY[code][0]


def severity_of(code):
    return REGISTRY[code][1]


def _note(code, message, kind):
    if code not in REGISTRY:
        raise ValueError(f"unregistered finding code {code!r}; add it to "
                         f"acidcat.core.infra.findings.REGISTRY")
    if REGISTRY[code][0] != kind:
        raise ValueError(f"{code!r} is a {REGISTRY[code][0]} code, not {kind}")
    return Note(message, kind, code=code)


def defect(code, message):
    """The file breaks its format."""
    return _note(code, message, DEFECT)


def environment(code, message):
    """Something outside the file: a sibling not found or not looked for."""
    return _note(code, message, ENVIRONMENT)


def info(code, message):
    """Worth knowing, not wrong."""
    return _note(code, message, INFO)


def error(code, message):
    """acidcat failed, not the file."""
    return _note(code, message, ERROR)


def anomaly_code(rule):
    """The code for a forensic finding's rule."""
    return f"anomaly.{rule}"
