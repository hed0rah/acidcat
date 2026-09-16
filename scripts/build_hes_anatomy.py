"""Generate docs/formats/hes-anatomy.html.

The page shell is lifted from an existing anatomy page so every page stays
identical below the content. Every byte in the map is from one real file
(a 16 KB PC Engine rip) and decodes to the stated value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "hes-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>HES Anatomy</h1></div></div>
      <div class="stamp"><b>NEC PC Engine</b>HuC6280 sound rip<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>HESM</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own; DATA blocks</b></div>
      <div>samples <b>none; six wavetable channels of chip</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.hes</b> is a PC Engine game's music with the game removed: the sound
    engine and its data, and a 16-byte header telling a player where to call and how to set up
    the memory map. It is the NSF idea on a different machine. The HuC6280 addresses 2 MB through
    eight <b>MPR</b> registers, each mapping an 8 KB physical page into one eighth of the 64 KB
    the CPU can see, so the header carries eight bytes of MPR presets where an NSF carries eight
    bank numbers. Then a <b>DATA</b> block: a tag, a size, an address, and the bytes.</p>

    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the header and the block</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="hes-head"></div>
        <p class="note"><b>The address field.</b> The specification calls it the block's load
        address in the 2 MB physical space. It is not read as one here, because in every file
        examined it holds 0x20, which is the offset of the block's own bytes in the file; the
        MPR presets are what place the bytes in memory. It is reported as the field it is.</p>
        <p class="note"><b>The size field is a claim.</b> A block can declare a whole cartridge
        and the file hold a page of it; a reader takes what is there.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">HESM</span></summary>
      <div class="rbody">
        <p class="note">Four bytes at zero. There is no version other than 0.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / hes anatomy</span>
    <span>nec pc engine . huc6280 . mpr presets . data block</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = """
  // the 32 bytes of header plus the first six of the block
  build("hes-head","byte",
    [0x48,0x45,0x53,0x4D, 0x00, 0x01, 0x00,0xE1, 0xFF,0xF8,0x00,0x00,0x01,0x00,0x00,0x00,
     0x44,0x41,0x54,0x41, 0xE0,0x3F,0x00,0x00, 0x20,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
     0x4C,0x80,0xE1, 0x4C,0x00,0xE2],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"HESM\\"",body:"Four bytes.",note:""},
      {label:"version",k:"enum",r:[4,4],val:"0",body:"The only one.",note:""},
      {label:"first track",k:"enum",r:[5,5],val:"1",body:"The track a player starts on.",note:""},
      {label:"init",k:"ptr",r:[6,7],val:"$E100",
       body:"The address the player calls with a track number, in the CPU's 64 KB view, once the MPRs below are set.",
       note:"little-endian."},
      {label:"MPR0-7",k:"enum",r:[8,15],val:"FF F8 00 00 01 00 00 00",
       body:"The eight page registers. MPR0 = FF maps the I/O page at $0000, MPR1 = F8 the work RAM at $2000, and the rest select physical pages of the block: page 0 at $4000, $6000, $A000, $C000 and $E000, page 1 at $8000. The init address $E100 is therefore 0x100 into the block.",
       note:"8 KB pages; $E000-$FFFF is MPR7."},
      {label:"DATA",k:"sync",r:[16,19],val:"\\"DATA\\"",body:"The block tag.",note:""},
      {label:"size",k:"size",r:[20,23],val:"0x3FE0 = 16,352",
       body:"Bytes in the block. 0x20 + 16,352 = 16,384, the file's size: it adds up here; it does not in every file.",
       note:""},
      {label:"address",k:"enum",r:[24,27],val:"0x20",
       body:"The spec's load address; see the note above.",note:""},
      {label:"unused",k:"pad",r:[28,31],val:"0",body:"Four bytes the block header reserves.",note:""},
      {label:"code",k:"enum",r:[32,37],val:"JMP $E180 . JMP $E200",
       body:"The block opens with a jump table: 4C is the HuC6280's JMP. The engine's entry points sit at page offsets 0x180 and 0x200.",
       note:"the first bytes of physical page 0."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / hes anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / hes anatomy</title>",
                  page, count=1, flags=re.S)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
