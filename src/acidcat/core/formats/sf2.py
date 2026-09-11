"""SoundFont 2 (.sf2) reader and sample extractor.

SF2 is a RIFF file (form 'sfbk') with three LIST chunks: INFO (metadata), sdta
(one 'smpl' chunk holding every sample's 16-bit PCM back to back), and pdta
(the preset/instrument/sample structure, ending in 'shdr' -- a table of sample
headers giving each sample's name, start/end index into smpl, loop, and rate).
Extracting a sample is a carve of smpl[start*2:end*2] wrapped in a WAV header.

Open, no access control -- codec/container work. SF3 (MuseScore) keeps the same
sfbk layout but stores each sample as an Ogg Vorbis stream inside smpl, marked
by sample-type bit 0x10, with the shdr start/end fields repurposed as byte
offsets into smpl. acidcat maps SF3 structure and extracts the Ogg streams
verbatim; decoding Vorbis to PCM needs a codec it does not bundle. The MuseScore
writer also omits RIFF pad bytes, which the chunk walker tolerates.
"""

import struct

_SHDR_LEN = 46          # bytes per sample header record
# The rest of pdta, which is the structure ABOVE the samples. A soundfont is a
# tree -- preset -> preset zone -> instrument -> instrument zone -> sample --
# and reading only shdr reports its leaves while saying nothing about which
# preset plays what, the first question anyone opening one asks.
_PHDR_LEN = 38          # name[20], preset, bank, bagNdx, library, genre, morph
_INST_LEN = 22          # name[20], bagNdx
_BAG_LEN = 4            # genNdx, modNdx  (both pbag and ibag)
_GEN_LEN = 4            # oper, amount    (both pgen and igen)
# The generator opers read here. A zone carries many; these are the ones that
# say what it PLAYS and where it sits on the keyboard.
_GEN_INSTRUMENT = 41    # in pgen: the index into inst this preset zone plays
_GEN_SAMPLE_ID = 53     # in igen: the index into shdr this instrument zone plays
_GEN_KEY_RANGE = 43     # lo in the LOW byte, hi in the high byte
_GEN_VEL_RANGE = 44
# A forged bag or generator index must not spin or allocate. Real fonts are far
# under this: a 6 MB General MIDI font carries 137 presets and 39,635 generators.
_MAX_RECORDS = 1 << 16
_INFO_STR = {b"INAM": "name", b"isng": "sound_engine", b"IPRD": "product",
             b"IENG": "engineer", b"ISFT": "software", b"ICMT": "comment",
             b"ICOP": "copyright", b"ICRD": "date", b"IART": "author"}


class Sf2Error(ValueError):
    """The bytes are not a decodable SF2 soundfont."""


def is_sf2(data):
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"sfbk"


def _riff_id_ok(data, pos):
    """A plausible RIFF chunk id starts here (4 printable-ASCII bytes)."""
    return pos + 4 <= len(data) and all(0x20 <= b < 0x7f for b in data[pos:pos + 4])


