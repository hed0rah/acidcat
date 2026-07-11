"""Concrete repairers: the IFF size cascade and the MP4 offset table, each
expressed through the shared constraint protocol.

These are thin adapters. The derivations themselves live in ``structure`` (the
IFF size cascade, the SIZE and ZERO kinds) and ``mp4repair`` (the MP4 chunk-offset
rebuild, the OFFSET kind); this module maps their output onto ``Violation`` and
guards the audio, so every verb above it (repair today, validate/audit next) is
format-agnostic.
"""

from acidcat.core import countrepair, flacrepair
from acidcat.core import mp4 as mp4mod
from acidcat.core import mp4repair, structure
from acidcat.core.constraints import (COUNT, OFFSET, SIZE, ZERO, Report, Repairer,
                                      Violation)


class AudioGuardError(Exception):
    """A repair would have altered an audio payload -- refused."""


# the primary audio payload id per IFF form type, guarded before/after a repair
_IFF_AUDIO = {b"WAVE": b"data", b"AIFF": b"SSND", b"AIFC": b"SSND"}


def _iff_audio(node):
    want = _IFF_AUDIO.get(node.form_type)
    if not want or not node.children:
        return None
    for c in node.children:
        if c.id == want and not c.is_container:
            return c.payload
    return None


def _iff_violation(change):
    """Map a structure.recompute change to a Violation. A top-level (master)
    size is witnessed by end-of-file; a nested size by its container's parsed
    contents; a pad byte by the spec."""
    if change["field"] == "pad_byte":
        return Violation(ZERO, change["path"], "pad_byte", change["old"],
                         change["new"], witness="spec (pad = 0x00)")
    top = "/" not in change["path"]
    witness = "end-of-file" if top else "container contents"
    return Violation(SIZE, change["path"], "size", change["old"], change["new"],
                     witness=witness)


class IffRepairer(Repairer):
    label = "IFF"

    def applies(self, data):
        return structure.is_iff(data)

    def _report(self, data, opts):
        node = structure.parse(data)
        changes = structure.recompute(node, normalize_pad=not (opts or {}).get("keep_pad"))
        label = node.form_type.decode("latin-1", "replace")
        return node, [_iff_violation(c) for c in changes], label

    def analyze(self, data, opts=None):
        _node, violations, label = self._report(data, opts)
        return Report(label, violations)

    def apply(self, data, opts=None):
        node, violations, label = self._report(data, opts)
        before = _iff_audio(structure.parse(data))
        new_data = structure.emit(node)
        after = _iff_audio(structure.parse(new_data))
        if before != after:
            raise AudioGuardError("audio payload would change")
        return new_data, Report(label, violations)


class Mp4OffsetRepairer(Repairer):
    label = "MP4"

    def applies(self, data):
        return mp4mod.is_mp4(data)

    def _mdat(self, data):
        b = mp4repair._find_boxes(data)["mdat"]
        return data[b["offset"] + b["hdr"]:b["offset"] + b["size"]]

    def _run(self, data):
        """Returns (new_bytes, Report). Out-of-scope files come back as a Report
        with a note and no violations rather than an error."""
        try:
            new_data, changes = mp4repair.repair_mp4(data)
        except mp4repair.Mp4RepairError as e:
            return data, Report(self.label, note=str(e))
        vios = [Violation(OFFSET, c["path"], c["field"], c["old"], c["new"],
                          witness="mdat position + stsz/stsc") for c in changes]
        return new_data, Report(self.label, vios)

    def analyze(self, data, opts=None):
        return self._run(data)[1]

    def apply(self, data, opts=None):
        before = self._mdat(data)
        new_data, report = self._run(data)
        if report.violations and self._mdat(new_data) != before:
            raise AudioGuardError("mdat payload would change")
        return new_data, report


class FlacRepairer(Repairer):
    label = "FLAC"

    def applies(self, data):
        return flacrepair.is_flac(data)

    def _violations(self, changes):
        out = []
        for c in changes:
            out.append(Violation(c["kind"], c["path"], c["field"], c["old"],
                                 c["new"], witness=c["witness"]))
        return out

    def _audio(self, data):
        _blocks, start, _ok = flacrepair.walk(data)
        return data[start:]

    def analyze(self, data, opts=None):
        return Report(self.label, self._violations(flacrepair.analyze(data)))

    def apply(self, data, opts=None):
        before = self._audio(data)
        new_data, changes = flacrepair.repair_flac(data)
        if changes and self._audio(new_data) != before:
            raise AudioGuardError("audio frames would change")
        return new_data, Report(self.label, self._violations(changes))


class CountRepairer(Repairer):
    """COUNT-kind: clamp a RIFF table-count (cue points, sample loops) that
    exceeds what the payload can hold. Length-preserving; never touches audio."""

    label = "WAVE"

    def applies(self, data):
        return countrepair.is_target(data)

    def _violations(self, changes):
        return [Violation(COUNT, c["path"], c["field"], c["old"], c["new"],
                          witness=c["witness"]) for c in changes]

    def analyze(self, data, opts=None):
        return Report(self.label, self._violations(countrepair.analyze(data)))

    def apply(self, data, opts=None):
        new_data, changes = countrepair.repair(data)
        return new_data, Report(self.label, self._violations(changes))
