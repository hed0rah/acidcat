"""
Xfer Serum preset primitives: the Serum 2 magic check, and the layout of the
Serum 1 preset inside a VST2 .fxp.

SERUM 2 writes its own file: an 'XferJson' header, a JSON metadata block, then
binary data. Decoding lives in the walker (core/walk/serum.py); this module
keeps the magic check.

SERUM 1 saves a VST2 opaque-chunk preset (CcnK, FPCh, plugin id XfsX; the
container is core/walk/fxp.py). Its chunk is three pieces back to back:

    zlib stream   the patch: one fixed-layout record (sizes below)
    zlib stream   the wavetable audio the patch embeds (often empty)
    u32 LE        the compressed length of the first stream, which is
                  where the second one starts

The patch record has grown with Serum's versions and is 20,704, 21,524,
21,528, 21,808, 28,232, 33,872, 34,016 or 172,736 bytes inflated. Every
version keeps the offsets below, so one layout reads them all. It is a
memory image: bytes no field claims can hold leftover data from the host
(fragments of text, other programs' strings), and they mean nothing.

    0x3460  248 float32 LE  the parameters, each normalized to 0-1
    0x3BE0  10 u32 LE       the effects rack order: a permutation of 0-9
    0x3C08  512 bytes       osc A wavetable file, relative to Serum's Tables
    0x3E08  512 bytes       osc B wavetable file
    0x4008  512 bytes       noise oscillator sample, relative to Noises
    0x4972  32 bytes        preset name (the CcnK header keeps only 28)
    0x49A0  48 bytes        author
    0x49D0  128 bytes       description
    0x4A60  4 x 32 bytes    macro 1-4 labels

Text is NUL-terminated; what follows the NUL can be leftover bytes.

THE PARAMETERS are the plugin's automation parameters 0-227, in the order the
host sees them, then 20 slots (228-247) that are 0 in nearly every preset.
The names are Serum's own, from the parameter listing Serum 1.334 writes
(SYParameters.txt, published with a reverse-engineering write-up of the
format; the write-up also places the array at 0x3460). The listing gives
each parameter's default. On 913 Serum 1 presets from 229 packs, the most
common stored value of 193 of parameters 0-227 is that default, normalized
(MasterVol 0.7, A Vol 0.75, Env1 Atk 0.11, Fil Reso 0.1, Hyp_Unison 4/7,
Bend U 26/48), and the matches run the whole array, so no stretch of it is
shifted. The 35 that miss are ten switches presets turn on (osc B and its
filter routing, sub, filter, multiband compression, five effects), eight values presets nearly always move (wavetable positions,
B level, mod 1-4 amounts, delay bandwidth by 0.01), the nine envelope curves,
which sit at 0.4 and 0.6 against the listing's 0.5 and 0.67, six of the seven
reverb values, and the two constants below. Steps agree where a control is
stepped: the unison counts are multiples of 1/15 (1-16 voices) and the sub
oscillator's octave of 1/8 in every preset. A Octave and B Octave are not:
only 205 and 291 of 913 sit on an eighth, though they centre on 0.5 and sit
lower in bass presets, as an octave would.

Parameters 80 and 222 (the listing's Mast.Tun and Amp.) are 0.5 in every
preset and have no range, so they are not named. Parameters 228-247 are not
the listing's Mod17-Mod26 (those default to 0.5 and 1; these are 0), so they
are not named either; the listing's parameters past 227 are stored somewhere
else in the record.

What 0-1 spans comes from the listing, where it maps linearly; the stored
value is always the normalized one. The listing names parameter 193 "Mod 8
out", as it does 195; by position it is slot 7's output.

THE WAVETABLE STREAM is the embedded tables as float32 LE samples, 2,048 to
a frame, and when the patch carries a microtuning, the AnaMark .tun text
after them. Of 2,694 presets, 1,023 embed nothing, 1,395 hold whole frames,
160 a tuning after their samples, and 116 a sample count that is not whole
frames, with no tuning; how those divide into tables is not decoded.

Checked on 2,694 Serum 1 presets: every chunk splits into the two streams
and a trailer that matches, and every record holds the text block.
"""

import struct
import zlib

from acidcat.core.formats.riff import decode_text
from acidcat.core.infra.findings import defect
from acidcat.core.infra.source import open_input

FXP_PLUGIN_ID = b"XfsX"
TRAILER = 4

