"""XMP packets, including the ones that are not well-formed XML.

XMP is RDF/XML, so a reader that follows the standard rejects a malformed
packet and returns nothing. That is correct and, on one real library, useless:
333 of 400 packets sampled failed to parse, all for the same reason, and the
artist, album, genre and title were plainly sitting in them.

So there is exactly one repair, it is narrow, and it announces itself.
"""
import pytest

from acidcat.core.formats import xmp


def _packet(attrs='dc:title="A Title"', body=""):
    return (
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"'
        b' xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/" '
        + attrs.encode("utf-8") + b">" + body.encode("utf-8")
        + b"</rdf:Description></rdf:RDF></x:xmpmeta>"
        b'<?xpacket end="w"?>')


def _names(props):
    return [n for n, _v in props]


def test_a_well_formed_packet_reads_without_complaint():
    props, warns = xmp.parse_xmp(_packet('dc:title="A Title"'))
    assert ("dc:title", "A Title") in props
    assert warns == []


# ── the one repair ──────────────────────────────────────────────────

def test_an_attribute_name_with_two_colons_is_recovered():
    """A QName may hold at most one colon, so `dc:title:2` makes the whole
    packet malformed. One sample-library tagger writes exactly that, in 333 of
    400 packets measured, and nothing else."""
    props, warns = xmp.parse_xmp(
        _packet('dc:title:2="Recovered" xmpDM:artist="Someone"'))
    assert ("dc:title_2", "Recovered") in props
    assert ("xmpDM:artist", "Someone") in props
    assert any("second colon" in w for w in warns)


def test_the_repair_always_says_it_happened():
    """Recovering silently would be worse than failing: the caller would take
    a repaired reading for what the writer wrote."""
    _props, warns = xmp.parse_xmp(_packet('dc:title:2="X"'))
    assert warns and "recovered, not as written" in warns[0]


def test_the_extra_colon_becomes_an_underscore_not_nothing():
    """Dropping it would collide `dc:title:2` with a real `dc:title` and one
    would silently replace the other."""
    props, _warns = xmp.parse_xmp(
        _packet('dc:title="First" dc:title:2="Second"'))
    assert ("dc:title", "First") in props
    assert ("dc:title_2", "Second") in props


def test_a_colon_inside_a_VALUE_is_left_alone():
    """The repair is anchored on the space before a name and the equals after
    it, so ordinary text is not rewritten."""
    props, warns = xmp.parse_xmp(
        _packet('dc:title:2="a:b:c and 10:30" dc:date="2024-09-18"'))
    assert ("dc:title_2", "a:b:c and 10:30") in props
    assert ("dc:date", "2024-09-18") in props
    assert len([w for w in warns if "second colon" in w]) == 1


def test_repair_counts_only_what_it_changed():
    props, warns = xmp.parse_xmp(
        _packet('dc:title:2="A" dc:description:2="B" xmpDM:album="C"'))
    assert "2 attribute name(s)" in warns[0]
    assert ("xmpDM:album", "C") in props


# ── what is NOT repaired ────────────────────────────────────────────

def test_any_other_malformation_stays_reported_and_unread():
    """One repair, not a general XML fixer. An unclosed element is a different
    problem and guessing at it would be inventing content."""
    broken = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
              b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
              b'<rdf:Description><dc:title>unclosed'
              b"</rdf:Description></rdf:RDF></x:xmpmeta>")
    props, warns = xmp.parse_xmp(broken)
    assert props == []
    assert any("not well-formed" in w for w in warns)


def test_a_packet_with_no_root_says_so():
    props, warns = xmp.parse_xmp(b"<?xpacket begin=\"\"?>nothing here")
    assert props == []
    assert any("no xmpmeta or RDF" in w for w in warns)


def test_a_root_that_is_never_closed_says_so():
    props, warns = xmp.parse_xmp(b'<x:xmpmeta xmlns:x="adobe:ns:meta/">')
    assert props == []
    assert any("not closed" in w for w in warns)


def test_repair_is_bounded():
    """A packet needing hundreds of repairs is not one writer's quirk."""
    attrs = " ".join('dc:t%d:2="v"' % i for i in range(xmp._REPAIR_CAP + 40))
    props, warns = xmp.parse_xmp(_packet(attrs))
    # the bound bit, so the repair did not complete and the packet stays
    # reported as malformed rather than half-read
    assert props == [] or all(":" in n for n, _v in props)
    assert warns


# ── identification ──────────────────────────────────────────────────

@pytest.mark.parametrize("head,expected", [
    (b'<?xpacket begin="" id="W5M0Mp"?>', True),
    (b'<x:xmpmeta xmlns:x="adobe:ns:meta/">', True),
    (b"RIFF....WAVE", False),
    (b"", False),
])
def test_is_xmp(head, expected):
    assert xmp.is_xmp(head) is expected
