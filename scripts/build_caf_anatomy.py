"""Generate docs/formats/caf-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from a real specimen and decodes to the stated
value. The specimen is a mono 16-bit 44.1 kHz CAF written by libsndfile 1.2.2
(4410 frames of a 440 Hz tone, 12,916 bytes), which is also what the walker was
built and verified against.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "caf-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>CAF Anatomy</h1></div></div>
      <div class="stamp"><b>Apple Core Audio Format</b>Mac OS X 10.4 . 2005<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>caff</b></div>
      <div>ids <b>4-char</b></div>
      <div>sizes <b>s64, signed</b></div>
      <div>endian <b>big</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">Apple's answer to the 4&nbsp;GB problem, and the third answer in a family that
    already had two. <b>CAF</b> keeps RIFF's four-character chunk ids and its payload-only sizes, but
    widens them to <b>signed 64-bit</b>, writes every field <b>big-endian</b>, and abandons the
    alignment rule entirely. Where RIFF pads chunks to 2 and Wave64 to 8, CAF chunks simply abut. A
    reader carrying either habit walks into the next chunk's id.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
        <div class="row"><span class="sw dark k-rsv">grey</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">payload</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the three places it differs</div>
  <p class="note"><b>The size is signed, and -1 is legal.</b> That is the whole reason the field is
  an <code>s64</code> rather than a <code>u64</code>. A writer streaming to a pipe does not know how
  long the audio will be, and CAF lets it say so: a <code>data</code> chunk whose size is
  <b>-1</b> runs to the end of the file. Read as unsigned, that same chunk claims
  <b>18,446,744,073,709,551,615</b> bytes, which is the failure mode the signedness exists to
  prevent and the one a reader ported from RIFF hits first.</p>
  <p class="note"><b>The container is big-endian; the samples are not necessarily.</b> Every
  structural field -- the version, every chunk id, every size -- is big-endian throughout. The
  <b>sample data is whatever <code>desc</code> says it is</b>, in a flags word that is independent of
  the container's own byte order. A reader that infers one from the other is right only by luck, and wrong
  silently, because wrong-endian PCM decodes to noise rather than to an error.</p>
  <p class="note"><b>There is no alignment rule.</b> A chunk ends where its payload ends and the
  next id begins on the very next byte. No pad, no rounding, no exceptions -- so a three-byte
  payload is followed immediately by a chunk id at an odd offset.</p>

  <div class="sec">the header</div>
  <p class="note">Eight bytes, and only the first four carry information a reader keys on. Unlike
  RIFF there is no size here and no form type: the file's structure begins immediately with the
  first chunk, and <code>desc</code> is required to be that chunk.</p>
"""

