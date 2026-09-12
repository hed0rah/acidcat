"""Apple Core Audio Format (.caf) structural walker.

The chunked-container shape this tool already reads a dozen times over, with
Apple's choices in the three places the family differs:

  endian    big, throughout the container -- but the SAMPLES follow a flag in
            `desc`, not the container, so a reader that assumes one from the
            other is wrong half the time
  sizes     s64, SIGNED, and payload-only (RIFF's meaning, not Wave64's).
            A size of -1 is legal and means "to the end of the file": the
            format's answer to writing a stream whose length is not yet known
  align     none. Chunks abut, with no padding rule to get wrong

`desc` is mandatory and must come first, which makes it the one chunk whose
absence is a structural finding rather than a missing nicety.

Byte facts verified against libsndfile 1.2.2 output, including the endianness
trap: a file whose desc flags are 0 stores big-endian samples, and decoding its
data little-endian reproduces noise rather than the tone that is there.
"""

import os
import struct

from acidcat.core.primitives.notes import coverage, is_coverage
from acidcat.core.walk.apple import _parse_chan
from acidcat.core.walk.base import _PAYLOAD_CAP, _f

MAGIC = b"caff"

_HEADER = 8                    # 'caff' + u16 version + u16 flags
_CHUNK_HEADER = 12             # 4cc + s64 size
_DESC = 32                     # the mandatory description chunk
_MAX_CHUNKS = 4096
_TO_EOF = -1                   # a legal size meaning "the rest of the file"
_STRING_CAP = 64               # info entries to list

# The four-character audio format ids CAF carries. Anything else is reported as
# its literal FOURCC rather than guessed at.
_FORMATS = {
    "lpcm": "linear PCM", "ima4": "IMA 4:1 ADPCM", "aac ": "AAC",
    "MAC3": "MACE 3:1", "MAC6": "MACE 6:1", "ulaw": "u-law", "alaw": "A-law",
    ".mp1": "MPEG-1 Layer 1", ".mp2": "MPEG-1 Layer 2", ".mp3": "MPEG-1 Layer 3",
    "alac": "Apple Lossless", "QDMC": "QDesign Music", "QDM2": "QDesign Music 2",
    "Qclp": "Qualcomm PureVoice", "aach": "AAC HE", "paac": "AAC (packetized)",
}

# desc.mFormatFlags, for lpcm. Both bits are about how to READ the samples, and
# neither is implied by the container's own byte order.
_PCM_FLOAT = 0x01
_PCM_LITTLE_ENDIAN = 0x02


def is_caf(head):
    """True for bytes opening with the CAF file magic."""
    return len(head) >= 4 and head[:4] == MAGIC


def _fourcc(b):
    """Printable chunk/format id, or hex when the bytes are not printable."""
    if len(b) == 4 and all(0x20 <= c < 0x7F for c in b):
        return b.decode("ascii")
    return "hex:" + b.hex()


