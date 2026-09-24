"""Generate docs/formats/hes-anatomy.html.

The page shell is lifted from an existing anatomy page so every page stays
identical below the content. Every byte in the map is from one real file
(a 3,040-byte MSX rip) and decodes to the stated value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "kss-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>KSS Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>MSX . Master System</b>Z80 sound rip<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>KSCC / KSSX</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own; init data + banks</b></div>
      <div>samples <b>none; PSG, and FM if flagged</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.kss</b> is an MSX game's music with the game removed: a Z80 sound driver
    and its data, and sixteen bytes saying where to load it, where to call, and which chips the
    driver talks to. In <b>Sega mode</b> the same container holds a Master System or Game Gear
    rip. After the header comes the <b>init data</b>, loaded at the load address, then any number
    of <b>banks</b> the driver switches through, 8 or 16 KB each. A file may end inside its last
    bank; the player fills the rest with zero, and nearly half of them do.</p>
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
      <summary><span class="chev">&#9656;</span><span class="rname">the header</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="kss-head"></div>
        <p class="note"><b>KSSX.</b> The extended magic adds an extension the byte at 0x0E sizes,
        sixteen bytes in every file seen, and the init data begins after it. What the extension
        holds is not documented; it is zero in almost every file.</p>
        <p class="note"><b>The bank byte.</b> Bit 7 set means 8 KB banks, clear means 16 KB; the
        low seven bits count them. Read the other way round, a third of real files stop adding
        up.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the chip flags</span><span class="rspan">0x0F</span></summary>
      <div class="rbody">
        <div class="kv">
          <div><span class="k">bit 0</span><span class="v">FMPAC, a YM2413 OPLL cartridge</span></div>
          <div><span class="k">bit 1</span><span class="v">FM unit</span></div>
          <div><span class="k">bit 2</span><span class="v">SN76489: Sega mode, a Master System rip</span></div>
          <div><span class="k">bit 3</span><span class="v">RAM mode</span></div>
          <div><span class="k">bit 4</span><span class="v">MSX-AUDIO, a Y8950</span></div>
        </div>
        <p class="note">Zero means the PSG alone, the AY-3-8910 every MSX has. The higher bits are
        left as they are found.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">KSCC . KSSX</span></summary>
      <div class="rbody">
        <p class="note">Four bytes at zero, one of two spellings.</p>
      </div>
    </details>

  <footer>
    <span>acidcat / kss anatomy</span>
    <span>msx . master system . z80 . init data and banks</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = """
  // the 16-byte header and the first six bytes of init data
  build("kss-head","byte",
    [0x4B,0x53,0x43,0x43, 0xD0,0x81, 0xD0,0x0B, 0xD0,0x81, 0xDA,0x81, 0x00, 0x00, 0x00, 0x02,
     0xC6,0x81, 0x32,0x00,0xD3, 0xC9],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"KSCC\\"",body:"The original spelling. KSSX adds an extension.",note:""},
      {label:"load",k:"sync",r:[4,5],val:"$81D0",body:"Where the init data is placed in the Z80 address space.",note:"little-endian."},
      {label:"init length",k:"sync",r:[6,7],val:"0x0BD0 = 3,024",
       body:"Bytes of init data. 16 + 3,024 = 3,040, the file: no banks in this one.",note:""},
      {label:"init",k:"sync",r:[8,9],val:"$81D0",body:"Called with the song number in A. Here it is the first byte of the data.",note:""},
      {label:"play",k:"sync",r:[10,11],val:"$81DA",body:"Called every tick, ten bytes in.",note:""},
      {label:"start bank",k:"enum",r:[12,12],val:"0",body:"The number of the first extra bank.",note:""},
      {label:"extra banks",k:"enum",r:[13,13],val:"0",body:"None. Bit 7 would pick 8 KB banks; the low seven bits count them.",note:""},
      {label:"reserved",k:"rsv",r:[14,14],val:"0",body:"In KSSX this is the extension size.",note:""},
      {label:"chips",k:"enum",r:[15,15],val:"0x02 = FM unit",body:"The driver expects an FM unit beside the PSG.",note:"see the flag table."},
      {label:"init code",k:"enum",r:[16,21],val:"ADD A,$81 . LD ($D300),A . RET",
       body:"The init routine, disassembled: add 0x81 to the song number, store it, return. The player then calls play and the driver reads the number back.",
       note:"Z80; C6 is ADD A,n."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / kss anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / kss anatomy</title>",
                  page, count=1, flags=re.S)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
