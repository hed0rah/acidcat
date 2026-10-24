"""Where a WAV's tempo comes from: every chunk that states one, the rules that
keep a stamped or session tempo from overriding the truth, and the text and
comment-key that reach the library index alongside it."""

import struct

import pytest

from acidcat.core.analysis import temposource as ts

RATE = 8000


def _chunk(cid, payload):
    pad = b"\x00" if len(payload) & 1 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def _wav(path, seconds, *chunks):
    frames = int(round(RATE * seconds))
    fmt = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16))
    data = _chunk(b"data", b"\x00" * (frames * 2))
    body = b"WAVE" + fmt + b"".join(chunks) + data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def _tempomap(*bpms):
    entries = b"".join(struct.pack(">II", i * 1920, int(b * 1000)) for i, b in enumerate(bpms))
    return _chunk(b"JUNK", b"TEMPOMAP" + struct.pack(">HIIH", 1, 30, 0, len(bpms)) + entries)


def _ixml(tempo, beats=None):
    attrs = f"<ATTR><NAME>MusicalTempo</NAME><TYPE>float</TYPE><VALUE>{tempo}</VALUE></ATTR>"
    if beats is not None:
        attrs += f"<ATTR><NAME>MusicalBeats</NAME><TYPE>int</TYPE><VALUE>{beats}</VALUE></ATTR>"
    attrs += ("<ATTR><NAME>MediaComment</NAME><TYPE>string</TYPE>"
              "<VALUE>Metal, Hinge, Squeak</VALUE></ATTR>")
    xml = (f"<?xml version=\"1.0\"?><BWFXML><IXML_VERSION>2.0</IXML_VERSION>"
           f"<STEINBERG><ATTR_LIST>{attrs}</ATTR_LIST></STEINBERG></BWFXML>")
    return _chunk(b"iXML", xml.encode() + b"\x00")


def _info(**tags):
    body = b"INFO" + b"".join(_chunk(k.encode(), v.encode() + b"\x00") for k, v in tags.items())
    return _chunk(b"LIST", body)


def _labl(text):
    return _chunk(b"LIST", b"adtl" + _chunk(b"labl", struct.pack("<I", 1) + text.encode() + b"\x00"))


def _bext(description):
    return _chunk(b"bext", description.encode().ljust(256, b"\x00") + b"\x00" * 346)


def _id3_comment(text):
    body = b"\x00eng\x00" + text.encode("latin-1")
    frame = b"COMM" + struct.pack(">I", len(body)) + b"\x00\x00" + body
    size = len(frame)
    synch = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return _chunk(b"id3 ", b"ID3\x03\x00\x00" + synch + frame)


def _row(path):
    from acidcat.core.catalogue.indexing import _from_wav
    return _from_wav(path, {})


class TestResolve:
    def test_tag_outranks_everything(self):
        got = ts.resolve([ts.claim("acid", 128), ts.claim("tag", 126),
                          ts.claim("name", 124)], 8.0)
        assert got == {"bpm": 126.0, "bpm_source": "tag"}

    def test_a_stamped_120_yields_to_the_name(self):
        got = ts.resolve([ts.claim("acid", 120), ts.claim("name", 124)], 8.0)
        assert got == {"bpm": 124.0, "bpm_source": "name"}

    def test_the_name_outranks_a_session_tempo(self):
        got = ts.resolve([ts.claim("tempomap", 74), ts.claim("name", 86)], 60 / 74 * 16)
        assert got["bpm_source"] == "name"

    def test_a_session_tempo_must_fit_the_length(self):
        assert ts.resolve([ts.claim("resu", 120)], 4.0)["bpm"] == 120.0       # 8 beats
        assert ts.resolve([ts.claim("resu", 120)], 4.3)["bpm"] is None        # 8.6
        # a one-shot bounced to two beats on the grid is not a loop
        assert ts.resolve([ts.claim("resu", 120)], 1.0)["bpm"] is None

    def test_ixml_beats_are_checked_against_the_length(self):
        assert ts.resolve([ts.claim("ixml", 175, beats=8)], 8 * 60 / 175)["bpm"] == 175.0
        assert ts.resolve([ts.claim("ixml", 175, beats=8)], 5.0)["bpm"] is None

    def test_nothing_usable(self):
        assert ts.resolve([ts.claim("acid", 0), ts.claim("tag", "fast"), None], 4.0) == {
            "bpm": None, "bpm_source": None}

    @pytest.mark.parametrize("text,want", [
        ("Tempo: 128.0", 128.0), ("tempo=90", 90.0), ("Tempo: 128 BPM", 128.0),
        ("Chorus", None), ("Tempo: fast", None),
    ])
    def test_cue_label(self, text, want):
        assert ts.cue_tempo([text]) == want


