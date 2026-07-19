"""IFF 8SVX (8-bit sampled voice) walker.

8SVX is the Amiga sampled-sound format and a direct ancestor of the RIFF/WAVE
lineage: Electronic Arts' IFF (1985), big-endian on the Motorola 68000. A
`FORM...8SVX` holds a `VHDR` voice header (the analogue of `fmt `), an optional
`NAME`/`AUTH`/`ANNO`/`(c) ` text set, optional `ATAK`/`RLSE` envelopes and a
`CHAN` channel map, then a `BODY` of 8-bit signed PCM -- optionally
Fibonacci-delta compressed. `ANNO` is the Amiga-side `ISFT`: most files stamp
their authoring tool there, so it is surfaced as the writer tell.

Chunk framing is IFF-standard: a 4-byte id, a big-endian u32 size, the payload,
and a pad byte when the size is odd. Unknown chunks are surfaced by id and size
without guessing at their bodies; the walk degrades on any malformed input.
"""

import os

from acidcat.core.walk.base import _bu16, _bu32, _dtext, _f

_READ_CAP = 64 * 1024 * 1024
_CHUNK_CAP = 4096

_TEXT_CHUNKS = {"NAME": "voice name", "AUTH": "author",
                "ANNO": "annotation / authoring-tool tell", "(c) ": "copyright"}
_COMPRESSION = {0: "none (raw 8-bit signed PCM)", 1: "Fibonacci-delta"}
_CHAN = {2: "left", 4: "right", 6: "stereo (left + right)"}


