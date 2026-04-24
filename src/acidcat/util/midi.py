"""MIDI note utilities."""

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_to_name(note_number):
    """Convert MIDI note number (0-127) to name like 'C4'."""
    if note_number is None:
        return None
    octave = (note_number // 12) - 1
    note = NOTES[note_number % 12]
    return f"{note}{octave}"


def midi_note_to_pitch_class(note_number):
    """Convert MIDI note number to pitch class only (no octave), e.g. 'C'.

    Use this when storing a musical key where the octave is noise
    (harmonic matching cares about pitch class). The octave info is
    preserved elsewhere (e.g. a `root_note` int column).
    """
    if note_number is None:
        return None
    return NOTES[note_number % 12]
