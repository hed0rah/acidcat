"""Gravis UltraSound GF1 patch (.PAT) walker.

The classic GUS instrument format (magic `GF1PATCH110` / `100`): a 129-byte
header, then one or more instruments, each with layers, each with sample records
that carry a 96-byte header followed by the sample's raw PCM. Fixed little-endian
layout -- no chunk grid, so it needs a dedicated walker rather than generic
triage.

Each sample header gives the PCM size, the sample rate, and a `modes` byte whose
bits select 16-bit vs 8-bit and unsigned vs signed -- everything needed to pull
the audio out. The walk degrades on malformed input and never raises.
"""

import struct

from acidcat.core.walk.base import _f

_HDR = 129
_INST = 63
_LAYER = 47
_SMP_HDR = 96
_READ_CAP = 64 * 1024 * 1024


def _cstr(b):
    return b.split(b"\x00", 1)[0].decode("latin1", "replace").strip()


def parse_gf1(data):
    """Return {name, version, samples: [ {name, pcm_off, data_size, rate, bits16,
    unsigned, modes} ]}. Best-effort: stops cleanly on truncation."""
    version = _cstr(data[:12])
    n_inst = data[82] if len(data) > 82 else 0
    samples = []
    pos = _HDR
    for _ in range(max(n_inst, 1)):
        if pos + _INST > len(data):
            break
        n_layers = data[pos + 22]
        pos += _INST
        for _ in range(max(n_layers, 1)):
            if pos + _LAYER > len(data):
                break
            n_smp = data[pos + 6]
            pos += _LAYER
            for _ in range(n_smp):
                if pos + _SMP_HDR > len(data):
                    break
                name = _cstr(data[pos:pos + 7])
                data_size = struct.unpack_from("<I", data, pos + 8)[0]
                rate = struct.unpack_from("<H", data, pos + 20)[0]
                modes = data[pos + 55]
                pcm_off = pos + _SMP_HDR
                samples.append({
                    "name": name, "pcm_off": pcm_off, "data_size": data_size,
                    "rate": rate, "bits16": bool(modes & 0x01),
                    "unsigned": bool(modes & 0x02), "modes": modes,
                })
                pos = pcm_off + data_size
    return {"name": _cstr(data[22:82][12:]) or version, "version": version,
            "samples": samples}


def inspect_gf1pat(filepath):
    import os
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        b = f.read(min(file_size, _READ_CAP))
    if len(b) < _HDR or b[:8] != b"GF1PATCH":
        return [], ["not a GF1PATCH (.PAT) file"]

    info = parse_gf1(b)
    n_inst = b[82]
    chunks = [{
        "id": "GF1", "offset": 0, "size": _HDR,
        "summary": f"Gravis UltraSound patch ({info['version']}) -- "
                   f"{n_inst} instrument(s), {len(info['samples'])} sample(s)",
        "fields": [
            _f(0x00, 12, "magic", info["version"]),
            _f(0x52, 1, "instruments", n_inst),
            _f(0x53, 1, "voices", b[83]),
            _f(0x55, 2, "waveforms", struct.unpack_from("<H", b, 85)[0],
               enc="<H", raw=struct.unpack_from("<H", b, 85)[0]),
        ],
        "warnings": [],
    }]
    for i, s in enumerate(info["samples"], 1):
        bits = "16-bit" if s["bits16"] else "8-bit"
        sign = "unsigned" if s["unsigned"] else "signed"
        chunks.append({
            "id": f"smp[{i}]", "offset": s["pcm_off"], "size": s["data_size"],
            "summary": f"{s['name'] or '(unnamed)'}  {s['data_size']:,} B {bits} "
                       f"{sign} PCM @ {s['rate']} Hz",
            "fields": [
                _f(None, 0, "sample_rate", s["rate"], "Hz"),
                _f(None, 0, "modes", f"0x{s['modes']:02x}", f"{bits}, {sign}"),
            ],
            "warnings": ([f"PCM runs past EOF (@0x{s['pcm_off']:x} + {s['data_size']:,})"]
                         if s["pcm_off"] + s["data_size"] > file_size else []),
            "payload_base": s["pcm_off"],
        })
    return chunks, []
