"""
AIFF/IFF parser.

Big-endian chunk-based format, Apple's counterpart to RIFF/WAV.
Also handles REX files (which are AIFF internally).
"""

import os
import struct
import binascii


def _parse_ieee_extended(data):
    """
    Parse 80-bit IEEE 754 extended precision float (big-endian).
    Used for AIFF sample rate in COMM chunk.
    """
    if len(data) < 10:
        return 0.0
    exponent = ((data[0] & 0x7F) << 8) | data[1]
    mantissa = 0
    for i in range(2, 10):
        mantissa = (mantissa << 8) | data[i]
    sign = -1 if data[0] & 0x80 else 1
    if exponent == 0 and mantissa == 0:
        return 0.0
    elif exponent == 0x7FFF:
        return float('inf') * sign
    else:
        f = mantissa / (1 << 63)
        f = f * (2 ** (exponent - 16383))
        return f * sign


def is_aiff(filepath):
    """Check if file is AIFF/AIFC format."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return False
            return (header[0:4] == b"FORM" and
                    header[8:12] in (b"AIFF", b"AIFC"))
    except Exception:
        return False


def iter_chunks(filepath):
    """Yield (chunk_id_str, offset, size) for each chunk in an AIFF file."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[0:4] != b"FORM":
            return
        form_type = header[8:12].decode("ascii", errors="ignore")
        if form_type not in ("AIFF", "AIFC"):
            return
        pos = 12
        while pos + 8 <= file_size:
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid = ch[0:4].decode("ascii", errors="ignore")
            try:
                csz = struct.unpack(">I", ch[4:8])[0]
            except struct.error:
                break
            yield (cid, pos, csz)
            pos += 8 + csz
            if csz % 2 == 1:
                pos += 1  # word alignment


def parse_aiff(filepath, enumerate_all=False):
    """
    Parse an AIFF/AIFC file.

    Returns:
        (results, meta, seen_order) matching the RIFF parser interface.
    """
    results = []
    meta = {
        "channels": None,
        "sample_rate": None,
        "bits_per_sample": None,
        "num_frames": None,
        "duration_sec": None,
        "compression": None,
    }
    seen_order = []
    seen_set = set()

    file_size = os.path.getsize(filepath)

    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[0:4] != b"FORM":
            return results, meta, seen_order

        form_type = header[8:12].decode("ascii", errors="ignore")
        if form_type not in ("AIFF", "AIFC"):
            return results, meta, seen_order

        meta["compression"] = "none" if form_type == "AIFF" else "aifc"

        pos = 12
        while pos + 8 <= file_size:
            f.seek(pos)
            ch_header = f.read(8)
            if len(ch_header) < 8:
                break
            chunk_id = ch_header[0:4]
            try:
                chunk_size = struct.unpack(">I", ch_header[4:8])[0]
            except struct.error:
                break

            cid_str = chunk_id.decode("ascii", errors="ignore")
            if cid_str and cid_str not in seen_set:
                seen_set.add(cid_str)
                seen_order.append(cid_str)

            chunk_data = f.read(min(chunk_size, 4096))  # cap read for large chunks

            if chunk_id == b"COMM":
                try:
                    num_channels = struct.unpack(">h", chunk_data[0:2])[0]
                    num_frames = struct.unpack(">I", chunk_data[2:6])[0]
                    bits_per_sample = struct.unpack(">h", chunk_data[6:8])[0]
                    sample_rate = _parse_ieee_extended(chunk_data[8:18])

                    meta["channels"] = num_channels
                    meta["num_frames"] = num_frames
                    meta["bits_per_sample"] = bits_per_sample
                    meta["sample_rate"] = int(sample_rate)
                    if sample_rate > 0:
                        meta["duration_sec"] = round(num_frames / sample_rate, 4)

                    # AIFC has compression type after sample rate
                    if form_type == "AIFC" and len(chunk_data) >= 22:
                        comp_type = chunk_data[18:22].decode("ascii", errors="ignore")
                        meta["compression"] = comp_type.strip()

                    if enumerate_all:
                        results.append(("COMM", "channels", num_channels))
                        results.append(("COMM", "num_frames", num_frames))
                        results.append(("COMM", "bits_per_sample", bits_per_sample))
                        results.append(("COMM", "sample_rate", int(sample_rate)))
                        if meta["compression"] != "none":
                            results.append(("COMM", "compression", meta["compression"]))
                except Exception as e:
                    if enumerate_all:
                        results.append(("COMM", "error", str(e)))

            elif chunk_id == b"MARK" and enumerate_all:
                try:
                    num_markers = struct.unpack(">H", chunk_data[0:2])[0]
                    results.append(("MARK", "num_markers", num_markers))
                except Exception as e:
                    results.append(("MARK", "error", str(e)))

            elif chunk_id == b"INST":
                try:
                    if len(chunk_data) >= 7:
                        base_note = chunk_data[0]
                        detune = struct.unpack(">b", chunk_data[1:2])[0]
                        low_note = chunk_data[2]
                        high_note = chunk_data[3]
                        low_vel = chunk_data[4]
                        high_vel = chunk_data[5]
                        gain = struct.unpack(">h", chunk_data[6:8])[0] if len(chunk_data) >= 8 else 0
                        if enumerate_all:
                            from acidcat.util.midi import midi_note_to_name
                            results.append(("INST", "base_note", midi_note_to_name(base_note)))
                            results.append(("INST", "detune", detune))
                            results.append(("INST", "key_range",
                                            f"{midi_note_to_name(low_note)}-{midi_note_to_name(high_note)}"))
                            results.append(("INST", "vel_range", f"{low_vel}-{high_vel}"))
                            results.append(("INST", "gain", gain))
                except Exception as e:
                    if enumerate_all:
                        results.append(("INST", "error", str(e)))

            elif chunk_id == b"NAME":
                try:
                    name = chunk_data[:chunk_size].decode("ascii", errors="ignore").strip("\x00").strip()
                    meta["name"] = name
                    if enumerate_all:
                        results.append(("NAME", "name", name))
                except Exception:
                    pass

            elif chunk_id == b"AUTH":
                try:
                    author = chunk_data[:chunk_size].decode("ascii", errors="ignore").strip("\x00").strip()
                    meta["author"] = author
                    if enumerate_all:
                        results.append(("AUTH", "author", author))
                except Exception:
                    pass

            elif chunk_id == b"(c) ":
                try:
                    copyright_text = chunk_data[:chunk_size].decode("ascii", errors="ignore").strip("\x00").strip()
                    meta["copyright"] = copyright_text
                    if enumerate_all:
                        results.append(("(c) ", "copyright", copyright_text))
                except Exception:
                    pass

            elif chunk_id == b"ANNO":
                try:
                    annotation = chunk_data[:chunk_size].decode("ascii", errors="ignore").strip("\x00").strip()
                    if enumerate_all:
                        results.append(("ANNO", "annotation", annotation))
                except Exception:
                    pass

            elif chunk_id == b"ID3 " and enumerate_all:
                # ID3v2 tag -- just note its presence and size
                results.append(("ID3 ", "size", chunk_size))

            elif enumerate_all and chunk_id != b"SSND":
                # unknown chunk, hex preview
                preview = binascii.hexlify(chunk_data[:16]).decode()
                results.append((cid_str, "raw", preview))

            pos += 8 + chunk_size
            if chunk_size % 2 == 1:
                pos += 1

    return results, meta, seen_order


def get_aiff_info(filepath):
    """Quick info extraction for the info command."""
    _, meta, seen = parse_aiff(filepath, enumerate_all=False)
    return meta, seen