PARAMS_AT = 0x3460
PARAM_SLOTS = 248
FX_ORDER_AT = 0x3BE0
FX_MODULES = 10
OSC_A_TABLE_AT = 0x3C08
OSC_B_TABLE_AT = 0x3E08
NOISE_SAMPLE_AT = 0x4008
PATH_LEN = 512
INFO_AT = 0x4972
NAME_LEN = 32
AUTHOR_AT = 0x49A0
AUTHOR_LEN = 48
DESCRIPTION_AT = 0x49D0
DESCRIPTION_LEN = 128
MACRO_LABEL_AT = 0x4A60
MACRO_LABEL_LEN = 32
MACROS = 4
INFO_END = MACRO_LABEL_AT + MACROS * MACRO_LABEL_LEN
FRAME_SAMPLES = 2048
TUNING_MARK = b"; AnaMark"

# (index, field name, what 0-1 spans where the listing maps it linearly, kind)
# kind: "" a normalized value, "switch" stored 0 or 1
_P = []


def _add(first, names, note="", kind=""):
    for i, n in enumerate(names):
        _P.append((first + i, n, note, kind))


def _osc(first, o):
    _add(first, ["%s_vol" % o], "0-100 %")
    _add(first + 1, ["%s_pan" % o], "-50 to +50")
    _add(first + 2, ["%s_octave" % o], "-4 to +4 octaves")
    _add(first + 3, ["%s_semi" % o], "-12 to +12 semitones")
    _add(first + 4, ["%s_fine" % o], "-100 to +100 cents")
    _add(first + 5, ["%s_unison" % o], "1-16 voices, in steps of 1/15")
    _add(first + 6, ["%s_uni_detune" % o])
    _add(first + 7, ["%s_uni_blend" % o], "0-100")
    _add(first + 8, ["%s_warp" % o])
    _add(first + 9, ["%s_coarse_pitch" % o])
    _add(first + 10, ["%s_wt_pos" % o])
    _add(first + 11, ["%s_rand_phase" % o], "0-100 %")
    _add(first + 12, ["%s_phase" % o])


def _env(first, n, sus_note):
    _add(first, ["env%d_attack" % n, "env%d_hold" % n, "env%d_decay" % n])
    _add(first + 3, ["env%d_sustain" % n], sus_note)
    _add(first + 4, ["env%d_release" % n])


_add(0, ["master_vol"], "0-100 %")
_osc(1, "a")
_osc(14, "b")
_add(27, ["noise_level", "noise_pitch"], "0-100 %")
_add(29, ["noise_fine"])
_add(30, ["noise_pan"], "-50 to +50")
_add(31, ["noise_rand_phase", "noise_phase"], "0-100 %")
_add(33, ["sub_level"], "0-100 %")
_add(34, ["sub_pan"], "-50 to +50")
_env(35, 1, "")
_add(40, ["osc_a_to_filter", "osc_b_to_filter", "noise_to_filter", "sub_to_filter"],
     kind="switch")
_add(44, ["filter_type", "filter_cutoff"])
_add(46, ["filter_reso", "filter_drive", "filter_var", "filter_mix", "filter_stereo"],
     "0-100 %")
_env(51, 2, "0-100 %")
_env(56, 3, "0-100 %")
_add(61, ["lfo1_rate", "lfo2_rate", "lfo3_rate", "lfo4_rate"])
_add(65, ["porta_time"])
_add(66, ["porta_curve"], "-100 to +100 %")
_add(67, ["chaos1_bpm_sync", "chaos2_bpm_sync"], kind="switch")
_add(69, ["chaos1_rate", "chaos2_rate"])
for _e in (1, 2, 3):
    # Serum's names: A curveN, D curveN, R curveN
    _add(68 + 3 * _e, ["env%d_attack_curve" % _e, "env%d_decay_curve" % _e,
                       "env%d_release_curve" % _e], "0-100 %")
_add(81, ["reverb_wet", "reverb_size"], "0-100 %")
_add(83, ["reverb_predelay"])
_add(84, ["reverb_low_cut", "reverb_damp", "reverb_high_cut", "reverb_width"],
     "0-100 %")
