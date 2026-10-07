"""Concrete repairers: the IFF size cascade and the MP4 offset table, each
expressed through the shared constraint protocol.

These are thin adapters. The derivations themselves live in ``structure`` (the
IFF size cascade, the SIZE and ZERO kinds) and ``mp4repair`` (the MP4 chunk-offset
rebuild, the OFFSET kind); this module maps their output onto ``Violation`` and
guards the audio, so every verb above it (repair today, validate/audit next) is
format-agnostic.
"""

from dataclasses import replace

from acidcat.core.write import countrepair, flacrepair, raterepair
from acidcat.core.formats import mp4 as mp4mod
from acidcat.core.write import mp4repair, structure
from acidcat.core.write.constraints import (COUNT, OFFSET, RATE, SIZE, ZERO, Report, Repairer,
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


def _lost_audio_violation(node):
    want = _IFF_AUDIO.get(node.form_type, b"data").decode("latin-1")
    return Violation(
        SIZE, f"{node.form_type.decode('latin-1', 'replace')}/{want}",
        "declared_size", None, None,
        witness="",                       # NOT repairable: the walk lost the audio
        detail=(f"the {want} chunk is in the file but the chunk walk never "
                f"reaches it: a size before it is wrong, so every chunk after "
                f"that one is misread. Recomputing the container size would "
                f"cut the audio off."))


def _desynced_pad(node, data, path=""):
    """(path, offset, id) of a non-zero "pad byte" that is really part of a
    chunk id the walk stepped over, or None.

    A size one too large (prg 6 -> 7) swallows the first byte of the next id,
    and the parse then calls the id's second byte a pad: --fix zeroed it and
    wrote new damage. On WAV/AIFF the audio guard refuses once the walk loses
    the audio chunk; nothing refused on a non-audio form such as APRG. Two
    tells, both required, so a real pad before appended junk is still fixed:
      - the walk broke right after the pad (it was the container's last child
        and the rest of the container went to its tail), and
      - a printable 4-char id containing the pad byte starts within the 3
        bytes before it, with a size that fits in the file -- a chunk header
        the parse misaligned on.
    """
    if not node.is_container:
        return None
    here = path + node.id.decode("latin-1", "replace").strip()
    for c in node.children:
        hit = _desynced_pad(c, data, here + "/")
        if hit:
            return hit
    if not node.children or not node.tail:
        return None
    last = node.children[-1]
    if not last.pad or last.pad_byte == 0:
        return None
    p = last.offset + 8 + last.computed_size()          # the pad byte
    for q in range(max(0, p - 3), p + 1):
        if (structure._id_ok(data, q) and q + 8 <= len(data)
                and q + 8 + int.from_bytes(bytes(data[q + 4:q + 8]),
                                           "little" if node.endian == "<"
                                           else "big") <= len(data)):
            return (here + "/" + last.id.decode("latin-1", "replace").strip(),
                    p, bytes(data[q:q + 4]).decode("latin-1"))
    return None


def _desync_violation(hit):
    path, off, cid = hit
    return Violation(
        SIZE, path, "size", None, None, witness="",
        detail=(f"the byte read as this chunk's pad (offset {off:,}) is part "
                f"of what looks like the chunk id '{cid}', and the walk breaks "
                f"right after it: a size here is wrong and the chunks after it "
                f"are misread. Rewriting from this parse would damage them."))


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
    # RF64 keeps its real sizes in the ds64 chunk and writes 0xFFFFFFFF in the
    # 32-bit fields. The structure model reads those fields literally, so every
    # valid RF64 failed validation ("data overruns the file") and repair
    # proposed an 88-byte RIFF size. Declined, as the tag editor declines it.
    _DS64_NOTE = ("RF64 keeps its sizes in the ds64 chunk; the 32-bit size "
                  "fields are placeholders, and checking them is not modelled")

    # the Akai S5000/S6000 writes 0 in an .akp's RIFF size and reads the
    # program to the end of the file; most .akp files in the wild carry it
    _AKAI_NOTE = ("the RIFF size is 0, as the Akai S5000/S6000 writes it; "
                  "the program runs to the end of the file")

    def applies(self, data):
        return structure.is_iff(data)

    def _is_rf64(self, data):
        return bytes(data[:4]) == b"RF64"

    def _akai_zero(self, data):
        return bytes(data[8:12]) == b"APRG" and bytes(data[4:8]) == bytes(4)

    def _report(self, data, opts):
        node = structure.parse(data)
        orphan = _orphaned_audio(node)
        # before recompute, which zeroes the pad bytes it reports
        desync = _desynced_pad(node, data)
        changes = structure.recompute(node, normalize_pad=not (opts or {}).get("keep_pad"))
        label = node.form_type.decode("latin-1", "replace")
        akai_short = None
        if self._akai_zero(data):
            # the stored 0 is the convention, so the size change itself is
            # dropped. what the 0 stands for -- the program runs to the end of
            # the file -- must still hold: the top-level parse reads to EOF, so
            # a recomputed size short of len-8 means a chunk overruns the file
            # (the parse leaves it as tail) or bytes follow the last chunk.
            # dropping that too blinded check to a truncated program.
            for c in changes:
                if (c["path"] == "RIFF" and c["field"] == "size"
                        and c["new"] != len(data) - 8):
                    akai_short = c["new"]
            changes = [c for c in changes if not (c["path"] == "RIFF"
                                                  and c["field"] == "size")]
        violations = [_iff_violation(c) for c in changes]
        if akai_short is not None:
            gap = len(data) - 8 - akai_short
            violations.insert(0, Violation(
                SIZE, "RIFF", "size", 0, akai_short, witness="",
                detail=(f"the RIFF size is 0 (Akai's run-to-end-of-file "
                        f"convention), but the chunk walk stops {gap:,} "
                        f"byte(s) short of the end of the file: a chunk "
                        f"overruns the file, a size before it is wrong, or "
                        f"bytes follow the last chunk")))
        want = _IFF_AUDIO.get(node.form_type)
        if orphan:
            # The master-size change is the destructive one, so it must stop
            # advertising itself as repairable -- otherwise validate and audit
            # both print "fix with: acidcat repair" for a file repair refuses,
            # which sends the user round a loop. Strip the witness; the orphan
            # violation, reported first, explains why.
            violations = [replace(v, witness="") for v in violations]
            violations.insert(0, _orphan_violation(node, orphan))
        elif want and _iff_audio(node) is None and want in bytes(data):
            # apply() refuses exactly this file (audio present, not in the
            # tree); analyze must not advertise a fix it will refuse. Checked
            # after the orphan case, whose overrunning chunk also sits outside
            # the tree and has the more specific explanation.
            violations = [replace(v, witness="") for v in violations]
            violations.insert(0, _lost_audio_violation(node))
        elif desync:
            # apply() refuses this file; analyze must not advertise a fix
            violations = [replace(v, witness="") for v in violations]
            violations.insert(0, _desync_violation(desync))
        return node, violations, label, orphan, desync

    def analyze(self, data, opts=None):
        if self._is_rf64(data):
            return Report("WAVE", note=self._DS64_NOTE)
        _node, violations, label, _orphan, _desync = self._report(data, opts)
        return Report(label, violations,
                      note=self._AKAI_NOTE if self._akai_zero(data) else "")

    def apply(self, data, opts=None):
        if self._is_rf64(data):
            return data, Report("WAVE", note=self._DS64_NOTE)
        node, violations, label, orphan, desync = self._report(data, opts)
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
        if desync:
            raise AudioGuardError(
                f"{desync[0]}: the byte read as its pad (offset {desync[1]:,}) "
                f"is part of the chunk id '{desync[2]}', so the chunk walk is "
                f"misaligned and zeroing it would damage that chunk. Nothing "
                f"written")
        if self._akai_zero(data) and not violations:
            return data, Report(label, [], note=self._AKAI_NOTE)
        new_data = structure.emit(node)
        if self._akai_zero(data):
            new_data = bytearray(new_data)
            new_data[4:8] = bytes(4)          # keep the writer's convention
            new_data = bytes(new_data)
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


class RateRepairer(Repairer):
    """RATE-kind: a WAV's block_align, avg_bytes_per_sec and smpl
    sample_period made to follow the sample format they are functions of
    (raterepair). Size-stable; never touches audio."""

    label = "WAVE"

    def applies(self, data):
        return raterepair.is_target(data)

    def _violations(self, changes):
        return [Violation(RATE, c["path"], c["field"], c["old"], c["new"],
                          witness=c["witness"]) for c in changes]

    def analyze(self, data, opts=None):
        return Report(self.label, self._violations(raterepair.analyze(data)))

    def apply(self, data, opts=None):
        new_data, changes = raterepair.repair(data)
        return new_data, Report(self.label, self._violations(changes))
