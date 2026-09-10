"""The acidcat-lab command surface.

Each verb reads a real carrier and writes one that did not exist. The carriers
are seeds, so this runs in a clone. The point of these tests is the wiring, not
the crafting itself, which `test_lab_loop.py` proves against acidcat's own
detectors: here we check that a verb dispatches, round-trips, refuses to clobber,
and picks the writer by sniffing unless `--into` overrides it.
"""

import os

import pytest

import seeds

cli = pytest.importorskip("acidcat_lab.cli", reason="acidcat_lab not installed")

_MSG = b"the payload that never existed until now"


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


@pytest.fixture
def wav(tmp_path):
    return _write(tmp_path / "track.wav", seeds.wav(frames=8000))


@pytest.fixture
def flac(tmp_path):
    return _write(tmp_path / "song.flac", seeds.build("flac"))


@pytest.fixture
def payload(tmp_path):
    return _write(tmp_path / "secret.bin", _MSG)


# ── cavity ──────────────────────────────────────────────────────────

def test_cavity_round_trips_via_sniff(tmp_path, wav, payload):
    out = str(tmp_path / "planted.wav")
    assert cli.main(["cavity", "embed", wav, "--payload", payload, "-o", out]) == 0
    back = str(tmp_path / "back.bin")
    assert cli.main(["cavity", "extract", out, "-o", back]) == 0
    assert open(back, "rb").read() == _MSG


def test_the_planted_carrier_still_walks(tmp_path, wav, payload):
    from acidcat.core.walk import walk_file
    out = str(tmp_path / "planted.wav")
    cli.main(["cavity", "embed", wav, "--payload", payload, "-o", out])
    label, chunks, _ = walk_file(out)
    assert label == "RIFF/WAVE"                          # a cavity does not break the file


def test_into_forces_a_writer_against_the_extension(tmp_path, flac):
    # the adversarial case: a FLAC named .wav. sniff would still say flac, so
    # force it the other way to prove --into wins over both name and content.
    liar = _write(tmp_path / "liar.wav", open(flac, "rb").read())
    out = str(tmp_path / "out.bin")
    assert cli.main(["cavity", "embed", liar, "--payload-text", "z",
                     "--into", "flac", "-o", out]) == 0


def test_an_unrecognized_carrier_is_refused(tmp_path):
    junk = _write(tmp_path / "x.bin", b"\x00" * 64)
    assert cli.main(["cavity", "embed", junk, "--payload-text", "z",
                     "-o", str(tmp_path / "o")]) == 2


def test_cavity_will_not_overwrite_its_input(wav):
    # the guard is acidcat's own outpath.refuse_self_overwrite, reused
    assert cli.main(["cavity", "embed", wav, "--payload-text", "z", "-o", wav]) == 2


# ── polyglot ────────────────────────────────────────────────────────

def test_polyglot_builds_a_file_valid_as_both(tmp_path, wav, payload):
    import zipfile

    from acidcat.core.walk import walk_file
    out = str(tmp_path / "poly.wav")
    assert cli.main(["polyglot", "build", wav, "--add", "s.bin=" + payload,
                     "-o", out]) == 0
    label, _chunks, _ = walk_file(out)                   # still a WAV to acidcat
    assert label == "RIFF/WAVE"
    assert zipfile.is_zipfile(out)                        # and a ZIP
    assert cli.main(["polyglot", "verify", out]) == 0


# ── stego ───────────────────────────────────────────────────────────

def test_stego_round_trips(tmp_path, wav):
    out = str(tmp_path / "st.wav")
    assert cli.main(["stego", "embed", wav, "--payload-text", "hi there",
                     "-o", out]) == 0
    back = str(tmp_path / "back.bin")
    assert cli.main(["stego", "extract", out, "-o", back]) == 0
    assert open(back, "rb").read() == b"hi there"


@pytest.mark.parametrize("method", ["replace", "match", "adaptive"])
def test_stego_method_round_trips_via_cli(tmp_path, method):
    # a carrier with real variance, so adaptive has noisy blocks to hide in
    import random
    import wave as _wave
    rng = random.Random(7)
    frames = ([(i % 6) * 4 for i in range(4000)]
              + [rng.randint(-6000, 6000) for _ in range(12000)])
    car = str(tmp_path / "car.wav")
    with _wave.open(car, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(__import__("array").array("h", frames).tobytes())
    out = str(tmp_path / "st.wav")
    assert cli.main(["stego", "embed", car, "--payload-text", "into the noise",
                     "-o", out, "--method", method]) == 0
    back = str(tmp_path / "back.bin")
    assert cli.main(["stego", "extract", out, "-o", back, "--method", method]) == 0
    assert open(back, "rb").read() == b"into the noise"


def test_stego_reports_capacity_and_refuses_an_overlarge_payload(tmp_path):
    small = _write(tmp_path / "small.wav", seeds.wav(frames=64))
    assert cli.main(["stego", "capacity", small]) == 0
    assert cli.main(["stego", "embed", small, "--payload-text", "x" * 500,
                     "-o", str(tmp_path / "o.wav")]) == 1


# ── the binary itself ───────────────────────────────────────────────

def test_no_verb_prints_help_and_succeeds(capsys):
    assert cli.main([]) == 0
    assert "cavity" in capsys.readouterr().out
