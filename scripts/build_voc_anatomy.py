"""Generate docs/formats/voc-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from a real specimen and verified against the
file afterwards. The specimens are shipped game data, named on the page:

    SWRDSTR1.VOC   Shadow Warrior, loose in gameroot/       3,392 B, block 01
    SPELEC.VOC     Shadow Warrior, inside SW.GRP           13,033 B, block 09
    FBALL1.VOC     Shadow Warrior, inside SW.GRP           16,407 B, blocks 01+05

The page states the FORMAT, not what a corpus measured: these are finalized
specs, not audit reports, and tests/test_anatomy_pages.py enforces that. The
corpus findings behind the walker live in core/walk/voc.py's docstring, which
is where a statement about a test run belongs.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "voc-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>VOC Anatomy</h1></div></div>
      <div class="stamp"><b>Creative Voice File</b>Sound Blaster . 1990<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>Creative Voice File</b></div>
      <div>header <b>26 bytes</b></div>
      <div>lengths <b>u24</b></div>
      <div>endian <b>little</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">The sample format of the DOS era, written by the Sound Blaster's own tools and
    carried inside almost every Build-engine and early-90s game archive. A 26-byte header, then a
    chain of typed blocks: a <b>type byte</b>, a <b>24-bit</b> little-endian length, and a payload.
    Three things about that chain send a reader quietly wrong rather than stopping it: the
    terminator has no length, the continuation block has no format, and the length is three bytes
    where the eye expects four.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
        <div class="row"><span class="sw dark k-rsv">grey</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">payload</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the three ways to get it wrong</div>
  <p class="note"><b>The terminator has no length field.</b> Every block is a type byte and a
  three-byte length -- except type <b>00</b>, which is one byte and ends the file. A reader that
  reads four bytes for every block header consumes three bytes past the end of the data, and on a
  file that ends exactly at its terminator it reads past EOF. This is the single most common way to
  walk the format wrong.</p>
  <p class="note"><b>Block 02 carries no format bytes.</b> It continues whichever sound block came
  before it, so a reader that treats every sound block as self-describing loses the rate on every
  file longer than one block. A long sound is commonly one block 01 and a run of 8,192-byte
  continuations; the byte map below shows one, with the samples either side of its header so the
  join can be read rather than taken on trust.</p>
  <p class="note"><b>The length is 24 bits, not 32.</b> Three bytes, little-endian, so a block
  caps at 16 MB and the fourth byte of what looks like a u32 is the <i>next</i> field. Read it as a
  u32 in a block 01 and the time constant is swallowed into the length, taking the sample rate with
  it.</p>

  <div class="sec">the header</div>
  <p class="note">Twenty-six bytes, and the last four are worth reading rather than skipping: a
  version, and a checksum <b>derived from it</b> as
  <code>(~version + 0x1234) &amp; 0xFFFF</code>. It validates nothing about the audio, but it is
  the only integrity check the format has, and a mismatch means the header was not written by
  something that knew the rule.</p>
"""

