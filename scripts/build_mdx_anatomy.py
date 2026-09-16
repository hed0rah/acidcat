"""Generate docs/formats/mdx-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from a real tune and decodes to the stated
value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "mdx-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>MDX Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Sharp X68000</b>MXDRV . YM2151<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>none</b></div>
      <div>endian <b>big</b></div>
      <div>container <b>own</b></div>
      <div>samples <b>separate .PDX</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">An <b>.mdx</b> is a Music Macro Language score for the Sharp X68000, played by
    MXDRV. It drives the <b>YM2151</b> FM chip, and where a tune uses ADPCM samples those live in a
    <b>separate <a href="pdx-anatomy.html">.PDX file</a></b> which the MDX names. The format has <b>no magic number</b>: a file
    opens with its title, so identification is arithmetic rather than a signature.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">nothing is at a fixed offset</div>
  <p class="note">The title and the sample-bank name are both variable length and neither declares
  its size, so every structure after them moves per file. The offset table's position is whatever
  those two happen to leave, and every offset inside it is measured from <b>the table's own first
  word</b> rather than from the start of the file. Read them as absolute and a tune points nowhere
  useful, by an amount that grows with the length of its title.</p>

  <div class="sec">header, through the offset table</div>
  <p class="note">A complete header: a Shift-JIS title, the three-byte terminator, a sample-bank
  name, then the table. Forty-seven bytes here; a different title makes it a different length.</p>
  <div id="mdx-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the offset table</span><span class="rspan">20 bytes . big-endian</span></summary>
      <div class="rbody">
        <p class="note">One word for the voice block, then one per channel. Every value is relative
        to the position of the <b>voiceOffset</b> word.</p>
        <div id="mdx-table"></div>
        <p class="note">The channel count is <b>not stored anywhere</b>. It is recovered from the
        table's own shape: the table runs right up to whatever is written after it, so the
        <b>smallest offset in the table</b> IS the table's size, and</p>
        <div class="kv">
          <div><span class="k">channels</span><span class="v">(smallest offset &minus; 2) / 2</span></div>
          <div><span class="k">here</span><span class="v">(0x0014 &minus; 2) / 2 = 9</span></div>
          <div><span class="k-en">only two values occur</span><span class="v">9 or 16; anything else is not an MDX</span></div>
        </div>
        <p class="note">That smallest offset is usually channel A's, because the channel streams
        are usually written first and the voice block last. It is a convention and not a rule:
        some tunes put the <b>voice block first</b>, and in those the voiceOffset is the table's
        size and channel A's offset points well past it. A reader that assumes channel A always
        comes first gets a channel count in the hundreds for those files and concludes they are
        not MDX at all.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">channels</span><span class="rspan">9 or 16</span></summary>
      <div class="rbody">
        <p class="note">Channels are lettered, not numbered, and the letters are not contiguous.</p>
        <div class="kv">
          <div><span class="k">A &ndash; H</span><span class="v">the eight YM2151 FM voices</span></div>
          <div><span class="k">P</span><span class="v">ADPCM, played from the .PDX sample bank</span></div>
          <div><span class="k">Q &ndash; W</span><span class="v">the extra voices a Mercury Unit expansion board provides</span></div>
          <div><span class="k-en">nine channels</span><span class="v">a stock machine: A through H, plus P</span></div>
          <div><span class="k-en">sixteen channels</span><span class="v">a Mercury Unit is fitted</span></div>
        </div>
        <p class="note">A channel that carries no music still has an entry. Its offset points at
        two bytes, which is how an empty stream is written rather than omitted.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the title, and why it is not a header</span><span class="rspan">0x00 . variable</span></summary>
      <div class="rbody">
        <p class="note">The file begins with text, in <b>Shift-JIS</b>, terminated by
        <code>0D 0A 1A</code>. That sequence is carriage return, line feed, and the DOS end-of-file
        character, so a tune printed to a console shows its title and stops.</p>
        <p class="note">There is no signature before it and no length in front of it. Identifying
        an MDX means finding the terminator, then a NUL-terminated name, then reading the table and
        checking that it resolves to a legal channel count with every offset inside the file. The
        arithmetic IS the identification.</p>
        <p class="note">One class of file passes the first half and fails the second on purpose.
        Tunes were distributed <b>packed</b> with the X68000 compressors of the day, and the
        packers compressed the file from the offset table onward while leaving the title and the
        sample-bank name readable. The result is a module whose text reads perfectly and whose
        table is compressor output, so the channel count resolves to nonsense. The compressor
        stamps its name and version a few bytes into the packed stream --
        <code>LZX</code>, <code>ZOO</code>, <code>LHA</code> or <code>LZS</code> -- which is
        what separates a packed module from a file that is simply not an MDX.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the sample bank</span><span class="rspan">after the terminator</span></summary>
      <div class="rbody">
        <p class="note">A NUL-terminated Human68k filename naming a <b>.PDX</b> file that holds the
        ADPCM samples. A bare NUL means the tune is FM only. The extension is usually left off the
        name, as it is here.</p>
        <p class="note">A tune referencing a bank is two files, and the MDX carries no copy of the
        samples. Separated from its .PDX, channel P has nothing to play.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">a voice</span><span class="rspan">27 bytes</span></summary>
      <div class="rbody">
        <p class="note">Voices are YM2151 register values, not an abstraction over them, so a voice
        is literally what gets written to the chip. Each four-byte field is the four operators in
        the order the chip numbers them.</p>
        <div id="mdx-voice"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">feedback and connection, bit by bit</span><span class="rspan">1 byte</span></summary>
      <div class="rbody">
        <p class="note">The second byte of a voice packs two fields and leaves the top two bits
        clear.</p>
        <div id="mdx-flcon"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">big-endian</span></summary>
      <div class="rbody">
        <p class="note">Every multi-byte value is big-endian, which for once follows the machine
        rather than fighting it: the X68000 is a 68000. The MML command streams the offsets point
        at are byte-oriented and carry no multi-byte words of their own.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / mdx anatomy</span>
    <span>sharp x68000 . mxdrv . ym2151 . no magic number</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = r"""  // a complete header: Shift-JIS title, terminator, sample bank, offset table
  build("mdx-head","byte",
    [0x82,0x73,0x61,0x62,0x69,0x64,0x61,0x63,0x68,0x69,0x20,0x2D,0x20,0x47,0x4F,0x46,0x45,0x52,0x20,0x2D,
     0x0D,0x0A,0x1A,
     0x67,0x6F,0x66,0x00,
     0x08,0x27,
     0x00,0x14, 0x01,0x30, 0x02,0x4F, 0x03,0x4C, 0x04,0x50, 0x05,0x03, 0x05,0xAE, 0x06,0x5C, 0x07,0x0A],
    [
      {label:"title",k:"enum",r:[0,19],val:"Shift-JIS text",
       body:"The file opens with its title and nothing else. No signature, no length prefix. 82 73 is a full-width capital T in Shift-JIS, which is why the first two bytes do not read as ASCII.",
       note:"variable length, and it is what moves everything after it."},
      {label:"terminator",k:"sync",r:[20,22],val:"0D 0A 1A",
       body:"Carriage return, line feed, and the DOS end-of-file character. Printing the file to a console shows the title and stops.",
       note:"the first fixed thing in the file, and its position is not."},
      {label:"pdxName",k:"enum",r:[23,26],val:'\'"gof"\'',sel:0,branch:[
        ["name","samples come from that .PDX"],["00 alone","FM only, no sample bank"]],
       body:"A NUL-terminated Human68k filename naming the ADPCM sample bank. The extension is usually omitted. A bare NUL means the tune uses no samples.",
       note:"67 6F 66 00."},
      {label:"voiceOffset",k:"sync",r:[27,28],val:"0x0827",
       body:"Where the voice definitions begin, measured from THIS field's own position. Absolute position here is 0x1B + 0x827 = 0x842.",
       note:"the base every other offset is measured from, including itself."},
      {label:"channel A",k:"enum",r:[29,30],val:"0x0014",
       body:"The first channel's offset. Here it is the smallest value in the table, so it is the table's size and gives away the channel count: (0x14 - 2) / 2 = 9 channels. In a tune that writes its voice block first, voiceOffset would be the smaller of the two instead.",
       note:"this single word is how the count is recovered."},
      {label:"channels B-P",k:"sync",r:[31,46],val:"8 more words",
       body:"One big-endian word per remaining channel, in order B C D E F G H P. Every one is relative to the voiceOffset field.",
       note:"nine channels total, so eighteen bytes of table."}
    ]);

  // the table on its own
  build("mdx-table","byte",
    [0x08,0x27, 0x00,0x14, 0x01,0x30, 0x02,0x4F, 0x03,0x4C, 0x04,0x50, 0x05,0x03, 0x05,0xAE, 0x06,0x5C, 0x07,0x0A],
    [
      {label:"voiceOffset",k:"sync",r:[0,1],val:"0x0827",
       body:"The voice block. Usually after the channel data, but not always -- when it comes first, this word rather than channel A's is the one that gives the table's size.",
       note:"the origin for every offset here, including this one."},
      {label:"channel A",k:"enum",r:[2,3],val:"0x0014 = 20",
       body:"Equal to the table's own size: 2 bytes for voiceOffset plus 9 channel words. That is what makes the channel count recoverable.",
       note:"(20 - 2) / 2 = 9."},
      {label:"channel B",k:"sync",r:[4,5],val:"0x0130",body:"FM channel B.",note:""},
      {label:"channel C",k:"sync",r:[6,7],val:"0x024F",body:"FM channel C.",note:""},
      {label:"channel D",k:"sync",r:[8,9],val:"0x034C",body:"FM channel D.",note:""},
      {label:"channel E",k:"sync",r:[10,11],val:"0x0450",body:"FM channel E.",note:""},
      {label:"channel F",k:"sync",r:[12,13],val:"0x0503",body:"FM channel F.",note:""},
      {label:"channel G",k:"sync",r:[14,15],val:"0x05AE",body:"FM channel G.",note:""},
      {label:"channel H",k:"sync",r:[16,17],val:"0x065C",
       body:"The eighth and last FM channel. A YM2151 has exactly eight.",note:""},
      {label:"channel P",k:"enum",r:[18,19],val:"0x070A",
       body:"The ADPCM channel, played from the .PDX sample bank rather than from the FM chip. In a sixteen-channel file this is followed by Q through W for the Mercury Unit.",
       note:"P, not I: the letters skip."}
    ]);

  // one voice, 27 bytes of YM2151 registers
  build("mdx-voice","byte",
    [0x0B, 0x24, 0x0F,
     0x3F,0x05,0x31,0x02,
     0x27,0x3A,0x08,0x08,
     0x1F,0x1F,0x58,0x9F,
     0x13,0x0E,0x91,0x91,
     0x04,0x05,0x08,0x08,
     0x55,0x62,0x05,0x05],
    [
      {label:"voice number",k:"sync",r:[0,0],val:"11",
       body:"Which voice this is. The MML stream selects a voice by this number, so the block is not required to be in order or contiguous.",
       note:"not an index into the block."},
      {label:"FL / CON",k:"enum",r:[1,1],val:"0x24",
       body:"Feedback and connection algorithm packed into one byte, drawn bit by bit below. The top two bits are unused and set to 0.",
       note:"FL 4, CON 4."},
      {label:"slot mask",k:"flag",r:[2,2],val:"0x0F",sel:0,branch:[
        ["0x0F","all four operators enabled"],["other","a subset"]],
       body:"Which of the four operators are active. The high nibble is unused.",
       note:"0x0F is every operator."},
      {label:"DT1 / MUL",k:"sync",r:[3,6],val:"3F 05 31 02",
       body:"Detune 1 and frequency multiplier, one byte per operator. The four bytes are the operators in the order the chip numbers them.",
       note:"x4, as every field below."},
      {label:"TL",k:"enum",r:[7,10],val:"27 3A 08 08",
       body:"Total level: the output attenuation of each operator, and therefore what actually sets the voice's balance. Larger is quieter.",
       note:"7 bits used, top bit clear."},
      {label:"KS / AR",k:"sync",r:[11,14],val:"1F 1F 58 9F",
       body:"Key scaling in the top two bits, attack rate in the low five.",note:""},
      {label:"AME / D1R",k:"sync",r:[15,18],val:"13 0E 91 91",
       body:"Amplitude modulation enable in the top bit, first decay rate in the low five.",note:""},
      {label:"DT2 / D2R",k:"sync",r:[19,22],val:"04 05 08 08",
       body:"Detune 2 in the top two bits, second decay rate in the low five.",note:""},
      {label:"D1L / RR",k:"enum",r:[23,26],val:"55 62 05 05",
       body:"First decay level in the high nibble, release rate in the low nibble. The last field of the record; the next voice begins immediately after.",
       note:"27 bytes exactly, no padding."}
    ]);

  // the FL/CON byte, MSB first
  build("mdx-flcon","bit",[0x24],[
    {label:"unused",k:"rsv",r:[0,1],val:"0",
     body:"The top two bits of the byte. Set to 0.",
     note:"two bits, always clear."},
    {label:"FL",k:"enum",r:[2,4],val:"4",sel:4,branch:[
      ["0","no feedback"],["1-7","increasing self-modulation of operator 1"]],
     body:"Feedback: how much operator 1 modulates itself. Three bits, so eight levels.",
     note:"100 = 4."},
    {label:"CON",k:"enum",r:[5,7],val:"4",sel:4,branch:[
      ["0","one chain of four"],["4","two parallel pairs"],["7","four in parallel"]],
     body:"The connection algorithm: how the four operators are wired to each other. The YM2151 has eight, from a single serial chain through to four independent carriers.",
     note:"100 = 4, two parallel pairs."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / mdx anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / mdx anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
