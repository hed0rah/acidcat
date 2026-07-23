"""Structural detection of headerless compressed-audio streams -- the third
`locate` engine.

The signature sweep needs a magic; the statistical detector needs raw-PCM
smoothness. Compressed audio with NO container has neither -- it is high-entropy
(so the statistical engine ignores it, correctly) and magicless. But it is not
structure-less: a codec stream is a chain of self-describing FRAMES. MPEG audio
(MP1/2/3) frames open with an 11-bit sync and carry a computable length, so a run
of consecutive valid frames each frame-length apart is an MPEG stream -- found by
CADENCE, not magic. This catches the raw .mp3 with no ID3 tag, audio ripped out
of a game asset, the CTF blob that `strings` can't touch.

Signature-found MP3s (an ID3 tag, caught by the sweep) are not this engine's job;
this is the headerless case. Free-format frames (length measured, not derived)
are skipped in v1.
"""

from acidcat.core.mp3 import decode_frame_header

_MIN_FRAMES = 12                 # a chain this long is a stream, not chance
_MAX_STREAMS = 4096
_READ_CAP = 256 * 1024 * 1024


def _chain(data, start, limit):
    """Frames in the consecutive-valid-frame chain from `start`, and its end.
    Requires a stable version/layer/sample_rate (VBR bitrate is fine)."""
    pos, frames, base = start, 0, None
    while pos + 4 <= limit:
        hdr = decode_frame_header(data[pos:pos + 4])
        if hdr is None:
            break
        flen = hdr["frame_length"]
        if not flen or flen < 4:                          # free-format / zero: stop
            break
        key = (hdr["version_id"], hdr["layer"], hdr["sample_rate"])
        if base is None:
            base = key
        elif key != base:                                 # a different codec config
            break
        frames += 1
        pos += flen
    return frames, pos


def find_mpeg_streams(data, min_frames=_MIN_FRAMES):
    """Find headerless MPEG-audio streams by frame-sync cadence. Returns records
    (kind='stream', format='mp3') shaped like the other locate engines."""
    n = min(len(data), _READ_CAP)
    out, i = [], 0
    while i < n - 4 and len(out) < _MAX_STREAMS:
        j = data.find(b"\xff", i, n)
        if j < 0 or j + 1 >= n:
            break
        if (data[j + 1] & 0xE0) != 0xE0:                  # not an 11-bit sync
            i = j + 1
            continue
        frames, end = _chain(data, j, n)
        if frames >= min_frames:
            hdr = decode_frame_header(data[j:j + 4])
            out.append({
                "kind": "stream", "format": "mp3",
                "offset": j, "end": end, "length": end - j,
                "confidence": round(min(0.60 + frames * 0.01, 0.99), 2),
                "inspectable": False, "evidence": None, "frames": frames,
                "stream_info": {"mpeg": hdr["version"], "layer": hdr["layer"],
                                "sample_rate": hdr["sample_rate"]},
            })
            i = end                                       # resume past the stream
        else:
            i = j + 1
    return out
