"""tests for acidcat.core.sniff, the canonical magic sniffer."""

import struct

from acidcat.core.infra.sniff import sniff, sniff_bytes


def _pad(b, n=16):
    return b + b"\x00" * max(0, n - len(b))


class TestSniffBytes:
    def test_wav(self):
        assert sniff_bytes(_pad(b"RIFF\x24\x00\x00\x00WAVE")) == "wav"

    def test_rf64(self):
        assert sniff_bytes(_pad(b"RF64\xff\xff\xff\xffWAVE")) == "rf64"

    def test_aiff_and_aifc(self):
        assert sniff_bytes(_pad(b"FORM\x00\x00\x00\x20AIFF")) == "aiff"
        assert sniff_bytes(_pad(b"FORM\x00\x00\x00\x20AIFC")) == "aifc"

    def test_midi_needs_14_bytes(self):
        head = b"MThd\x00\x00\x00\x06\x00\x01\x00\x02\x01\xe0"
        assert sniff_bytes(head) == "midi"
        # a 13-byte head is not enough to be dispatched as MIDI
        assert sniff_bytes(head[:13]) is None

    def test_serum(self):
        assert sniff_bytes(_pad(b"XferJson")) == "serum"

    def test_bitwig(self):
        assert sniff_bytes(_pad(b"BtWg0001")) == "bitwig"

    def test_vital(self):
        assert sniff_bytes(_pad(b'{"synth_v')) == "vital"

    def test_mp4(self):
        assert sniff_bytes(_pad(b"\x00\x00\x00\x20ftypM4A ")) == "mp4"

    def test_ni_hsin(self):
        assert sniff_bytes(_pad(b"\x00" * 12 + b"hsin")) == "ni"

    def test_ni_niks_riff(self):
        # RIFF/NIKS is an NI preset, not a WAV
        assert sniff_bytes(_pad(b"RIFF\x10\x00\x00\x00NIKS")) == "ni"

    def test_flac(self):
        assert sniff_bytes(_pad(b"fLaC\x00\x00\x00\x22")) == "flac"

    def test_ogg(self):
        assert sniff_bytes(_pad(b"OggS\x00\x02")) == "ogg"

    def test_id3_is_mp3(self):
        assert sniff_bytes(_pad(b"ID3\x04\x00\x00\x00\x00\x00\x00")) == "mp3"

    def test_bare_mpeg_frame(self):
        # MPEG 1 Layer III, 128 kbps, 44100 Hz
        assert sniff_bytes(_pad(b"\xff\xfb\x90\x00")) == "mp3"

    def test_garbage_sync_rejected(self):
        # sync bits set but reserved layer (layer bits 00): not a frame header
        assert sniff_bytes(_pad(b"\xff\xe1\x90\x00")) is None

    def test_unknown_and_short(self):
        assert sniff_bytes(b"") is None
        assert sniff_bytes(_pad(b"\x00" * 16)) is None
        assert sniff_bytes(_pad(b"NOPE")) is None


class TestSniffFile:
    def test_wav_file(self, minimal_wav):
        assert sniff(minimal_wav) == "wav"

    def test_empty_file(self, empty_file):
        assert sniff(empty_file) is None

    def test_id3_wrapping_wav_is_flagged(self, tmp_path):
        # ID3v2 header, empty 0-byte body, then a RIFF/WAVE container
        tag = b"ID3\x03\x00\x00\x00\x00\x00\x00"
        wav = b"RIFF" + struct.pack("<I", 4) + b"WAVE"
        p = tmp_path / "wrapped.wav"
        p.write_bytes(tag + wav)
        assert sniff(str(p)) == "id3-wrapped"

    def test_id3_over_mpeg_stays_mp3(self, tmp_path):
        tag = b"ID3\x03\x00\x00\x00\x00\x00\x00"
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 100
        p = tmp_path / "tagged.mp3"
        p.write_bytes(tag + frame)
        assert sniff(str(p)) == "mp3"

    def test_free_format_mp3_confirmed_by_twin_sync(self, tmp_path):
        # bitrate index 0 (free format) sniffs as mp3 only when a matching
        # second sync confirms the constant frame length
        frame = bytes([0xFF, 0xFB, 0x00, 0xC0]) + b"\x00" * 296
        p = tmp_path / "free.bin"                  # extensionless on purpose
        p.write_bytes(frame * 3)
        assert sniff(str(p)) == "mp3"

    def test_lone_free_sync_not_sniffed(self, tmp_path):
        p = tmp_path / "junk.bin"
        p.write_bytes(bytes([0xFF, 0xFB, 0x00, 0xC0]) + b"\x11" * 500)
        assert sniff(str(p)) is None


