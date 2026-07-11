"""MIDI note utilities."""

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_to_name(note_number):
    """Convert MIDI note number (0-127) to name like 'C3'.

    Uses the DAW octave convention where middle C (MIDI 60) is C3, matching
    Bitwig, Ableton, FL, Cubase, and Logic, so acidcat's note names line up
    with what a producer sees in the piano roll (not scientific pitch notation,
    which would call MIDI 60 C4)."""
    if note_number is None:
        return None
    octave = (note_number // 12) - 2
    note = NOTES[note_number % 12]
    return f"{note}{octave}"


# major/minor key names by signature, indexed by |sf| (0-7). mi=1 names the
# relative minor (major root + 9 semitones), not the major root with an 'm'.
_KEY_MAJOR_SHARP = ("C", "G", "D", "A", "E", "B", "F#", "C#")
_KEY_MAJOR_FLAT = ("C", "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb")
_KEY_MINOR_SHARP = ("A", "E", "B", "F#", "C#", "G#", "D#", "A#")
_KEY_MINOR_FLAT = ("A", "D", "G", "C", "F", "Bb", "Eb", "Ab")


def key_signature_name(sf, mi):
    """MIDI key-signature meta (sf sharps if >=0 else flats; mi=1 minor) to a
    key name like 'D' or 'Bm'. Shared by the walker and the legacy parser so
    the two never disagree on the name (they used to: one showed '+2 sharps')."""
    if mi == 1:
        table = _KEY_MINOR_SHARP if sf >= 0 else _KEY_MINOR_FLAT
    else:
        table = _KEY_MAJOR_SHARP if sf >= 0 else _KEY_MAJOR_FLAT
    return table[min(abs(sf), 7)] + ("m" if mi == 1 else "")


def midi_note_to_pitch_class(note_number):
    """Convert MIDI note number to pitch class only (no octave), e.g. 'C'.

    Use this when storing a musical key where the octave is noise
    (harmonic matching cares about pitch class). The octave info is
    preserved elsewhere (e.g. a `root_note` int column).
    """
    if note_number is None:
        return None
    return NOTES[note_number % 12]
