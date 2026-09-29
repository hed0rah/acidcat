"""SFZ sampler instruments (.sfz): plain text, headers and opcodes.

    <control> default_path=samples/
    <group> lovel=1 hivel=64
    <region> sample=kick 01.wav key=36

A header opens a section that runs to the next header. An opcode is
`name=value`; the value ends at the next opcode, header, comment or line end,
so a sample path may hold spaces. `//` and `/* */` are comments. `#define $X
value` and `#include "file"` are preprocessor directives.

Positions are byte offsets into the file, so every value can be placed on the
bytes it came from.
"""

import re

HEADERS = (b"region", b"group", b"control", b"global", b"master", b"curve",
           b"effect", b"midi", b"sample")

_TOKEN = re.compile(
    rb"(?P<lcomment>//[^\r\n]*)"
    rb"|(?P<bcomment>/\*.*?(?:\*/|\Z))"
    rb"|<(?P<header>[A-Za-z_]+)>"
    rb"|(?P<directive>\#(?:define|include)\b[^\r\n]*)"
    rb"|(?P<key>[A-Za-z0-9_$]+)=",
    re.S)
# where a value stops: another opcode, a header, a comment, or the line end
_VALUE_END = re.compile(rb"\s+[A-Za-z0-9_$]+=|<[A-Za-z_]+>|//|/\*|[\r\n]")
_DEFINE = re.compile(rb"#define\s+(\$\w+)\s+(\S+)")
_INCLUDE = re.compile(rb'#include\s+"([^"]*)"')

# sample values that name a built-in generator, not a file
GENERATORS = ("*sine", "*saw", "*square", "*triangle", "*tri", "*noise",
              "*silence")


def looks_like_sfz(data):
    """A header token outside a comment, in the first bytes of the file."""
    for m in _TOKEN.finditer(data):
        if m.group("header") is not None:
            return m.group("header").lower() in HEADERS
        if m.group("key") is not None:
            return False
    return False


def tokenize(data):
    """(sections, directives). A section is {header, at, end, opcodes}; an
    opcode is (key, key_at, value, value_at, value_len). Opcodes before the
    first header go in a section whose header is None."""
    sections = [{"header": None, "at": 0, "end": 0, "opcodes": []}]
    directives = []
    pos = 0
    n = len(data)
    while pos < n:
        m = _TOKEN.search(data, pos)
        if m is None:
            break
        if m.group("header") is not None:
            sections[-1]["end"] = m.start()
            sections.append({"header": m.group("header").decode("latin-1").lower(),
                             "at": m.start(), "end": n, "opcodes": []})
            pos = m.end()
        elif m.group("directive") is not None:
            directives.append((m.group("directive").decode("utf-8", "replace").strip(),
                               m.start(), m.end() - m.start()))
            pos = m.end()
        elif m.group("key") is not None:
            vstart = m.end()
            e = _VALUE_END.search(data, vstart)
            vend = e.start() if e else n
            raw = data[vstart:vend].rstrip()
            sections[-1]["opcodes"].append(
                (m.group("key").decode("latin-1"), m.start(),
                 raw.decode("utf-8", "replace"), vstart, len(raw)))
            pos = max(vend, vstart + 1)
        else:
            pos = m.end()
    sections[-1]["end"] = n
    if not sections[0]["opcodes"]:
        sections.pop(0)
    return sections, directives


def defines(directives):
    out = {}
    for text, _at, _n in directives:
        m = _DEFINE.match(text.encode("utf-8"))
        if m:
            out[m.group(1).decode("utf-8", "replace")] = m.group(2).decode("utf-8", "replace")
    return out


def includes(directives):
    out = []
    for text, at, n in directives:
        m = _INCLUDE.match(text.encode("utf-8"))
        if m:
            out.append((m.group(1).decode("utf-8", "replace"), at, n))
    return out


def expand(value, defs):
    """A value with its $variables replaced, longest name first."""
    for k in sorted(defs, key=len, reverse=True):
        value = value.replace(k, defs[k])
    return value


def sample_path(value, default_path=""):
    """The file a sample opcode names, with '/' separators, or None for a
    built-in generator."""
    if value.lower().startswith(GENERATORS):
        return None
    return (default_path + value).replace("\\", "/")
