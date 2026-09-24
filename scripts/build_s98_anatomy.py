"""Generate docs/formats/s98-anatomy.html.

The page shell is lifted from an existing anatomy page so every page stays
identical below the content. Every byte in the maps is from one real file
(a 2,050-byte Sharp X1 rip, S98 v3) and decodes to the stated value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "s98-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>S98 Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>NEC PC-98 and kin</b>a sound-chip register log<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>S98 + version digit</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own; v1 and v3 differ</b></div>
      <div>samples <b>none; FM and PSG writes</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">An <b>.s98</b> is the VGM idea from the Japanese PC scene: every write a program made
    to its sound chips, with the time between, so a player holding the same chips plays it back. Two
    versions are in the wild and they put things in different places. <b>v1</b> is a 32-byte header,
    a plain-text title right after it, and the dump at the next 128-byte boundary running to the end
    of the file. <b>v3</b> adds a device table after the header and a <code>[S98]</code> tag of
    <code>key=value</code> lines <i>after</i> the dump. The tag offset field is the same in both; where
    it points tells you which layout you have.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">file regions</div>

    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the header and device table</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="s98-head"></div>
        <p class="note"><b>The timer.</b> One sync is numerator / denominator seconds; zero in either
        field means the default, 10 / 1000, ten milliseconds. This file says 1 / 0: one millisecond
        per sync.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the dump</span><span class="rspan">a byte code . drawn below</span></summary>
      <div class="rbody">
        <p class="note">One opcode, then operands. <code>0x00-0x7F</code> is a write: the device is
        the opcode halved, the port its low bit, and two bytes follow, register and value.
        <code>0xFF</code> is one sync. <code>0xFE</code> is a sync count plus two, as a
        <b>7-bit varint</b>, low bits first, bit 7 meaning another byte follows, in <i>every</i>
        version. <code>0xFD</code> ends the dump, and in a well-formed file that is where the tag
        (v3) or the file (v1) begins.</p>
        <div id="s98-dump"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the tag</span><span class="rspan">[S98] . key=value . drawn below</span></summary>
      <div class="rbody">
        <p class="note">v3: five bytes of mark, then <code>key=value</code> lines separated by LF, in
        Shift-JIS unless a <code>utf8=</code> line says otherwise. The keys are free; title, artist,
        game, year, genre, comment, copyright, s98by and system are the ones players show. v1 has
        only a title, plain text, NUL-padded, sitting between the header and the dump.</p>
        <div id="s98-tag"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">S98 + 1 or 3</span></summary>
      <div class="rbody">
        <p class="note">Three letters and a version digit as text. v2 was defined and never used.</p>
      </div>
    </details>

  <footer>
    <span>acidcat / s98 anatomy</span>
    <span>pc-98 . x1 . register log . varint syncs</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = """
  // the 32-byte header and two device entries of a real v3 file (a Sharp X1 tune)
  build("s98-head","byte",
    [0x53,0x39,0x38,0x33, 0x01,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
     0x6C,0x07,0x00,0x00, 0x80,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x02,0x00,0x00,0x00,
     0x01,0x00,0x00,0x00, 0x00,0x09,0x3D,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
     0x05,0x00,0x00,0x00, 0x00,0x09,0x3D,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"S983\\"",body:"Three letters and the version as a text digit: 3.",note:"53 39 38 33."},
      {label:"timer numerator",k:"enum",r:[4,7],val:"1",body:"With the denominator below: one sync is 1 / 1000 s.",note:""},
      {label:"timer denominator",k:"enum",r:[8,11],val:"0 = default 1000",body:"Zero means the default.",note:""},
      {label:"compression",k:"rsv",r:[12,15],val:"0",body:"Defined, never used.",note:""},
      {label:"tag offset",k:"sync",r:[16,19],val:"0x076C = 1900",body:"Past the dump: this is a v3 layout. In a v1 file this points at 0x20, the title before the dump.",note:"which layout, by where it points."},
      {label:"dump offset",k:"sync",r:[20,23],val:"0x80 = 128",body:"Where the byte code begins. 0x20 + two 16-byte device entries is 0x40; the rest to 0x80 is zero padding.",note:""},
      {label:"loop offset",k:"sync",r:[24,27],val:"0 = no loop",body:"An offset inside the dump to return to, or zero.",note:""},
      {label:"devices",k:"sync",r:[28,31],val:"2",body:"How many 16-byte device entries follow.",note:"v3 only."},
      {label:"device 0",k:"enum",r:[32,47],val:"type 1 = YM2149 PSG @ 4 MHz",body:"Type, clock in Hz, pan, reserved. Opcodes 0x00 and 0x01 write to it.",note:"0x3D0900 = 4,000,000."},
      {label:"device 1",k:"enum",r:[48,63],val:"type 5 = YM2151 OPM @ 4 MHz",body:"The second device: opcodes 0x02 and 0x03.",note:""}
    ]);

  // the first commands of the dump, and the first counted sync
  build("s98-dump","byte",
    [0x02,0x1F,0x00, 0x02,0x1E,0x00, 0x02,0x1D,0x00, 0x02,0x1C,0x00, 0xFE,0xE5,0x02, 0xFF],
    [
      {label:"write",k:"sync",r:[0,2],val:"device 1, reg 0x1F = 0",body:"Opcode 0x02: device 1 (the OPM), port 0. Then the register and the value. The tune opens by clearing the chip's registers downward.",note:"three bytes."},
      {label:"writes",k:"enum",r:[3,11],val:"regs 0x1E, 0x1D, 0x1C = 0",body:"Three more, the same shape.",note:""},
      {label:"sync n",k:"sync",r:[12,14],val:"0xFE, varint 0x165 -> 359 syncs",body:"0xE5 has bit 7 set, so 0x02 follows: 0x65 + (0x02 << 7) = 357, plus 2 = 359 syncs of 1 ms. A reader that takes one byte here lands inside the next command and misreads everything after.",note:"low seven bits first."},
      {label:"sync",k:"sync",r:[15,15],val:"0xFF = 1 sync",body:"One tick.",note:""}
    ]);

  // the start of the tag block
  build("s98-tag","byte",
    [0x5B,0x53,0x39,0x38,0x5D, 0x53,0x79,0x73,0x74,0x65,0x6D,0x3D,0x58,0x31,0x0A, 0x53,0x39,0x38,0x62,0x79,0x3D],
    [
      {label:"mark",k:"sync",r:[0,4],val:"\\"[S98]\\"",body:"Five bytes where the tag offset lands.",note:""},
      {label:"System=X1",k:"enum",r:[5,14],val:"key=value, LF",body:"The first line: this is a Sharp X1 rip, which is why the devices are a PSG and an OPM rather than the OPNA a PC-98 has.",note:"Shift-JIS unless utf8= appears."},
      {label:"S98by=",k:"enum",r:[15,20],val:"the next key",body:"Who made the file. The lines continue to the end of the file.",note:""}
    ]);
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
    page = head + BODY + engine + BUILDS + after
    page = page.replace("acidcat / rmid anatomy", "acidcat / s98 anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / s98 anatomy</title>",
                  page, count=1, flags=re.S)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
