"""Every bound in the tree is accounted for, and a bound that bites says so.

This exists because the same defect kept arriving from different directions: a
cap, filter or read window applied, and the bounded result then presented as the
whole answer. A scan reporting its 20-item cap as "20 concealed sectors" when
there were 65. A directory walk printing "all N consistent" for a tree it never
opened. A 64 MB read cap reporting a CRC failure on a pristine file, because it
stopped mid-frame and judged the fragment. Nine of those were fixed one at a
time; this file is the attempt to stop the tenth.

TWO HALVES, and the second matters more.

The SWEEP patches a cap small, feeds an input that crosses it, and asserts the
announcement appears. That proves the sites it covers.

The LEDGER proves the sites it does not. Every module-level constant whose name
claims it bounds something must be in exactly one bucket: swept, exempt with a
reason from a closed set, or pending with a target. A new `_FOO_CAP` cannot be
added without landing in one of them, and the pending list can only shrink.
Without that, a registry is just a list of the caps somebody remembered.

WHY NOT A CLI SUBPROCESS SWEEP. monkeypatch cannot reach across a process
boundary, so forcing a cap small in a subprocess would mean an env-var override
at every site -- a production change to make a test possible. The sweep runs
in-process against the core function and asserts on the returned warnings;
whether a warning survives to the screen is a property of the renderer, tested
separately and once, not re-tested at every site.
"""

import ast
import enum
import importlib
import pathlib
import re
import struct

import pytest

SRC = pathlib.Path(__file__).parent.parent / "src" / "acidcat"
ROOTS = ("core", "tui_app", "commands", "util")

# a name that claims to bound something. The regex IS the contract, so it is
# guarded below: if it stops matching, this whole file asserts nothing.
_NAME = re.compile(r"^_[A-Z0-9_]*(?:CAP|MAX|LIMIT|FINDINGS|CANDS)[A-Z0-9_]*$")

