"""Generate docs/formats/pdx-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from a real bank and decodes to the stated
value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "pdx-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>PDX Anatomy</h1></div></div>
      <div class="stamp"><b>Sharp X68000</b>MXDRV . MSM6258V<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>none</b></div>
      <div>endian <b>big</b></div>
      <div>container <b>own</b></div>
      <div>played by <b>an .MDX</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.PDX</b> is the ADPCM sample bank a Sharp X68000 tune plays its percussion
    and voice hits from. An <a href="mdx-anatomy.html">.MDX</a> score carries no samples at all: it
    names a bank, and every hit lives in that separate file. The format is a <b>pointer table and
    nothing else</b> -- no magic, no version, no count, no names -- so like MDX,
    identifying one is arithmetic.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the row number is the sample number</div>
  <p class="note">Every other part of the format follows from this. A slot is eight bytes at a fixed
  row, and the <b>row index is the sample's identity</b>: MML says a number and that is the row it
  reads. A bank whose only sample sits at row 33 carries ninety-five empty rows in front of it, and
  those rows are not waste to be tidied away -- removing one renumbers everything after it and
  silently retunes every tune that plays the bank.</p>

  <div class="sec">a bank table, its first four rows</div>
  <p class="note">Big-endian offset and length, eight bytes per row, ninety-six rows. An unused row
  is eight zero bytes. Sample data begins immediately after the last row.</p>
  <div id="pdx-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the slot table</span><span class="rspan">768 bytes per bank . big-endian</span></summary>
      <div class="rbody">
        <p class="note">Ninety-six rows of eight bytes is one <b>bank</b>, 768 bytes. Offsets are
        absolute file positions, not relative to anything -- the one place this format is
        simpler than the MDX that references it.</p>
        <div class="kv">
          <div><span class="k">row</span><span class="v">offset u32 BE, length u32 BE</span></div>
          <div><span class="k">one bank</span><span class="v">96 &times; 8 = 768 bytes</span></div>
          <div><span class="k">unused row</span><span class="v">eight zero bytes</span></div>
          <div><span class="k-en">length is in BYTES</span><span class="v">not in audio samples; see below</span></div>
        </div>
        <p class="note">Nothing in the file states how many banks there are. A bank holding more
        than ninety-six samples simply repeats the table, and the sample data starts after the last
        one, so the count is recovered the same way MDX recovers its channel count:</p>
        <div class="kv">
          <div><span class="k">table size</span><span class="v">the smallest offset in the table</span></div>
          <div><span class="k">banks</span><span class="v">table size / 768</span></div>
          <div><span class="k-en">the constraint</span><span class="v">the first sample must begin exactly where the table ends</span></div>
        </div>
        <p class="note">An offset that is not a whole number of banks past zero describes no table
        at all, which is what makes the arithmetic an identification rather than a guess.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">two rows, one sample</span><span class="rspan">aliasing</span></summary>
      <div class="rbody">
        <p class="note">Rows may hold the <b>same offset and the same length</b>. That is a bank
        mapping one hit to several numbers so a part can play it without changing sample, and it is
        the normal case rather than a defect.</p>
        <p class="note">What does <b>not</b> happen is a row pointing part-way into another row's
        sample. Sharing is whole or not at all; a bank aliases, it does not slice. Anything reading
        a bank has to count distinct <b>regions</b>, not filled rows, or a shared hit is claimed
        twice and the file measures larger than it is.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the samples</span><span class="rspan">MSM6258V ADPCM</span></summary>
      <div class="rbody">
        <p class="note">The sample data is OKI <b>MSM6258V</b> ADPCM, which is the chip the X68000
        has. Four bits per sample, one nibble per step, so a row's length in bytes is
        <b>twice</b> the number of audio samples it addresses.</p>
        <div class="kv">
          <div><span class="k">coding</span><span class="v">OKI ADPCM, 4 bits per sample</span></div>
          <div><span class="k">audio samples</span><span class="v">length &times; 2</span></div>
          <div><span class="k-en">sample rate</span><span class="v">not in the file; the player sets it</span></div>
          <div><span class="k-en">channels</span><span class="v">not in the file; mono in practice</span></div>
          <div><span class="k">nibble order</span><span class="v">low nibble of each byte first</span></div>
          <div><span class="k">step table</span><span class="v">49 entries, 16 to 1552; 12-bit output</span></div>
          <div><span class="k">step delta</span><span class="v">step/8 + step/4&middot;b0 + step/2&middot;b1 + step&middot;b2, each term truncated</span></div>
          <div><span class="k">default rate</span><span class="v">15.6 kHz (the chip at 8 MHz / 512); 3.9, 5.2, 7.8, 10.4 also selectable</span></div>
        </div>
        <p class="note"><b>The delta is the datasheet's, not a shortcut.</b> Some decoders compute
        the delta as <code>((2&middot;d + 1) &middot; step) &gt;&gt; 3</code>, which is the same thing
        up to rounding. The predictor integrates, so the rounding is not the same thing: an X68000
        encoder modelled the chip, and only the per-term form brings a recorded hit back to silence
        at its end. The other drifts by hundreds of units per sample.</p>
        <p class="note">There is no rate, no loop point, no root key and no name. A bank is an
        addressable pile of nibbles, and everything about how a sample is meant to sound lives in
        the MDX that plays it.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">one row, in full</span><span class="rspan">8 bytes</span></summary>
      <div class="rbody">
        <p class="note">A filled row. Both values are unsigned and big-endian, and the offset is
        measured from the start of the file.</p>
        <div id="pdx-slot"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification, without a signature</span><span class="rspan">no magic</span></summary>
      <div class="rbody">
        <p class="note">There is no magic number and no header before the table, so the table's own
        shape is the only claim the file makes about itself. Recognising one means reading
        ninety-six rows, discarding the empty ones, checking every remaining row lands inside the
        file, and checking the smallest offset is a whole number of banks.</p>
        <p class="note">Banks were also distributed <b>packed</b> with the X68000 compressors of
        the day, and unlike a packed MDX nothing survives: a PDX is all table, so a compressed one
        has no readable text to fall back on. The compressor stamps its name a few bytes in
        -- <code>LZX</code>, <code>ZOO</code>, <code>LHA</code> or <code>LZS</code> --
        which names what was done to the file but proves nothing about what is underneath it.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">big-endian</span></summary>
      <div class="rbody">
        <p class="note">Both words in every row are big-endian, following the 68000 the machine is
        built around. The sample data itself is nibbles and has no byte order to get wrong.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / pdx anatomy</span>
    <span>sharp x68000 . mxdrv . msm6258v adpcm . no magic number</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the first four rows of a real bank: one empty, then three filled
  build("pdx-head","byte",
    [0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
     0x00,0x00,0x03,0x00, 0x00,0x00,0x0F,0x20,
     0x00,0x00,0x12,0x20, 0x00,0x00,0x03,0x60,
     0x00,0x00,0x15,0x80, 0x00,0x00,0x24,0x54],
    [
      {label:"slot 0",k:"rsv",r:[0,7],val:"empty",
       body:"Eight zero bytes. Sample number 0 is unused in this bank, and the row stays because the row index IS the sample number -- closing the gap would renumber every sample after it.",
       note:"an empty row is not a missing row."},
      {label:"slot 1 offset",k:"sync",r:[8,11],val:"0x00000300 = 768",
       body:"An absolute file position, not a relative one. 768 is exactly the size of one 96-row bank, which is what says this file has a single bank and no more.",
       note:"the smallest offset in the table gives the table's size."},
      {label:"slot 1 length",k:"enum",r:[12,15],val:"0x00000F20 = 3,872",
       body:"Bytes, not audio samples. MSM6258V ADPCM packs one sample per nibble, so this row addresses 7,744 samples.",
       note:"length x 2 = audio samples."},
      {label:"slot 2",k:"sync",r:[16,23],val:"0x1220, 0x0360",
       body:"Offset 4,640 and length 864. 768 + 3,872 = 4,640: this sample begins exactly where the previous one ended, which is how a bank is normally laid out even though nothing requires it.",
       note:"samples run back to back through the file."},
      {label:"slot 3",k:"sync",r:[24,31],val:"0x1580, 0x2454",
       body:"Offset 5,504 and length 9,300. Again contiguous: 4,640 + 864 = 5,504.",
       note:"sample number 3, 9,300 bytes."}
    ]);

  // one row on its own
  build("pdx-slot","byte",
    [0x00,0x00,0x03,0x00, 0x00,0x00,0x0F,0x20],
    [
      {label:"offset",k:"sync",r:[0,3],val:"0x00000300 = 768",
       body:"Where the sample begins, measured from the start of the file. Two rows carrying the same offset and length are the same sample under two numbers, which banks do routinely.",
       note:"absolute, unsigned, big-endian."},
      {label:"length",k:"enum",r:[4,7],val:"0x00000F20 = 3,872",
       body:"How many bytes of ADPCM the sample occupies. There is no rate, no loop point and no root key anywhere in the file, so this length and its offset are the entire description of a sample.",
       note:"bytes; halve nothing, double for sample count."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / pdx anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / pdx anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
