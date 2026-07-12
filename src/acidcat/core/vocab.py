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

WAVE_FORMAT_TAGS = {
    0x0001: "PCM",
    0x0002: "MS ADPCM",
    0x0003: "IEEE float",
    0x0006: "A-law",
    0x0007: "mu-law",
    0x0011: "IMA ADPCM",
    0x0055: "MPEG Layer III",
    0xFFFE: "extensible",
}

# a grammar Enum names a table by its id; the walker and the descriptor share
# this one canonical dict, so there is a single source of truth (no divergence
# for a parity test to miss).
TABLES = {
    "wave_format_tags": WAVE_FORMAT_TAGS,
}


# ── the semantic ctx-key contract ──
# the file-global ctx dict is the decode-once handoff the scan/index path reads
# (core/indexing.py) and the walkers use for cross-chunk facts. A grammar Field
# may publish its raw value under one of these keys via Field.ctx; the name is
# validated at descriptor construction, so a typo fails loudly in trusted code
# instead of silently missing an index column. Extend this set (citing the
# source) when a descriptor publishes a genuinely new semantic key.
CTX_KEYS = frozenset({
    # WAV fmt fields the descriptor publishes today (walk/wav.py:73)
    "format_tag", "channels", "sample_rate", "block_align", "bits",
    # WAV cross-chunk + scan/index-read keys (walk/wav.py, core/indexing.py)
    "data_off", "data_bytes", "fact_samples", "frames", "duration",
    "acid_bpm", "acid_beats", "acid_one_shot", "acid_root", "smpl_root",
})