BUILDS = """
  // real bytes: SWRDSTR1.VOC, Shadow Warrior, 3,392 bytes
  build("voc-header","byte",[0x43,0x72,0x65,0x61,0x74,0x69,0x76,0x65,0x20,0x56,
                             0x6F,0x69,0x63,0x65,0x20,0x46,0x69,0x6C,0x65,0x1A,
                             0x1A,0x00,0x0A,0x01,0x29,0x11],[
    {label:"magic",k:"sync",r:[0,19],val:"Creative Voice File\\\\x1a",
     body:"Twenty bytes, and the last of them is a DOS end-of-file character rather than part of the words. TYPE a .VOC at a DOS prompt and it stops there, which is why it is in the magic at all.",
     note:"19 chars + 0x1A."},
    {label:"header_size",k:"enum",r:[20,21],val:"26",
     body:"Where the block chain starts. Read it rather than assuming 26: the field exists so the header could grow, and a reader that hardcodes the constant is trusting that it never did.",
     note:"1A 00 = 26."},
    {label:"version",k:"enum",r:[22,23],val:"1.10",
     body:"Minor in the low byte, major in the high one. 1.10 is the original; 1.20 added block 09, which is the version that matters because it is what decides whether a file can describe its own sample rate directly.",
     note:"00 0A = 1.10."},
    {label:"checksum",k:"enum",r:[24,25],val:"0x1129",
     body:"Not a checksum over the data: it is computed from the VERSION alone, as (~version + 0x1234) & 0xFFFF. It says nothing about the audio, and it is the only integrity check the format carries, so a mismatch means the header was not written by something that knew the rule.",
     note:"~0x010A + 0x1234 = 0x1129."}
  ]);

  // real bytes: the block 01 header of the same specimen, at offset 26
  build("voc-block01","byte",[0x01,0x21,0x0D,0x00,0xA4,0x00,0x7E,0x7E,0x7D,0x7D],[
    {label:"type",k:"sync",r:[0,0],val:"01",
     body:"Sound data, the original block, and the one most files are built from whatever the later revisions added.",
     note:"the commonest block."},
    {label:"length",k:"enum",r:[1,3],val:"3,361",
     body:"A 24-BIT little-endian length, counting the two format bytes below plus the samples. Three bytes, so a block caps at 16 MB -- and reading four here swallows the time constant into the length.",
     note:"21 0D 00 = 3361."},
    {label:"time_constant",k:"enum",r:[4,4],val:"164",
     body:"Not a rate. The 8-bit divisor the hardware was loaded with, and the rate comes back as 1000000 / (256 - tc) -- here 10,870 Hz, not a round number and not meant to be. The constant is worth carrying alongside the rate, because the divisor is the number the file actually holds and the rate is the one derived from it.",
     note:"A4 = 164 -> 10870 Hz."},
    {label:"codec",k:"enum",r:[5,5],val:"0",
     body:"0 is unsigned 8-bit PCM. The format also defines 4-bit and 2.6-bit Creative ADPCM here, both rare enough in the wild that a reader should name them rather than assume it can decode them.",
     note:"00 = 8-bit unsigned PCM."},
    {label:"samples",k:"rsv",r:[6,9],val:"7E 7E 7D 7D",
     body:"Unsigned 8-bit mono, with 0x80 as silence. These four sit just above it, which is what the opening of a quiet sound looks like.",
     note:"3,359 bytes follow."}
  ]);

  // real bytes: SPELEC.VOC, inside SW.GRP, 13,033 bytes
  build("voc-block09","byte",[0x09,0xCA,0x32,0x00,0x11,0x2B,0x00,0x00,0x08,0x01,
                              0x00,0x00,0x00,0x00,0x00,0x00,0x80,0x80,0x80,0x80],[
    {label:"type",k:"sync",r:[0,0],val:"09",
     body:"Sound data, version 1.20's replacement for block 01. It exists because the time constant could not express a rate exactly, and this one states the rate as a plain u32.",
     note:"added in v1.20."},
    {label:"length",k:"enum",r:[1,3],val:"13,002",
     body:"24-bit, as everywhere: the twelve format bytes below plus the samples.",
     note:"CA 32 00 = 13002."},
    {label:"sample_rate",k:"enum",r:[4,7],val:"11025",
     body:"An explicit u32 in Hz. No divisor, no arithmetic, no cluster of near-misses -- which is the entire reason block 09 was added.",
     note:"11 2B 00 00 = 11025."},
    {label:"bits",k:"enum",r:[8,8],val:"8",
     body:"Bits per sample. 16 is legal here and uncommon in practice.",
     note:"08."},
    {label:"channels",k:"enum",r:[9,9],val:"1",
     body:"Channel count. Stereo is expressible and almost never written.",
     note:"01."},
    {label:"format",k:"enum",r:[10,11],val:"0",
     body:"The codec. 0 is unsigned PCM and 4 is signed PCM; the ADPCM codes block 01 allows are defined here too.",
     note:"00 00 = unsigned PCM."},
    {label:"reserved",k:"rsv",r:[12,15],val:"00 00 00 00",
     body:"Four reserved bytes, written zero. They are not padding a reader may skip by guess -- they are inside the declared length and the samples begin after them.",
     note:"required zero."},
    {label:"samples",k:"rsv",r:[16,19],val:"80 80 80 80",
     body:"0x80 is silence in unsigned 8-bit, so a sound that opens on four of them opens on nothing, which is what a recorded sample with a little lead-in looks like.",
     note:"12,990 bytes follow."}
  ]);

  // real bytes: !BOSS.VOC, inside DUKE3D.GRP -- its first block 02 at 8224,
  // and the six bytes of block 01 that end immediately before it
  build("voc-block02","byte",[0x81,0x81,0x81,0x7F,0x7D,0x7F,
                              0x02,0x00,0x20,0x00,0x7F,0x7D,0x7C,0x7C],[
    {label:"previous samples",k:"rsv",r:[0,5],val:"81 81 81 7F 7D 7F",
     body:"The last six samples of the block 01 that came before. They are here to be compared with the six on the other side of the header: the waveform does not restart, it carries straight on.",
     note:"end of the preceding block."},
    {label:"type",k:"sync",r:[6,6],val:"02",
     body:"Sound continuation, and the format's worst trap. It carries NO time constant and NO codec byte -- a reader that treats every sound block as self-describing loses the rate here and reports the rest of the file at whatever it defaults to.",
     note:"inherits everything."},
    {label:"length",k:"enum",r:[7,9],val:"8,192",
     body:"24-bit, as everywhere. This is ALL payload: unlike block 01 there are no format bytes to subtract, so every byte after the header is a sample.",
     note:"00 20 00 = 8192."},
    {label:"samples",k:"rsv",r:[10,13],val:"7F 7D 7C 7C",
     body:"Read against the six bytes at the start of this map: 81 81 81 7F 7D 7F, then 7F 7D 7C. That is a waveform continuing, not a new sound beginning, which is what a continuation looks like when it is checked rather than assumed.",
     note:"the rate comes from the block before."}
  ]);

  // real bytes: FBALL1.VOC, inside SW.GRP -- its block 05 at 16394, and the
  // type-00 terminator that immediately follows it
  build("voc-block05","byte",[0x05,0x07,0x00,0x00,0x6D,0x61,0x67,0x69,0x63,0x30,0x34,0x00],[
    {label:"type",k:"sync",r:[0,0],val:"05",
     body:"A text marker, and the reason a .VOC sometimes tells you what it is. Optional, and more common in game data than anywhere else.",
     note:"ASCII text block."},
    {label:"length",k:"enum",r:[1,3],val:"7",
     body:"Seven bytes, covering the string EXACTLY: no terminator is counted and none is written inside the block. A reader that assumes a NUL and trims one character loses the last letter of every label.",
     note:"07 00 00 = 'magic04'."},
    {label:"text",k:"enum",r:[4,10],val:"magic04",
     body:"The string itself. Game data labels its own sounds surprisingly often, which makes block 05 worth reading rather than skipping as metadata.",
     note:"'magic04'."},
    {label:"end of file",k:"sync",r:[11,11],val:"00",
     body:"NOT part of the text block: block 05 ended at the previous byte. This is the type-00 terminator, the one block with no length field, and it is what the chain runs into next.",
     note:"the one-byte block 00."}
  ]);
"""

