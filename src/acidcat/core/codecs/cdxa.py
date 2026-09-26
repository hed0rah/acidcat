"""CD-XA ADPCM: detect a raw CD sector image, demux its XA audio streams, and
decode CD-XA ADPCM to linear PCM.

CD-XA is the streaming-audio format of the PlayStation and its CD-XA kin
(Saturn, 3DO, CD-i). Game music is not raw PCM: it is 4-bit ADPCM interleaved
through the data track in Mode2 Form-2 sectors, tagged by an 8-byte subheader.
A statistical detector that meets it will call it "raw PCM" with low confidence;
played as linear PCM it is recognisable but janky, because it is 4 bits/sample
run through a 2-tap predictor, not 16-bit samples.

Pipeline: detect_cd_image -> xa_streams (demux by file/channel) -> decode_stream.
Verified bit-plausible against the LSD Dream Emulator disc (stereo/37800/4-bit):
the decoded stream shows sample-to-sample autocorrelation ~0.98 (coherent audio),
versus ~0 for the same bytes read as linear PCM.

Only the 4-bit coding is decoded (mono and stereo, 37800 and 18900 Hz); it covers
essentially all PS1 streaming audio. The rare 8-bit XA coding raises
NotImplementedError rather than emit an unverified guess.
"""

import struct

from acidcat.core.infra.source import open_input, input_size
from acidcat.core.primitives.pcm import PS_ADPCM_FILTER, clip16, interleave_stereo, signed_nibble
from acidcat.core.primitives.wavio import pcm_wav

SECTOR = 2352                       # Mode1/Mode2 raw sector (with sync + headers)
_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"     # 12-byte sector sync mark
_XA_DATA = 24                       # audio payload offset: 12 sync + 4 header + 8 subheader
_XA_AUDIO_BYTES = 2304              # 18 sound groups x 128 bytes
_GROUP = 128
_SUBMODE_AUDIO = 0x04               # subheader submode bit: this is an audio sector

# CD-XA ADPCM filter coefficients (f0, f1), scaled by 1/64. Filters 0-3 are the
# XA set; 4 appears in some implementations. Matches ffmpeg's xa_adpcm_table.