_OPS = {ast.Mult: lambda a, b: a * b, ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b, ast.LShift: lambda a, b: a << b,
        ast.FloorDiv: lambda a, b: a // b, ast.Pow: lambda a, b: a ** b}


def _const_int(node):
    """The number an expression evaluates to, or None.

    ast.literal_eval is not enough and the difference is not academic: it
    refuses `64 * 1024 * 1024` and `1 << 22`, which is how most byte caps in
    this tree are spelled. Using it here dropped 33 of 84 constants, silently,
    including every _READ_CAP -- the exact class this file is about.

    Floats count too, and leaving them out was the same mistake one layer down.
    A bound does not stop being a bound for being fractional: `_FF_DENSITY_MAX`
    decides whether a byte range is reported as MPEG audio, and this ledger
    could not see it. Two constants were invisible that way, in a file whose
    whole purpose is that none are.
    """
    if isinstance(node, ast.Constant):
        return (node.value
                if isinstance(node.value, (int, float))
                and not isinstance(node.value, bool) else None)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _const_int(node.left), _const_int(node.right)
        if a is not None and b is not None:
            try:
                return _OPS[type(node.op)](a, b)
            except (ValueError, ZeroDivisionError, OverflowError):
                return None
    return None


def declared_bounds():
    """(dotted_module, name, lineno, value) for every module-level int bound.

    AST rather than grep: it will not match a name inside a docstring or a
    comment, and it yields a line number, which is what makes a failure here
    actionable instead of annoying.
    """
    out = []
    for root in ROOTS:
        for py in sorted((SRC / root).rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:                     # pragma: no cover
                continue
            for node in tree.body:                  # module level, deliberately
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name) or not _NAME.match(target.id):
                    continue
                value = _const_int(node.value)
                if value is None:
                    continue
                rel = str(py.relative_to(SRC)).replace("\\", "/")[:-3]
                out.append(("acidcat." + rel.replace("/", "."),
                            target.id, node.lineno, value))
    return out


class Reason(enum.Enum):
    """Why a bound is not in the announce-it class. A closed set on purpose:
    a new cap cannot be filed under "misc"."""

    DEPTH_GUARD = "crossing it is a verdict about the file, not a shortened answer"
    SEARCH_WINDOW = "a lookahead inside a search; invisible in the result"
    RUNAWAY_BACKSTOP = "set far above any real file; crossing it is the anomaly"
    FIELD_SANITY = "a ceiling on a value READ from the file, used to reject it"
    RESOURCE_LIMIT = "crossing must raise, not truncate; asserted separately"
    VIEWPORT = "a scroll window; a view that scrolls is not a truncated report"


# (module, const) -> (Reason, prose). The prose must say where the behaviour IS
# covered, if anywhere. An exemption pointing at another test is a redirect; one
# pointing at nothing is a hole.
EXEMPT = {
    ("acidcat.core.walk.apple", "_RESU_INFLATE_CAP"):
        (Reason.RESOURCE_LIMIT, "an inflate bound on a zlib payload, so a "
                                "crafted ResU cannot expand without limit. "
                                "Crossing it DOES announce -- the parser emits "
                                "a coverage note naming the cap -- but it is a "
                                "resource ceiling rather than a listing "
                                "shortened for readability, and a real Logic "
                                "chunk inflates to a few thousand bytes "
                                "against this 4 MB"),
    ("acidcat.core.walk.base", "_OPAQUE_SCAN_CAP"):
        (Reason.SEARCH_WINDOW, "how far into a vendor chunk whose layout is not "
                               "published to scan for readable text. Invisible "
                               "in the result: the chunk is NAMED rather than "
                               "decoded, and the runs are a hint about what it "
                               "holds -- covered by tests/test_riff.py::"
                               "test_an_avid_chunk_is_named_not_decoded"),
    ("acidcat.core.infra.sniff", "_ZERO_HEAD_CAP"):
        (Reason.SEARCH_WINDOW, "how far past a zeroed head the sniffer looks "
                               "for the first MPEG frame. Invisible in the "
                               "result: the answer is a format id or nothing, "
                               "not a shortened report, and a zeroed region is "
                               "a damaged head rather than a container -- a "
                               "real one is a sector or two. Covered by "
                               "tests/test_sniff.py::TestZeroedHeadMp3"),
    ("acidcat.core.walk.wav", "_PEAK_CHANNEL_CAP"):
        (Reason.RUNAWAY_BACKSTOP, "peak records are one per channel and fmt is "
                                  "already linted for an implausible channel "
                                  "count. 64 is far above any real file; "
                                  "crossing it means the chunk disagrees with "
                                  "fmt, which is reported as its own warning "
                                  "-- covered by tests/test_riff.py::"
                                  "test_peak_record_count_is_checked_against_"
                                  "the_channels"),
    ("acidcat.core.walk.dsd", "_TAG_CAP"):
        (Reason.SEARCH_WINDOW, "how much of an embedded ID3 tag to read out "
                               "of a 200 MB one-bit stream. Invisible in the "
                               "result: a DSD tag is a few hundred bytes and "
                               "the frame listing inside it has its own bound "
                               "in formats.mp3, which announces -- covered by "
                               "tests/test_dsd.py::TestDsf::"
                               "test_the_id3_tag_is_read_through_the_shared_"
                               "reader"),
    ("acidcat.core.walk.tracker", "_STM_INSTRUMENT_CAP"):
        (Reason.RUNAWAY_BACKSTOP, "31 is the instrument table's FIXED size in "
                                  "the Scream Tracker 2 format -- the header "
                                  "has no count, so the walk cannot read more "
                                  "than the format defines and crossing it is "
                                  "impossible rather than merely unlikely"),
    ("acidcat.core.formats.xmp", "_VALUE_CAP"):
        (Reason.VIEWPORT, "one property's display width. The value is "
                          "elided with an ellipsis IN the value, which says "
                          "so where a reader is looking -- a warning about a "
                          "long description would fire on ordinary files"),
    ("acidcat.core.walk.apple", "_APPLE_SCAN_CAP"):
        (Reason.SEARCH_WINDOW, "how far into an AFAn/AFmd typedstream to scan "
                               "for class names. Invisible in the result: the "
                               "in the result: the chunk is NAMED rather than "
                               "class list is a hint about what the archive "
                               "holds rather than the answer -- covered by "
                               "tests/test_riff.py::"
                               "test_apple_metadata_is_named_not_decoded"),
    ("acidcat.core.walk.base", "_PADDING_TEXT_CAP"):
        (Reason.SEARCH_WINDOW, "how far into a non-zero padding block to "
                               "look for readable text. Invisible in the "
                               "result: the finding is that the padding is not "
                               "zero, which is reported from the byte count "
                               "over the whole chunk, and this only bounds how "
                               "much of it is scanned for a preview -- covered "
                               "by tests/test_riff.py::"
                               "test_padding_that_is_not_zero_is_a_finding"),
    ("acidcat.core.formats.sf2", "_MAX_RECORDS"):
        (Reason.FIELD_SANITY, "a ceiling on a record count derived from a "
                              "declared chunk size, used to refuse a forged "
                              "pdta table rather than to shorten a listing. "
                              "The three list caps in walk/sf2.py are the "
                              "coverage bounds and they are in SWEPT; a real "
                              "General MIDI font carries 137 presets against "
                              "this 65,536"),
    ("acidcat.core.walk.multisample", "_ENTRY_CAP"):
        (Reason.RESOURCE_LIMIT, "a manifest inflating past 16 MB raises "
                                "BadZipFile, which the walker reports as a "
                                "did-not-parse warning -- covered by "
                                "tests/test_container_coverage.py::"
                                "test_multisample_deflate_bomb_is_capped"),
    ("acidcat.core.primitives.signal", "_LOG2_CAP"):
        (Reason.SEARCH_WINDOW, "a lookup-table size, not a coverage bound: a "
                               "count past it takes a direct math.log2 and the "
                               "returned entropy is bit-identical -- covered by "
                               "tests/test_primitives_signal.py::"
                               "test_log2_table_stays_capped"),
    ("acidcat.core.walk.au", "_HEAD_CAP"):
        (Reason.SEARCH_WINDOW, "the header/annotation read window, not a coverage "
                               "bound: the audio size comes from the header and "
                               "os.path.getsize, so a file past it reports its "
                               "full data size -- covered by tests/test_au.py::"
                               "test_a_file_larger_than_the_header_window_still_"
                               "sizes_its_audio"),
    ("acidcat.core.walk.au", "_ANNOT_CAP"):
        (Reason.SEARCH_WINDOW, "how many annotation bytes are rendered into the "
                               "field; a comment past it is display-truncated and "
                               "the audio geometry is unaffected -- covered by "
                               "tests/test_au.py::"
                               "test_a_huge_annotation_is_display_bounded"),
    ("acidcat.core.census", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "nesting past this is malformed; census reports it"),
    ("acidcat.core.containers.gcm", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "covered by tests/test_container_hostile.py -- a "
                             "backwards child range must terminate, not truncate"),
    ("acidcat.core.containers.wiidisc", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "the gcm twin; same test asserts they have not diverged"),
    ("acidcat.core.formats.mp4", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "box nesting past 16 is malformed, not abridged"),
    ("acidcat.core.write.structure", "_MAX_NESTING"):
        (Reason.DEPTH_GUARD, "a write refuses rather than emitting a partial tree"),
    ("acidcat.core.forensics.explore", "_MAX_DEPTH"):
        (Reason.DEPTH_GUARD, "a container nested 32 deep has stopped describing "
                             "itself; the tree renders the stop as a leaf rather "
                             "than trimming a level off an otherwise complete "
                             "answer -- covered by tests/test_explore.py, which "
                             "pins that crossing it refuses to go deeper rather "
                             "than returning fewer children"),

    ("acidcat.core.forensics.toc", "_MAX_FIELDS"):
        (Reason.SEARCH_WINDOW, "how many field layouts a table-of-contents "
                               "hypothesis is tried against. A detector "
                               "returning None never claimed the file has no "
                               "table, only that this shape did not chain -- "
                               "covered by tests/test_toc.py, which pins that "
                               "the negative is a non-result, not a verdict"),

    ("acidcat.core.forensics.toc", "_MAX_STRIDE"):
        (Reason.SEARCH_WINDOW, "the widest fixed-width record considered. The "
                               "widest measured in a shipped archive is Quake's "
                               "64-byte entry, so this sits eight times above "
                               "the real maximum and bounds the search, not the "
                               "answer"),
    ("acidcat.core.forensics.toc", "_MAX_CANDIDATE_CHAINS"):
        (Reason.SEARCH_WINDOW, "how many stride chains are validated per window. "
                               "This one genuinely CAN hide a table, so what it "
                               "ranks by is the part that matters: ordering by "
                               "raw length once dropped DUKE3D.GRP's real "
                               "directory below eighty longer chains of texture "
                               "data, and it now orders by NUL-terminated names "
                               "-- covered by tests/test_toc.py, which pins that "
                               "a real directory outranks the junk around it"),
    ("acidcat.core.forensics.toc", "_MAX_CHECKS"):
        (Reason.SEARCH_WINDOW, "payload magics read while RANKING placement "
                               "hypotheses. The winner is then re-verified in "
                               "full, so the verified/checked pair that reaches "
                               "a caller is a measurement rather than this "
                               "budget -- covered by tests/test_toc.py, which "
                               "pins that an archive with more entries than this "
                               "reports more than this"),

    ("acidcat.core.forensics.toc", "_MAX_RECORDS"):
        (Reason.RUNAWAY_BACKSTOP, "200,000 records against a real maximum of "
                                  "3,610 measured across ten shipped game "
                                  "archives. The walk that would reach it is "
                                  "stopped far earlier by running out of NUL "
                                  "padding"),
    ("acidcat.core.forensics.framescan", "_FF_DENSITY_MAX"):
        (Reason.FIELD_SANITY, "the share of a claimed MPEG stream that may be "
                              "the sync byte itself. A ceiling on a value "
                              "MEASURED from the bytes, used to reject them: "
                              "real payload runs 0.003 to 0.019, a field of "
                              "0xFF runs 0.70 to 0.75, and crossing it means "
                              "the region is not audio rather than that the "
                              "answer was shortened -- covered by "
                              "tests/test_framescan.py, which pins both that it "
                              "rejects the field and that it does not cost a "
                              "real stream"),
    ("acidcat.core.forensics.locate", "_BLOB_CONF_MAX"):
        (Reason.FIELD_SANITY, "the top of the confidence scale a statistical "
                              "blob may occupy, so a guess can never present "
                              "as a verified signature. It shortens no answer; "
                              "it bounds how loudly one is stated"),
    ("acidcat.core.forensics.checksums", "_LOOKAHEAD_CANDS"):
        (Reason.SEARCH_WINDOW, "how many spurious CRC-8 hits to step over while "
                               "finding one frame end; changes which candidate "
                               "wins, not how much of the file is represented"),
    ("acidcat.core.formats.mp3", "_FREE_SCAN_CAP"):
        (Reason.SEARCH_WINDOW, "the window used to measure a free-format frame"),

    ("acidcat.core.forensics.triage", "_WALK_CAP"):
        (Reason.RUNAWAY_BACKSTOP, "1,000,000 chunks against a real maximum in the "
                                  "thousands. If this is ever lowered below ~10x "
                                  "the largest real observation it leaves this "
                                  "category and must be swept"),
    ("acidcat.commands.probe", "_SHOWN_CAP"):
        (Reason.VIEWPORT, "how many hits probe LISTS; the count printed is the "
                          "true total. Covered by "
                          "test_probe_find_reports_the_true_hit_count and its "
                          "quiet-when-it-fits control in this file"),
    ("acidcat.commands.probe", "_STRINGS_CAP"):
        (Reason.VIEWPORT, "runs listed by `probe strings`; the count is the true "
                          "total. Covered by "
                          "test_probe_strings_reports_how_many_it_found"),
    ("acidcat.commands.probe", "_DIFF_SHOWN_CAP"):
        (Reason.VIEWPORT, "ranges listed by `probe diff`; the count is the true "
                          "total. Covered by "
                          "test_probe_diff_counts_every_changed_range"),
    ("acidcat.core.forensics.resync", "_MAX_RECORDS"):
        (Reason.RUNAWAY_BACKSTOP, "announced, but through `inspect --resync` "
                                  "rather than walk_file, so the generic sweep "
                                  "cannot reach it; covered by "
                                  "test_resync_marks_coverage_as_a_lower_bound "
                                  "in this file"),
    ("acidcat.core.forensics.framescan", "_MAX_STREAMS"):
        (Reason.RUNAWAY_BACKSTOP, "4,096 distinct headerless streams in one image "
                                  "is not a real file"),

    ("acidcat.core.walk.mpc", "_INT64_MAX"):
        (Reason.FIELD_SANITY, "not a bound on our output at all; the largest "
                              "value the field can hold"),
    ("acidcat.core.forensics.checksums", "_MAX_FRAME"):
        (Reason.FIELD_SANITY, "no real FLAC frame approaches this; a larger span "
                              "means the frame does not verify"),
    ("acidcat.core.formats.bitwig", "_MAX_LEN"):
        (Reason.FIELD_SANITY, "a declared length larger than this is rejected"),

    ("acidcat.core.infra.sandbox", "_MAX_RESULT"):
        (Reason.RESOURCE_LIMIT, "a sandbox that truncated its own result and "
                                "returned it as complete would be worse than any "
                                "bug this file guards; it raises"),
    ("acidcat.core.infra.sandbox", "_FSIZE_CAP"):
        (Reason.RESOURCE_LIMIT, "rlimit on the worker; exceeding it kills the "
                                "worker rather than shortening an answer"),

    ("acidcat.tui_app.render", "_HEX_CAP"):
        (Reason.VIEWPORT, "the hex pane prints '.. N more bytes' and scrolls"),
    ("acidcat.tui_app.render", "_ROW_CAP"):
        (Reason.VIEWPORT, "the tree prints '... N more rows (+ to show more)'"),
    ("acidcat.tui_app.render", "_CHUNK_CAP"):
        (Reason.VIEWPORT, "prints '... N more chunks'; covered by "
                          "tests/test_tui_fundamentals.py"),
    ("acidcat.tui_app.render", "_HEXEDIT_CAP"):
        (Reason.VIEWPORT, "refuses to edit a region larger than this, and says so"),
    ("acidcat.tui_app.render", "_DIFF_CAP"):
        (Reason.VIEWPORT, "the count is the true total and the list is a prefix; "
                          "covered by tests/test_tui_fundamentals.py"),
    ("acidcat.tui_app.render", "_SEARCH_CAP"):
        (Reason.VIEWPORT, "the count is the true total and cycling is a prefix; "
                          "covered by tests/test_tui_fundamentals.py"),
    ("acidcat.tui_app.render", "_UNDO_CAP"):
        (Reason.VIEWPORT, "undo depth, not a report about the file"),
    ("acidcat.tui_app.render", "_UNDO_BYTES_CAP"):
        (Reason.VIEWPORT, "undo memory ceiling, not a report about the file"),
}


# Bounds that ARE in the class and are not yet swept. This list may shrink and
# must never grow: a new cap goes to SWEPT or EXEMPT, not here.
PENDING_1_0_1 = {
    ("acidcat.core.analysis.pcm", "_MAX_FRAMES"),
    ("acidcat.core.catalogue.search", "_CANDIDATE_CAP"),
    ("acidcat.core.census", "_MAX_CHUNKS"),
    ("acidcat.core.forensics.checksums", "_MAX_FINDINGS"),
    ("acidcat.core.forensics.checksums", "_READ_CAP"),
    ("acidcat.core.forensics.concealment", "_MAX_FINDINGS"),
    ("acidcat.core.forensics.framescan", "_READ_CAP"),
    ("acidcat.core.forensics.integrity", "_SCAN_CAP"),
    ("acidcat.core.forensics.lsb", "_DE_CAP"),
    ("acidcat.core.forensics.lsb", "_MAX_PCM"),
    ("acidcat.core.forensics.transforms", "_READ_CAP"),
    ("acidcat.core.forensics.triage", "_LIST_CAP"),
    ("acidcat.core.forensics.triage", "_READ_CAP"),
    ("acidcat.core.formats.bitwig", "_SCAN_CAP"),
    ("acidcat.core.formats.mp3", "_RESYNC_LIMIT"),
    ("acidcat.core.formats.ni", "_MAX_INFLATE"),
    ("acidcat.core.probe", "_MARK_LIMIT"),
    ("acidcat.core.walk.ableton", "_AMXD_MAX_CHUNKS"),
    ("acidcat.core.walk.ableton", "_ASD_READ_CAP"),
    ("acidcat.core.walk.akai", "_KGRP_CAP"),
    ("acidcat.core.walk.amiga", "_CAP"),
    ("acidcat.core.walk.base", "_FRAME_LISTING_CAP"),
    ("acidcat.core.walk.base", "_ID3_READ_CAP"),
    ("acidcat.core.walk.bfdlac", "_CHUNK_CAP"),
    ("acidcat.core.walk.bfdlac", "_READ_CAP"),
    ("acidcat.core.walk.emu", "_MAX_CHUNKS"),
    ("acidcat.core.walk.emu", "_READ_CAP"),
    ("acidcat.core.walk.emu", "_REF_CAP"),
    ("acidcat.core.walk.emu", "_TOC_LIST_CAP"),
    ("acidcat.core.walk.emu", "_VOICE_CAP"),
    ("acidcat.core.walk.emu", "_VOICE_DETAIL_CAP"),
    ("acidcat.core.walk.emu", "_ZONE_CAP"),
    ("acidcat.core.walk.flac", "_SEEKPOINT_ROW_CAP"),
    ("acidcat.core.walk.gf1pat", "_READ_CAP"),
    ("acidcat.core.walk.krz", "_OBJECT_CAP"),
    ("acidcat.core.walk.krz", "_READ_CAP"),
    ("acidcat.core.walk.labx", "_META_CAP"),
    ("acidcat.core.walk.labx", "_PRESET_CAP"),
    ("acidcat.core.walk.mp4", "_MOOV_CAP"),
    ("acidcat.core.walk.mp4", "_STCO_CAP"),
    ("acidcat.core.walk.mpc", "_PGM_PAD_CAP"),
    ("acidcat.core.walk.mpc", "_XPM_SAMPLE_CAP"),
    ("acidcat.core.walk.mpc", "_XPN_ENTRY_CAP"),
    ("acidcat.core.walk.mpc", "_XPN_XML_CAP"),
    ("acidcat.core.walk.mpc", "_XTD_CAP"),
    ("acidcat.core.walk.multisample", "_ZONE_CAP"),
    ("acidcat.core.walk.rmid", "_RMID_CAP"),
    ("acidcat.core.walk.rx2", "_MAX"),
    ("acidcat.core.walk.sf2", "_SAMPLE_LIST_CAP"),
    ("acidcat.core.walk.svx", "_READ_CAP"),
    ("acidcat.core.walk.tracker", "_SAMPLE_CAP"),
    ("acidcat.core.walk.sf2", "_SF2_CAP"),
    ("acidcat.core.walk.sigmf", "_ANNOTATION_CAP"),
    ("acidcat.core.walk.sigmf", "_EXT_KEY_CAP"),
    ("acidcat.core.walk.sigmf", "_META_CAP"),
    ("acidcat.core.walk.tracker", "_ORDER_CAP"),
    ("acidcat.core.walk.tracker", "_XREF_CAP"),
    ("acidcat.commands.inspect", "_FULL_RAW_CAP"),
    ("acidcat.commands.od", "_AUTO_DUMP_CAP"),
    ("acidcat.commands.od", "_MARK_CAP"),
}


# ── the sweep ───────────────────────────────────────────────────────

def _iff(form, body_id, chunks):
    body = body_id + b"".join(chunks)
    return form + struct.pack(">I", len(body)) + body


def _svx_many_chunks(tmp_path, n):
    """An 8SVX with n tiny chunks, to cross svx's chunk cap."""
    ch = [b"ANNO" + struct.pack(">I", 2) + b"hi" for _ in range(n)]
    p = tmp_path / "many.8svx"
    p.write_bytes(_iff(b"FORM", b"8SVX", ch))
    return str(p)


def _wav_many_plst_segments(tmp_path, n):
    """A playlist with n segments."""
    import struct as _s
    import test_riff

    body = _s.pack("<I", n) + b"".join(_s.pack("<III", 2, 100, 1)
                                       for _ in range(n))
    p = tmp_path / "plst.wav"
    p.write_bytes(test_riff._wav_with(test_riff._chunk(b"plst", body)))
    return str(p)


def _wav_many_xmp_properties(tmp_path, n):
    """An XMP packet carrying n properties."""
    import test_riff

    props = "".join(f"<dc:p{i}>v{i}</dc:p{i}>" for i in range(n))
    packet = ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
              '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
              '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
              '<rdf:Description rdf:about="" '
              'xmlns:dc="http://purl.org/dc/elements/1.1/">'
              + props +
              "</rdf:Description></rdf:RDF></x:xmpmeta>").encode("utf-8")
    data = test_riff._wav_with(test_riff._chunk(b"_PMX", packet))
    p = tmp_path / "xmp.wav"
    p.write_bytes(data)
    return str(p)


