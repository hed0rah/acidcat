"""Generate docs/formats/sndh-anatomy.html.

The page shell is lifted from an existing anatomy page so every page stays
identical below the content. Every byte in the maps is from one real file
(a Pack-Ice SNDH of 677 bytes that unpacks to 2,154) and decodes to the
stated value; the header bytes are from the unpacked image.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "sndh-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>SNDH Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Atari ST</b>SNDH music file<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>SNDH at 12</b></div>
      <div>endian <b>big (68000)</b></div>
      <div>container <b>Pack-Ice</b></div>
      <div>samples <b>none; code</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">An <b>.sndh</b> is a program: the music's own 68000 player routine, ripped from
    the game or demo it came from, with a small tag header saying what it is and how to call it.
    Three branches come first -- <b>init</b> (with the subtune number in d0), <b>exit</b> and
    <b>play</b>, the last called at the replay rate -- and <code>SNDH</code> at offset 12 opens the
    tags. To hear one, a 68000 runs init once and play every tick while the YM2149 (and on the STe,
    the DMA sound) is emulated. Nearly every file is then packed with Pack-Ice.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
        <div class="row"><span class="sw dark k-rsv">grey</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">reserved</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">file regions</div>

    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the Pack-Ice header</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="sndh-ice"></div>
        <p class="note"><b>Read backwards.</b> The packed stream is consumed from the end of the file
        toward this header, and the image is written from its end toward its start. Bits come from
        bytes taken from the end, top bit first; the lowest set bit of each byte is a marker that says
        it is spent. The stream alternates literal runs (a flag, then a length in fields of 1, 2, 2, 3,
        8 and 15 bits, each all-ones field stepping to the next) and strings copied from what has
        already been written: a length from 2 to 1033 and an offset in one of three widths, or for a
        two-byte string one of two. One more bit after the image says whether a 32,000-byte picture
        was also reordered from bitplanes; music never sets it.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the entry branches</span><span class="rspan">unpacked, 0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="sndh-entry"></div>
        <p class="note">Usually <code>BRA.W</code>: 0x6000 and a signed 16-bit displacement from the
        address after the opcode. An entry with nothing to do can be a bare <code>RTS</code>.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the tags</span><span class="rspan">SNDH to HDNS . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Text tags are four letters and a NUL-terminated string:
        <code>TITL</code>, <code>COMM</code> (composer), <code>RIPP</code>, <code>CONV</code>,
        <code>YEAR</code>, and <code>FLAG</code>, a <code>~</code> and letters for what the player
        needs (y for the YM2149, e for the STe, a-d for the timers it takes over). Zero bytes may pad
        between tags. In this file the composer, ripper, converter and year sit between the title
        and the part drawn second.</p>
        <div id="sndh-tags"></div>
        <div class="kv">
          <div><span class="k">##nn</span><span class="v">subtune count, two ASCII digits, often followed by a NUL</span></div>
          <div><span class="k">!#nn</span><span class="v">default subtune; the specification writes #!, files write !#</span></div>
          <div><span class="k">TAnnn-TDnnn</span><span class="v">replay rate in Hz on MFP timer A-D, NUL-terminated; C is the default</span></div>
          <div><span class="k">!Vnn</span><span class="v">replay on the vertical blank at that rate</span></div>
          <div><span class="k">TIME</span><span class="v">a 16-bit length in seconds per subtune</span></div>
          <div><span class="k">FRMS</span><span class="v">a 32-bit length in frames per subtune, 0 for endless; replaces TIME</span></div>
          <div><span class="k">!#SN</span><span class="v">a 16-bit offset per subtune, from the tag's own first byte, to each subtune's NUL-terminated name</span></div>
          <div><span class="k">HDNS</span><span class="v">end of the header, on an even address</span></div>
        </div>
        <p class="note"><b>Before HDNS.</b> The end tag came with version 2 of the format. An older
        header just stops: the last tag's terminator, a byte of padding if needed, and the player's
        code at the next even address.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the player</span><span class="rspan">68000 code and data</span></summary>
      <div class="rbody">
        <p class="note">Everything after the header: the routine the three branches reach, its
        pattern and instrument data, and on STe or Falcon files the DMA samples. It is position
        independent or relocates itself, since it is loaded wherever the host player puts it.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">ICE! . SNDH</span></summary>
      <div class="rbody">
        <p class="note">On disk, <code>ICE!</code> at zero (Pack-Ice 2.3 and 2.4; earlier versions
        write <code>Ice!</code> and are not described here); inside, <code>SNDH</code> at offset 12,
        after the three entries.</p>
      </div>
    </details>

  <footer>
    <span>acidcat / sndh anatomy</span>
    <span>atari st . 68000 player . pack-ice</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = """
  // the 12-byte Pack-Ice header of a real file
  build("sndh-ice","byte",
    [0x49,0x43,0x45,0x21, 0x00,0x00,0x02,0xA5, 0x00,0x00,0x08,0x6A],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"ICE!\\"",body:"",note:""},
      {label:"packed size",k:"sync",r:[4,7],val:"677",body:"Big-endian, including this header: the whole file.",note:""},
      {label:"unpacked size",k:"enum",r:[8,11],val:"2,154",body:"The SNDH image. The stream must fill exactly this many bytes.",note:""}
    ]);

  // the unpacked image: three entries, SNDH, the title
  build("sndh-entry","byte",
    [0x60,0x00,0x00,0x68, 0x60,0x00,0x00,0x7E, 0x60,0x00,0x01,0x62, 0x53,0x4E,0x44,0x48,
     0x54,0x49,0x54,0x4C,0x53,0x75,0x70,0x65,0x72,0x20,0x48,0x75,0x65,0x79,0x00],
    [
      {label:"init",k:"sync",r:[0,3],val:"BRA.W to 0x6A",body:"2 + 0x68. Called once with the subtune in d0.",note:""},
      {label:"exit",k:"sync",r:[4,7],val:"BRA.W to 0x84",body:"6 + 0x7E. Silences the chip and restores what init took.",note:""},
      {label:"play",k:"sync",r:[8,11],val:"BRA.W to 0x16C",body:"10 + 0x162. Called at the replay rate.",note:""},
      {label:"SNDH",k:"sync",r:[12,15],val:"\\"SNDH\\"",body:"The header starts.",note:""},
      {label:"TITL",k:"enum",r:[16,30],val:"\\"Super Huey\\"",body:"Tag and NUL-terminated text.",note:""}
    ]);

  // the end of the tags, from ##
  build("sndh-tags","byte",
    [0x23,0x23,0x30,0x31, 0x00, 0x21,0x23,0x30,0x31, 0x00, 0x21,0x56,0x35,0x30,0x00, 0x00,
     0x54,0x49,0x4D,0x45,0x00,0x4D, 0x48,0x44,0x4E,0x53],
    [
      {label:"##01",k:"enum",r:[0,3],val:"1 subtune",body:"Two ASCII digits.",note:""},
      {label:"padding",k:"rsv",r:[4,4],val:"0",body:"",note:""},
      {label:"!#01",k:"enum",r:[5,8],val:"default 1",body:"",note:""},
      {label:"padding",k:"rsv",r:[9,9],val:"0",body:"",note:""},
      {label:"!V50",k:"enum",r:[10,14],val:"VBL, 50 Hz",body:"Digits and a NUL.",note:""},
      {label:"padding",k:"rsv",r:[15,15],val:"0",body:"To an even address.",note:""},
      {label:"TIME",k:"enum",r:[16,21],val:"77 s = 1:17",body:"One 16-bit length per subtune.",note:""},
      {label:"HDNS",k:"sync",r:[22,25],val:"\\"HDNS\\"",body:"End of the header; the player starts two bytes on.",note:""}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / sndh anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / sndh anatomy</title>",
                  page, count=1, flags=re.S)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
