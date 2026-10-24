"""Key resolution from several sources: defaults are not keys, a root-only
source borrows its mode, and an unknown mode is not reported as major."""


from acidcat.core.analysis import keysource as ks
from acidcat.core.analysis.detect import key_from_path_detail, parse_key_from_path
from acidcat.core.catalogue import index as idx


class TestClaims:
    def test_root_only_sources_never_carry_a_mode(self):
        assert ks.claim("acid", note=65) == ("acid", 5, None)
        assert ks.claim("basc", note=69) == ("basc", 9, None)

    def test_tag_and_name_carry_their_mode(self):
        assert ks.claim("tag", "Fm") == ("tag", 5, "minor")
        assert ks.claim("tag", "8A") == ("tag", 9, "minor")
        assert ks.claim("name", "C#") == ("name", 1, "major")

    def test_bare_letter_name_has_no_mode(self):
        assert ks.claim("name", "A", mode_known=False) == ("name", 9, None)

    def test_unparseable_and_unset_say_nothing(self):
        assert ks.claim("tag", "probe") is None
        assert ks.claim("acid", note=0) is None

    def test_smpl_sixty_is_no_evidence(self):
        assert ks.smpl_claim(60) is None
        assert ks.smpl_claim(0) is None
        assert ks.smpl_claim(61) == ("smpl", 1, None)

    def test_midi_c_major_is_the_default_signature(self):
        assert ks.midi_claim("C") is None
        assert ks.midi_claim("Am") == ("midi", 9, "minor")
        assert ks.midi_claim("D") == ("midi", 2, "major")


class TestResolve:
    def test_nothing(self):
        assert ks.resolve([None, None])["key"] is None

    def test_name_outranks_an_agreeing_acid_root(self):
        # the "Fm" loop that used to index as F
        r = ks.resolve([ks.claim("acid", note=65), ks.claim("name", "Fm")])
        assert r["key"] == "Fm"
        assert r["key_mode"] == "minor"
        assert r["key_source"] == "name"
        assert r["key_claims"] == "name=Fm acid=F?"

    def test_name_outranks_a_disagreeing_acid_root(self):
        # a pack-stamped root (B on every file) against the name
        r = ks.resolve([ks.claim("acid", note=71), ks.claim("name", "Am")])
        assert (r["key"], r["key_mode"], r["key_source"]) == (
            "Am", "minor", "name")
        assert r["key_claims"] == "name=Am acid=B?"

    def test_folder_outranks_acid(self):
        r = ks.resolve([ks.claim("acid", note=59), ks.claim("folder", "D#")])
        assert (r["key"], r["key_source"]) == ("D#", "folder")

    def test_acid_root_alone_is_still_a_key(self):
        r = ks.resolve([ks.claim("acid", note=62)])
        assert (r["key"], r["key_mode"], r["key_source"]) == ("D", None, "acid")

    def test_bare_name_borrows_mode_from_the_folder(self):
        r = ks.resolve([ks.claim("name", "F", mode_known=False),
                        ks.claim("folder", "Fm")])
        assert (r["key"], r["key_mode"], r["key_source"]) == (
            "Fm", "minor", "name+folder")
        assert r["key_claims"] == "name=F? folder=Fm"

    def test_root_only_alone_has_unknown_mode(self):
        r = ks.resolve([ks.claim("smpl", note=62)])
        assert (r["key"], r["key_mode"], r["key_source"]) == ("D", None, "smpl")

    def test_tag_outranks_name(self):
        r = ks.resolve([ks.claim("name", "G"), ks.claim("tag", "Em")])
        assert (r["key"], r["key_source"]) == ("Em", "tag")

    def test_display_marks_unknown_mode(self):
        assert ks.display("A", None) == "A?"
        assert ks.display("A", "major") == "A"
        assert ks.display("Am", None) == "Am"
        assert ks.display(None, None) is None


class TestPathDetail:
    def test_parenthesised_key(self):
        # every file in a real 808 pack names its key this way
        assert parse_key_from_path("/x/Cymatics - 808 Alert (C).wav") == "C"
        assert key_from_path_detail("/x/Cymatics - 808 Alert (C).wav") == (
            "C", "name", False)

    def test_worded_mode_is_known(self):
        assert key_from_path_detail("/x/pad_F_minor.wav") == ("Fm", "name", True)

    def test_bare_letter_mode_unknown(self):
        assert key_from_path_detail("/x/SL_FTH_Bass_A_123BPM.wav") == (
            "A", "name", False)

    def test_chord_quality_neighbour_fixes_mode(self):
        assert key_from_path_detail("/x/maj7_C.wav") == ("C", "name", True)

    def test_folder_is_its_own_source(self):
        assert key_from_path_detail("/x/Am/loop 3.wav") == ("Am", "folder", True)


