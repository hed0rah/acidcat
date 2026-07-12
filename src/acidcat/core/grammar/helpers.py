"""Named decode helpers: the budgeted escape hatch for genuinely irregular
decode that neither the Type layer nor the guard/switch vocabulary can express.

Signature ``(payload, pos, local, ctx) -> (fields, warns)``. Decode helpers get
the region-local dict AND a warning channel; relation/summary helpers (Phase 1
PR-C) never receive payload bytes -- the presence of ``payload`` in the signature
IS the category boundary, so a relation/summary helper cannot smuggle byte
decoding. Two decode helpers in WAV fmt (the measurable budget): the MS-ADPCM
coefficient block and the WAVEFORMATEXTENSIBLE GUID sub-format.
"""

import struct

from acidcat.core.vocab import KSDATAFORMAT_TAIL, WAVE_FORMAT_TAGS
from acidcat.core.walk.base import _f

_ADPCM_STD = ["(256,0)", "(512,-256)", "(0,0)", "(192,64)", "(240,0)",
              "(460,-208)", "(392,-232)"]


def _wav_adpcm_coefs(payload, pos, local, ctx):
    """MS-ADPCM predictor pairs: ONE composite field of space-joined (c1,c2),
    the pair count clamped to the cb window, standard-predictor-set detection,
    and the declares-N-holds-M warning. ``payload`` is the windowed sub-payload,
    so ``(len(payload) - pos) // 4`` is the walker's ``(len(ext) - 4) // 4``
    capacity. Preserves the walker's ``if pairs:`` quirk: an empty window emits
    neither the field nor the warning."""
    ncoef = local.get("num_coef_pairs", 0)
    count = min(ncoef, (len(payload) - pos) // 4)
    pairs = []
    for i in range(count):
        c1, c2 = struct.unpack_from("<hh", payload, pos + i * 4)
        pairs.append(f"({c1},{c2})")
    fields, warns = [], []
    if pairs:
        std = pairs[:7] == _ADPCM_STD and ncoef == 7
        fields.append(_f(pos, len(pairs) * 4, "adpcm_coefficients", " ".join(pairs),
                         "the standard predictor set" if std else "custom predictors"))
        if ncoef > len(pairs):
            warns.append(f"declares {ncoef} coefficient pairs but the "
                         f"extension holds {len(pairs)}")
    return fields, warns


def _wav_ext_subformat(payload, pos, local, ctx):
    """WAVEFORMATEXTENSIBLE 16-byte sub-format GUID: display = the format-tag
    table lookup of the leading u16 ("guid 0x.." fallback, distinct from Enum's
    "unknown 0x.."), note = KSDATAFORMAT-tail check, a warning on a non-standard
    tail, and the LATER-FIELD-WINS ctx override -- ctx["format_tag"] takes the
    GUID's tag while ``local`` keeps the original 0xFFFE (so PR-C's summary/lint
    helpers, which read local, stay correct)."""
    sub = payload[pos:pos + 16]
    sub_tag = int.from_bytes(sub[:2], "little")
    name = WAVE_FORMAT_TAGS.get(sub_tag, f"guid 0x{sub_tag:04x}")
    tail_ok = sub[2:] == KSDATAFORMAT_TAIL
    fields = [_f(pos, 16, "sub_format", name,
                 "KSDATAFORMAT_SUBTYPE" if tail_ok else "non-standard GUID")]
    warns = ([] if tail_ok else
             ["sub_format GUID tail is not the standard KSDATAFORMAT_SUBTYPE suffix"])
    ctx["format_tag"] = sub_tag
    return fields, warns


_HELPERS = {
    "wav_adpcm_coefs": _wav_adpcm_coefs,
    "wav_ext_subformat": _wav_ext_subformat,
}
