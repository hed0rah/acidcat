"""RATE-kind repair for WAV: the fmt and smpl fields that follow from the
sample format, brought back into line with it.

Three fields are functions of others, and the WAV walker already reports each
disagreement as a `field.inconsistent` defect; this is the same arithmetic as
a repair, so an edit that changes one side (`RIFF/fmt_#sample_rate`) can have
the other follow instead of leaving the file inconsistent:

    block_align        channels * ceil(bits_per_sample / 8)   (PCM, tag 1)
    avg_bytes_per_sec  sample_rate * block_align              (PCM, tag 1)
    smpl sample_period round(1e9 / sample_rate), within 1 ns

The conditions are the walker's, exactly: a compressed format's byte rate is
not PCM arithmetic, so only format tag 1 is held to the first two. Size-stable
(each field is overwritten in place) and never touches audio.
"""

import struct

from acidcat.core.write.countrepair import _riff_chunks, is_target  # noqa: F401


def _changes(data):
    """[{path, field, off, fmt, old, new, witness}] for each derived field
    that disagrees with its witnesses."""
    out = []
    fmt = rate = None
    for cid, poff, psize in _riff_chunks(data):
        if cid == b"fmt " and psize >= 16 and fmt is None:
            tag, ch, rate, avg, align, bits = struct.unpack_from("<HHIIHH", data, poff)
            fmt = True
            if tag == 1 and ch and bits:
                want = ch * ((bits + 7) // 8)
                if align != want:
                    out.append({"path": "RIFF/fmt_", "field": "block_align",
                                "off": poff + 12, "fmt": "<H", "old": align,
                                "new": want,
                                "witness": "channels * ceil(bits_per_sample / 8)"})
                    align = want
            if tag == 1 and rate and align and avg != rate * align:
                if rate * align <= 0xFFFFFFFF:
                    out.append({"path": "RIFF/fmt_", "field": "avg_bytes_per_sec",
                                "off": poff + 8, "fmt": "<I", "old": avg,
                                "new": rate * align,
                                "witness": "sample_rate * block_align"})
        elif cid == b"smpl" and psize >= 12 and rate:
            period = struct.unpack_from("<I", data, poff + 8)[0]
            want = round(1e9 / rate)
            if period and abs(period - want) > 1:
                out.append({"path": "RIFF/smpl", "field": "sample_period",
                            "off": poff + 8, "fmt": "<I", "old": period,
                            "new": want, "witness": "1e9 / fmt sample_rate"})
    return out


def analyze(data):
    return _changes(data)


def repair(data):
    """(new bytes, the changes made)."""
    changes = _changes(data)
    if not changes:
        return data, []
    out = bytearray(data)
    for c in changes:
        struct.pack_into(c["fmt"], out, c["off"], c["new"])
    return bytes(out), changes