def _parse_desc(b, ctx):
    """CAFAudioDescription: the mandatory first chunk, 32 bytes, big-endian."""
    fields, warns = [], []
    if len(b) < _DESC:
        return "truncated", fields, [
            f"desc payload is {len(b)} bytes, the spec fixes it at {_DESC}"]
    rate, fmt_id, flags, bpp, fpp, chans, bits = struct.unpack_from(
        ">d4sIIIII", b, 0)
    fid = _fourcc(fmt_id)
    name = _FORMATS.get(fid, f"unknown {fid!r}")
    ctx.update(sample_rate=rate, format_id=fid, channels=chans,
               bits_per_channel=bits, bytes_per_packet=bpp,
               frames_per_packet=fpp)

    fields.append(_f(0x00, 8, "sample_rate", f"{rate:g}", "f64, big-endian"))
    fields.append(_f(0x08, 4, "format_id", fid, name))
    flag_note = ""
    if fid == "lpcm":
        # The trap. The container is big-endian; the samples are whatever this
        # says, and a reader that infers one from the other is wrong on every
        # file written the other way.
        flag_note = ("float" if flags & _PCM_FLOAT else "integer") + ", " + (
            "little-endian samples" if flags & _PCM_LITTLE_ENDIAN
            else "big-endian samples")
        ctx["pcm_float"] = bool(flags & _PCM_FLOAT)
        ctx["pcm_little_endian"] = bool(flags & _PCM_LITTLE_ENDIAN)
    fields.append(_f(0x0C, 4, "format_flags", f"0x{flags:08x}", flag_note))
    fields.append(_f(0x10, 4, "bytes_per_packet", bpp,
                     "0 means variable: the packet table has the sizes"))
    fields.append(_f(0x14, 4, "frames_per_packet", fpp))
    fields.append(_f(0x18, 4, "channels_per_frame", chans))
    fields.append(_f(0x1C, 4, "bits_per_channel", bits,
                     "0 for a compressed format"))

    if rate <= 0:
        warns.append(f"sample rate is {rate:g}, which cannot be played")
    if chans == 0:
        warns.append("channels_per_frame is 0")
    if fid == "lpcm" and bpp and chans and bits:
        expect = chans * ((bits + 7) // 8)
        if bpp != expect:
            warns.append(
                f"bytes_per_packet is {bpp} but {chans}ch x {bits}-bit needs "
                f"{expect}")
    summary = f"{name} {rate:g} Hz, {chans}ch"
    if bits:
        summary += f", {bits}-bit"
    if flag_note:
        summary += f" ({flag_note})"
    return summary, fields, warns


def _parse_data(b, ctx, size):
    """The audio. Its first four bytes are an edit count, NOT samples."""
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["data payload is too short for its edit count"]
    edits = struct.unpack_from(">I", b, 0)[0]
    audio = max(0, size - 4)
    fields.append(_f(0x00, 4, "edit_count", edits,
                     "increments on every edit; the audio starts after it"))
    fields.append(_f(None, 0, "audio_bytes", f"{audio:,}",
                     "the payload less the 4-byte edit count"))
    bpp = ctx.get("bytes_per_packet") or 0
    fpp = ctx.get("frames_per_packet") or 0
    rate = ctx.get("sample_rate") or 0
    summary = f"audio payload, {audio:,} bytes"
    if bpp and fpp:
        frames = audio // bpp * fpp
        fields.append(_f(None, 0, "frames", f"{frames:,}"))
        if rate > 0:
            summary += f", {frames / rate:.3f} s"
        if audio % bpp:
            warns.append(
                f"{audio:,} audio bytes is not a whole number of {bpp}-byte "
                f"packets ({audio % bpp} trail)")
    elif bpp == 0:
        # Not a defect: a variable-bitrate format says so with bpp 0 and puts
        # the sizes in `pakt`. Saying that beats reporting no duration at all.
        summary += ", variable packet size (see pakt)"
    return summary, fields, warns


def _parse_info(b, _ctx):
    """A u32 count, then that many NUL-terminated key/value string pairs."""
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["info payload is too short for its count"]
    n = struct.unpack_from(">I", b, 0)[0]
    parts = b[4:].split(b"\x00")
    pairs = 0
    for i in range(0, len(parts) - 1, 2):
        key = parts[i].decode("utf-8", "replace")
        if not key:
            continue
        val = parts[i + 1].decode("utf-8", "replace")
        if pairs < _STRING_CAP:
            fields.append(_f(None, 0, key[:40], val[:120]))
        pairs += 1
    if pairs > _STRING_CAP:
        warns.append(coverage(f"listing the first {_STRING_CAP} of {pairs} entries"))
    if n != pairs:
        warns.append(f"declares {n:,} entries, {pairs:,} strings are present")
    return f"{pairs} metadata entr{'y' if pairs == 1 else 'ies'}", fields, warns


def _parse_pakt(b, _ctx):
    """The variable-bitrate packet table's header (the table itself is a
    stream of variable-length integers, reported as a region)."""
    fields, warns = [], []
    if len(b) < 24:
        return "truncated", fields, [
            f"pakt payload is {len(b)} bytes, the header alone is 24"]
    packets, valid, priming, remainder = struct.unpack_from(">qqii", b, 0)
    fields.append(_f(0x00, 8, "packets", f"{packets:,}"))
    fields.append(_f(0x08, 8, "valid_frames", f"{valid:,}"))
    fields.append(_f(0x10, 4, "priming_frames", priming,
                     "encoder latency to discard at the start"))
    fields.append(_f(0x14, 4, "remainder_frames", remainder,
                     "padding to discard at the end"))
    if packets < 0:
        warns.append(f"packet count is negative ({packets:,})")
    return f"{packets:,} packets, {valid:,} valid frames", fields, warns

def _parse_peak(b, ctx):
    """Per-channel peak amplitude and the frame it occurs on."""
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["peak payload is too short"]
    edits = struct.unpack_from(">I", b, 0)[0]
    fields.append(_f(0x00, 4, "edit_count", edits))
    chans = ctx.get("channels") or 0
    pos, i = 4, 0
    while pos + 12 <= len(b) and i < 64:
        val, frame = struct.unpack_from(">fQ", b, pos)
        fields.append(_f(pos, 12, f"peak[{i}]", f"{val:.6f} @ frame {frame:,}"))
        pos += 12
        i += 1
    if chans and i != chans:
        warns.append(f"{i} peak entr{'y' if i == 1 else 'ies'} for "
                     f"{chans} channel(s)")
    return f"{i} channel peak(s)", fields, warns


_PARSERS = {
    "desc": _parse_desc,
    "info": _parse_info,
    "pakt": _parse_pakt,
    "chan": _parse_chan,
    "peak": _parse_peak,
}

# Chunks whose payload is opaque or simply reserved space; named so the walk
# reports what they are rather than dumping their first bytes.
_KNOWN_OPAQUE = {
    "free": "reserved free space",
    "kuki": "codec magic cookie (opaque decoder config)",
    "ovvw": "overview / waveform preview",
    "strg": "string table",
    "mark": "markers",
    "regn": "regions",
    "uuid": "application-defined (UUID-keyed)",
    "midi": "embedded MIDI",
    "umid": "SMPTE UMID",
}


def inspect_caf(filepath):
    """Walk a CAF file: an 8-byte header, then 4cc/s64 chunks."""
    file_size = os.path.getsize(filepath)
    ctx = {"file_size": file_size}
    chunks, file_warns = [], []

    with open(filepath, "rb") as f:
        hdr = f.read(_HEADER)
        if len(hdr) < _HEADER:
            # reachable through fmt_override, which promises to degrade like
            # any other walk
            return chunks, [f"file is {len(hdr)} bytes; a CAF header needs "
                            f"{_HEADER}"]
        if hdr[:4] != MAGIC:
            file_warns.append("missing the 'caff' magic")
        version, flags = struct.unpack_from(">HH", hdr, 4)
        if version != 1:
            file_warns.append(f"file version is {version}, the spec defines 1")

        chunks.append({
            "id": "caff", "offset": 0, "size": _HEADER,
            "payload_base": 0, "payload_len": _HEADER, "extent_len": _HEADER,
            "summary": f"Core Audio Format v{version}",
            "fields": [
                _f(0x00, 4, "magic", _fourcc(hdr[:4])),
                _f(0x04, 2, "version", version, enc=">H", raw=version),
                _f(0x06, 2, "flags", f"0x{flags:04x}",
                   "no flags are defined for version 1"),
            ],
            "warnings": [],
        })

        pos = _HEADER
        seen = []
        while pos + _CHUNK_HEADER <= file_size:
            if len(seen) >= _MAX_CHUNKS:
                file_warns.append(coverage(
                    f"stopped after {_MAX_CHUNKS} chunks; the file may continue"))
                break
            f.seek(pos)
            head = f.read(_CHUNK_HEADER)
            if len(head) < _CHUNK_HEADER:
                break
            cid = _fourcc(head[:4])
            size = struct.unpack_from(">q", head, 4)[0]
            body = pos + _CHUNK_HEADER
            avail = max(0, file_size - body)

            if size == _TO_EOF:
                # Legal, and the reason the size is signed: a writer that did
                # not know the length says so rather than lying about it.
                real = avail
                file_warns.append(
                    f"chunk {cid!r} declares the -1 'to end of file' size; "
                    f"reading {real:,} bytes")
            elif size < 0:
                file_warns.append(
                    f"chunk {cid!r} at {pos} declares a negative size "
                    f"({size:,}) that is not the -1 sentinel; stopping")
                break
            else:
                real = min(size, avail)
                if size > avail:
                    file_warns.append(
                        f"chunk {cid!r} claims {size:,} bytes but only "
                        f"{avail:,} remain")

            payload = f.read(min(real, _PAYLOAD_CAP))
            entry = {
                "id": cid, "offset": pos, "size": real,
                "payload_base": body, "payload_len": real,
                "extent_len": _CHUNK_HEADER + real,
                "summary": "", "fields": [], "warnings": [],
            }
            try:
                if cid == "data":
                    entry["summary"], pf, pw = _parse_data(payload, ctx, real)
                    entry["fields"].extend(pf)
                    entry["warnings"].extend(pw)
                elif cid in _PARSERS:
                    entry["summary"], pf, pw = _PARSERS[cid](payload, ctx)
                    entry["fields"].extend(pf)
                    entry["warnings"].extend(pw)
                elif cid in _KNOWN_OPAQUE:
                    entry["summary"] = f"{_KNOWN_OPAQUE[cid]}, {real:,} bytes"
                else:
                    entry["summary"] = (f"unparsed, first bytes: "
                                        f"{payload[:16].hex(' ')}")
            except Exception as e:                      # noqa: BLE001
                entry["warnings"].append(
                    f"parse error: {e.__class__.__name__}: {e}")
            # "we stopped looking" is a fact about the WALK, not about this
            # chunk, so it belongs where a caller reads walk-level caveats too.
            # Promoted by the note's KIND, never by matching its text -- the
            # defect primitives.notes exists to prevent.
            file_warns.extend(w for w in entry["warnings"] if is_coverage(w))
            if size != _TO_EOF and size > avail:
                entry["warnings"].append("payload runs past the end of the file")
            chunks.append(entry)
            seen.append(cid)

            # No alignment rule: the next chunk begins where this one ends.
            nxt = body + real
            if nxt <= pos:
                file_warns.append(
                    f"chunk {cid!r} at {pos} does not advance the cursor; stopping")
                break
            pos = nxt

    if seen and seen[0] != "desc":
        file_warns.append(
            f"first chunk is {seen[0]!r}; the spec requires desc to come first")
    elif not seen:
        file_warns.append("no chunks after the header; desc is mandatory")
    if seen and "data" not in seen:
        file_warns.append("no data chunk: the file describes audio it does not carry")
    return chunks, file_warns