def detect_cd_image(path):
    """Return a dict describing a raw CD sector image, or None if `path` is not
    one. Checks the 12-byte sync mark on the first two sectors.

    keys: sector_size, mode (1 or 2), sectors, xa (bool: Mode2 with a plausible
    XA subheader)."""
    try:
        size = input_size(path)
        with open_input(path) as f:
            s0 = f.read(SECTOR)
            f.seek(SECTOR)
            s1 = f.read(SECTOR)
    except OSError:
        return None
    if len(s0) < SECTOR or s0[:12] != _SYNC or s1[:12] != _SYNC:
        return None
    mode = s0[15]
    # Mode2 XA carries an 8-byte subheader at 0x10; its submode/coding bytes are
    # duplicated (bytes 0x10-0x11 == 0x14-0x15 in a well-formed sector).
    xa = mode == 2 and s0[16:18] == s0[20:22]
    return {"sector_size": SECTOR, "mode": mode, "sectors": size // SECTOR, "xa": xa}


def coding_of(byte):
    """Decode an XA coding byte into {stereo, rate, bits}."""
    return {
        "stereo": bool(byte & 0x03),
        "rate": 18900 if (byte >> 2) & 0x03 else 37800,
        "bits": 8 if (byte >> 4) & 0x03 else 4,
    }


def xa_streams(path):
    """Scan every audio sector (submode bit 0x04) and group by (file, channel).

    Returns {(file, channel): {"sectors": [index...], "coding": byte}} where
    coding is the dominant coding byte across the stream (sectors occasionally
    carry an outlier)."""
    from collections import Counter
    sectors = {}
    codings = {}
    with open_input(path) as f:
        idx = 0
        while True:
            s = f.read(SECTOR)
            if len(s) < SECTOR:
                break
            if s[18] & _SUBMODE_AUDIO:
                key = (s[16], s[17])
                sectors.setdefault(key, []).append(idx)
                codings.setdefault(key, Counter())[s[19]] += 1
            idx += 1
    return {k: {"sectors": v, "coding": codings[k].most_common(1)[0][0]}
            for k, v in sectors.items()}






def _decode_group(blk, state, stereo):
    """Decode one 128-byte sound group (4-bit). Returns (chan0, chan1) sample
    lists; for mono chan1 is empty and chan0 holds all 224 samples in order.
    `state` = [a1, a2, b1, b2] predictor history, updated in place (a = chan0,
    b = chan1/high-nibble planes)."""
    hdr, data = blk[0:16], blk[16:128]
    c0, c1 = [], []
    a1, a2, b1, b2 = state
    for i in range(4):
        # low-nibble plane
        h = hdr[4 + i * 2]
        sh = 12 - (h & 0x0F)
        if sh < 0:
            sh = 3                       # ranges 13..15 are invalid; clamp
        f0, f1 = PS_ADPCM_FILTER[min(h >> 4, 4)]
        for j in range(28):
            t = signed_nibble(data[i + j * 4])
            s = clip16((t << sh) + ((a1 * f0 + a2 * f1 + 32) >> 6))
            a2, a1 = a1, s
            c0.append(s)
        # high-nibble plane -> chan1 (stereo) or continues chan0 (mono)
        h = hdr[5 + i * 2]
        sh = 12 - (h & 0x0F)
        if sh < 0:
            sh = 3
        f0, f1 = PS_ADPCM_FILTER[min(h >> 4, 4)]
        if stereo:
            for j in range(28):
                t = signed_nibble(data[i + j * 4] >> 4)
                s = clip16((t << sh) + ((b1 * f0 + b2 * f1 + 32) >> 6))
                b2, b1 = b1, s
                c1.append(s)
        else:
            for j in range(28):
                t = signed_nibble(data[i + j * 4] >> 4)
                s = clip16((t << sh) + ((a1 * f0 + a2 * f1 + 32) >> 6))
                a2, a1 = a1, s
                c0.append(s)
    state[0], state[1], state[2], state[3] = a1, a2, b1, b2
    return c0, c1


def decode_sectors(payloads, stereo):
    """Decode an iterable of 2304-byte XA audio payloads (4-bit) into interleaved
    16-bit PCM bytes. Predictor state carries across sectors, as the stream
    demands."""
    import array
    state = [0, 0, 0, 0]
    out = array.array("h")
    for pay in payloads:
        c0, c1 = [], []
        for g in range(0, _XA_AUDIO_BYTES, _GROUP):
            a, b = _decode_group(pay[g:g + _GROUP], state, stereo)
            c0 += a
            c1 += b
        if stereo:
            out.frombytes(interleave_stereo(c0, c1))
        else:
            out += array.array("h", c0)
    return out.tobytes()


def _payloads(path, sector_indices):
    with open_input(path) as f:
        for idx in sector_indices:
            f.seek(idx * SECTOR)
            yield f.read(SECTOR)[_XA_DATA:_XA_DATA + _XA_AUDIO_BYTES]


def audio_sectors_in_range(path, lba, count):
    """Yield the audio-submode sector indices within [lba, lba+count) -- the XA
    audio belonging to one ISO file (a .STR movie or .XA stream)."""
    with open_input(path) as f:
        f.seek(lba * SECTOR)
        for idx in range(lba, lba + count):
            s = f.read(SECTOR)
            if len(s) < SECTOR:
                break
            if s[18] & _SUBMODE_AUDIO:
                yield idx


def decode_range(path, lba, count, max_audio=None):
    """Decode the XA audio inside one ISO file's sector range. Returns
    (pcm_bytes, info) or None if the range holds no 4-bit XA audio. max_audio
    caps the number of audio sectors decoded (a fast preview)."""
    import itertools
    gen = audio_sectors_in_range(path, lba, count)
    secs = list(itertools.islice(gen, max_audio)) if max_audio else list(gen)
    if not secs:
        return None
    with open_input(path) as f:
        f.seek(secs[0] * SECTOR)
        cod = coding_of(f.read(SECTOR)[19])
    if cod["bits"] != 4:
        return None
    pcm = decode_sectors(_payloads(path, secs), cod["stereo"])
    ch = 2 if cod["stereo"] else 1
    return pcm, {"channels": ch, "rate": cod["rate"], "bits": 16,
                 "frames": len(pcm) // (2 * ch)}


def decode_stream(path, key=None):
    """Decode one XA stream to PCM. `key` is a (file, channel) tuple; defaults to
    the largest stream on the disc. Returns (pcm_bytes, info) where info has
    channels, rate, bits, frames, key. Raises NotImplementedError for 8-bit XA."""
    streams = xa_streams(path)
    if not streams:
        raise ValueError("no XA audio sectors found")
    if key is None:
        key = max(streams, key=lambda k: len(streams[k]["sectors"]))
    cod = coding_of(streams[key]["coding"])
    if cod["bits"] != 4:
        raise NotImplementedError("8-bit XA-ADPCM is not decoded yet")
    pcm = decode_sectors(_payloads(path, streams[key]["sectors"]), cod["stereo"])
    ch = 2 if cod["stereo"] else 1
    frames = len(pcm) // (2 * ch)
    return pcm, {"channels": ch, "rate": cod["rate"], "bits": 16,
                 "frames": frames, "key": key}


def write_wav(pcm, info, out_path):
    """Write interleaved 16-bit PCM to a WAV file using decode_stream's info."""
    with open(out_path, "wb") as f:
        f.write(pcm_wav(pcm, info["rate"], info["channels"]))


def split_gaps(pcm, info, thresh=25, min_gap_s=1.5, min_song_s=5.0):
    """Find song boundaries in decoded PCM by silence gaps. Returns a list of
    (start_frame, end_frame) spans longer than min_song_s, split on runs of
    quiet longer than min_gap_s. A coarse envelope (mean |amplitude| per 0.1 s
    window) drives it; good enough to carve a continuous soundtrack into tracks."""
    import array
    a = array.array("h")
    a.frombytes(pcm)
    ch = info["channels"]
    win = max(1, info["rate"] // 10)                 # 0.1 s windows
    step = win * ch
    env = []
    for i in range(0, len(a) - step + 1, step):
        seg = a[i:i + step]
        env.append(sum(x if x >= 0 else -x for x in seg) / len(seg))
    min_run = max(1, int(min_gap_s * 10))            # windows of quiet
    gaps, run = [], 0
    for i, e in enumerate(env):
        if e < thresh:
            run += 1
        else:
            if run >= min_run:
                gaps.append((i - run, i))
            run = 0
    if run >= min_run:
        gaps.append((len(env) - run, len(env)))
    songs, prev = [], 0
    min_win = int(min_song_s * 10)
    for a0, b0 in gaps:
        if a0 - prev > min_win:
            songs.append((prev * win, a0 * win))     # in frames
        prev = b0
    if len(env) - prev > min_win:
        songs.append((prev * win, len(env) * win))
    return songs
