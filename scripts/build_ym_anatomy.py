"""Generate docs/formats/ym-anatomy.html.

The page shell is lifted from an existing anatomy page so every page stays
identical below the content. Every byte in the maps is from one real file
(an LHA-packed YM6 of 1,503 bytes that unpacks to 140,155) and decodes to
the stated value; the register bytes are from the unpacked image.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "ym-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>YM Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>ST-Sound</b>YM2149 register dump<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>YM2! to YM6!</b></div>
      <div>endian <b>big</b></div>
      <div>container <b>LHA level 0, -lh5-</b></div>
      <div>samples <b>digidrums, 8-bit</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.ym</b> holds no code and no score: it is what a player routine wrote to the
    Yamaha YM2149 (the Atari ST's sound chip, and the AY-3-8910's twin in the Spectrum and the CPC),
    one frame of registers per tick, usually fifty a second. Sixteen bytes a frame is a lot, so the
    registers are stored <b>interleaved</b> -- every frame's r0, then every frame's r1 -- which turns
    slow-changing registers into long runs, and the file is packed in an LHA archive. What sits on
    disk is the archive; the tune is inside it.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
        <div class="row"><span class="sw dark k-flag">sand</span><span class="swsep">&#8594;</span><span class="sw light k-flag">flag</span></div>
        <div class="row"><span class="sw dark k-rsv">grey</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">reserved</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">file regions</div>

    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the LHA member header</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="ym-lha"></div>
        <p class="note"><b>Level 0 only.</b> ST-Sound reads only the oldest header layout, so a YM
        is archived with it. The header's own checksum is the byte sum of everything after
        the first two bytes. The body that follows is <b>-lh5-</b>: LZSS over an 8 KB window with
        Huffman codes rebuilt every block, bits read most significant first. A block opens with its
        symbol count and three code-length tables -- a small one that codes the next, the 510
        literal-and-length symbols (256 bytes and match lengths 3 to 256), and 14 position classes --
        and a position class <i>p</i> above 1 is followed by <i>p</i>-1 raw bits. The CRC-16 (the
        ARC one: polynomial 0x8005, reflected, starting at zero) is of the unpacked tune. A single zero byte ends the
        archive.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the YM6 header</span><span class="rspan">unpacked, 0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="ym-head"></div>
        <p class="note"><b>YM5 and YM6</b> share this header. After it come the digidrums, each a
        32-bit size and that many 8-bit samples, then the song name, author and comment as
        NUL-terminated strings, then the register data, then <code>End!</code>.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the strings</span><span class="rspan">NUL-terminated . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Name, author, comment, in that order, each ending in a zero. An empty one is
        the zero alone. This file's comment, not drawn, names who converted it.</p>
        <div id="ym-str"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the registers</span><span class="rspan">16 x frames . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Sixteen registers a frame. r0-r13 are the chip's, with the bits it does not
        use cleared; r14 and r15 carry YM6's special effects (SID voice, digidrum, sync buzzer), whose
        parameters also ride in the chip registers' spare bits. Interleaved, register <i>k</i> of
        frame <i>f</i> is at <i>k</i> x frames + <i>f</i>. Drawn: the first twelve frames of r8,
        voice A's volume.</p>
        <div class="kv">
          <div><span class="k">r0-r5</span><span class="v">tone periods A, B, C: 8 bits fine, 4 coarse</span></div>
          <div><span class="k">r6</span><span class="v">noise period, 5 bits</span></div>
          <div><span class="k">r7</span><span class="v">mixer: tone off A-C (bits 0-2), noise off A-C (3-5)</span></div>
          <div><span class="k">r8-r10</span><span class="v">volumes A-C, 4 bits; bit 4 hands the voice to the envelope</span></div>
          <div><span class="k">r11-r12</span><span class="v">envelope period, fine and coarse</span></div>
          <div><span class="k">r13</span><span class="v">envelope shape, 4 bits; 0xFF means not written this frame, since a write restarts the envelope</span></div>
          <div><span class="k">r14-r15</span><span class="v">YM6 effect data</span></div>
        </div>
        <div id="ym-regs"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the end</span><span class="rspan">End! . drawn below</span></summary>
      <div class="rbody">
        <div id="ym-end"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the older versions</span><span class="rspan">YM2 . YM3 . YM3b . YM4</span></summary>
      <div class="rbody">
        <p class="note"><b>YM3!</b> is the magic and nothing else: fourteen registers a frame,
        interleaved, at 50 Hz on a 2 MHz chip, and the frame count is what fits in the file.
        <b>YM2!</b> is the same layout from the Mad Max player, whose drums came from samples built
        into the player rather than the file. <b>YM3b</b> adds a 32-bit loop frame as the last four
        bytes. <b>YM4!</b> is an intermediate version between YM3b and YM5 and is not drawn here.
        <b>MIX1</b>, <b>YMT1</b> and <b>YMT2</b> carry the same <code>LeOnArD!</code> but are
        ST-Sound's sample-remix and sample-tracker types, not register dumps.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">-lh5- . YMn!</span></summary>
      <div class="rbody">
        <p class="note">On disk, <code>-lh5-</code> at offset 2 of an LHA member; inside, one of
        <code>YM2!</code>, <code>YM3!</code>, <code>YM3b</code>, <code>YM4!</code>, <code>YM5!</code>,
        <code>YM6!</code> at zero, and YM4 and later follow it with <code>LeOnArD!</code>.</p>
      </div>
    </details>

  <footer>
    <span>acidcat / ym anatomy</span>
    <span>st-sound . yamaha ym2149 . register dump in lha</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = """
  // the 34-byte LHA level-0 member header of a real file
  build("ym-lha","byte",
    [0x20,0x8B, 0x2D,0x6C,0x68,0x35,0x2D, 0xBC,0x05,0x00,0x00, 0x7B,0x23,0x02,0x00, 0xED,0x72,0x7C,0x27,
     0x20, 0x00, 0x0A, 0x59,0x4D,0x5F,0x30,0x31,0x34,0x2E,0x42,0x49,0x4E, 0xCE,0x2C],
    [
      {label:"header size",k:"sync",r:[0,0],val:"32",body:"Bytes of header after these first two.",note:""},
      {label:"checksum",k:"sync",r:[1,1],val:"0x8B",body:"The low byte of the sum of the 32 header bytes that follow.",note:""},
      {label:"method",k:"enum",r:[2,6],val:"\\"-lh5-\\"",body:"LZSS, 8 KB window, static Huffman. -lh0- would be stored.",note:""},
      {label:"packed size",k:"sync",r:[7,10],val:"1,468",body:"Little-endian. The body after this header.",note:""},
      {label:"original size",k:"enum",r:[11,14],val:"140,155",body:"The unpacked YM.",note:""},
      {label:"time",k:"enum",r:[15,18],val:"1999-11-28 14:23:26",body:"MS-DOS time then date: 0x72ED, 0x277C.",note:""},
      {label:"attribute",k:"flag",r:[19,19],val:"0x20",body:"MS-DOS archive bit.",note:""},
      {label:"level",k:"enum",r:[20,20],val:"0",body:"The header layout.",note:""},
      {label:"name length",k:"sync",r:[21,21],val:"10",body:"",note:""},
      {label:"name",k:"enum",r:[22,31],val:"\\"YM_014.BIN\\"",body:"The member's name; players ignore it.",note:""},
      {label:"CRC-16",k:"enum",r:[32,33],val:"0x2CCE",body:"CRC-16/ARC of the 140,155 unpacked bytes.",note:""}
    ]);

  // the unpacked image: the YM6 header
  build("ym-head","byte",
    [0x59,0x4D,0x36,0x21, 0x4C,0x65,0x4F,0x6E,0x41,0x72,0x44,0x21, 0x00,0x00,0x22,0x33, 0x00,0x00,0x00,0x01,
     0x00,0x00, 0x00,0x1E,0x84,0x80, 0x00,0x32, 0x00,0x00,0x00,0x08, 0x00,0x00],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"YM6!\\"",body:"",note:""},
      {label:"check",k:"sync",r:[4,11],val:"\\"LeOnArD!\\"",body:"The format author's name, as a check string.",note:""},
      {label:"frames",k:"enum",r:[12,15],val:"8,755",body:"2:55 at 50 Hz.",note:""},
      {label:"attributes",k:"flag",r:[16,19],val:"0x00000001 = interleaved",body:"Bit 0: registers stored column by column. Bits 1 and 2 describe the digidrums' sample format.",note:""},
      {label:"digidrums",k:"enum",r:[20,21],val:"0",body:"",note:""},
      {label:"clock",k:"enum",r:[22,25],val:"2,000,000 Hz",body:"The Atari ST's YM2149. A Spectrum's is 1,773,400, a CPC's 1,000,000.",note:""},
      {label:"rate",k:"enum",r:[26,27],val:"50 Hz",body:"Frames per second.",note:""},
      {label:"loop frame",k:"enum",r:[28,31],val:"8",body:"Where playback returns after the last frame.",note:""},
      {label:"extra data",k:"rsv",r:[32,33],val:"0",body:"Bytes of future header to skip.",note:""}
    ]);

  // the strings: name, then an empty author
  build("ym-str","byte",
    [0x53,0x70,0x61,0x63,0x65,0x20,0x47,0x75,0x6E,0x00, 0x00],
    [
      {label:"name",k:"enum",r:[0,9],val:"\\"Space Gun\\"",body:"NUL-terminated.",note:""},
      {label:"author",k:"enum",r:[10,10],val:"empty",body:"Just the terminator.",note:""}
    ]);

  // r8, voice A's volume, frames 0-11
  build("ym-regs","byte",
    [0x00,0x00, 0x0C,0x0C, 0x0E,0x0E, 0x0F,0x0F, 0x0E,0x0E,0x0D,0x0D],
    [
      {label:"frames 0-1",k:"enum",r:[0,1],val:"0",body:"Silent.",note:""},
      {label:"frames 2-3",k:"enum",r:[2,3],val:"12",body:"The note starts.",note:""},
      {label:"frames 4-5",k:"enum",r:[4,5],val:"14",body:"",note:""},
      {label:"frames 6-7",k:"enum",r:[6,7],val:"15",body:"Full volume, a software attack over six frames.",note:""},
      {label:"frames 8-11",k:"enum",r:[8,11],val:"14, 14, 13, 13",body:"And a decay, two frames a step.",note:""}
    ]);

  // the last bytes of the image
  build("ym-end","byte",
    [0x00,0x00,0x00,0x00, 0x45,0x6E,0x64,0x21],
    [
      {label:"r15, last frames",k:"rsv",r:[0,3],val:"0",body:"The last register column ends here.",note:""},
      {label:"end",k:"sync",r:[4,7],val:"\\"End!\\"",body:"The end marker.",note:""}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / ym anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / ym anatomy</title>",
                  page, count=1, flags=re.S)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
