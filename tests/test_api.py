"""The public library API: ``import acidcat`` exposes a stable engine surface,
and importing it does not pull the optional (textual/librosa/mutagen) deps."""
import struct
import sys

import acidcat


def _wav(tmp_path, rate=44100):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    data = b"data" + struct.pack("<I", 8) + b"\x00" * 8
    body = b"WAVE" + fmt + data
    p = tmp_path / "t.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


def test_public_names_present():
    for name in ("walk", "walk_file", "Unsupported", "probe", "viz",
                 "analyze", "repair", "Report", "Violation", "anomalies_scan",
                 "__version__"):
        assert hasattr(acidcat, name), name
    assert set(acidcat.__all__) <= set(dir(acidcat))


def test_walk_via_api(tmp_path):
    fmt, chunks, warns = acidcat.walk(_wav(tmp_path))
    assert fmt == "RIFF/WAVE"
    assert any(str(c["id"]).strip() == "data" for c in chunks)


def test_probe_via_api(tmp_path):
    p = _wav(tmp_path, rate=48000)
    off, ln, note = acidcat.probe.resolve(p, "fmt.sample_rate")
    data = open(p, "rb").read()
    assert acidcat.probe.read_typed(data, off, "u32", 1, "little") == [48000]
    assert acidcat.probe.scan_value(data, 48000, "u32")


def test_viz_via_api():
    grid, side = acidcat.viz.hilbert_grid(bytes(range(256)) * 4, order=4)
    assert side == 16
    ent = acidcat.viz.windowed_entropy(b"\x00" * 512, 8)
    assert max(ent) == 0.0


def test_constraints_via_api(tmp_path):
    good = open(_wav(tmp_path, rate=44100), "rb").read()
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 3)          # stale RIFF size
    report = acidcat.analyze(bytes(broken))
    assert report.violations
    fixed, _report = acidcat.repair(bytes(broken))
    assert fixed == good


def test_import_stays_light():
    # the public engine must not drag in the optional command dependencies.
    # run in a fresh interpreter -- other tests in this session import mutagen etc.
    import subprocess
    code = ("import acidcat, sys; "
            "bad=[m for m in ('textual','librosa','mutagen') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"import acidcat pulled: {r.stdout.strip()}"