BUILDS = """
  // real bytes: tone_mono.caf, libsndfile 1.2.2, 12,916 bytes
  build("caf-header","byte",[0x63,0x61,0x66,0x66,0x00,0x01,0x00,0x00],[
    {label:"magic",k:"sync",r:[0,3],val:"caff",
     body:"The file type. Four ASCII characters, and unlike RIFF it is not followed by a size: a CAF does not declare its own length anywhere, so the file system is the only authority on where it ends.",
     note:"63 61 66 66."},
    {label:"version",k:"enum",r:[4,5],val:"1",
     body:"The file version. 1 is the only value the specification defines. A reader meeting anything else is looking at a format it has not been told about, which is a finding rather than something to parse through.",
     note:"u16 big-endian."},
    {label:"flags",k:"rsv",r:[6,7],val:"0",
     body:"No flags are defined for version 1. Reserved, and written zero.",
     note:"u16 big-endian."}
  ]);

  // real bytes: the desc chunk of the same specimen, at offset 8
  build("caf-desc","byte",[0x64,0x65,0x73,0x63,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x20,
                           0x40,0xE5,0x88,0x80,0x00,0x00,0x00,0x00,
                           0x6C,0x70,0x63,0x6D,0x00,0x00,0x00,0x00,
                           0x00,0x00,0x00,0x02,0x00,0x00,0x00,0x01,
                           0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x10],[
    {label:"id",k:"sync",r:[0,3],val:"desc",
     body:"The audio description, and the specification requires it to be the FIRST chunk. Its absence is not a missing nicety, it is a structural finding: nothing after it can be interpreted without the geometry it carries.",
     note:"64 65 73 63."},
    {label:"size",k:"enum",r:[4,11],val:"32",
     body:"The payload length, NOT counting these twelve header bytes -- RIFF's meaning, not Wave64's. Signed: a size of -1 means the chunk runs to the end of the file, which is legal for data and is why the field is not a u64.",
     note:"s64 big-endian, fixed at 32 here."},
    {label:"sample_rate",k:"enum",r:[12,19],val:"44100",
     body:"A 64-bit IEEE double, not an integer. CAF is the only member of this family that stores the rate as a float, which lets it express the pulled-down rates video work actually uses -- 44100/1.001 is exact here and is not an integer anywhere.",
     note:"40 E5 88 80 .. = 44100.0."},
    {label:"format_id",k:"sync",r:[20,23],val:"lpcm",
     body:"A four-character codec id. lpcm is linear PCM; alac, aac, ima4 and the telephony codecs use the same field. Everything below is interpreted relative to this.",
     note:"6C 70 63 6D."},
    {label:"format_flags",k:"enum",r:[24,27],val:"0x00000000",
     body:"For lpcm, two bits that decide how to READ the samples: bit 0 float rather than integer, bit 1 little-endian rather than big. Zero here means big-endian integers -- the container's byte order and the sample's agree on this file and are not required to.",
     note:"the trap: this is independent of the container."},
    {label:"bytes_per_packet",k:"enum",r:[28,31],val:"2",
     body:"Bytes in one packet. ZERO is meaningful rather than missing: a variable-bitrate codec puts the per-packet sizes in a pakt chunk instead, so a duration cannot be derived from the byte count alone.",
     note:"1 channel x 16-bit = 2."},
    {label:"frames_per_packet",k:"enum",r:[32,35],val:"1",
     body:"Frames in one packet. 1 for uncompressed audio; 1024 and 2048 are the usual values for AAC, which is what makes a packet table necessary rather than optional.",
     note:"uncompressed: always 1."},
    {label:"channels_per_frame",k:"enum",r:[36,39],val:"1",
     body:"Channel count. The channel LAYOUT -- which speaker each one drives -- is a separate chan chunk, so a bare count here says how many and not which.",
     note:"mono."},
    {label:"bits_per_channel",k:"enum",r:[40,43],val:"16",
     body:"Bits in one sample of one channel. ZERO for a compressed format, where the on-disk width is not a fixed number of bits and asking the question makes no sense.",
     note:"00 00 00 10 = 16."}
  ]);

  // real bytes: the data chunk header of the same specimen, at offset 4080
  build("caf-data","byte",[0x64,0x61,0x74,0x61,0x00,0x00,0x00,0x00,0x00,0x00,0x22,0x78,
                           0x00,0x00,0x00,0x00],[
    {label:"id",k:"sync",r:[0,3],val:"data",
     body:"The audio. In this specimen it is not the second chunk: libsndfile writes a free chunk of 4,016 bytes between desc and data, reserving room to grow the metadata later without rewriting the file.",
     note:"64 61 74 61."},
    {label:"size",k:"enum",r:[4,11],val:"8,824",
     body:"The payload length. 8,824 bytes, of which the first four are NOT audio -- the edit count below -- leaving 8,820 sample bytes, which at 2 bytes a frame is 4,410 frames and exactly 0.100 s at 44,100 Hz.",
     note:"00 00 22 78 = 8824."},
    {label:"edit_count",k:"rsv",r:[12,15],val:"0",
     body:"A u32 that increments on every edit, and the single most commonly mishandled field in the format: it sits INSIDE the data payload, before the samples. A reader that treats the payload as audio from byte zero puts four bytes of metadata at the head of every carve and shifts the whole stream by two samples.",
     note:"the audio starts at payload + 4."}
  ]);
"""

