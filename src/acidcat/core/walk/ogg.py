"""Ogg structural walker: page census, codec identity, and the
Vorbis/Opus comment header. Page primitives live in core/ogg.py."""


from acidcat.core.formats import ogg as oggmod
from acidcat.core.infra.limits import hit
from acidcat.core.primitives.notes import is_coverage
from acidcat.core.walk.base import _f, _open, _size

# A comment header is a handful of tags; 200 is far above any real one
# and bounds a crafted header rather than a normal file.
_TAG_LIST_CAP = 200

def inspect_ogg(filepath):
    """Structural view of an Ogg stream: page count/codec and the Vorbis/Opus
    comment header (vendor + tags). The audio packets are opaque."""
    file_size = _size(filepath)
    with _open(filepath) as f:
        data = f.read(min(file_size, 16 * 1024 * 1024))
    pages = list(oggmod.iter_pages(data))
    ch = oggmod.comment_header(data)
    ident = oggmod.identification(data)
    codec = ch[0] if ch else (ident[0] if ident else "unknown")
    serial = pages[0]["serial"] if pages else 0
    # the first page's capture pattern is the one field at a fixed place; the
    # codec is derived from the identification/comment header, not stored at
    # the page start (positioned at 0 it claimed the bytes "OggS")
    fields = [_f(0x00, 4, "capture_pattern",
                 data[:4].decode("latin-1") if len(data) >= 4 else "",
                 "every Ogg page starts with it"),
              _f(None, 0, "codec", codec),
              _f(None, 0, "pages", len(pages)),
              _f(None, 0, "bitstream_serial", serial)]
    warns = []
    serials = {p["serial"] for p in pages}
    if len(serials) > 1:
        warns.append(f"{len(serials)} logical bitstreams (chained/muxed); "
                     "duration and pages describe the first stream only")
    rate_txt = ""
    if ident and ident[1]:
        info = ident[1]
        chn, sr = info.get("channels"), info.get("sample_rate")
        # A stream cannot have nought channels or run at nought hertz. Both are
        # reported rather than dropped or asserted: the value is what the file
        # says, and the note is what the format says about it. Dropping the
        # field hides the evidence, and printing it bare states an impossibility
        # as a fact -- which is what anything downstream will divide by.
        if chn is not None:
            fields.append(_f(None, 0, "channels", chn,
                             "" if chn > 0 else
                             "impossible: the identification header declares no "
                             "channels, so this stream describes no audio"))
            if chn <= 0:
                warns.append("the identification header declares 0 channels; "
                             "nothing downstream can use this as a divisor")
        if sr is not None and sr <= 0:
            fields.append(_f(None, 0, "sample_rate", sr,
                             "impossible: a stream cannot run at 0 Hz"))
            warns.append("the identification header declares a 0 Hz sample "
                         "rate; duration cannot be derived from it")
        if sr:
            note = "Opus always decodes at 48 kHz" if "pre_skip" in info else ""
            fields.append(_f(None, 0, "sample_rate", sr, note))
            rate_txt = f", {chn}ch {sr} Hz"
        if "pre_skip" in info:
            fields.append(_f(None, 0, "pre_skip", info["pre_skip"],
                             "priming samples dropped at decode start"))
            if info.get("input_sample_rate"):
                fields.append(_f(None, 0, "input_sample_rate",
                                 info["input_sample_rate"],
                                 "encoder input rate (informational)"))
        # duration from the last granule position (a running sample count),
        # scoped to the first stream's serial so a chained/muxed file does not
        # mix counters. opus granules run at 48 kHz and include pre_skip.
        gran_rate = 48000 if "opus" in codec.lower() else sr
        last_gran = max((p["granule"] for p in pages
                         if p["serial"] == serial and p.get("granule", -1) >= 0),
                        default=0)
        if gran_rate and last_gran > 0:
            samples = max(0, last_gran - info.get("pre_skip", 0))
            duration = samples / gran_rate
            fields.append(_f(None, 0, "duration", f"{duration:.3f} s"))
            rate_txt += f", {duration:.3f} s"
    chunks = [{"id": "OggS", "offset": 0, "size": file_size,
               "summary": f"Ogg {codec}, {len(pages)} page(s){rate_txt}",
               "fields": fields, "warnings": [], "payload_base": 0}]
    # The comment header is emitted whenever it EXISTS, not only when it holds
    # tags. Its vendor string is the encoder's own name -- "Xiph.Org libVorbis
    # I 20020717" -- and most files in the wild carry exactly that and no tags
    # at all, so gating on the tag count threw away the one provenance fact the
    # header was written to hold.
    if ch and (ch[1] or ch[2]):
        _, vendor, tags = ch
        fields = []
        if vendor:
            fields.append(_f(None, 0, "vendor", vendor[:200],
                             "the encoder's own name for itself"))
        for k, v in list(tags.items())[:_TAG_LIST_CAP]:
            fields.append(_f(None, 0, k, str(v)[:200]))
        cwarns = []
        if len(tags) > _TAG_LIST_CAP:
            cwarns.append(hit("list_rows", _TAG_LIST_CAP, len(tags),
                              f"listing the first {_TAG_LIST_CAP} of "
                              f"{len(tags)} comments"))
        summary = f"{len(tags)} Vorbis comment(s)" if tags else "no comments"
        if vendor:
            summary += f" -- {vendor[:80]}"
        chunks.append({"id": "comments", "offset": 0, "size": 0,
                       "summary": summary,
                       "fields": fields, "warnings": cwarns})
        warns.extend(w for w in cwarns if is_coverage(w))
    return chunks, warns
