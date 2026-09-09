"""Shared format vocabulary in one core-owned home: value->label tables and
the semantic ctx-key contract, so the hand-written walkers and the
declarative grammar engine read the SAME definitions instead of one
importing the other's internals.

Value->label tables live here, not in a walker, because the descriptor
engine that will eventually supersede a walker must outlive it. Bit-field
enum tables used by the enc-language keep living in fieldcodec (_BITMAPS /
_DYNMAPS, already sourced from core primitives); this module is the
byte-field value->label side. One namespace for both is the goal: a grammar
table id and a fieldcodec bitsmap MAPID cannot then drift apart.
"""

# ── value->label tables (referenced by name by grammar Enum + the walkers) ──

# The `fmt ` chunk's format tag, and the sub-format inside a
# WAVEFORMATEXTENSIBLE GUID -- one table, both readers.
#
# NOT the whole registry. Microsoft's is 265 entries deep and most of it is
# codecs for hardware nobody has owned since 1998; naming those would be bulk,
# not knowledge. What is here is what a real audio corpus actually contains,
# because for a tool whose subject is telling you what bytes ARE, answering
# "unknown 0xf1ac" about a FLAC stream is the wrong answer.
#
# Every value is cross-checked against the Kaitai Struct wav spec by
# tests/test_kaitai_cross_check.py, which asserts acidcat names no tag the
# registry does not. That check is why a tag is added by looking it up rather
# than by remembering it.
WAVE_FORMAT_TAGS = {
    0x0001: "PCM",
    0x0002: "MS ADPCM",
    0x0003: "IEEE float",
    0x0006: "A-law",
    0x0007: "mu-law",
    0x0010: "OKI ADPCM",
    0x0011: "IMA ADPCM",
    0x0020: "Yamaha ADPCM",
    0x0031: "GSM 6.10",
    0x0032: "MSN Audio",
    0x0039: "Roland RDAC (RFC 2361; mmreg.h squats Crystal IMA ADPCM here)",
    0x0040: "G.721 ADPCM",
    0x0050: "MPEG Layer I/II",
    0x0055: "MPEG Layer III",
    0x0064: "G.726 ADPCM",
    0x0065: "G.722 ADPCM",
    0x0092: "Dolby AC-3 over S/PDIF",
    0x00FF: "raw AAC",
    0x0161: "WMA v2",
    0x0162: "WMA Pro",
    0x0163: "WMA Lossless",
    0x0164: "WMA over S/PDIF",
    # mmreg.h assigns 0x2000 to Fast Multimedia AG's DVM, but AC-3 muxed into
    # RIFF is written with it in practice (ffmpeg maps AC-3 here). Both names
    # are true of the same two bytes, so both are printed -- the same call the
    # 0x0039 entry above makes.
    0x2000: "DVM, or Dolby AC-3 (mmreg.h says DVM; AC-3-in-WAV uses this tag)",
    0x674F: "Ogg Vorbis mode 1",
    0x6750: "Ogg Vorbis mode 2",
    0x6751: "Ogg Vorbis mode 3",
    0x676F: "Ogg Vorbis mode 1+",
    0x6770: "Ogg Vorbis mode 2+",
    0x6771: "Ogg Vorbis mode 3+",
    0xA106: "MPEG-4 AAC",
    0xF1AC: "FLAC",
    0xFFFE: "extensible",
}

MP3_PADDING = {0: "ISO padding", 1: "padding always", 2: "padding never"}
MPEGLAYER3_ID = {1: "MPEGLAYER3_ID_MPEG"}

# WAVEFORMATEXTENSIBLE channel-mask bit positions (bit i -> speaker), and the
# fixed 14-byte tail of every KSDATAFORMAT_SUBTYPE GUID (its first 2 bytes are
# the format tag, little-endian).
WAV_SPEAKER_POSITIONS = [
    "FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC", "BC", "SL", "SR",
    "TC", "TFL", "TFC", "TFR", "TBL", "TBC", "TBR",
]
KSDATAFORMAT_TAIL = bytes.fromhex("000000001000800000aa00389b71")

# a grammar Enum/NoteLookup names a value->label table by its id; the walker and
# the descriptor share these canonical dicts, so there is a single source of
# truth (no divergence for a parity test to miss).
TABLES = {
    "wave_format_tags": WAVE_FORMAT_TAGS,
    "mp3_padding": MP3_PADDING,
    "mpeglayer3_id": MPEGLAYER3_ID,
}

# flag tables for NoteFlags: a bit-position -> name list decomposed against the
# raw value (the walk/base._flag_names / _channel_mask_names pattern).
FLAGS = {
    "wav_speaker_positions": WAV_SPEAKER_POSITIONS,
    # ACID type_flags bits 0..3 (walk/wav.py _ACID_FLAGS: 0x1/0x2/0x4/0x8)
    "acid_flags": ["one-shot", "root set", "stretch", "disk-based"],
}


# ── the semantic ctx-key contract ──
# the file-global ctx dict is the decode-once handoff the scan/index path reads
# (core/indexing.py) and the walkers use for cross-chunk facts. A grammar Field
# may publish its raw value under one of these keys via Field.ctx; the name is
# validated at descriptor construction, so a typo fails loudly in trusted code
# instead of silently missing an index column. Extend this set (citing the
# source) when a descriptor publishes a genuinely new semantic key.
CTX_KEYS = frozenset({
    # WAV fmt fields the descriptor publishes today (walk/wav.py)
    "format_tag", "channels", "sample_rate", "block_align", "bits",
    # WAV cross-chunk + scan/index-read keys (walk/wav.py, core/indexing.py)
    "data_off", "data_bytes", "fact_samples", "frames", "duration",
    "acid_bpm", "acid_beats", "acid_one_shot", "acid_root",
    "smpl_root", "smpl_loop_start", "smpl_loop_end",
    # AIFF walker keys the scan path reads (walk/aiff.py, core/indexing.py)
    "rate", "compression", "marker_ids", "inst_loop_marker_ids",
    "basc_beats", "basc_root_key", "basc_scale",
    "name", "author", "copyright", "annotation",
    # MIDI walker scan keys (walk/midi.py, core/indexing.py)
    "tempo_bpm", "key_sig", "time_sig", "track_name", "track_names",
    "note_count", "note_min", "note_max", "duration_ticks", "channels_used",
    "division", "format", "tracks",
})
# invariant (test_ctx_keys_covers_walker in test_grammar_wav.py, plus the
# aiff/midi checks in test_walker_invariants.py): CTX_KEYS must stay a superset
# of every ctx key the fixed-key walkers (wav/aiff/midi) publish, so a real key
# can never fail Field.ctx validation and a walker rename cannot silently
# desynchronize from the scan path. The Serum walker is excluded on purpose:
# it publishes the preset's raw JSON keys, an open set.
