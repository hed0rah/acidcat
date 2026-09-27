"""MP4 offset-table (stco/co64) repair: rebuild a broken chunk-offset table from
the mdat position + sample sizes witness, without moving a byte of audio."""
import struct

import pytest

from acidcat.core.write import mp4repair as R


def _box(btype, payload):
    return struct.pack(">I", 8 + len(payload)) + btype + payload


def _fullbox(btype, payload):
    return _box(btype, b"\x00\x00\x00\x00" + payload)   # version+flags = 0


def _stsz(sizes):
    return _fullbox(b"stsz", struct.pack(">II", 0, len(sizes))
                    + b"".join(struct.pack(">I", s) for s in sizes))


def _stsc(runs):
    # runs: list of (first_chunk, samples_per_chunk)
    body = struct.pack(">I", len(runs))
    for fc, spc in runs:
        body += struct.pack(">III", fc, spc, 1)
    return _fullbox(b"stsc", body)


def _stco(offsets):
    return _fullbox(b"stco", struct.pack(">I", len(offsets))
                    + b"".join(struct.pack(">I", o) for o in offsets))


def _build(sizes, runs, offsets, mdat_payload):
    stbl = _stsz(sizes) + _stsc(runs) + _stco(offsets)
    tree = _box(b"moov", _box(b"trak", _box(b"mdia",
               _box(b"minf", _box(b"stbl", stbl)))))
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x00\x00")
    mdat = _box(b"mdat", mdat_payload)
    return ftyp + tree + mdat


def _mdat_payload_start(sizes, runs, placeholder_offsets, payload):
    # build once with placeholder offsets to learn where mdat lands
    blob = _build(sizes, runs, placeholder_offsets, payload)
    boxes = R._find_boxes(blob)
    return boxes["mdat"]["offset"] + boxes["mdat"]["hdr"]


def _make_multichunk():
    sizes = [10, 10, 20, 20, 30, 30]        # 6 samples, 120 bytes
    runs = [(1, 2)]                          # 2 samples per chunk -> 3 chunks
    payload = bytes(range(120))
    start = _mdat_payload_start(sizes, runs, [0, 0, 0], payload)
    good = [start, start + 20, start + 60]  # chunk_bytes 20/40/60
    return sizes, runs, payload, start, good


def test_healthy_stco_is_noop():
    sizes, runs, payload, start, good = _make_multichunk()
    data = _build(sizes, runs, good, payload)
    new, changes = R.repair_mp4(data)
    assert changes == [] and new == data


def test_shifted_stco_rebuilt_to_correct_offsets():
    sizes, runs, payload, start, good = _make_multichunk()
    shift = good[0] - 8                       # push offsets before mdat, non-negative
    broken = [o - shift for o in good]        # mdat "moved", table not patched
    data = _build(sizes, runs, broken, payload)
    new, changes = R.repair_mp4(data)
    _, _, _, fixed = R._parse_stco(new, R._find_boxes(new)["stco"])
    assert fixed == good
    assert changes and "rebuilt" in changes[0]["new"]


def test_patch_false_reports_without_materializing_a_copy():
    # the read-only analyze path (validate/audit) asks for the changes but must
    # not build a patched full-file copy
    sizes, runs, payload, start, good = _make_multichunk()
    shift = good[0] - 8
    broken = [o - shift for o in good]
    data = _build(sizes, runs, broken, payload)
    unpatched, changes = R.repair_mp4(data, patch=False)
    assert unpatched is data                 # the input object, not a copy
    patched, ref = R.repair_mp4(data)
    assert changes == ref                    # same verdict as the patching path
    _, _, _, fixed = R._parse_stco(patched, R._find_boxes(patched)["stco"])
    assert fixed == good


def test_repair_never_touches_mdat():
    sizes, runs, payload, start, good = _make_multichunk()
    broken = [start, start, start]           # all wrong
    data = _build(sizes, runs, broken, payload)
    new, _ = R.repair_mp4(data)
    b = R._find_boxes(new)["mdat"]
    assert new[b["offset"] + b["hdr"]:b["offset"] + b["size"]] == payload
    assert len(new) == len(data)             # length-preserving


def _stsz_fixed(sample_size, sample_count):
    # stsz FullBox: version/flags, sample_size (non-zero = all samples equal),
    # sample_count. no per-sample table follows.
    return _fullbox(b"stsz", struct.pack(">II", sample_size, sample_count))


def test_uniform_sample_size_stsz():
    # the sample_size != 0 path: 8 samples * 25 bytes, 4 per chunk -> 2x100-byte chunks
    runs = [(1, 4)]
    payload = bytes(200)
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x00\x00")

    def _tree(offsets):
        stbl = _stsz_fixed(25, 8) + _stsc(runs) + _stco(offsets)
        return _box(b"moov", _box(b"trak", _box(b"mdia",
                   _box(b"minf", _box(b"stbl", stbl)))))

    probe = ftyp + _tree([0, 0]) + _box(b"mdat", payload)
    ms = R._find_boxes(probe)["mdat"]
    ps = ms["offset"] + ms["hdr"]
    data = ftyp + _tree([8, 108]) + _box(b"mdat", payload)   # broken: before mdat
    new, changes = R.repair_mp4(data)
    _, _, _, fixed = R._parse_stco(new, R._find_boxes(new)["stco"])
    assert fixed == [ps, ps + 100]