class TestWav:
    def test_tempomap_in_junk_is_a_tempo_not_padding(self, tmp_path):
        from acidcat.core.walk.wav import inspect_wav
        path = _wav(tmp_path / "loop.wav", 8 * 60 / 90, _tempomap(90))
        chunks, _ = inspect_wav(path)
        junk = next(c for c in chunks if c["id"] == "JUNK")
        assert junk["summary"].startswith("TEMPOMAP, 90 BPM")
        assert not any(w.get("code") == "padding.nonzero" for w in junk["warnings"])
        assert (_row(path)["bpm"], _row(path)["bpm_source"]) == (90.0, "tempomap")

    def test_tempomap_lists_every_change(self, tmp_path):
        from acidcat.core.walk.wav import inspect_wav
        chunks, _ = inspect_wav(_wav(tmp_path / "x.wav", 1.0, _tempomap(90, 92, 94)))
        junk = next(c for c in chunks if c["id"] == "JUNK")
        assert "then 2 change(s)" in junk["summary"]
        assert [f["name"] for f in junk["fields"]].count("entry[2]") == 1

    def test_tempomap_on_a_one_shot_is_not_its_tempo(self, tmp_path):
        assert _row(_wav(tmp_path / "hit.wav", 0.31, _tempomap(94)))["bpm"] is None

    def test_steinberg_ixml(self, tmp_path):
        row = _row(_wav(tmp_path / "KIT01 LOOP 05.wav", 8 * 60 / 175, _ixml(175, 8)))
        assert (row["bpm"], row["bpm_source"]) == (175.0, "ixml")
        assert "Metal, Hinge, Squeak" in row["comment"]

    def test_cue_label_tempo(self, tmp_path):
        row = _row(_wav(tmp_path / "guitar.wav", 4.0, _labl("Tempo: 120.0")))
        assert (row["bpm"], row["bpm_source"]) == (120.0, "cue")

    def test_folder_tempo_for_a_stem(self, tmp_path):
        path = tmp_path / "Kit 4 - F minor - 124 bpm" / "stems wav" / "Lead Synth.wav"
        row = _row(_wav(path, 16 * 60 / 124))
        assert (row["bpm"], row["bpm_source"]) == (124.0, "folder")

    def test_text_reaches_the_row(self, tmp_path):
        row = _row(_wav(tmp_path / "door.wav", 1.0,
                        _info(INAM="Door Slam", IART="Foley Co", IGNR="SFX", ICMT="close mic"),
                        _bext("Door, Wood, Slam")))
        assert (row["title"], row["artist"], row["genre"]) == ("Door Slam", "Foley Co", "SFX")
        assert row["comment"] == "close mic | Door, Wood, Slam"

    def test_mixed_in_key_comment_is_a_key(self, tmp_path):
        row = _row(_wav(tmp_path / "loop.wav", 1.0, _id3_comment("Cm - Energy 4")))
        assert (row["key"], row["key_mode"], row["key_source"]) == ("Cm", "minor", "comment")

    def test_a_comment_that_is_not_only_a_key_is_not_one(self, tmp_path):
        row = _row(_wav(tmp_path / "loop.wav", 1.0, _id3_comment("Am I dreaming")))
        assert row["key"] is None

    def test_bpm_source_is_stored(self, tmp_path):
        from acidcat.core.catalogue import index as idx
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = tmp_path / "lib"
        _wav(lib / "bass_124bpm.wav", 1.0)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True)
            got = conn.execute("SELECT bpm, bpm_source FROM samples").fetchone()
            assert tuple(got) == (124.0, "name")
        finally:
            conn.close()