_add(88, ["eq_freq_low", "eq_freq_high"])
_add(90, ["eq_q_low", "eq_q_high"], "0-100 %")
_add(92, ["eq_gain_low", "eq_gain_high"], "-24 to +24 dB")
_add(94, ["eq_type_low", "eq_type_high"])
_add(96, ["dist_wet", "dist_drive", "dist_lbh"], "0-100 %")
_add(99, ["dist_mode", "dist_freq", "dist_bw", "dist_pre_post"])
_add(103, ["flanger_wet"], "0-100 %")
_add(104, ["flanger_bpm_sync"], kind="switch")
_add(105, ["flanger_rate"])
_add(106, ["flanger_depth", "flanger_feedback"], "0-100 %")
_add(108, ["flanger_stereo"], "0-360 degrees")
_add(109, ["phaser_wet"], "0-100 %")
_add(110, ["phaser_bpm_sync"], kind="switch")
_add(111, ["phaser_rate"])
_add(112, ["phaser_depth"], "0-100 %")
_add(113, ["phaser_freq"])
_add(114, ["phaser_feedback"], "0-100 %")
_add(115, ["phaser_stereo"], "0-360 degrees")
_add(116, ["chorus_wet"], "0-100 %")
_add(117, ["chorus_bpm_sync"], kind="switch")
_add(118, ["chorus_rate", "chorus_delay", "chorus_delay2", "chorus_depth",
           "chorus_feedback", "chorus_filter"])
_add(124, ["delay_wet"], "0-100 %")
_add(125, ["delay_freq"])
_add(126, ["delay_bw"])
_add(127, ["delay_bpm_sync", "delay_link"], kind="switch")
_add(129, ["delay_time_l", "delay_time_r", "delay_mode"])
_add(132, ["delay_feedback"], "0-100 %")
_add(133, ["delay_offset_l", "delay_offset_r", "comp_threshold", "comp_ratio",
           "comp_attack", "comp_release", "comp_gain"])
_add(140, ["comp_multiband"], kind="switch")
_add(141, ["fx_filter_wet"], "0-100 %")
_add(142, ["fx_filter_type", "fx_filter_freq"])
_add(144, ["fx_filter_reso", "fx_filter_drive", "fx_filter_var", "hyper_wet"],
     "0-100 %")
_add(148, ["hyper_rate"])
_add(149, ["hyper_detune"], "0-100 %")
_add(150, ["hyper_unison"])
_add(151, ["hyper_retrig"], kind="switch")
_add(152, ["dimension_size", "dimension_mix"], "0-100 %")
_add(154, ["dist_on", "flanger_on", "phaser_on", "chorus_on", "delay_on", "comp_on",
           "reverb_on", "eq_on", "fx_filter_on", "hyper_on",
           "a_pitch_track", "b_pitch_track"], kind="switch")
_add(166, ["bend_up", "bend_down"], "-24 to +24 semitones")
# Serum's names: WarpOscA, WarpOscB; the warp menu, not the A Warp amount
_add(168, ["a_warp_mode", "b_warp_mode", "sub_shape"])
_add(171, ["sub_octave"], "-4 to +4 octaves, in steps of 1/8")
_add(172, ["a_uni_lr", "b_uni_lr"], "0-100")
_add(174, ["a_uni_warp", "b_uni_warp", "a_uni_wt_pos", "b_uni_wt_pos"],
     "-100 to +100")
_add(178, ["a_uni_stack", "b_uni_stack"])
for _m in range(1, 17):
    _add(178 + 2 * _m, ["mod%d_amount" % _m], "-100 to +100")
    _add(179 + 2 * _m, ["mod%d_output" % _m], "0-100")
_add(212, ["osc_a_on", "osc_b_on", "noise_on", "sub_on", "filter_on"], kind="switch")
_add(217, ["mod_wheel", "macro1", "macro2", "macro3", "macro4"], "0-100 %")
_add(223, ["lfo1_smooth", "lfo2_smooth", "lfo3_smooth", "lfo4_smooth"], "0-100")
_add(227, ["pitch_bend"])

PARAMS = {i: (name, note, kind) for i, name, note, kind in _P}
NAMED = len(PARAMS)
assert NAMED == 226 and len({p[0] for p in PARAMS.values()}) == NAMED
NOTES = {193: "Serum's own name for it is 'Mod 8 out', the same as 195's"}


class ChunkError(ValueError):
    """A chunk that is not a Serum 1 chunk, or not a whole one."""


def is_serum_preset(filepath):
    """Check if file is a Serum 2 preset."""
    try:
        with open_input(filepath) as f:
            header = f.read(8)
            return header == b"XferJson"
    except Exception:
        return False


def _inflate(src, cap):
    """(bytes, compressed length, capped) of the zlib stream that starts src.
    Raises ChunkError when it is not zlib or ends early."""
    d = zlib.decompressobj()
    try:
        out = d.decompress(src, cap + 1)
    except zlib.error as e:
        raise ChunkError(str(e)) from None
    if len(out) > cap:
        return out[:cap], None, True
    if not d.eof:
        raise ChunkError("the stream ends before its end marker")
    return out, len(src) - len(d.unused_data), False


