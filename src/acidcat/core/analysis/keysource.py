"""One musical key from the several places a file can state one.

A sample can carry its key in a tag, an ACID chunk, a smpl chunk, an Apple
Loops basc chunk, a MIDI key signature, its file name, its folder names, or
only in its audio. Those places are not equally honest, and two of them hold
writer defaults far more often than real values:

  smpl unity note 60   the sampler playback root. Writers fill in middle C
                       when nobody set one, and the chunk has no "set" flag.
                       Over 2,736 files whose only key source was a smpl note
                       of 60, the name agreed on 172 of 1,259 (14%, near
                       chance); over 59 with any other note, 11 of 11.
  MIDI 0 sharps major  the key signature a sequencer writes by default. Of 29
                       such files with a key in the name, none matched.

Those are dropped as no evidence. The ACID root without its root-set flag is
already zeroed by the WAV walker. With the flag set it is still a weak
claim: over 456 files with a key in the name, it matched 186 (41%), and 110
of 358 (31%) where the name states a mode or an accidental. Packs stamp one
root on every file. So a name outranks the chunk roots, and an ID3 TKEY,
right on 62 of 65 and on the mode of 58 of 58, outranks the name. Even the
audio estimate beats the chunk roots: past its confidence gate it found the
name's root on 65% of 6,145 files, and on 130 files where it and a set ACID
root disagreed it was right 54 times to ACID's 24.

The other problem is the mode. ACID, smpl and basc store a root note only,
so a loop named "Fm" read as "F", which every consumer took to mean F major.
A root-only claim now borrows its mode from another claim on the same root,
and when nothing supplies one the mode is unknown rather than major.

A claim is (source, root pitch class 0-11, mode) with mode "major", "minor"
or None. `resolve` returns the stored key fields:

  key        "Am" for minor, "A" for major OR unknown (the historical form,
             so existing queries keep working); read key_mode to tell which
  key_mode   "major", "minor" or None
  key_source the source of the root, "+source" when the mode came from
             elsewhere: "name+folder"
  key_claims every claim, "name=Am acid=B?", so disagreement stays visible
"""

import re

from acidcat.core.analysis.camelot import parse_key, pitch_class_to_name

# root precedence, strongest first. a tag or a MIDI signature is somebody
# stating the key; a name is a label somebody typed per file; basc matched the
# name on 75 of 76; audio is an estimate, and a better one than the acid and
# smpl roots, which a writer or a pack stamped, usually without looking.
PRECEDENCE = ("tag", "comment", "midi", "name", "folder", "basc", "audio",
              "acid", "smpl")

# sources that hold a root and never a mode
ROOT_ONLY = frozenset({"acid", "smpl", "basc"})

SMPL_DEFAULT_NOTE = 60


def claim(source, key=None, *, note=None, mode_known=True):
    """A claim from `source`, or None when it says nothing.

    Give either a key string ("Am", "F# minor", "8A") or a MIDI `note`. A
    root-only source gets mode None whatever the string says; `mode_known`
    False marks a name token that was a bare letter.
    """
    if note is not None:
        if not 0 < note < 128:
            return None
        pc, mode = note % 12, None
    else:
        parsed = parse_key(key)
        if parsed is None:
            return None
        pc, m = parsed
        mode = "minor" if m == 1 else "major"
    if source in ROOT_ONLY or not mode_known:
        mode = None
    return (source, pc, mode)


def smpl_claim(unity_note):
    """The smpl unity note as a claim; 60 is the writer default, not a key."""
    if not unity_note or unity_note == SMPL_DEFAULT_NOTE:
        return None
    return claim("smpl", note=unity_note)


def midi_claim(key_sig):
    """A MIDI key signature as a claim; C major (0 sharps) is the default."""
    if not key_sig or key_sig == "C":
        return None
    return claim("midi", key_sig)


def _name(pc, mode):
    return pitch_class_to_name(pc, 1 if mode == "minor" else 0)


def _label(c):
    _, pc, mode = c
    return _name(pc, mode) + ("?" if mode is None else "")


def resolve(claims):
    """Combine claims into the stored key fields (see the module docstring)."""
    claims = [c for c in claims if c]
    out = {"key": None, "key_mode": None, "key_source": None, "key_claims": None}
    if not claims:
        return out
    order = {s: i for i, s in enumerate(PRECEDENCE)}
    claims.sort(key=lambda c: order.get(c[0], len(order)))
    source, pc, mode = claims[0]
    mode_from = source
    if mode is None:
        for other in claims[1:]:
            if other[1] == pc and other[2] is not None:
                mode, mode_from = other[2], other[0]
                break
    out["key"] = _name(pc, mode)
    out["key_mode"] = mode
    out["key_source"] = source if mode_from == source else f"{source}+{mode_from}"
    out["key_claims"] = " ".join(f"{c[0]}={_label(c)}" for c in claims)
    return out


def display(key, mode):
    """A stored key for people: "Am", "A", or "A?" when the mode is unknown.

    Rows written before key_mode existed have mode None and print with "?",
    which is the truth about them: their mode was never recorded.
    """
    if not key:
        return None
    if key.endswith("m") or mode is not None:
        return key
    return key + "?"


def path_claim(filepath):
    """The file's name, or failing that its folders, as a claim."""
    from acidcat.core.analysis.detect import key_from_path_detail
    hit = key_from_path_detail(filepath)
    if hit is None:
        return None
    key, where, mode_known = hit
    return claim(where, key, mode_known=mode_known)


# Mixed In Key writes the key it hears into the comment: "Abm", "Cm - Energy
# 4" or the Camelot "8A - Energy 4". The root matched the name on 277 of 280
# WAVs that carry one and the mode on 203 of 203, so a comment that is only
# a key is a claim; a comment with anything else in it is not read for one.
_COMMENT_KEY = re.compile(
    r"^\s*((?:[A-G](?:#|b)?\s*(?:m|min|minor|maj|major)?)|(?:1[0-2]|[1-9])[AB])"
    r"(?:\s*-\s*Energy\s*\d+)?\s*$", re.I)


def comment_claim(text):
    """A key claim from a comment that holds only a key, or None."""
    m = _COMMENT_KEY.match(text or "")
    return claim("comment", m.group(1).replace(" ", "")) if m else None


def wav_claims(ctx, filepath):
    """Every key claim a WAV makes, from an inspect_wav ctx and its path.

    The walker already zeroes an ACID root whose root-set flag is clear.
    """
    acid = ctx.get("acid_root")
    return [
        claim("tag", ctx["id3_key"]) if ctx.get("id3_key") else None,
        comment_claim(ctx.get("id3_comment")),
        claim("acid", note=acid) if acid else None,
        smpl_claim(ctx.get("smpl_root")),
        path_claim(filepath),
    ]


_SOURCE_WORDS = {"tag": "tag", "comment": "comment", "midi": "MIDI key signature",
                 "acid": "ACID",
                 "smpl": "SMPL", "basc": "basc", "name": "file name",
                 "folder": "folder name", "audio": "audio"}


def describe(resolved):
    """A resolved key for a card: "F (from file name, mode from folder name)"."""
    key, mode, source = (resolved.get("key"), resolved.get("key_mode"),
                         resolved.get("key_source"))
    if not key:
        return None
    root_from, _, mode_from = (source or "").partition("+")
    words = _SOURCE_WORDS.get(root_from, root_from)
    if mode_from:
        words += f", mode from {_SOURCE_WORDS.get(mode_from, mode_from)}"
    elif mode is None and not key.endswith("m"):
        words += ", mode unknown"
    return f"{display(key, mode)} (from {words})"
