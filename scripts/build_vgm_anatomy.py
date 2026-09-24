"""Generate docs/formats/vgm-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from one real file (a Master System tune,
VGM 1.01, one SN76489) and decodes to the stated value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "vgm-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>VGM Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Video Game Music</b>a sound-chip register log<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>Vgm </b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own; .vgz is gzip</b></div>
      <div>samples <b>data blocks, when a chip has RAM</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.vgm</b> is neither a score nor a recording. It is every byte a game wrote to
    its sound chips, in order, with the time between writes, so that a player holding the same chips
    (emulated) plays it back exactly. Three parts: a <b>header</b> of chip clocks, where a non-zero
    clock is the only thing that says a chip is present; a <b>command stream</b>, one opcode per
    write or wait; and a <b>GD3 tag</b> at the end, in UTF-16, saying what the tune is. The format
    has grown a chip at a time since 2001 and the version byte says how much of the header exists.
    A <b>.vgz</b> is the same file inside gzip and nothing more.</p>
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
        <p class="note">Every offset in the header is <b>relative to its own field</b>: the end-of-file
        value is added to 0x04, the GD3 offset to 0x14, the loop point to 0x1C, the stream offset to
        0x34. A reader that adds them to zero lands 4 to 52 bytes early, every time.</p>
        <div id="vgm-head"></div>
        <p class="note"><b>The version decides what the header holds.</b> 1.00 has the two clocks
        drawn above and nothing else; 1.10 adds YM2612 and YM2151 at 0x2C; 1.50 adds the stream
        offset at 0x34 (before it, the stream begins at 0x40); 1.51 adds a page of chip clocks from
        0x38 to 0x7F; 1.61 another from 0x80 to 0xB7; 1.71 more to 0xE3. A field the file's
        version predates is not a field, whatever bytes are there. From 1.51 the top bit of a
        clock means <i>two</i> of that chip.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the command stream</span><span class="rspan">a byte code . drawn below</span></summary>
      <div class="rbody">
        <p class="note">One opcode, then its operands. A chip write names the chip by its opcode and
        carries the register and value; a wait carries a sample count at 44,100 Hz. The two most
        common waits have their own one-byte opcodes, <code>0x62</code> for one NTSC frame (735
        samples) and <code>0x63</code> for one PAL frame (882), and <code>0x70-0x7F</code> wait
        1 to 16 samples in the low nibble.</p>
        <div id="vgm-cmds"></div>
        <div class="kv">
          <div><span class="k">0x50 dd</span><span class="v">SN76489 write</span></div>
          <div><span class="k">0x51-0x5F aa dd</span><span class="v">a Yamaha chip write: 0x52/0x53 YM2612 port 0/1, 0x54 YM2151, 0x5A YM3812 ...</span></div>
          <div><span class="k">0x61 nn nn</span><span class="v">wait n samples</span></div>
          <div><span class="k">0x62 . 0x63</span><span class="v">wait 735 . wait 882</span></div>
          <div><span class="k">0x66</span><span class="v">end of stream</span></div>
          <div><span class="k">0x67 66 tt ss ss ss ss</span><span class="v">data block: type, size, then that many bytes of PCM or ROM for a chip's memory</span></div>
          <div><span class="k">0x70-0x7F</span><span class="v">wait n+1</span></div>
          <div><span class="k">0x80-0x8F</span><span class="v">YM2612 DAC write from the data block, then wait n</span></div>
          <div><span class="k">0xA0-0xBF aa dd</span><span class="v">two-operand writes: AY8910, RF5C68, Game Boy, NES APU, OKIM6258 ...</span></div>
          <div><span class="k">0xC0-0xDF</span><span class="v">three operands: SegaPCM, QSound, YMF278B ...</span></div>
          <div><span class="k">0xE0-0xFF</span><span class="v">four operands: PCM seek, C352 ...</span></div>
        </div>
        <p class="note"><b>Reserved ranges have lengths.</b> The spec fixes the operand count of
        every opcode it has not yet assigned (0x30-0x3F one, 0x40-0x4E two, 0xC0-0xDF three,
        0xE0-0xFF four) so that an old reader can skip a command a newer file uses. A stream can
        therefore always be walked to its <code>0x66</code>, and in a well-formed file that lands
        exactly on the GD3 tag.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">data blocks</span><span class="rspan">0x67 . samples</span></summary>
      <div class="rbody">
        <p class="note">Chips with sample memory (the YM2612's DAC stream, the RF5C68's RAM, an
        OKI's ROM) get it from data blocks in the stream, each typed by a byte: 0x00-0x3F are
        uncompressed streams, 0x40-0x7E compressed, 0x80-0xBF ROM images, 0xC0-0xDF RAM writes.
        A block is the one part of a VGM that is audio rather than instructions, and the one part
        a reader can lift out.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the GD3 tag</span><span class="rspan">Gd3 . UTF-16 . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Four bytes of magic, a version, a byte length, then eleven strings each ended
        by a 16-bit NUL: title, title in Japanese, game, game in Japanese, system, system in
        Japanese, author, author in Japanese, release date, who made the file, notes. An empty
        string is a lone NUL. The tag is the last thing in the file.</p>
        <div id="vgm-gd3"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">Vgm . or gzip around it</span></summary>
      <div class="rbody">
        <p class="note">Four bytes at zero, with a trailing space. A .vgz is a gzip stream whose
        first inflated bytes are the same four; the extension says nothing and a reader confirms
        what is inside.</p>
      </div>
    </details>

  <footer>
    <span>acidcat / vgm anatomy</span>
    <span>video game music . register log . chip clocks . gd3</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the 64-byte header of a real 1.01 file, one SN76489
  build("vgm-head","byte",
    [0x56,0x67,0x6D,0x20, 0x1C,0x29,0x00,0x00, 0x01,0x01,0x00,0x00, 0x94,0x9E,0x36,0x00,
     0x00,0x00,0x00,0x00, 0x12,0x28,0x00,0x00, 0x25,0x07,0x22,0x00, 0x2F,0x03,0x00,0x00,
     0x64,0x27,0x1A,0x00, 0x3C,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
     0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"Vgm \\"",
       body:"Four bytes, the last a space.",
       note:"56 67 6D 20."},
      {label:"eof offset",k:"sync",r:[4,7],val:"0x291C -> file ends at 10,528",
       body:"Relative to this field: 0x291C + 4 = 10,528, and the file is 10,528 bytes. The first of four self-relative offsets.",
       note:"add 4."},
      {label:"version",k:"enum",r:[8,11],val:"0x00000101 = 1.01",
       body:"Binary-coded decimal. 1.01 means the header ends at 0x40 and only the two clocks below exist.",
       note:"0x151 is 1.51, not 337."},
      {label:"SN76489 clock",k:"enum",r:[12,15],val:"3,579,540 Hz",
       body:"Non-zero, so the tune uses this chip: the Master System's PSG at its NTSC clock. Zero would mean no such chip.",
       note:"the only presence flag there is."},
      {label:"YM2413 clock",k:"enum",r:[16,19],val:"0",
       body:"Zero: no FM chip in this tune.",
       note:""},
      {label:"GD3 offset",k:"sync",r:[20,23],val:"0x2812 -> tag at 10,278",
       body:"Relative to this field: 0x2812 + 0x14 = 10,278, where the four bytes Gd3 begin.",
       note:"add 0x14."},
      {label:"total samples",k:"sync",r:[24,27],val:"2,230,053 = 50.6 s",
       body:"The length of the tune in samples at 44,100 Hz, which is what every wait in the stream adds up to.",
       note:"divide by 44,100."},
      {label:"loop offset",k:"sync",r:[28,31],val:"0x32F -> loops to 843",
       body:"Relative to this field: 0x32F + 0x1C = 843, a command in the stream. Zero would mean the tune does not loop.",
       note:"add 0x1C."},
      {label:"loop samples",k:"sync",r:[32,35],val:"1,714,020 = 38.9 s",
       body:"How much of the total is the loop.",
       note:""},
      {label:"rate",k:"enum",r:[36,39],val:"60 Hz",
       body:"The frame rate of the machine the log was taken from, so a player can slow a 60 Hz tune to 50. Added in 1.01.",
       note:"0 = unspecified."},
      {label:"reserved",k:"rsv",r:[40,63],val:"0",
       body:"In a 1.01 file the rest of the 64-byte header is zero and the stream starts at 0x40. Later versions put chip clocks here: YM2612 and YM2151 at 0x2C from 1.10, the stream offset at 0x34 from 1.50.",
       note:"not fields in this version."}
    ]);

  // the first 26 bytes of the stream: eleven PSG writes and a wait
  build("vgm-cmds","byte",
    [0x50,0x8F, 0x50,0x08, 0x50,0xA5, 0x50,0x0A, 0x50,0xCB, 0x50,0x15, 0x50,0xE4, 0x50,0x9F, 0x50,0xBF, 0x50,0xDF, 0x50,0xFF, 0x61,0x3F,0x00, 0x50],
    [
      {label:"PSG write",k:"sync",r:[0,1],val:"0x50 0x8F",
       body:"Opcode 0x50: one byte to the SN76489. 0x8F is a latch/data byte: channel 0, tone, low four bits of the period.",
       note:"one operand."},
      {label:"PSG write",k:"sync",r:[2,3],val:"0x50 0x08",
       body:"The high six bits of the same period. The chip takes a 10-bit tone in two writes.",
       note:""},
      {label:"PSG writes",k:"enum",r:[4,13],val:"channels 1 and 2",
       body:"Two more tone pairs, and a noise-channel byte. Every write is two bytes here; the stream is mostly this.",
       note:""},
      {label:"volumes",k:"enum",r:[14,21],val:"0x9F 0xBF 0xDF 0xFF",
       body:"Attenuation writes for the four channels: 0x9F silences channel 0, 0xFF the noise channel. A tune opens by setting everything.",
       note:"0xF = silent."},
      {label:"wait",k:"sync",r:[22,24],val:"0x61 63 samples",
       body:"Opcode 0x61 with a 16-bit sample count: 0x003F = 63 samples, 1.4 ms. The waits are the rhythm; everything else is instantaneous.",
       note:"little-endian count."},
      {label:"next",k:"sync",r:[25,25],val:"0x50",
       body:"The next write. The stream continues like this to the 0x66 at byte 10,277, one byte before the tag.",
       note:""}
    ]);

  // the tag header and its first string
  build("vgm-gd3","byte",
    [0x47,0x64,0x33,0x20, 0x00,0x01,0x00,0x00, 0xEE,0x00,0x00,0x00,
     0x4F,0x00,0x76,0x00,0x65,0x00,0x72,0x00,0x77,0x00,0x6F,0x00,0x72,0x00,0x6C,0x00,0x64,0x00,0x20,0x00,0x35,0x00,0x00,0x00, 0x00,0x00],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"Gd3 \\"",
       body:"Where the header's GD3 offset lands.",
       note:"47 64 33 20."},
      {label:"version",k:"enum",r:[4,7],val:"0x00000100",
       body:"1.00, the only GD3 version.",
       note:""},
      {label:"length",k:"sync",r:[8,11],val:"238 bytes",
       body:"Bytes of string data that follow. The tag ends at the file's end.",
       note:""},
      {label:"title",k:"enum",r:[12,35],val:"\\"Overworld 5\\"",
       body:"UTF-16LE, ended by a 16-bit NUL. The first of eleven strings.",
       note:"two bytes per character."},
      {label:"title (JP)",k:"rsv",r:[36,37],val:"empty",
       body:"A lone NUL: no Japanese title. The game name follows.",
       note:""}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / vgm anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / vgm anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