def inspect_8svx(filepath):
    """Walk an IFF 8SVX file, returning (chunks, file_warnings)."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        b = f.read(min(file_size, _READ_CAP))
    chunks, warns = [], []
    if len(b) < 12 or b[:4] != b"FORM" or b[8:12] != b"8SVX":
        return chunks, ["not an IFF FORM 8SVX file"]

    form_size = _bu32(b, 4)
    hdr = {"id": "FORM", "offset": 0, "size": 12, "payload_base": 0,
           "summary": "IFF 8SVX (Amiga 8-bit sampled voice)",
           "fields": [
               _f(0x00, 4, "magic", "FORM"),
               _f(0x04, 4, "form_size", form_size,
                  "declared size of the form body", enc=">I", raw=form_size),
               _f(0x08, 4, "form_type", "8SVX"),
           ], "warnings": []}
    # the FORM size should be (file length - 8); an undercount is a real writer
    # fingerprint in this corpus (e.g. an AudioMaster-lineage tool off by 12).
    expected = file_size - 8
    if form_size != expected:
        hdr["warnings"].append(
            f"form_size {form_size:,} != file length - 8 ({expected:,}); "
            f"off by {expected - form_size} (trust the chunks, not the FORM size)")
    chunks.append(hdr)

    vh = None                                          # VHDR fields, for BODY duration
    name = tool = None
    pos, n = 12, 0
    from collections import Counter
    kinds = Counter()
    while pos + 8 <= len(b) and n < _CHUNK_CAP:
        cid = b[pos:pos + 4]
        size = _bu32(b, pos + 4)
        payload = pos + 8
        cid_s = cid.decode("ascii", "replace")
        avail = max(0, min(size, len(b) - payload))
        p = b[payload:payload + avail]
        n += 1
        try:
            chunk = _chunk(cid, cid_s, pos, size, p, avail, vh)
        except Exception as e:                         # never raise on a bad chunk
            chunk = {"id": cid_s, "offset": pos, "size": size, "fields": [],
                     "summary": "unparsed chunk",
                     "warnings": [f"chunk decode error: {e.__class__.__name__}: {e}"]}
        if cid == b"VHDR":
            vh = chunk.get("_vh")
        elif cid == b"NAME" and not name:
            name = _dtext(p)
        elif cid == b"ANNO" and not tool:
            tool = _dtext(p)
        chunk.pop("_vh", None)
        if avail < size:
            chunk.setdefault("warnings", []).append(
                f"chunk declares {size:,} bytes, only {avail:,} present (truncated)")
        kinds[cid_s] += 1
        chunks.append(chunk)
        step = 8 + size + (size & 1)                   # pad odd sizes to even
        if step <= 8:
            warns.append(f"chunk at 0x{pos:08x} has size {size}; stopping the walk")
            break
        pos += step

    if n >= _CHUNK_CAP:
        warns.append(f"chunk walk stopped at the {_CHUNK_CAP}-chunk cap")

    # enrich the FORM summary with rate/duration, the voice name, and the tool
    bits = []
    if vh and vh.get("rate"):
        bits.append(f"{vh['rate']} Hz")
        total = (vh.get("one", 0) or 0) + (vh.get("rep", 0) or 0)
        if total and vh["rate"]:
            bits.append(f"{total / vh['rate']:.2f}s")
        if vh.get("comp"):
            bits.append(_COMPRESSION.get(vh["comp"], "compressed"))
    if name:
        bits.append(f"'{name}'")
    if tool:
        bits.append(f"tool: {tool}")
    if bits:
        chunks[0]["summary"] += " -- " + ", ".join(bits)
    return chunks, warns


def _chunk(cid, cid_s, pos, size, p, avail, vh):
    fields, cwarns = [], []
    summary = None

    if cid == b"VHDR":
        return _vhdr_chunk(cid_s, pos, size, p, avail)

    if cid_s in _TEXT_CHUNKS:
        text = _dtext(p)
        fields.append(_f(0x00, avail, "text", text or "(empty)",
                         _TEXT_CHUNKS[cid_s]))
        summary = f"{_TEXT_CHUNKS[cid_s]}: {text}" if text else _TEXT_CHUNKS[cid_s]

    elif cid == b"BODY":
        summary = f"sample data, {size:,} bytes"
        if vh:
            enc = _COMPRESSION.get(vh.get("comp", 0), "?")
            summary += f" ({enc})"
            total = (vh.get("one", 0) or 0) + (vh.get("rep", 0) or 0)
            if total and vh.get("rate"):
                fields.append(_f(None, 0, "duration",
                                 f"{total / vh['rate']:.3f} s",
                                 f"{total:,} samples @ {vh['rate']} Hz (high octave)"))
            if vh.get("octs", 1) > 1:
                fields.append(_f(None, 0, "octaves", vh["octs"],
                                 "successively halved copies follow the high octave"))

    elif cid in (b"ATAK", b"RLSE"):
        pts = avail // 4                               # EGPoint = u16 duration + u16 dest
        label = "attack" if cid == b"ATAK" else "release"
        summary = f"{label} envelope, {pts} point(s)"
        fields.append(_f(0x00, avail, "eg_points", pts,
                         f"{label} envelope shape (duration,dest word pairs)"))

    elif cid == b"CHAN":
        ch = _bu32(p, 0) if avail >= 4 else 0
        summary = "channel: " + _CHAN.get(ch, f"0x{ch:x}")
        fields.append(_f(0x00, 4, "channel", ch, _CHAN.get(ch, "unknown mapping")))

    else:
        summary = f"unrecognized chunk, {size:,} bytes"

    return {"id": cid_s, "offset": pos, "size": size,
            "summary": summary, "fields": fields, "warnings": cwarns}


def _vhdr_chunk(cid_s, pos, size, p, avail):
    """VHDR (20 bytes): the 8SVX voice header, analogue of WAV's fmt chunk."""
    if avail < 20:
        return {"id": cid_s, "offset": pos, "size": size, "fields": [],
                "summary": "truncated VHDR",
                "warnings": [f"VHDR is {avail} bytes, spec is 20"]}
    one = _bu32(p, 0)
    rep = _bu32(p, 4)
    cyc = _bu32(p, 8)
    rate = _bu16(p, 12)
    octs = p[14]
    comp = p[15]
    vol = _bu32(p, 16)
    vh = {"rate": rate, "one": one, "rep": rep, "comp": comp, "octs": octs}
    fields = [
        _f(0x00, 4, "oneShotHiSamples", one,
           "1-shot part length in samples (high octave)"),
        _f(0x04, 4, "repeatHiSamples", rep,
           "repeating/loop part length in samples (0 = one-shot)"),
        _f(0x08, 4, "samplesPerHiCycle", cyc,
           "samples per waveform cycle (0 = sampled sound, not a wavetable)"),
        _f(0x0C, 2, "samplesPerSec", rate, f"{rate} Hz playback rate"),
        _f(0x0E, 1, "ctOctave", octs, f"{octs} octave(s) of waveforms stored"),
        _f(0x0F, 1, "sCompression", comp,
           _COMPRESSION.get(comp, f"unknown compression ({comp})")),
        _f(0x10, 4, "volume", vol, f"{vol / 0x10000:.3f}x playback (Unity = 0x10000)",
           enc=">I", raw=vol),
    ]
    cwarns = []
    if comp not in _COMPRESSION:
        cwarns.append(f"unknown sCompression {comp}")
    return {"id": cid_s, "offset": pos, "size": size, "_vh": vh,
            "summary": f"voice header: {rate} Hz, {octs} octave(s), "
                       f"{_COMPRESSION.get(comp, '?')}",
            "fields": fields, "warnings": cwarns}
