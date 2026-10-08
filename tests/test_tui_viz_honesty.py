"""The byte views must describe what they actually measured.

`_viz_render` read `min(fsize, 8 MB)` and every view captioned itself
"(whole file)". Over 8 MB that caption was false, and it is the same defect this
audit has been clearing everywhere else: a cap reported as a fact about the
file. Entropy and hilbert now stream and cover all of it; the histogram still
needs bytes in hand and says how many it read.

The container-end marker is the other half. A hot band to the right of it is
appended data, which is the polyglot tell and most of the reason to look at an
entropy view at all.
"""

import os
import struct

import pytest

from acidcat.tui_app.app import AcidcatTUI


class _VizProbe:
    """The renderers, without a running app.

    Only the geometry is stubbed -- how wide and how tall the pane is. The
    honesty of the captions is what these tests are about, and that must come
    from the real code, so every method under test is the real one.
    """

    _viz_entropy = AcidcatTUI._viz_entropy
    _viz_hilbert = AcidcatTUI._viz_hilbert
    _viz_mark_container_end = AcidcatTUI._viz_mark_container_end
    _declared_end = AcidcatTUI._declared_end
    _viz_chart_height = AcidcatTUI._viz_chart_height
    _hilbert_order = AcidcatTUI._hilbert_order
    _VIZ_CHROME_ROWS = AcidcatTUI._VIZ_CHROME_ROWS
    # scope and scale, added when the views learned to cover one region and to
    # rescale. Real methods, not stubs, for the reason in the class docstring.
    _viz_range = AcidcatTUI._viz_range
    _short_name = AcidcatTUI._short_name
    _node_name = AcidcatTUI._node_name
    _viz_caption = AcidcatTUI._viz_caption
    _scale_for = AcidcatTUI._scale_for
    _VIZ_SCALES = AcidcatTUI._VIZ_SCALES
    # the window-size floor and the ceiling note that came with region scoping
    _ENTROPY_MIN_WINDOW = AcidcatTUI._ENTROPY_MIN_WINDOW
    _entropy_windows = AcidcatTUI._entropy_windows

    def __init__(self, path, chunks=(), width=72, rows=39):
        self.work = path
        self.fsize = os.path.getsize(path)
        self.chunks = list(chunks)
        self._w = width
        self._rows = rows
        self._viz_scope = "file"
        self._viz_scale = {}
        self._cur_node = None

    # The app reads a node's byte range through this now, not a dict keyed on
    # id(node). A probe that still offered the dict would be testing an
    # interface the app no longer calls.
    @staticmethod
    def _meta(node):
        return getattr(node, "data", None)

    def _viz_width(self):
        return self._w

    def _viz_rows(self):
        return self._rows


def _wav_plus(path, trailing=b""):
    pcm = b"\x00\x01" * 4000
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body + trailing)
    return str(path)


def test_entropy_says_whole_file_when_it_read_the_whole_file(tmp_path):
    p = _VizProbe(_wav_plus(tmp_path / "a.wav"))
    out = p._viz_entropy().plain
    assert "whole file" in out
    assert "(sampled)" not in out, "an exact curve must not hedge"


def test_entropy_says_sampled_when_it_sampled(tmp_path):
    """Coverage and exactness are separate claims and are now stated separately.

    The caption used to print one OR the other, which conflated them: a sampled
    run said "(sampled)" and stopped, leaving how much of the file it spanned
    unstated. Both functions anchor every window to the offset range it stands
    for, so a sampled curve does span the whole file -- it is estimated, not
    truncated. "whole file (sampled)" says both; the old wording said neither
    clearly.
    """
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(3_000_000))
    out = _VizProbe(str(big), width=8)._viz_entropy().plain
    assert "(sampled)" in out, "estimated the curve and captioned it as measured"
    assert "whole file" in out, "spans the file; say so alongside the hedge"


def test_hilbert_covers_the_file_and_says_how(tmp_path):
    small = _VizProbe(_wav_plus(tmp_path / "s.wav"))
    out = small._viz_hilbert().plain
    assert "whole file" in out and "(sampled)" not in out
    big = tmp_path / "b.bin"
    big.write_bytes(os.urandom(2_000_000))
    assert "(sampled)" in _VizProbe(str(big))._viz_hilbert().plain


