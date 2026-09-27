"""Concrete repairers: the IFF size cascade and the MP4 offset table, each
expressed through the shared constraint protocol.

These are thin adapters. The derivations themselves live in ``structure`` (the
IFF size cascade, the SIZE and ZERO kinds) and ``mp4repair`` (the MP4 chunk-offset
rebuild, the OFFSET kind); this module maps their output onto ``Violation`` and
guards the audio, so every verb above it (repair today, validate/audit next) is
format-agnostic.
"""

from dataclasses import replace

from acidcat.core.write import countrepair, flacrepair
from acidcat.core.formats import mp4 as mp4mod
from acidcat.core.write import mp4repair, structure
from acidcat.core.write.constraints import (COUNT, OFFSET, SIZE, ZERO, Report, Repairer,
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


def _orphaned_audio(node):
    """Bytes of audio that the parse left OUTSIDE the container, or None.

    A recorder that dies mid-take leaves a `data` chunk whose declared size
    overruns the file. structure.parse deliberately refuses to absorb an
    overrunning chunk (it could be appended junk), so `data` lands in
    node.tail instead of node.children -- and the top level does not count its
    tail toward the recomputed size.

    That combination is destructive. _iff_audio compares payloads before and
    after, which is vacuous here: the payload was never a child on either side,
    so both are None, the guard passes, and repair writes a master size that
    ends before the audio. Measured on a 5-second truncated recording: 882,000
    bytes readable by the stdlib `wave` module before, unreadable after, exit
    code 0.

    So look for the audio chunk in the tail specifically -- narrow on purpose,
    because a tail that is genuinely appended junk should still be repairable.
    """
    want = _IFF_AUDIO.get(node.form_type)
    if not want or not node.tail:
        return None
    # offset 1 as well as 0: a mis-parse that stops on a pad byte leaves the
    # audio header one byte into the tail (the odd-nested-LIST case)
    if node.tail[:4] != want and node.tail[1:5] != want:
        return None
    return len(node.tail)


def _orphan_violation(node, n_bytes):
    want = _IFF_AUDIO.get(node.form_type, b"data").decode("latin-1")
    return Violation(
        SIZE, f"{node.form_type.decode('latin-1', 'replace')}/{want}",
        "declared_size", n_bytes, 0,
        witness="",                       # NOT repairable: no safe rewrite
        detail=(f"the {want} chunk declares more bytes than the file holds, so "
                f"{n_bytes:,} bytes of audio sit outside the container. "
                f"Recomputing the size here would orphan them."))


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
        orphan = _orphaned_audio(node)
        changes = structure.recompute(node, normalize_pad=not (opts or {}).get("keep_pad"))
        label = node.form_type.decode("latin-1", "replace")
        violations = [_iff_violation(c) for c in changes]
        if orphan:
            # The master-size change is the destructive one, so it must stop
            # advertising itself as repairable -- otherwise validate and audit
            # both print "fix with: acidcat repair" for a file repair refuses,
            # which sends the user round a loop. Strip the witness; the orphan
            # violation, reported first, explains why.
            violations = [replace(v, witness="") for v in violations]
            violations.insert(0, _orphan_violation(node, orphan))
        return node, violations, label, orphan

    def analyze(self, data, opts=None):
        _node, violations, label, _orphan = self._report(data, opts)
        return Report(label, violations)

    def apply(self, data, opts=None):
        node, violations, label, orphan = self._report(data, opts)
        if orphan:
            want = _IFF_AUDIO.get(node.form_type, b"data").decode("latin-1")
            raise AudioGuardError(
                f"the {want} chunk overruns the file; recomputing the container "
                f"size would leave {orphan:,} bytes of audio outside it and "
                f"unreadable. Nothing written -- the audio is still intact")
        before = _iff_audio(structure.parse(data))
        want = _IFF_AUDIO.get(node.form_type)
        if want and before is None and want in bytes(data):
            # the equality guard below is vacuous when the audio chunk was
            # never located as a child: None == None passes while a mis-parse
            # quietly writes a size that ends before the audio. If the audio id
            # exists anywhere in the bytes but not in the tree, refuse rather
            # than certify a comparison of nothing against nothing.
            raise AudioGuardError(
                f"cannot locate the {want.decode('latin-1')} chunk in the "
                f"parsed structure, so audio preservation cannot be verified. "
                f"Nothing written")
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

    def _run(self, data, patch=True):
        """Returns (new_bytes, Report). Out-of-scope files come back as a Report
        with a note and no violations rather than an error."""
        try:
            new_data, changes = mp4repair.repair_mp4(data, patch=patch)
        except mp4repair.Mp4Damage as e:
            # internally inconsistent sample tables: report damage with no
            # witness (nothing safe to rewrite), never an out-of-scope OK --
            # validate must not certify a forged table as consistent.
            return data, Report(self.label, [Violation(
                COUNT, "moov sample tables", "entry_count", None, None,
                witness="", detail=str(e))])
        except mp4repair.Mp4RepairError as e:
            return data, Report(self.label, note=str(e))
        vios = [Violation(OFFSET, c["path"], c["field"], c["old"], c["new"],
                          witness="mdat position + stsz/stsc") for c in changes]
        return new_data, Report(self.label, vios)

    def analyze(self, data, opts=None):
        # patch=False: analyze is read-only, so do not materialize the
        # patched full-file copy just to enumerate the violations
        return self._run(data, patch=False)[1]

    def apply(self, data, opts=None):
        # locate mdat only when there is a rewrite to guard: finding it first
        # raised on a multi-track file _run would have reported as out of scope
        new_data, report = self._run(data)
        if (report.violations and new_data is not data
                and self._mdat(new_data) != self._mdat(data)):
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