def _wav_big_xmp_packet(tmp_path, n):
    """An XMP packet longer than the read cap."""
    import test_riff

    pad = b" " * (n * 4)
    packet = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
              b'<x:xmpmeta xmlns:x="adobe:ns:meta/">' + pad
              + b"</x:xmpmeta>")
    data = test_riff._wav_with(test_riff._chunk(b"_PMX", packet))
    p = tmp_path / "bigxmp.wav"
    p.write_bytes(data)
    return str(p)


def _wav_many_avid_runs(tmp_path, n):
    """A Pro Tools chunk holding n distinct readable runs."""
    import test_riff

    body = b"".join(b"\x00" + ("Record%03d" % i).encode("ascii")
                    for i in range(n))
    data = test_riff._wav_with(test_riff._chunk(b"DGDA", body))
    p = tmp_path / "dgda.wav"
    p.write_bytes(data)
    return str(p)


def _dff_many_comments(tmp_path, n):
    """A DSDIFF whose COMT chunk carries n comments."""
    import struct as _s
    import test_dsd

    body = _s.pack(">H", n) + b"".join(test_dsd.make_comment(f"c{i}")
                                       for i in range(n))
    p = tmp_path / "comt.dff"
    p.write_bytes(test_dsd.make_dff(extra=test_dsd._bchunk(b"COMT", body)))
    return str(p)


