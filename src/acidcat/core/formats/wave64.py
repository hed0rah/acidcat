"""Sony/Sonic Foundry Wave64 identification, and the GUID arithmetic behind it.

Wave64 replaces RIFF's 4-character chunk ids with 128-bit GUIDs, but not
arbitrarily: for every id it inherits from RIFF the first four bytes on disk
are the FOURCC in LOWERCASE ASCII, followed by one of two constant 12-byte
suffixes. So an id is read by SPLITTING the GUID, and a chunk this codebase
has never seen still reports a readable name.

The two suffixes are genuine version-1 UUIDs with only the 32-bit time_low
overwritten to carry the FOURCC. The surviving timestamp bits date the
container pair to 8 April 1996 and the audio batch to 7 December 1999.

Here rather than in the walker because `sniff` needs the container GUID and is
below the walk layer, and because `census` already carried a second copy of
the same constant -- which is the drift one definition per format exists to
end. Verified against libsndfile 1.2.2 output.
"""

# The 12-byte suffixes. Container ids are riff and list; every audio-level id
# (wave, fmt, data, fact, ...) uses the other.
CONTAINER_SUFFIX = bytes.fromhex("2e91cf11a5d628db04c10000")
AUDIO_SUFFIX = bytes.fromhex("f3acd3118cd100c04f8edb8a")

RIFF_GUID = b"riff" + CONTAINER_SUFFIX
WAVE_GUID = b"wave" + AUDIO_SUFFIX

HEADER = 40                    # riff GUID + u64 size + wave GUID
CHUNK_HEADER = 24              # GUID + u64 size


def is_wave64(head):
    """True for bytes opening with the Wave64 RIFF GUID.

    Safe next to the RIFF test: this GUID spells 'riff' in lowercase, and the
    twelve bytes after it are fixed, so the two cannot be confused.
    """
    return len(head) >= 16 and head[:16] == RIFF_GUID


def chunk_id(guid):
    """(printable id, note) for a 16-byte chunk GUID.

    Wave64's two native chunks, MARKER and SUMMARYLIST, carry no FOURCC and so
    cannot be split. Their GUIDs are deliberately NOT tabulated: no source
    available to this repo publishes them, and a constant written from memory
    that mislabels a chunk is worse than no constant at all. They fall through
    to `guid:<hex>`, which is a fact about the file rather than a guess about
    the format.
    """
    if len(guid) < 16:
        return "guid:" + guid.hex(), "truncated GUID"
    suffix = guid[4:]
    if suffix == AUDIO_SUFFIX:
        return guid[:4].decode("ascii", "replace"), ""
    if suffix == CONTAINER_SUFFIX:
        return guid[:4].decode("ascii", "replace"), "container-class GUID suffix"
    return "guid:" + guid.hex(), "unrecognized GUID suffix"