def _iter_riff(data, start, end):
    """Yield (id, data_offset, size) for chunks in [start, end). LIST chunks
    yield their own id 'LIST' with the list type as the first 4 payload bytes.

    RIFF pads an odd-sized chunk to an even boundary, but MuseScore's SF3 writer
    omits the pad byte, which desyncs a strict reader on the first odd chunk (the
    smpl blob). Skip the pad only when it keeps us aligned to a real chunk id;
    when the padded position is garbage but the unpadded one is a valid id, this
    writer did not pad."""
    pos = start
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + size > end:
            break
        yield cid, pos + 8, size
        nxt = pos + 8 + size
        if size & 1 and nxt < end:
            if _riff_id_ok(data, nxt) and not _riff_id_ok(data, nxt + 1):
                pass                       # unpadded writer (SF3)
            else:
                nxt += 1                   # spec even-alignment pad
        pos = nxt


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
    # every pdta sub-table by id, so the structure above the samples can be
    # read without walking the RIFF a second time
    pdta = {}
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
            elif ltype == b"pdta":
                pdta[scid.decode("latin-1", "replace")] = (soff, ssize)
                if scid == b"shdr":
                    shdr_off, shdr_size = soff, ssize
    if smpl_off is None or shdr_off is None:
        raise Sf2Error("missing the smpl (sample data) or shdr (sample header) chunk")

    # SF3 (MuseScore) keeps the sfbk layout but stores each sample as an Ogg
    # Vorbis stream inside smpl and marks it with sample-type bit 0x10; the
    # shdr start/end fields then hold byte offsets into smpl, not sample indices.
    smpl_samples = smpl_size // 2
    samples = []
    any_compressed = False
    for i in range(shdr_size // _SHDR_LEN):
        o = shdr_off + i * _SHDR_LEN
        name = data[o:o + 20].split(b"\x00")[0].decode("latin-1", "replace").strip()
        start, end_i, ls, le, rate = struct.unpack_from("<IIIII", data, o + 20)
        pitch, corr, link, stype = struct.unpack_from("<BbHH", data, o + 40)
        if name == "EOS" or not name:
            continue
        compressed = bool(stype & 0x10)
        if compressed:
            # start/end are byte offsets; the region is an Ogg stream
            byte_off, byte_len = smpl_off + start, end_i - start
            in_range = 0 <= start < end_i <= smpl_size
        else:
            byte_off, byte_len = smpl_off + start * 2, (end_i - start) * 2
            in_range = start < end_i <= smpl_samples
        # a sane, in-range header only (a lied-about index must not carve garbage)
        if not (rate and in_range):
            continue
        any_compressed = any_compressed or compressed
        samples.append({"name": name, "start": start, "end": end_i,
                        "loop_start": ls, "loop_end": le, "rate": rate,
                        "pitch": pitch, "correction": corr,
                        "type": stype & 0x0f, "compressed": compressed,
                        "byte_off": byte_off, "byte_len": byte_len})
    # The tree indexes shdr by its RAW record number, which is not the index
    # into `samples`: that list drops the EOS sentinel and any header whose
    # range does not fit, so the two go out of step at the first bad record.
    # Names are collected in record order here so a sampleID resolves to the
    # sample the file meant.
    shdr_names = [_name_at(data, shdr_off + i * _SHDR_LEN)
                  for i in range(shdr_size // _SHDR_LEN)]
    tree = parse_sf2_tree(data, pdta, shdr_names)

    return {"version": version, "info": info, "sample_count": len(samples),
            "smpl_offset": smpl_off, "smpl_size": smpl_size,
            "sf3": any_compressed, "samples": samples,
            "presets": tree["presets"], "instruments": tree["instruments"]}



def _table(tables, name, width):
    """(offset, count) for one pdta sub-table, or (None, 0) when absent.

    The count is floored by the declared size, so a table whose bytes do not
    divide evenly contributes whole records and drops the remainder rather than
    reading a partial one off the end.
    """
    ent = tables.get(name)
    if not ent:
        return None, 0
    off, size = ent
    return off, min(size // width, _MAX_RECORDS)


def _zone_gen_span(data, bag_off, bag_n, zone):
    """The generator range [g0, g1) belonging to one bag record.

    Each bag holds the index of its FIRST generator, and a zone's generators
    end where the next zone's begin -- which is why every bag table carries one
    extra terminal record. A file that omits it, or whose indices run
    backwards, gets an empty span rather than a read past the table.
    """
    if bag_off is None or not (0 <= zone < bag_n - 1):
        return 0, 0
    g0 = struct.unpack_from("<H", data, bag_off + zone * _BAG_LEN)[0]
    g1 = struct.unpack_from("<H", data, bag_off + (zone + 1) * _BAG_LEN)[0]
    return (g0, g1) if g1 >= g0 else (g0, g0)


def _gens(data, gen_off, gen_n, g0, g1):
    """{oper: amount} for one zone's generators, the last value winning.

    The spec gives a repeated oper its final value, and a zone can carry
    dozens; the caller keeps only the handful that say what it plays.
    """
    out = {}
    if gen_off is None:
        return out
    for g in range(max(0, g0), min(g1, gen_n)):
        oper, amount = struct.unpack_from("<HH", data, gen_off + g * _GEN_LEN)
        out[oper] = amount
    return out


def _name_at(data, off, width=20):
    """A fixed-width NUL-padded name, as every pdta table stores one."""
    raw = data[off:off + width]
    return raw.split(b"\x00")[0].decode("latin-1", "replace").strip()


def parse_sf2_tree(data, tables, sample_names):
    """The preset and instrument structure that sits above the samples.

    Returns {presets, instruments}: a preset names the bank and MIDI program it
    answers to and the instruments its zones reach; an instrument names the
    sample each of its zones plays and the key range that zone covers.

    Every index is bounds-checked against the table it points into. A soundfont
    is a tree of cross-references and nothing in the file guarantees they are
    consistent, so an out-of-range index drops that one zone rather than
    stopping the walk or inventing a target.
    """
    phdr_off, phdr_n = _table(tables, "phdr", _PHDR_LEN)
    inst_off, inst_n = _table(tables, "inst", _INST_LEN)
    pbag_off, pbag_n = _table(tables, "pbag", _BAG_LEN)
    ibag_off, ibag_n = _table(tables, "ibag", _BAG_LEN)
    pgen_off, pgen_n = _table(tables, "pgen", _GEN_LEN)
    igen_off, igen_n = _table(tables, "igen", _GEN_LEN)

    instruments = []
    # inst_n - 1: the final record is the EOI sentinel. It exists to terminate
    # the last real instrument's bag span, and counting it gives every
    # soundfont an instrument it does not have.
    for i in range(max(0, inst_n - 1)):
        o = inst_off + i * _INST_LEN
        first = struct.unpack_from("<H", data, o + 20)[0]
        last = struct.unpack_from("<H", data, o + _INST_LEN + 20)[0]
        zones = []
        for z in range(first, min(last, ibag_n)):
            gen = _gens(data, igen_off, igen_n,
                        *_zone_gen_span(data, ibag_off, ibag_n, z))
            sid = gen.get(_GEN_SAMPLE_ID)
            # a zone with no sampleID is the instrument's GLOBAL zone: it
            # carries defaults for the others and plays nothing itself
            if sid is None:
                continue
            kr = gen.get(_GEN_KEY_RANGE)
            vr = gen.get(_GEN_VEL_RANGE)
            zones.append({
                "sample_id": sid,
                "sample": sample_names[sid] if 0 <= sid < len(sample_names) else None,
                "key_lo": kr & 0xFF if kr is not None else None,
                "key_hi": kr >> 8 if kr is not None else None,
                "vel_lo": vr & 0xFF if vr is not None else None,
                "vel_hi": vr >> 8 if vr is not None else None,
            })
        instruments.append({"name": _name_at(data, o), "zones": zones})

    presets = []
    for i in range(max(0, phdr_n - 1)):          # the EOP sentinel, as above
        o = phdr_off + i * _PHDR_LEN
        program, bank, first = struct.unpack_from("<HHH", data, o + 20)
        last = struct.unpack_from("<H", data, o + _PHDR_LEN + 24)[0]
        used = []
        for z in range(first, min(last, pbag_n)):
            idx = _gens(data, pgen_off, pgen_n,
                        *_zone_gen_span(data, pbag_off, pbag_n, z)
                        ).get(_GEN_INSTRUMENT)
            # as above: a preset zone with no instrument is its global zone
            if idx is None or not (0 <= idx < len(instruments)):
                continue
            if idx not in used:
                used.append(idx)
        presets.append({
            "name": _name_at(data, o), "bank": bank, "program": program,
            "zones": max(0, last - first), "instruments": used,
            # bank 128 is the percussion bank, where `program` selects a drum
            # kit rather than an instrument
            "percussion": bank == 128,
        })
    return {"presets": presets, "instruments": instruments}


def sample_bytes(data, sample):
    """The raw sample region: 16-bit PCM for SF2, an Ogg Vorbis stream for SF3."""
    return data[sample["byte_off"]:sample["byte_off"] + sample["byte_len"]]


def sample_wav(data, smpl_offset, sample):
    """A mono 16-bit WAV for one uncompressed (SF2) sample header. Raises for a
    compressed SF3 sample, whose Ogg stream must be extracted with
    ``sample_bytes`` (decoding Vorbis to PCM needs a codec acidcat does not
    bundle)."""
    if sample.get("compressed"):
        raise Sf2Error("SF3 sample is Ogg Vorbis; extract with sample_bytes")
    pcm = sample_bytes(data, sample)
    rate = sample["rate"]
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body