def _dff_many_markers(tmp_path, n):
    """A DSDIFF whose DIIN carries n MARK chunks."""
    import struct as _s
    import test_dsd

    mark = _s.pack(">HBBIiHHH", 0, 0, 1, 0, 0, 0, 0, 0) + _s.pack(">I", 0)
    diin = b"".join(test_dsd._bchunk(b"MARK", mark) for _ in range(n))
    p = tmp_path / "mark.dff"
    p.write_bytes(test_dsd.make_dff(extra=test_dsd._bchunk(b"DIIN", diin)))
    return str(p)


def _dff_many_chunks(tmp_path, n):
    """A DSDIFF carrying n trailing chunks."""
    import test_dsd

    extra = b"".join(test_dsd._bchunk(b"COMT", b"x" * 4) for _ in range(n))
    p = tmp_path / "many.dff"
    p.write_bytes(test_dsd.make_dff(extra=extra))
    return str(p)


def _dff_many_channels(tmp_path, n):
    """A DSDIFF whose CHNL chunk declares n channels."""
    import test_dsd

    ids = tuple(b"C%03d" % i for i in range(n))
    p = tmp_path / "chans.dff"
    p.write_bytes(test_dsd.make_dff(channel_ids=ids))
    return str(p)


def _ogg_many_comments(tmp_path, n):
    """An Ogg stream whose comment header carries n tags."""
    import test_ogg_comments

    tags = tuple(f"T{i}=v{i}".encode("ascii") for i in range(n))
    return test_ogg_comments._ogg(
        tmp_path, test_ogg_comments._comments(tags=tags), name="tags.ogg")


def _aiff_many_transients(tmp_path, n):
    """An Apple Loops transient table with n slice points."""
    import test_aiff

    head = (struct.pack(">HHHH", 1, 50, 16, 0) + struct.pack(">I", 0)
            + b"\x00" * 0x3C + struct.pack(">I", n))
    assert len(head) == 76
    body = head + b"".join(struct.pack(">HHI", 1, 0, i * 4410) + b"\x00" * 16
                           for i in range(n))
    data = test_aiff._form(b"AIFF", test_aiff._comm_aiff(frames=n * 4410),
                           test_aiff._ssnd(), test_aiff._chunk(b"trns", body))
    p = tmp_path / "trns.aif"
    p.write_bytes(data)
    return str(p)


def _aiff_many_categories(tmp_path, n):
    """An Apple Loops category chunk carrying n labels."""
    import test_aiff

    body = struct.pack(">I", n)
    for i in range(n):
        label = ("Label%03d" % i).encode("ascii")
        body += label + b"\x00" * (50 - len(label))
    data = test_aiff._form(b"AIFF", test_aiff._comm_aiff(), test_aiff._ssnd(),
                           test_aiff._chunk(b"cate", body))
    p = tmp_path / "cate.aif"
    p.write_bytes(data)
    return str(p)


