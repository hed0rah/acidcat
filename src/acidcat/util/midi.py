"""MIDI note utilities."""

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_to_name(note_number):
    """Convert MIDI note number (0-127) to name like 'C4'."""
    if note_number is None:
        return None
    octave = (note_number // 12) - 1
    note = NOTES[note_number % 12]
    return f"{note}{octave}"