EXTRA_BODY = """
  <div class="map" id="voc-header" data-build></div>

  <div class="sec">block 01 -- the original sound block</div>
  <p class="note">A time constant and a codec byte, then unsigned 8-bit samples. The constant is a
  hardware divisor rather than a rate, and the rate it produces is deliberately not round.</p>
  <div class="map" id="voc-block01" data-build></div>

  <div class="sec">block 02 -- the one that carries no format</div>
  <p class="note">A sound longer than one block is split, and every block after the first is a
  <b>type 02</b> that states nothing about itself. No time constant, no codec, no rate. It inherits
  all of it from the sound block before it, so a reader that asks each block what it is gets an
  answer from the first one and silence from the rest.</p>
  <div class="map" id="voc-block02" data-build></div>

  <div class="sec">block 09 -- what 1.20 added</div>
  <p class="note">The same audio, described directly: an explicit <b>u32 rate</b>, a bit width, a
  channel count and a format code. Block 09 exists because the time constant could not express a
  rate exactly, and it is the version worth preferring when a file offers both.</p>
  <div class="map" id="voc-block09" data-build></div>

  <div class="sec">block 05 -- the file labelling itself</div>
  <p class="note">Game data names its own sounds more often than you would expect, which makes the
  text block worth reading rather than skipping past as metadata.</p>
  <div class="map" id="voc-block05" data-build></div>

  <div class="sec">the block types</div>
  <table class="tbl">
    <thead><tr><th>type</th><th>what it is</th><th>notes</th></tr></thead>
    <tbody>
      <tr><td><code>00</code></td><td>terminator</td><td><b>one byte, no length field</b></td></tr>
      <tr><td><code>01</code></td><td>sound data: time constant + codec, then samples</td><td>the original, and the common case</td></tr>
      <tr><td><code>02</code></td><td>sound continuation</td><td><b>no format bytes of its own</b></td></tr>
      <tr><td><code>03</code></td><td>silence: a duration and a time constant</td><td>occupies time, carries no samples</td></tr>
      <tr><td><code>04</code></td><td>marker</td><td>a u16 id</td></tr>
      <tr><td><code>05</code></td><td>ASCII text</td><td>NUL-terminated, inside the length</td></tr>
      <tr><td><code>06</code></td><td>repeat start</td><td>a u16 count</td></tr>
      <tr><td><code>07</code></td><td>repeat end</td><td>closes the nearest 06</td></tr>
      <tr><td><code>08</code></td><td>extended format</td><td>precedes a block 01 and overrides it</td></tr>
      <tr><td><code>09</code></td><td>sound data with an explicit u32 rate</td><td>added in v1.20</td></tr>
    </tbody>
  </table>
  <p class="note">Types <b>03, 04, 06, 07 and 08</b> are defined by the format and seldom written.
  A reader should frame them from their documented layouts so the chain does not derail on a file
  that has them, and should not claim to have verified what it has never seen.</p>

  <div class="sec">the rate, and why it is two numbers</div>
  <p class="note">A block 01 stores a <b>divisor</b>, not a rate, and the rate is
  <code>1000000 / (256 - tc)</code>. The constants in use produce values like 5988, 8000, 10870,
  10989, 11111, 21739 and 22222&nbsp;Hz -- clustering around the era's 11025 and 22050 in exactly
  the way a coarse integer divisor would, and never landing on them.</p>
  <p class="note">A file is not required to state its rate twice, so nothing inside a block 01
  confirms the formula. That is the argument for reporting the <b>constant the file holds</b>
  alongside the rate derived from it: the constant is a fact about the bytes, and the rate is an
  inference from a documented formula. Block 09 removes the question entirely by writing the rate
  as a plain u32, which is why a file that offers both is better read from the 09.</p>
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

    foot_at = tpl.index("  <footer>", start)
    foot = tpl[foot_at:script_at]
    foot = foot.replace("riff (le) . rmid . wraps a big-endian smf",
                        "creative voice file . u24 lengths . a one-byte terminator")

    page = head + BODY + EXTRA_BODY + foot + engine + BUILDS + after
    page = page.replace("acidcat / rmid anatomy", "acidcat / voc anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / voc anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
