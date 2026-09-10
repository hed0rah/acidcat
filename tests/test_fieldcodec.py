

def test_synchsafe_decode_and_encode_are_a_round_trip():
    """`enc`/`raw` is the edit contract: a field is re-serialised to its exact
    on-disk bytes by encoding what was decoded. A decoder that returns a value
    its own encoder refuses breaks that, and the failure lands in the TUI's
    hex-to-value toggle where a user is editing a real file.

    `_synchsafe_encode` has always refused values past 28 bits. `_synchsafe_
    decode` did not mask, so a malformed `80 00 00 00` decoded to 268,435,456
    and could not be written back.

    This was the THIRD decoder of this field in the tree. The other two
    disagreed with each other until one was fixed; this one was still
    disagreeing after.
    """
    from acidcat.core.infra.fieldcodec import decode_value, encode_value

    for raw in (bytes([0x80, 0x00, 0x00, 0x00]),      # malformed: high bit set
                bytes([0xFF, 0xFF, 0xFF, 0xFF]),      # malformed: all of them
                bytes([0x7F, 0x7F, 0x7F, 0x7F]),      # the largest legal value
                bytes([0x00, 0x00, 0x02, 0x01])):     # 257, the textbook case
        value = decode_value("synchsafe", raw)
        assert 0 <= value < (1 << 28), (
            "synchsafe decoded %r to %d, which is outside the 28 bits its own "
            "encoder accepts" % (raw, value))
        # and the encoder takes it back without raising
        encode_value("synchsafe", str(value))


def test_every_synchsafe_decoder_in_the_tree_agrees():
    """Three modules decode this field. They are supposed to be the same
    reading, and for a while they were not: core/formats/mp3 and
    core/infra/fieldcodec both omitted the mask that forensics/anomalies
    applied, so the same four bytes had two answers 268 MB apart.
    """
    from acidcat.core.formats.mp3 import synchsafe
    from acidcat.core.infra.fieldcodec import decode_value

    for raw in (bytes([0x80, 0x00, 0x00, 0x00]),
                bytes([0x00, 0x7F, 0x80, 0x01]),
                bytes([0x7F, 0x7F, 0x7F, 0x7F])):
        # the reading forensics/anomalies.py has always used
        expected = (((raw[0] & 0x7F) << 21) | ((raw[1] & 0x7F) << 14)
                    | ((raw[2] & 0x7F) << 7) | (raw[3] & 0x7F))
        assert synchsafe(raw) == expected, "core/formats/mp3 disagrees on %r" % raw
        assert decode_value("synchsafe", raw) == expected, (
            "core/infra/fieldcodec disagrees on %r" % raw)
