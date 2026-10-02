"""Sampler programs stored as XML: TAL-Sampler (.talsmpl) and UVI (.uvip).

Both are read with regular expressions over the bytes, never an XML parser,
so no entity expansion runs on a file from elsewhere, and every value keeps
the byte position it came from.

    TAL-Sampler  <tal version=...><programs><program programname=...>, then
                 per sample layer <multisample url=... urlRelativeToPresetDirectory=...
                 rootkey= lowkey= highkey= velocitystart= velocityend= ...>
    UVI          <UVI4><Program DisplayName=... ProgramPath=...>, then per
                 <Keygroup LowKey= HighKey= LowVelocity= HighVelocity=> a
                 <SamplePlayer SamplePath=... > whose path is relative to the
                 program file
"""

import re

_ATTR = re.compile(rb'([A-Za-z_][\w.:-]*)="([^"]*)"')


def is_tal(head):
    return b"<tal " in head[:2048] and head.lstrip(b"\xef\xbb\xbf \t\r\n")[:5] in (b"<?xml", b"<tal ")


def is_uvi(head):
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<UVI4>")


def unescape(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


def elements(data, tag, max_count):
    """[(start, {name: (value, value_at, value_len)})] for each <tag ...>,
    at most max_count, and how many there were."""
    out = []
    total = 0
    # [^<>], not [^>]: a tag never holds '<', and with [^>] every unclosed
    # '<tag' scanned to the end of the file, O(n^2) over many of them
    for m in re.finditer(rb"<" + re.escape(tag) + rb"\b([^<>]*)>", data):
        total += 1
        if len(out) >= max_count:
            continue
        attrs = {}
        base = m.start(1)
        for a in _ATTR.finditer(m.group(1)):
            attrs[a.group(1).decode("latin-1")] = (
                unescape(a.group(2).decode("utf-8", "replace")),
                base + a.start(2), len(a.group(2)))
        out.append((m.start(), attrs))
    return out, total