def _midi_many_unknown_meta(tmp_path, n):
    """A track using n distinct meta types the format does not define."""
    trk = b""
    for i in range(n):
        etype = 0x60 + i          # undefined range, above the named types
        trk += bytes([0x00, 0xFF, etype, 1, 0x01])
    trk += bytes([0x00, 0xFF, 0x2F, 0x00])
    data = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
            + b"MTrk" + struct.pack(">I", len(trk)) + trk)
    p = tmp_path / "meta.mid"
    p.write_bytes(data)
    return str(p)


def _wav_many_apple_classes(tmp_path, n):
    """An AFAn typedstream referencing n distinct NS* classes."""
    import test_riff

    body = bytes([0x04, 0x0B]) + b"streamtyped"
    for i in range(n):
        name = ("NSClass%03d" % i).encode("ascii")
        body += bytes([len(name)]) + name
    data = test_riff._wav_with(test_riff._chunk(b"AFAn", body))
    p = tmp_path / "apple.wav"
    p.write_bytes(data)
    return str(p)


def _wav_many_slices(tmp_path, n):
    """A WAV whose ACID slice table carries n markers, to cross the listing cap.

    Built through test_riff's own builder: the record stride is DERIVED from
    the body length, so a second hand-rolled table would be testing this
    file's arithmetic rather than the walker's.
    """
    import test_riff

    data = test_riff._wav_with(
        test_riff._strc([i * 1000 for i in range(n)]), frames=n * 1000 + 1000)
    p = tmp_path / "slices.wav"
    p.write_bytes(data)
    return str(p)


def _wav_many_id3_frames(tmp_path, n):
    """A WAV carrying an in-RIFF ID3 tag of n text frames."""
    import test_riff

    frames = [("TXX%d" % (i % 10), "value %d" % i) for i in range(n)]
    # every frame needs a distinct id or the tag is not what a reader meets;
    # T-prefixed ids decode as text, which is what the listing shows
    frames = [("T%03d" % i, "value %d" % i) for i in range(n)]
    data = test_riff._wav_with(test_riff._id3_chunk(frames))
    p = tmp_path / "tagged.wav"
    p.write_bytes(data)
    return str(p)


def _sf2_tree(tmp_path, name, presets, instruments, zones_per_inst=1):
    """A soundfont with a real phdr/pbag/pgen/inst/ibag/igen chain.

    Built through the sf2 test module's own builder rather than a second
    hand-rolled one: the bag spans are cross-references between five tables and
    a second copy would get them subtly wrong, which is the failure this whole
    file exists to make visible.
    """
    import test_sf2

    samples = [("S%d" % i, i * 10, i * 10 + 8, 0, 0, 44100) for i in range(4)]
    data = test_sf2._make_tree_sf2(
        presets=[("P%d" % i, 0, i % 128, [i % max(1, instruments)])
                 for i in range(presets)],
        instruments=[("I%d" % i,
                      [(0, 127, j % len(samples)) for j in range(zones_per_inst)])
                     for i in range(instruments)],
        samples=samples)
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _sf2_many_presets(tmp_path, n):
    return _sf2_tree(tmp_path, "presets.sf2", presets=n, instruments=2)


def _sf2_many_instruments(tmp_path, n):
    return _sf2_tree(tmp_path, "insts.sf2", presets=2, instruments=n)


def _sf2_many_zones(tmp_path, n):
    return _sf2_tree(tmp_path, "zones.sf2", presets=1, instruments=1,
                     zones_per_inst=n)


def _caf_many_chunks(tmp_path, n):
    """A CAF with n tiny chunks, to cross the walker's chunk cap."""
    import test_caf
    body = test_caf._chunk(b"desc", test_caf._desc())
    body += b"".join(test_caf._chunk(b"free", b"\x00" * 4) for _ in range(n))
    body += test_caf._data()
    p = tmp_path / "many.caf"
    p.write_bytes(b"caff" + struct.pack(">HH", 1, 0) + body)
    return str(p)


def _caf_many_info(tmp_path, n):
    """A CAF whose info chunk carries n key/value pairs, to cross the string
    cap that decides how many are listed."""
    import test_caf
    pairs = b"".join(b"k%d\x00v%d\x00" % (i, i) for i in range(n))
    info = test_caf._chunk(b"info", struct.pack(">I", n) + pairs)
    p = tmp_path / "info.caf"
    p.write_bytes(test_caf._make_caf(extra=info))
    return str(p)


def _w64_many_chunks(tmp_path, n):
    """A Wave64 with n tiny chunks, to cross the walker's chunk cap.

    Built through the test module's own builder rather than a second hand-rolled
    Wave64: the 24-byte-inclusive size and the 8-byte padding are exactly the
    arithmetic a second copy would get wrong.
    """
    import test_wave64
    body = b"".join(test_wave64._chunk(b"fmt ", test_wave64._fmt())
                    for _ in range(n))
    body += test_wave64._chunk(b"data", b"\x00" * 32, pad=False)
    data = (test_wave64._RIFF + struct.pack("<Q", 40 + len(body))
            + test_wave64._WAVE + body)
    p = tmp_path / "many.w64"
    p.write_bytes(data)
    return str(p)


def _smus_many_chunks(tmp_path, n):
    """FORM/SMUS, which is what routes to the amiga walker. A generic FORM/ILBM
    falls through to structural triage instead, which has its own bounds and
    would have made this test assert about the wrong module."""
    ch = [b"SHDR" + struct.pack(">I", 6) + struct.pack(">HHH", 120, 100, 1)]
    ch += [b"NAME" + struct.pack(">I", 2) + b"hi" for _ in range(n)]
    p = tmp_path / "many.smus"
    p.write_bytes(_iff(b"FORM", b"SMUS", ch))
    return str(p)


def _voc_over_cap(tmp_path, n):
    """A .voc bigger than n bytes, to cross the VOC walker's read cap.

    That cap is the one bound in the walker that shortens the ANSWER rather
    than a search: blocks past it are never seen at all, so a file crossing it
    has to say the description covers a prefix and not the file.
    """
    ver = 0x0114
    head = (b"Creative Voice File\x1a"
            + struct.pack("<HHH", 26, ver, (~ver + 0x1234) & 0xFFFF))
    body = struct.pack("<IBBH", 11025, 8, 1, 0) + bytes(4) + bytes([0x80]) * (n * 4)
    blk = bytes([9, len(body) & 0xFF, (len(body) >> 8) & 0xFF,
                 (len(body) >> 16) & 0xFF]) + body
    q = tmp_path / "big.voc"
    q.write_bytes(head + blk + bytes(1))
    return str(q)


def _dmx_over_cap(tmp_path, n):
    """A Doom DS* lump larger than n bytes, to cross the DMX read cap.

    The lump has to stay self-consistent while it grows: identification is the
    arithmetic `8 + count == length`, so a fixture that crosses the cap without
    also declaring the right count is refused before the cap can bite.
    """
    samples = bytes([0x80]) * (n * 4)
    q = tmp_path / "big.lmp"
    q.write_bytes(struct.pack("<HHI", 3, 11025, len(samples)) + samples)
    return str(q)


def _sid_over_cap(tmp_path, n):
    """A .sid larger than n bytes, to cross the SID walker's read cap.

    This cap shortens the ANSWER rather than a search. The C64 memory image
    past it is never read, so the reported image length -- and the memory
    extent derived from it, which is the whole point of the data chunk --
    would describe a prefix while reading as a fact about the tune.
    """
    from test_sid import _sid
    q = tmp_path / "big.sid"
    q.write_bytes(_sid(data=struct.pack("<H", 0x1000) + bytes(max(1, n))))
    return str(q)