class TestWeakJsonMagic:
    """A bare '{' is the weakest magic in the table: it claims every JSON file,
    and every RTF, since those open "{\rtf". In a real 3,229-file sample library
    the only content/extension mismatch found was exactly this -- RTF licence
    agreements reported as walkable Vital presets.

    classify consults sniff before its own foreign-file table, so the `{\rtf`
    entry already in _FOREIGN_MAGICS was never reached.
    """

    def _w(self, tmp_path, name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    def test_a_real_vital_preset_is_still_recognised(self, tmp_path):
        p = self._w(tmp_path, "p.vital",
                    b'{"synth_version":"1.0.7","preset_name":"x","settings":{}}')
        assert sniff(p) == "vital"

    def test_rtf_is_not_a_vital_preset(self, tmp_path):
        p = self._w(tmp_path, "readme.rtf", rb"{\rtf1\ansi\deff0 licence text}")
        assert sniff(p) != "vital"

    def test_ordinary_json_is_not_a_vital_preset(self, tmp_path):
        p = self._w(tmp_path, "package.json", b'{"name":"pkg","version":"1.0"}')
        assert sniff(p) != "vital"

    def test_rtf_reaches_the_foreign_table(self, tmp_path):
        """The point of the fix: classify must get to name it."""
        from acidcat.core.forensics import classify as C
        p = self._w(tmp_path, "readme.rtf", rb"{\rtf1\ansi\deff0 licence text}"
                    + b"\x00" * 200)
        assert C.classify(p)["shape"] == C.FOREIGN

    def test_the_key_is_found_at_the_TAIL_of_a_large_preset(self, tmp_path):
        """The reason a head-only check was wrong.

        Vital serialises JSON with keys in alphabetical order, so `settings` --
        a wavetable and base64 blob routinely hundreds of KB -- always precedes
        `synth_version`, which lands about 24 bytes from EOF. Measured on 40
        real presets: the key sat at filesize-24 in every one, and files ran
        170 KB to 3.2 MB. A head-only check found it in NONE of them, which
        made the sniffer stricter than the parser and left inspect unable to
        reach any real preset.
        """
        big = (b'{"author":"x","settings":{"blob":"' + b"A" * 300_000
               + b'"},"synth_version":"1.5.5"}')
        p = self._w(tmp_path, "real.vital", big)
        assert sniff(p) == "vital"

    def test_a_huge_json_without_the_key_is_still_refused(self, tmp_path):
        """The tail window must not become a way in for any large JSON."""
        p = self._w(tmp_path, "big.json",
                    b'{"a":"' + b"x" * 300_000 + b'","b":1}')
        assert sniff(p) != "vital"


def test_labx_zip_probe_reads_forty_bytes_not_the_member(tmp_path):
    """Found by an adversarial audit: the labx probe did z.read(n)[:40], which
    inflates the WHOLE member to keep 40 bytes -- an allocation bomb inside the
    sniffer, upstream of every walker boundary and every cap. The probe must
    stream. (Bound asserted via tracemalloc: a 60 MB member, ~60 KB zipped.)"""
    import io
    import tracemalloc
    import zipfile as zf
    from acidcat.core.infra.sniff import sniff as _sniff
    buf = io.BytesIO()
    with zf.ZipFile(buf, "w", zf.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("X/User/B/P",
                   b"22 serialization::archive " + b"\x00" * (60 * 1024 * 1024))
    p = tmp_path / "probe.zip"
    p.write_bytes(buf.getvalue())
    tracemalloc.start()
    try:
        fmt = _sniff(str(p))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert fmt == "labx"                 # still recognized from the 40 bytes
    assert peak < 8 * 1024 * 1024, f"peak {peak >> 20} MB inside the sniffer"


class TestZeroedHeadMp3:
    """MPEG audio behind a run of NUL bytes is still MPEG audio.

    Files turn up that are ordinary MP3s with their first sector or two erased
    -- a failed write, or a head reserved and never filled. The frame sync is
    not at offset 0, so every magic test misses it and the whole file reads as
    unrecognized while 131 seconds of audio sits intact behind the hole.

    The rule is narrow because scanning forward for any sync would call half a
    disk an MP3: the skipped bytes must be ZERO, the sync must decode, and a
    second frame must sit exactly one frame length on.
    """

    # 128 kbps, 44.1 kHz, Layer III: a 417-byte frame with no padding bit
    FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413

    def _write(self, tmp_path, head, name="x.mp3"):
        p = tmp_path / name
        p.write_bytes(head + self.FRAME * 4)
        return str(p)

    def test_a_zeroed_head_does_not_hide_the_audio(self, tmp_path):
        assert sniff(self._write(tmp_path, b"\x00" * 1451)) == "mp3"

    def test_one_non_zero_byte_and_it_is_not_ours_to_claim(self, tmp_path):
        """The load-bearing condition. A single non-zero byte in front means
        this is some other container whose magic acidcat does not know, and
        guessing is worse than saying so."""
        head = b"\x00" * 700 + b"\x01" + b"\x00" * 750
        assert sniff(self._write(tmp_path, head, "x.bin")) is None

    def test_a_zeroed_head_over_noise_is_not_an_mp3(self, tmp_path):
        """The other condition: without a second frame at the right distance a
        lone sync is a byte pair, not a stream."""
        p = tmp_path / "noise.bin"
        p.write_bytes(b"\x00" * 512 + b"\xff\xfb\x90\x00" + b"\x11" * 2000)
        assert sniff(str(p)) is None

    def test_an_all_zero_file_is_not_an_mp3(self, tmp_path):
        p = tmp_path / "zero.bin"
        p.write_bytes(b"\x00" * 8192)
        assert sniff(str(p)) is None