def split_chunk(chunk, cap):
    """The three pieces of a Serum 1 chunk, each stream inflated up to `cap`.

    Keys: patch (bytes, or None when the cap stopped it), patch_len (its
    compressed length), tables (bytes, or None when the cap stopped it or it
    did not inflate), tables_len (compressed length, or None when it did not
    inflate), trailer and trailer_at (the u32 and its chunk offset, or None),
    and a finding, or None, for each piece that breaks the layout:
    tables_note, trailer_note, stray_note. Raises ChunkError when the patch
    stream does not inflate: then nothing in the chunk can be located."""
    out = {"patch": None, "patch_len": None, "tables": None, "tables_len": None,
           "trailer": None, "trailer_at": None,
           "tables_note": None, "trailer_note": None, "stray_note": None}
    patch, n1, capped = _inflate(chunk, cap)
    if capped:
        return out
    out["patch"], out["patch_len"] = patch, n1
    rest = chunk[n1:]
    try:
        tables, n2, capped = _inflate(rest, cap)
    except ChunkError as e:
        out["tables_note"] = defect(
            "parse.failed", "the wavetable stream at chunk byte %d does not "
            "inflate: %s" % (n1, e))
        return out
    if capped:
        # the stream's end is unknown, so the trailer is read where it must be
        n2, tables = max(0, len(rest) - TRAILER), None
    out["tables"], out["tables_len"] = tables, n2
    tail = rest[n2:]
    if len(tail) < TRAILER:
        out["trailer_note"] = defect(
            "chunk.short", "the chunk ends %d bytes into its 4-byte trailer" % len(tail))
        return out
    out["trailer_at"] = n1 + n2
    out["trailer"] = struct.unpack_from("<I", tail)[0]
    if out["trailer"] != n1:
        out["trailer_note"] = defect(
            "field.inconsistent", "the trailer says the patch stream is %d bytes; "
            "it is %d" % (out["trailer"], n1))
    if len(tail) > TRAILER:
        out["stray_note"] = defect(
            "bytes.stray", "%d bytes follow the Serum chunk's trailer"
            % (len(tail) - TRAILER))
    return out


def text(patch, at, length):
    raw = patch[at:at + length]
    return decode_text(raw.split(b"\x00", 1)[0]).strip()


def params(patch):
    """The stored parameter values, as many of the 248 as the record holds."""
    n = max(0, min(PARAM_SLOTS, (len(patch) - PARAMS_AT) // 4))
    return list(struct.unpack_from("<%df" % n, patch, PARAMS_AT)) if n else []


def fx_order(patch):
    if len(patch) < FX_ORDER_AT + 4 * FX_MODULES:
        return None
    return list(struct.unpack_from("<%dI" % FX_MODULES, patch, FX_ORDER_AT))


def patch_info(patch):
    """The text the patch record names itself with, as much as it holds."""
    out = {}
    for key, at, n in (("osc_a_table", OSC_A_TABLE_AT, PATH_LEN),
                       ("osc_b_table", OSC_B_TABLE_AT, PATH_LEN),
                       ("noise_sample", NOISE_SAMPLE_AT, PATH_LEN),
                       ("name", INFO_AT, NAME_LEN),
                       ("author", AUTHOR_AT, AUTHOR_LEN),
                       ("description", DESCRIPTION_AT, DESCRIPTION_LEN)):
        if len(patch) >= at + n:
            out[key] = text(patch, at, n)
    if len(patch) >= INFO_END:
        out["macro_labels"] = [text(patch, MACRO_LABEL_AT + i * MACRO_LABEL_LEN,
                                    MACRO_LABEL_LEN) for i in range(MACROS)]
    return out


def tuning_at(tables):
    """Where the AnaMark tuning text starts in the wavetable stream, or None."""
    at = tables.find(TUNING_MARK)
    return at if at >= 0 else None


# preset_meta reads the patch from a bounded prefix; the largest record seen
# is 172,736 bytes
_META_INFLATE_CAP = 1 << 20


def preset_info(data):
    """Name, author and description of a Serum 1 .fxp from its leading
    bytes, or None when they are not one."""
    if len(data) < 60 or data[:4] != b"CcnK" or data[8:12] != b"FPCh" \
            or data[16:20] != FXP_PLUGIN_ID:
        return None
    try:
        patch, _n, capped = _inflate(data[60:], _META_INFLATE_CAP)
    except ChunkError:
        return None
    if capped or len(patch) < INFO_END:
        return None
    info = patch_info(patch)
    info["header_name"] = data[28:56].split(b"\x00", 1)[0].decode("latin-1").strip()
    return info