def _mdx_over_cap(tmp_path, n):
    """An .mdx larger than n bytes, to cross the MDX walker's read cap.

    The cap shortens the ANSWER. Channel extents are derived from where the
    next stream starts, so a file read short reports the last channel and the
    voice region as smaller than they are, and the coverage adds up.
    """
    from test_mdx import _mdx
    q = tmp_path / "big.mdx"
    q.write_bytes(_mdx(mml_len=max(64, n)))
    return str(q)


def _pdx_many_samples(tmp_path, n):
    """A .pdx holding n samples, to cross the sample-listing cap.

    Every sample is a distinct region, because the cap counts regions rather
    than slots -- a bank that aliases one sample to n numbers has one region
    and would not cross anything.
    """
    import seeds
    q = tmp_path / "many.pdx"
    q.write_bytes(seeds.SEEDS["pdx"][0](samples=n, length=8))
    return str(q)


def _s3p_many_keygroups(tmp_path, n):
    """An .s3p with n keygroups, to cross the keygroup-listing cap."""
    import seeds
    q = tmp_path / "many.s3p"
    q.write_bytes(seeds.SEEDS["s3p"][0](keygroups=n))
    return str(q)


def _s3p_over_cap(tmp_path, n):
    """An .s3p larger than n bytes, to cross the read cap. Each keygroup block
    is a fixed 397 bytes on the wire, so the size comes from the count."""
    import seeds
    q = tmp_path / "big.s3p"
    q.write_bytes(seeds.SEEDS["s3p"][0](keygroups=max(2, n // 397 + 2)))
    return str(q)


def _hps_over_cap(tmp_path, n):
    """An .hps larger than n bytes, to cross the stream walkers' read cap.

    HPS is the one of the four where the cap shortens an ANSWER rather than a
    search: the block chain is walked through the buffer, so a file read short
    reports fewer blocks than it has, and a block count is exactly the kind of
    number a reader would take as complete.
    """
    from test_streams import _hps
    q = tmp_path / "big.hps"
    q.write_bytes(_hps(channels=2, blocks=max(4, n // 32)))
    return str(q)


def _cdxa_over_cap(tmp_path, n):
    """A CD image with more sectors than the walker will scan.

    The cap here shortens a SEARCH, and the result of a shortened search reads
    exactly like the result of a complete one: "2 XA streams" looks the same
    whether the disc has two or whether the third begins past the last sector
    examined. So the walker states how far it got.
    """
    from test_cdxa import _xa_sector
    q = tmp_path / "big.cdxa"
    q.write_bytes(_xa_sector(1, 0, 0x01, bytes(2304)) * (n + 4))
    return str(q)


def _nsf_over_cap(tmp_path, n):
    """An NSF larger than the read cap.

    The cap has to stay above the 128-byte header or the walk takes the
    truncated-header path instead, which is a different answer to a different
    question. What is being checked is that a file read SHORT still says so.
    """
    from test_chiptune import _nsf
    q = tmp_path / "big.nsf"
    q.write_bytes(_nsf(body=b"\xea" * (n * 2)))
    return str(q)


def _nsfe_over_cap(tmp_path, n):
    """An NSFe with more chunks than the walk will follow.

    A chunk count is exactly the kind of number a reader takes as complete, so
    stopping early has to be said out loud rather than inferred from a suspiciously
    round total.
    """
    from test_chiptune import _chunk, _info, _nsfe
    chunks = [_chunk(b"INFO", _info())]
    chunks += [_chunk(b"tlbl", b"x\x00")] * (n + 4)
    chunks.append(_chunk(b"DATA", b"\xea"))
    q = tmp_path / "many.nsfe"
    q.write_bytes(_nsfe(chunks))
    return str(q)


SWEPT = [
    # (module that READS the constant, name, patched value, builder, says)
    ("acidcat.core.walk.svx", "_CHUNK_CAP", 4, _svx_many_chunks, "cap"),
    ("acidcat.core.walk.amiga", "_CHUNK_CAP", 4, _smus_many_chunks, "cap"),
    ("acidcat.core.walk.voc", "_READ_CAP", 64, _voc_over_cap, "only the first"),
    ("acidcat.core.walk.dmx", "_READ_CAP", 64, _dmx_over_cap, "only the first"),
    ("acidcat.core.walk.sid", "_SID_READ_CAP", 64, _sid_over_cap, "parsed the first"),
    ("acidcat.core.walk.mdx", "_MDX_READ_CAP", 64, _mdx_over_cap, "parsed the first"),
    ("acidcat.core.walk.pdx", "_PDX_SAMPLE_CAP", 4, _pdx_many_samples,
     "listing the first"),
    ("acidcat.core.walk.akai", "_S3P_KEYGROUP_CAP", 4, _s3p_many_keygroups,
     "listing the first"),
    ("acidcat.core.walk.akai", "_S3P_READ_CAP", 2048, _s3p_over_cap,
     "parsed the first"),
    ("acidcat.core.walk.pdx", "_PDX_SLOT_FIELD_CAP", 4, _pdx_many_samples,
     "filled slots"),
    ("acidcat.core.walk.streams", "_HEAD_CAP", 64, _hps_over_cap, "lower bound"),
    ("acidcat.core.walk.containers", "_XA_SCAN_CAP", 8, _cdxa_over_cap, "examined the first"),
    ("acidcat.core.walk.chiptune", "_NSF_READ_CAP", 256, _nsf_over_cap,
     "read the first"),
    ("acidcat.core.walk.chiptune", "_NSFE_CHUNK_MAX", 4, _nsfe_over_cap,
     "stopped after"),
    ("acidcat.core.walk.wave64", "_MAX_CHUNKS", 4, _w64_many_chunks,
     "stopped after"),
    ("acidcat.core.walk.caf", "_MAX_CHUNKS", 4, _caf_many_chunks,
     "stopped after"),
    ("acidcat.core.walk.caf", "_STRING_CAP", 4, _caf_many_info,
     "listing the first"),
    ("acidcat.core.walk.sf2", "_PRESET_LIST_CAP", 4, _sf2_many_presets,
     "listing the first"),
    ("acidcat.core.walk.sf2", "_INSTRUMENT_LIST_CAP", 4, _sf2_many_instruments,
     "listing the first"),
    ("acidcat.core.walk.sf2", "_ZONE_LIST_CAP", 4, _sf2_many_zones,
     "more zone(s)"),
    ("acidcat.core.walk.wav", "_STRC_SLICE_CAP", 4, _wav_many_slices,
     "listing the first"),
    ("acidcat.core.walk.wav", "_ID3_FRAME_CAP", 4, _wav_many_id3_frames,
     "listing the first"),
    ("acidcat.core.walk.apple", "_APPLE_CLASS_CAP", 4, _wav_many_apple_classes,
     "listing the first"),
    ("acidcat.core.walk.midi", "_UNKNOWN_META_CAP", 4, _midi_many_unknown_meta,
     "listing the first"),
    ("acidcat.core.walk.apple", "_TRNS_LIST_CAP", 4, _aiff_many_transients,
     "listing the first"),
    ("acidcat.core.walk.apple", "_CATE_LABEL_CAP", 4, _aiff_many_categories,
     "listing the first"),
    ("acidcat.core.walk.wav", "_XMP_PROPERTY_CAP", 4, _wav_many_xmp_properties,
     "listing the first"),
    ("acidcat.core.walk.wav", "_PLST_SEGMENT_CAP", 4, _wav_many_plst_segments,
     "listing the first"),
    ("acidcat.core.walk.base", "_OPAQUE_RUN_CAP", 4, _wav_many_avid_runs,
     "listing the first"),
    ("acidcat.core.formats.xmp", "_PACKET_CAP", 64, _wav_big_xmp_packet,
     "reading the first"),
    ("acidcat.core.walk.ogg", "_TAG_LIST_CAP", 4, _ogg_many_comments,
     "listing the first"),
    ("acidcat.core.walk.dsd", "_MAX_CHUNKS", 4, _dff_many_chunks,
     "stopped after"),
    ("acidcat.core.walk.dsd", "_CHANNEL_LIST_CAP", 4, _dff_many_channels,
     "listing the first"),
    ("acidcat.core.walk.dsd", "_COMMENT_CAP", 4, _dff_many_comments,
     "listing the first"),
    ("acidcat.core.walk.dsd", "_MARKER_CAP", 4, _dff_many_markers,
     "listing the first"),
]


@pytest.mark.parametrize("module,const,small,build,says",
                         SWEPT, ids=[f"{m.rsplit('.', 1)[-1]}.{c}"
                                     for m, c, _s, _b, _y in SWEPT])
def test_a_bound_that_bites_says_so(tmp_path, monkeypatch, module, const, small,
                                    build, says):
    """Patch the cap small, cross it, and require the walker to say so.

    Asserting on the returned warnings rather than on rendered output: whether
    the note reaches the screen is the renderer's job, tested once elsewhere,
    and a table that reflows should not break a semantic test.
    """
    from acidcat.core.walk import walk_file
    mod = importlib.import_module(module)
    assert hasattr(mod, const), (
        f"{module} does not read {const}; the registry must name the module "
        f"that READS a constant, not the one that defines it -- patching the "
        f"definition does not reach a `from x import CAP` binding")
    monkeypatch.setattr(mod, const, small)
    path = build(tmp_path, small * 4)
    _label, _chunks, warns = walk_file(path)
    assert any(says in w for w in warns), (
        f"{const} was crossed and nothing said so; warnings were {warns}")


@pytest.mark.parametrize("module,const,small,build,says",
                         SWEPT, ids=[f"{m.rsplit('.', 1)[-1]}.{c}"
                                     for m, c, _s, _b, _y in SWEPT])
def test_a_bound_that_does_not_bite_stays_quiet(tmp_path, module, const, small,
                                                build, says):
    """The control. Without it this suite passes for a walker that emits the
    note unconditionally, which is how an honest tool becomes a noisy one."""
    from acidcat.core.walk import walk_file
    path = build(tmp_path, 3)                 # well under the shipping cap
    _label, _chunks, warns = walk_file(path)
    assert not any(says in w for w in warns), (
        f"{const} was not crossed but something announced it: {warns}")


# ── the ledger ──────────────────────────────────────────────────────

def test_the_enumerator_still_matches_something():
    """Guards the guard. If the regex or the AST walk stops matching, every
    assertion below passes by finding nothing -- a green file that checked no
    code at all."""
    found = declared_bounds()
    assert len(found) > 60, (
        f"only {len(found)} bounds found; the enumerator has stopped working "
        f"and this file is asserting nothing")
    names = {n for _m, n, _l, _v in found}
    assert "_READ_CAP" in names, (
        "no _READ_CAP found: the arithmetic evaluator is broken, and byte caps "
        "spelled `64 * 1024 * 1024` are being dropped silently")


def test_every_bound_is_accounted_for():
    """A new bound must land in exactly one bucket: swept, exempt, or pending.

    This is the half that makes the file worth having. Fixing twenty sites is a
    day's work that stays fixed; what stops the twenty-first is that it cannot
    be added without someone deciding which bucket it belongs in.
    """
    swept = {(m, c) for m, c, _s, _b, _y in SWEPT}
    known = swept | set(EXEMPT) | PENDING_1_0_1
    found = {(m, n) for m, n, _l, _v in declared_bounds()}
    lines = {(m, n): line for m, n, line, _v in declared_bounds()}
    new = found - known
    assert not new, (
        "a new bounded result appeared. Register it in SWEPT, or add it to "
        "EXEMPT with a Reason, or to PENDING_1_0_1:\n  "
        + "\n  ".join(f"{m}:{lines[(m, n)]} {n}" for m, n in sorted(new)))


def test_the_ledger_has_no_ghosts():
    """The other direction, and the half people forget. A constant that no
    longer exists must leave the ledger, or the lists slowly become fiction."""
    found = {(m, n) for m, n, _l, _v in declared_bounds()}
    swept = {(m, c) for m, c, _s, _b, _y in SWEPT}
    for label, bucket in (("SWEPT", swept), ("EXEMPT", set(EXEMPT)),
                          ("PENDING_1_0_1", PENDING_1_0_1)):
        ghosts = bucket - found
        assert not ghosts, (
            f"{label} names constants that no longer exist; drop them:\n  "
            + "\n  ".join(f"{m} {n}" for m, n in sorted(ghosts)))


def test_pending_only_shrinks():
    """A ratchet, after tests/test_targets.py. The number is written down so
    that adding to the list is a visible act rather than a quiet one."""
    assert len(PENDING_1_0_1) <= 61, (
        f"PENDING_1_0_1 has grown to {len(PENDING_1_0_1)}. A new bound belongs "
        f"in SWEPT or EXEMPT; this list is debt and may only shrink.")


def test_every_exemption_gives_a_reason():
    """A Reason from the closed set, and prose that says where the behaviour is
    covered instead. An exemption with an empty reason is a skip wearing a
    costume."""
    for (mod, const), value in EXEMPT.items():
        assert isinstance(value, tuple) and len(value) == 2, (mod, const)
        reason, prose = value
        assert isinstance(reason, Reason), f"{mod}.{const} has no Reason"
        assert len(prose) > 20, f"{mod}.{const}: reason is too thin to audit"


# ── probe: the RE surface the workflow leans on hardest ─────────────

def _repeated(tmp_path, name, unit, times):
    p = tmp_path / name
    p.write_bytes(unit * times)
    return str(p)


def _probe(*args):
    import subprocess
    import sys as _sys
    return subprocess.run([_sys.executable, "-m", "acidcat", "probe", *args],
                          capture_output=True, text=True, timeout=600)


def test_probe_find_reports_the_true_hit_count(tmp_path):
    """`find_bytes` stopped at 512 and its own docstring said "every offset",
    so the command printed the cap as the number of hits. On a file with 3,000
    occurrences it said "512 hit(s)"."""
    p = _repeated(tmp_path, "many.bin", b"AB", 3000)
    r = _probe("find", "41", p)
    assert "3,000 hit(s)" in r.stderr, r.stderr
    assert "listing the first 512" in r.stderr, r.stderr


def test_probe_says_nothing_when_everything_fits(tmp_path):
    """Silence is the claim of completeness. A caveat on a result that was not
    truncated is noise, and noise is how the real ones stop being read."""
    p = _repeated(tmp_path, "few.bin", b"AB", 10)
    r = _probe("find", "41", p)
    assert "10 hit(s)" in r.stderr
    assert "listing the first" not in r.stderr, r.stderr


def test_probe_diff_counts_every_changed_range(tmp_path):
    """The loop EXITED at 256, so trailing differences were never examined and
    the printed count was the cap."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(bytes(4000))
    b.write_bytes(bytes([(i % 2) * 255 for i in range(4000)]))
    r = _probe("diff", str(a), str(b))
    assert "2,000 changed range(s)" in r.stdout, r.stdout
    assert "listing the first 256" in r.stderr, r.stderr


def test_probe_strings_reports_how_many_it_found(tmp_path):
    p = tmp_path / "s.bin"
    p.write_bytes(b"".join(b"HELLO%04d\x00" % i for i in range(1500)))
    r = _probe("strings", str(p))
    assert "1,500 string(s)" in r.stderr, r.stderr
    assert "listing the first 1,000" in r.stderr


def test_the_cap_note_never_lands_on_stdout(tmp_path):
    """stdout carries records. A note there would break `probe --json ... | jq`
    the same way a trailing summary once made validate's JSON unparseable."""
    import json as _json
    p = _repeated(tmp_path, "many.bin", b"AB", 3000)
    r = _probe("--json", "find", "41", p)
    doc = _json.loads(r.stdout)              # must parse despite the note
    assert len(doc["hits"]) == 512
    assert "listing the first" in r.stderr


# ── caps that deflate a NUMBER rather than shorten a list ───────────

def test_resync_marks_coverage_as_a_lower_bound(tmp_path):
    """The coverage percentage is the evidence a recovery is real.

    `recover` built its chain from a scan that stopped at 4,096 records, so on
    a larger file the percentage was an underestimate printed as a measurement.
    The docstring calls high coverage "a strong sign the recovery is real",
    which makes a deflated one manufacture doubt about a chain that may be
    complete -- the same shape as the FLAC read cap manufacturing damage.
    """
    p = tmp_path / "many.bin"
    p.write_bytes(b"".join(b"data" + struct.pack("<I", 8) + bytes(8)
                           for _ in range(6000)))
    r = _probe_cli("inspect", str(p), "--resync")
    assert "at least" in r.stdout, r.stdout
    assert "record cap" in r.stderr, r.stderr


def test_resync_states_coverage_plainly_when_it_fits(tmp_path):
    p = tmp_path / "few.bin"
    p.write_bytes(b"".join(b"data" + struct.pack("<I", 8) + bytes(8)
                           for _ in range(50)))
    r = _probe_cli("inspect", str(p), "--resync")
    assert "at least" not in r.stdout, r.stdout
    assert "record cap" not in r.stderr


def test_census_counts_every_flag_hit_not_every_example(tmp_path):
    """The example list stops at 25 and the renderer printed len() of it, so a
    flag seen 900 times displayed as "25". The header disclaimed the EXAMPLES
    while the integer beside the name read as a file count and was not one."""
    body = (b"WAVEfmt " + struct.pack(">IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack(">I", 4) + bytes(4))
    for i in range(60):
        (tmp_path / f"f{i:03d}.wav").write_bytes(
            b"RIFX" + struct.pack(">I", len(body)) + body)
    for extra in ([], ["--jobs", "4"]):
        r = _probe_cli("census", str(tmp_path), "-q", *extra)
        assert "rifx_big_endian" in r.stdout
        line = [x for x in r.stdout.splitlines() if "rifx_big_endian" in x][0]
        assert " 60 " in line or " 60\t" in line or "60  e.g." in line, (
            f"with {extra or 'no flags'}: {line}")


def test_inspect_force_says_how_many_walkers_it_tried(tmp_path):
    """Ten rows read as the whole field of candidates. The footer says none
    verified a magic number, which needs a denominator to mean anything."""
    p = tmp_path / "blob.bin"
    p.write_bytes(bytes(range(256)) * 8)
    r = _probe_cli("inspect", str(p), "--force")
    assert "tried" in r.stdout, r.stdout


def test_audit_signal_says_which_checks_did_not_run(tmp_path):
    """`--signal` on a file it cannot decode was byte-identical to a file that
    passed both checks: the caller asked for the signal checks and got silence
    that read as clean."""
    from conftest import CORPUS_MP3_GS
    src = pathlib.Path(CORPUS_MP3_GS)
    p = tmp_path / "u.mp3"
    p.write_bytes(src.read_bytes())
    r = _probe_cli("audit", str(p), "--signal")
    assert "NOT" in r.stdout and "run" in r.stdout, r.stdout
    assert r.returncode == 0, "our decode limitation must not condemn the file"


def _probe_cli(*args):
    import subprocess
    import sys as _sys
    return subprocess.run([_sys.executable, "-m", "acidcat", *args],
                          capture_output=True, text=True, timeout=600)


def test_counting_hits_does_not_materialise_them():
    """The obvious way to report a true total is to fetch everything and take
    len(). That is what shipped first, and it cost 720 MB of Python list on a
    40 MB input -- over an mmap that exists precisely so a large file does not
    cost its size in RAM. `probe strings` over a multi-GB image would have
    built a list of every printable run in it.

    Counting past the cap while storing up to it is the same shape the TUI
    search already uses. Memory must not scale with the match count.
    """
    import tracemalloc
    from acidcat.core import probe as pr

    data = b"AB" * 500_000                       # 1 MB, 500k matches
    tracemalloc.start()
    offs, total = pr.find_bytes_counted(data, b"A", 512)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    assert total == 500_000, "the count must still be the truth"
    assert len(offs) == 512, "the stored list must still be capped"
    # 500k ints would be ~16 MB; the bound is what this asserts, not a number
    assert peak < 1_000_000, (
        f"peak {peak:,} bytes for 500,000 matches -- memory is scaling with the "
        f"match count again")


def test_the_counted_variants_agree_with_the_plain_ones():
    """Two code paths for one question, so they are pinned together."""
    from acidcat.core import probe as pr

    data = bytes(range(256)) * 40
    offs, total = pr.find_bytes_counted(data, b"\x07", None)
    assert offs == pr.find_bytes(data, b"\x07", None)
    assert total == len(offs)

    a, b = bytes(200), bytes([(i % 3 == 0) * 9 for i in range(200)])
    ranges, n, la, lb = pr.diff_counted(a, b, None)
    plain, la2, lb2 = pr.diff(a, b, limit=None)
    assert ranges == plain and n == len(plain) and (la, lb) == (la2, lb2)

    runs, rtotal = pr.strings_counted(b"HELLO\x00WORLD\x00hi\x00", 4, None)
    assert runs == pr.strings(b"HELLO\x00WORLD\x00hi\x00", 4, limit=None)
    assert rtotal == len(runs)


def test_resync_json_carries_the_cap_flag(tmp_path):
    """The text path said "at least 68%" while the JSON said 68.

    That is the machine face unable to tell a capped run from a complete one --
    the same defect fixed in `scan --json` this release, recreated on the very
    function that gained the flag. The JSON is the only face a script sees.
    """
    import json as _json
    p = tmp_path / "many.bin"
    p.write_bytes(b"".join(b"data" + struct.pack("<I", 8) + bytes(8)
                           for _ in range(6000)))
    r = _probe_cli("inspect", str(p), "--resync", "--json")
    doc = _json.loads(r.stdout)
    assert doc["capped"] is True, doc
    assert doc["coverage_is_lower_bound"] is True

    q = tmp_path / "few.bin"
    q.write_bytes(b"".join(b"data" + struct.pack("<I", 8) + bytes(8)
                           for _ in range(50)))
    doc = _json.loads(_probe_cli("inspect", str(q), "--resync", "--json").stdout)
    assert doc["capped"] is False, doc
    assert doc["coverage_is_lower_bound"] is False