def test_multitrack_refused():
    # two stco boxes -> out of scope, raises rather than guessing
    sizes, runs, payload, start, good = _make_multichunk()
    stbl = _stsz(sizes) + _stsc(runs) + _stco(good) + _stco(good)
    tree = _box(b"moov", _box(b"trak", _box(b"mdia",
               _box(b"minf", _box(b"stbl", stbl)))))
    data = _box(b"ftyp", b"M4A \x00\x00\x00\x00") + tree + _box(b"mdat", payload)
    with pytest.raises(R.Mp4RepairError):
        R.repair_mp4(data)


def _forged_count(btype, count, body=b""):
    """A FullBox whose count field claims entries its box does not hold."""
    return _fullbox(btype, struct.pack(">I", count) + body)


def _forged_stsz(count):
    return _fullbox(b"stsz", struct.pack(">II", 0, count))   # zero table bytes


def _hostile(stsz=None, stsc=None, stco=None):
    """A structurally valid single-track m4a with one forged sample table."""
    sizes, runs, payload, start, good = _make_multichunk()
    stbl = ((stsz if stsz is not None else _stsz(sizes))
            + (stsc if stsc is not None else _stsc(runs))
            + (stco if stco is not None else _stco([1, 1, 1])))  # broken on purpose
    tree = _box(b"moov", _box(b"trak", _box(b"mdia",
               _box(b"minf", _box(b"stbl", stbl)))))
    return (_box(b"ftyp", b"M4A \x00\x00\x00\x00") + tree
            + _box(b"mdat", payload))


def test_forged_counts_raise_damage_not_struct_error():
    """The defect this pins, found by an adversarial audit: stsz/stsc/stco
    counts were fed to unpack_from unbounded, so a 196-byte crafted .m4a took
    repair, validate AND audit down with an uncaught struct.error (and a
    large-but-satisfiable count was a MemoryError). The count must be checked
    against the bytes its own box holds, and the failure must be Mp4Damage --
    in-contract, catchable, and reported as damage rather than out-of-scope."""
    for hostile in (_hostile(stsz=_forged_stsz(0x00FFFFFF)),
                    _hostile(stsc=_forged_count(b"stsc", 0x00FFFFFF)),
                    _hostile(stco=_forged_count(b"stco", 0x00FFFFFF))):
        with pytest.raises(R.Mp4Damage):
            R.repair_mp4(hostile)


def test_forged_count_fails_validate_without_advertising_repair(tmp_path, capsys):
    """validate must not certify a forged table as consistent (the old
    behavior after merely catching the error would be a silent OK), and must
    not send the user to a repair that has nothing safe to rewrite."""
    from acidcat.cli import main
    p = tmp_path / "hostile.m4a"
    p.write_bytes(_hostile(stsz=_forged_stsz(0x00FFFFFF)))
    rc = main(["validate", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "declares" in out
    assert "fix with: acidcat repair" not in out


def test_forged_count_repair_leaves_the_file_alone(tmp_path):
    from acidcat.cli import main
    p = tmp_path / "hostile.m4a"
    original = _hostile(stsz=_forged_stsz(0x00FFFFFF))
    p.write_bytes(original)
    main(["repair", "--overwrite", str(p)])
    assert p.read_bytes() == original


def test_out_of_scope_is_still_a_quiet_note_not_damage(tmp_path, capsys):
    """The multi-track refusal must stay an out-of-scope note: a healthy file
    the repairer declines is not a damaged file, and validate must keep
    saying OK for it."""
    from acidcat.cli import main
    sizes, runs, payload, start, good = _make_multichunk()
    stbl = _stsz(sizes) + _stsc(runs) + _stco(good) + _stco(good)
    tree = _box(b"moov", _box(b"trak", _box(b"mdia",
               _box(b"minf", _box(b"stbl", stbl)))))
    p = tmp_path / "multi.m4a"
    p.write_bytes(_box(b"ftyp", b"M4A \x00\x00\x00\x00") + tree
                  + _box(b"mdat", payload))
    assert main(["validate", str(p)]) == 0


def test_repair_declines_a_multitrack_file_without_raising():
    """`constraints.repair` promises a report, never an exception. The MP4
    repairer located mdat BEFORE its guarded run, and locating it raised on a
    multi-track file, so a real two-track video escaped as Mp4RepairError."""
    from acidcat.core.write import constraints
    sizes, runs, payload, start, good = _make_multichunk()
    stbl = _stsz(sizes) + _stsc(runs) + _stco(good) + _stco(good)
    tree = _box(b"moov", _box(b"trak", _box(b"mdia",
               _box(b"minf", _box(b"stbl", stbl)))))
    data = _box(b"ftyp", b"M4A \x00\x00\x00\x00") + tree + _box(b"mdat", payload)
    new_data, _report = constraints.repair(data)
    assert new_data == data