EXTRA_BODY = """
  <div class="map" id="caf-header" data-build></div>

  <div class="sec">the audio description</div>
  <p class="note">Thirty-two fixed bytes, required first, and the only chunk whose absence stops a
  reader dead. Two of its fields are the ones a port from another format gets wrong: the sample rate
  is a <b>double</b> rather than an integer, and <code>format_flags</code> decides the sample byte
  order independently of the container.</p>
  <div class="map" id="caf-desc" data-build></div>

  <div class="sec">the audio</div>
  <p class="note">The <code>data</code> chunk's payload does not begin with samples. Its first four
  bytes are an <b>edit count</b>, and the audio starts after them. Everything downstream -- duration,
  frame count, a carve of the raw stream -- is wrong by four bytes if that is missed, which for
  16-bit mono is a two-sample shift and for a carve is four bytes of metadata glued to the front of
  the audio.</p>
  <div class="map" id="caf-data" data-build></div>

  <div class="sec">the chunks</div>
  <table class="tbl">
    <thead><tr><th>id</th><th>what it carries</th><th>notes</th></tr></thead>
    <tbody>
      <tr><td><code>desc</code></td><td>rate, codec, channels, bit depth, packet geometry</td><td>required, and required first</td></tr>
      <tr><td><code>data</code></td><td>the audio, after a u32 edit count</td><td>size -1 means "to the end of the file"</td></tr>
      <tr><td><code>pakt</code></td><td>packet count, valid frames, priming and remainder</td><td>required when bytes_per_packet is 0</td></tr>
      <tr><td><code>chan</code></td><td>channel layout tag, bitmap, per-channel descriptions</td><td>which speaker, not how many</td></tr>
      <tr><td><code>info</code></td><td>a u32 count then NUL-terminated key/value strings</td><td>free-form metadata</td></tr>
      <tr><td><code>peak</code></td><td>per-channel peak amplitude and the frame it lands on</td><td>a float and a u64 per channel</td></tr>
      <tr><td><code>free</code></td><td>reserved space</td><td>room to grow the metadata without rewriting</td></tr>
      <tr><td><code>kuki</code></td><td>codec magic cookie</td><td>opaque decoder configuration</td></tr>
    </tbody>
  </table>

  <div class="sec">where the family diverges</div>
  <table class="tbl">
    <thead><tr><th></th><th>RIFF / WAVE</th><th>Wave64</th><th>CAF</th></tr></thead>
    <tbody>
      <tr><td>chunk id</td><td>4 chars</td><td>16-byte GUID</td><td>4 chars</td></tr>
      <tr><td>size field</td><td>u32</td><td>u64</td><td><b>s64, signed</b></td></tr>
      <tr><td>size counts</td><td>payload</td><td>payload + its 24-byte header</td><td>payload</td></tr>
      <tr><td>alignment</td><td>2 bytes</td><td>8 bytes</td><td><b>none</b></td></tr>
      <tr><td>endian</td><td>little</td><td>little</td><td><b>big</b></td></tr>
      <tr><td>sample endian</td><td>little</td><td>little</td><td><b>a flag in desc</b></td></tr>
      <tr><td>unknown length</td><td>--</td><td>--</td><td><b>size = -1</b></td></tr>
    </tbody>
  </table>
  <p class="note">Three of those rows are places a reader written for one member produces a
  confident wrong answer on another rather than an error, which is the argument for reading the
  size field's <i>type</i> as carefully as its value.</p>
"""


def build():
    with io.open(TEMPLATE, encoding="utf-8") as fh:
        tpl = fh.read()

    start = tpl.index('<div class="sheet">')
    head = tpl[:start]

    script_at = tpl.index("<script>", start)
    tail = tpl[script_at:]
    calls_at = tail.index("  // RIFF wrapper")
    engine = tail[:calls_at]
    after = tail[tail.index("})();\n</script>", calls_at):]

    # the closing furniture: the template's own <footer> and the wrapper that
    # closes .sheet. Taken verbatim so the page ends the way every other one
    # does, with only the two footer strings rewritten.
    foot_at = tpl.index("  <footer>", start)
    foot = tpl[foot_at:script_at]
    foot = foot.replace(
        "riff (le) . rmid . wraps a big-endian smf",
        "caff (be) . s64 sizes . no alignment")

    page = head + BODY + EXTRA_BODY + foot + engine + BUILDS + after
    page = page.replace("acidcat / rmid anatomy", "acidcat / caf anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / caf anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
