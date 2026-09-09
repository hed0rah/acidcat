"""Zip local-header helpers shared by the zip-backed walkers (labx/mpc/multisample).

``ZipInfo.header_offset`` points at the PK local file header, not the payload.
The header is 30 fixed bytes followed by the file name and extra field, so the
entry's real on-disk data begins at header_offset + 30 + namelen + extralen --
what a carve region must start at to be the literal entry bytes.
"""


def zip_data_offset(z, zi):
    """Absolute file offset of a zip entry's data, past its local file header.

    Reads the 30-byte local header via ``z.fp``; the fp position afterward is
    unspecified, so seek explicitly if you need to read from the data start.

    Raises ``ValueError`` if the local header cannot be read where the central
    directory says it is. A mutated or truncated archive can carry a
    ``header_offset`` that is negative (``seek`` then raises ``OSError``) or
    past the end (the read comes back short), and a bare ``OSError`` out of a
    walker breaks the degrade-never-raise contract. Fuzzing the .xpn seed found
    exactly this. Callers that walk entries should treat it as a corrupt entry
    and skip it, not abort the file.
    """
    try:
        z.fp.seek(zi.header_offset)
        hdr = z.fp.read(30)
    except OSError as e:
        raise ValueError(f"zip entry {zi.filename!r}: unreadable local header "
                         f"at offset {zi.header_offset}") from e
    if len(hdr) < 30 or hdr[:4] != b"PK\x03\x04":
        raise ValueError(f"zip entry {zi.filename!r}: no local file header at "
                         f"offset {zi.header_offset}")
    n = int.from_bytes(hdr[26:28], "little")     # file name length
    m = int.from_bytes(hdr[28:30], "little")     # extra field length
    return zi.header_offset + 30 + n + m