def test_hilbert_fits_the_pane_and_says_which_size_it_drew(tmp_path):
    """The map is sized to the space, so it must state the size it chose.

    Order sets how many bytes fold into one cell, so this is not decoration:
    a caption naming a resolution the drawing does not have is the same defect
    as captioning a capped read "(whole file)".
    """
    path = _wav_plus(tmp_path / "h.wav")

    # a pane with room for a 64-wide, 32-row map
    out = _VizProbe(path, width=72, rows=39)._viz_hilbert().plain
    assert "64x64" in out, out.splitlines()[0]

    # a short pane cannot fit 32 rows, so it must drop an order and say so
    out = _VizProbe(path, width=72, rows=20)._viz_hilbert().plain
    assert "32x32" in out, out.splitlines()[0]
    assert "64x64" not in out

    # a narrow pane is bounded by columns instead
    out = _VizProbe(path, width=40, rows=39)._viz_hilbert().plain
    assert "32x32" in out, out.splitlines()[0]


def test_the_container_end_is_marked_when_bytes_follow_it(tmp_path):
    """The polyglot tell. Without the mark the eye has no reference for where
    the file was supposed to stop."""
    trailing = os.urandom(20_000)
    path = _wav_plus(tmp_path / "poly.wav", trailing=trailing)
    declared = os.path.getsize(path) - len(trailing)
    chunks = [{"id": "data", "offset": 0, "size": declared}]
    out = _VizProbe(path, chunks=chunks)._viz_entropy().plain
    assert "container ends at" in out
    assert f"{len(trailing):,} bytes follow" in out
    assert "^" in out


def test_no_marker_on_a_file_with_nothing_after_the_container(tmp_path):
    """It must not cry wolf on every ordinary file."""
    path = _wav_plus(tmp_path / "clean.wav")
    chunks = [{"id": "data", "offset": 0, "size": os.path.getsize(path)}]
    assert "container ends at" not in _VizProbe(path, chunks=chunks)._viz_entropy().plain


def test_no_marker_when_nothing_was_walked(tmp_path):
    path = _wav_plus(tmp_path / "unknown.bin")
    assert "container ends at" not in _VizProbe(path, chunks=[])._viz_entropy().plain


# ── the forensics panel, the other place a non-answer can read as a pass ──

def _panel_text(app):
    """What the forensics panel would render, with the app's real state."""
    from rich.text import Text
    captured = {}

    class _P:
        def update(self, t):
            captured["t"] = t

    class _Box:
        def set_class(self, *a):
            pass

    app.query_one = lambda sel, *a: _Box() if sel == "#idbox" else _P()
    AcidcatTUI._render_anomalies(app)
    t = captured.get("t")
    return t.plain if isinstance(t, Text) else str(t)


def _bare(findings, scan_note):
    """An object carrying only what _render_anomalies reads."""
    class _A:
        pass
    a = _A()
    a.findings = findings
    a.scan_note = scan_note
    a.query_one = None
    a._idbox_width = lambda: 60       # the width it lays the panel out in
    return a


def test_a_scan_that_did_not_run_does_not_render_as_clean():
    """`findings = []` meant both "scanned, nothing there" and "never scanned".

    The panel printed "clean: no findings" for both, so a file too large to
    scan whole, and a file whose scanner raised, both read as a clean bill of
    health. That is the defect this whole file exists to prevent, one panel
    over from the views it already guards.
    """
    a = _bare([], "not scanned: the file is too large to scan whole, so "
                  "nothing here is a verdict")
    out = _panel_text(a)
    assert "clean" not in out, out
    assert "not scanned" in out


def test_a_scan_that_crashed_says_so():
    a = _bare([], "scan failed (ValueError); this file was NOT screened")
    out = _panel_text(a)
    assert "clean" not in out, out
    assert "NOT screened" in out


def test_a_scan_that_ran_and_found_nothing_still_says_clean():
    """The other half: the hedge must not fire on a genuine clean result, or
    it becomes noise and stops being read."""
    out = _panel_text(_bare([], None))
    assert "clean: no findings" in out