class TestMigrationV4:
    def test_v3_rows_keep_their_key_and_gain_empty_provenance(self, tmp_path):
        db = str(tmp_path / "lib.db")
        conn = idx.open_db(db)
        conn.execute("INSERT INTO samples (path, key) VALUES ('/a.wav', 'F')")
        conn.commit()
        # roll the file back to a v3 layout: drop the v4 columns
        for col in idx.V4_COLUMNS:
            conn.execute(f"ALTER TABLE samples DROP COLUMN {col}")
        conn.execute("UPDATE meta SET v = '3' WHERE k = 'schema_version'")
        conn.commit()
        conn.close()

        conn = idx.open_db(db)
        try:
            row = dict(conn.execute(
                "SELECT key, key_mode, key_source, key_claims, reader "
                "FROM samples").fetchone())
            ver = conn.execute(
                "SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0]
        finally:
            conn.close()
        assert ver == str(idx.SCHEMA_VERSION)
        assert row == {"key": "F", "key_mode": None, "key_source": None,
                       "key_claims": None, "reader": None}


class TestReaderStamp:
    """A row records the reader that wrote it, and an index run re-reads a
    file whose reader has changed even though the file has not."""

    def _wav(self, path, smpl_note):
        import struct
        fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
        data = b"data" + struct.pack("<I", 4) + b"\x00" * 4
        smpl = b"smpl" + struct.pack("<I", 36) + struct.pack(
            "<IIIIIIIII", 0, 0, 0, smpl_note, 0, 0, 0, 0, 0)
        body = b"WAVE" + fmt + data + smpl
        path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    def test_stale_reader_rereads_and_current_skips(self, tmp_path):
        from acidcat.core.catalogue import indexing
        lib = tmp_path / "lib"
        lib.mkdir()
        self._wav(lib / "pad_Fm.wav", 60)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            first = indexing.walk_and_upsert(conn, str(lib), quiet=True)
            assert first["added"] == 1
            (stamp,) = conn.execute("SELECT reader FROM samples").fetchone()
            assert indexing.reader_is_current(stamp)

            again = indexing.walk_and_upsert(conn, str(lib), quiet=True)
            assert (again["skipped"], again["updated"]) == (1, 0)

            # a row from an older reader, as 2.0.1 left every row: its key
            # still says the smpl default
            conn.execute("UPDATE samples SET reader = NULL, key = 'C'")
            conn.commit()
            stale = indexing.walk_and_upsert(conn, str(lib), quiet=True)
            assert (stale["skipped"], stale["updated"]) == (0, 1)
            key, mode = conn.execute(
                "SELECT key, key_mode FROM samples").fetchone()
            assert (key, mode) == ("Fm", "minor")
        finally:
            conn.close()

    def test_unknown_or_old_stamps_are_stale(self):
        from acidcat.core.catalogue.indexing import READER_VERSIONS, reader_is_current
        assert not reader_is_current(None)
        assert not reader_is_current("wav:1")
        assert not reader_is_current("nonsense")
        assert reader_is_current(f"wav:{READER_VERSIONS['wav']}")


class TestAudioClaim:
    def test_audio_outranks_acid_and_smpl_but_not_a_name(self):
        r = ks.resolve([ks.claim("acid", note=71), ks.claim("audio", "Am")])
        assert (r["key"], r["key_source"]) == ("Am", "audio")
        r = ks.resolve([ks.claim("audio", "Am"), ks.claim("name", "Cm")])
        assert (r["key"], r["key_source"]) == ("Cm", "name")

    def test_analyze_listens_when_the_key_rests_on_acid(self, monkeypatch):
        from acidcat.core.analysis import detect
        from acidcat.core.catalogue import indexing
        monkeypatch.setattr(detect, "estimate_librosa_metadata",
                            lambda p: {"estimated_key": "Em",
                                       "estimated_bpm": 99.0})
        row = {"bpm": 128.0}
        indexing._set_key(row, [ks.claim("acid", note=64)])
        assert indexing._wants_audio(row)
        indexing._fill_from_librosa("x.wav", row)
        assert (row["key"], row["key_mode"], row["key_source"]) == (
            "Em", "minor", "audio")
        assert row["key_claims"] == "audio=Em acid=E?"
        assert row["bpm"] == 128.0              # a stated tempo is kept

    def test_analyze_leaves_a_named_key_alone(self):
        from acidcat.core.catalogue import indexing
        row = {"bpm": 128.0}
        indexing._set_key(row, [ks.claim("name", "Am")])
        assert not indexing._wants_audio(row)
