"""A JSON record must be enough to locate its own bytes.

`dump --json` reported `offset` for the 8-byte [id][size] header while `size`
and `hex` described the payload, so feeding one record into `carve --offset`
read eight bytes early and returned the ASCII chunk id. `inspect --json` had the
same skew: `chunk.offset + field.off` landed on the header, not the field.

It was format-dependent, which is what made it dangerous -- a headerless chunk
model like MOD has no skew, so a script tuned on trackers broke silently on
RIFF and AIFF. `inspect --full` already emitted absolute offsets; the cheap
paths now do too.

These tests carve at the offsets the JSON gives and compare against the bytes
the JSON claims are there, so they cannot pass with a skew of any size.
"""

import json
import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x11\x22" * 64
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _run(*args):
    r = subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    _wav(p)
    return str(p)


def test_od_record_locates_its_own_payload(wav):
    """2.0: `dump --json` is `od --json`; its offset is the payload's, and
    carving that range gives the same bytes."""
    rec = json.loads(_run("od", wav, "fmt", "--json"))[0]
    carved = _run("carve", wav, "@%d+%d" % (rec["offset"], rec["length"]),
                  "--encoding", "hex")
    assert carved.split() == [rec["hex"][i:i + 2]
                              for i in range(0, len(rec["hex"]), 2)]


def test_a_node_carved_raw_starts_at_its_header(wav):
    """A node ADDR means its payload; --raw is its whole extent, header first."""
    payload = json.loads(_run("od", wav, "fmt", "--json"))[0]
    header = _run("carve", wav, "fmt", "--raw", "--encoding", "hex")
    raw = bytes.fromhex(header.replace(" ", ""))
    assert raw[:4] == b"fmt " and raw[8:].hex() == payload["hex"]


def test_inspect_field_abs_is_the_real_byte(wav):
    """2.0: --json is the v1 Document, whose `at` is absolute by contract."""
    doc = json.loads(_run("inspect", "--json", wav).splitlines()[0])
    fmt = [c for c in doc["nodes"][0]["children"] if c["id"] == "RIFF/fmt_"][0]
    assert fmt["payload"]["off"] == fmt["extent"]["off"] + 8

    by_key = {f["key"]: f for f in fmt["fields"]}
    rate = by_key["sample_rate"]["at"]

    raw = _run("carve", wav, "--offset", str(rate["off"]),
               "--length", str(rate["len"]), "--encoding", "hex")
    assert struct.unpack("<I", bytes.fromhex(raw.replace(" ", "")))[0] == 44100

    chans = by_key["channels"]["at"]
    raw = _run("carve", wav, "--offset", str(chans["off"]),
               "--length", str(chans["len"]), "--encoding", "hex")
    assert struct.unpack("<H", bytes.fromhex(raw.replace(" ", "")))[0] == 2


def test_the_document_and_explores_dump_agree_on_absolute_offsets(wav, capsys):
    """explore builds its page from inspect's in-process positioned dump;
    the Document --json prints must place every field where that does."""
    from acidcat.commands import _legacy, inspect
    plain = json.loads(_run("inspect", "--json", wav).splitlines()[0])
    ns = _legacy.parser_for(inspect, "inspect").parse_args([wav, "--json"])
    ns.full = True
    capsys.readouterr()
    assert inspect.run(ns) == 0
    full = json.loads(capsys.readouterr().out.splitlines()[0])

    a = {(n["name"], f["name"]): f["at"]["off"]
         for n in plain["nodes"][0]["children"] for f in n["fields"] if "at" in f}
    b = {(c["id"].strip(), f["name"]): f.get("abs")
         for c in full["chunks"] for f in c["fields"] if f.get("off") is not None}
    assert a and a == b
