"""SoundFont 2 (.sf2) reader and sample extractor.

SF2 is a RIFF file (form 'sfbk') with three LIST chunks: INFO (metadata), sdta
(one 'smpl' chunk holding every sample's 16-bit PCM back to back), and pdta
(the preset/instrument/sample structure, ending in 'shdr' -- a table of sample
headers giving each sample's name, start/end index into smpl, loop, and rate).
Extracting a sample is a carve of smpl[start*2:end*2] wrapped in a WAV header.

Open, uncompressed, no access control -- codec/container work. (SF3 keeps the
same layout but stores each sample as Ogg Vorbis inside smpl; not handled yet.)
"""

import struct

_SHDR_LEN = 46          # bytes per sample header record
_INFO_STR = {b"INAM": "name", b"isng": "sound_engine", b"IPRD": "product",
             b"IENG": "engineer", b"ISFT": "software", b"ICMT": "comment",
             b"ICOP": "copyright", b"ICRD": "date", b"IART": "author"}


class Sf2Error(ValueError):
    """The bytes are not a decodable SF2 soundfont."""


def is_sf2(data):
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"sfbk"


def _iter_riff(data, start, end):
    """Yield (id, data_offset, size) for chunks in [start, end). LIST chunks
    yield their own id 'LIST' with the list type as the first 4 payload bytes."""
    pos = start
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + size > end:
            break
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def parse_sf2(data):
    """Decode an SF2 into {version, info, sample_count, smpl_offset, smpl_size,
    samples: [{name, start, end, loop_start, loop_end, rate, pitch, type}]}.
    Samples exclude the terminal EOS record and empty-named entries. Raises
    Sf2Error on malformed input."""
    if not is_sf2(data):
        raise Sf2Error("not a RIFF/sfbk SoundFont")
    riff_size = struct.unpack_from("<I", data, 4)[0]
    end = min(len(data), 8 + riff_size)
    info = {}
    version = None
    smpl_off = smpl_size = None
    shdr_off = shdr_size = None
    for cid, off, size in _iter_riff(data, 12, end):
        if cid != b"LIST":
            continue
        ltype = data[off:off + 4]
        for scid, soff, ssize in _iter_riff(data, off + 4, off + size):
            if ltype == b"INFO":
                if scid == b"ifil" and ssize >= 4:
                    version = f"{struct.unpack_from('<H', data, soff)[0]}." \
                              f"{struct.unpack_from('<H', data, soff + 2)[0]}"
                elif scid in _INFO_STR:
                    info[_INFO_STR[scid]] = data[soff:soff + ssize].split(
                        b"\x00")[0].decode("latin-1", "replace").strip()
            elif ltype == b"sdta" and scid == b"smpl":
                smpl_off, smpl_size = soff, ssize
            elif ltype == b"pdta" and scid == b"shdr":
                shdr_off, shdr_size = soff, ssize
    if smpl_off is None or shdr_off is None:
        raise Sf2Error("missing the smpl (sample data) or shdr (sample header) chunk")

    samples = []
    smpl_samples = smpl_size // 2
    for i in range(shdr_size // _SHDR_LEN):
        o = shdr_off + i * _SHDR_LEN
        name = data[o:o + 20].split(b"\x00")[0].decode("latin-1", "replace").strip()
        start, end_i, ls, le, rate = struct.unpack_from("<IIIII", data, o + 20)
        pitch, corr, link, stype = struct.unpack_from("<BbHH", data, o + 40)
        if name == "EOS" or not name:
            continue
        # a sane, in-range header only (a lied-about index must not carve garbage)
        if not (rate and start < end_i <= smpl_samples):
            continue
        samples.append({"name": name, "start": start, "end": end_i,
                        "loop_start": ls, "loop_end": le, "rate": rate,
                        "pitch": pitch, "correction": corr, "type": stype})
    return {"version": version, "info": info, "sample_count": len(samples),
            "smpl_offset": smpl_off, "smpl_size": smpl_size, "samples": samples}


def sample_wav(data, smpl_offset, sample):
    """A mono 16-bit WAV for one parsed sample header (its PCM range in smpl)."""
    a = smpl_offset + sample["start"] * 2
    b = smpl_offset + sample["end"] * 2
    pcm = data[a:b]
    rate = sample["rate"]
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body
