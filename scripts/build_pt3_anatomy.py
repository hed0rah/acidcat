"""Generate docs/formats/pt3-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from one real 2,048-byte module (PT 3.5,
seven patterns, 22 samples) and decodes to the stated value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "pt3-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>PT3 Anatomy</h1></div></div>
      <div class="stamp"><b>ZX Spectrum</b>ProTracker 3 / Vortex Tracker<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>ProTracker 3. / Vortex Tracker II</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own; absolute pointers</b></div>
      <div>samples <b>tables, not PCM</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.pt3</b> is a score for the ZX Spectrum's AY-3-8910: three square-wave
    channels, one envelope, one noise generator. There is no PCM anywhere. A "sample" is a table of
    per-tick settings (volume, tone offset, noise, which mixers are on) and an "ornament" a table of
    semitone offsets, and a note is a pointer into both. The file was made to be loaded at a known
    address and played in place, so <b>every pointer in it is an absolute offset</b>, 16 bits, and
    the whole file can be tiled by sorting them: each region begins where a pointer says and ends
    where the next one begins.</p>

    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the header</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Ninety-nine bytes of text, then the numbers. The signature is thirty
        characters that name the tracker; the name and the author are thirty-two each with
        <code> by </code> between them, so the text reads as a sentence.</p>
        <div id="pt3-head"></div>
        <p class="note"><b>The position list is stored times three.</b> Each byte after the pointer
        tables is a pattern number multiplied by 3, because a Z80 player used it directly as an
        index into the pattern table's six-byte rows without a multiply. The list ends at 0xFF.</p>
        <div id="pt3-pos"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the pattern table</span><span class="rspan">three pointers per pattern . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Where the header's pattern pointer lands: for each pattern, three 16-bit
        absolute offsets, one per channel, each to a stream of notes and effects. Two patterns may
        point at the same stream. The count of patterns is not stored; it is one more than the
        highest number in the position list.</p>
        <div id="pt3-ptab"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">samples and ornaments</span><span class="rspan">tables . drawn below</span></summary>
      <div class="rbody">
        <p class="note">A sample record is a loop point, a length, and that many four-byte rows,
        one per tick: mixer and envelope flags, the volume with accumulate bits, and a signed
        16-bit tone offset. An ornament is a loop point, a length, and that many signed bytes of
        semitone offset. Both declare their own size, which the walk checks against the room to
        the next pointer.</p>
        <div id="pt3-smp"></div>
        <div id="pt3-orn"></div>
        <div class="kv">
          <div><span class="k">sample</span><span class="v">loop, length, length &times; 4 bytes</span></div>
          <div><span class="k">ornament</span><span class="v">loop, length, length &times; 1 byte</span></div>
          <div><span class="k">slot 0</span><span class="v">a sample pointer of 0 means no sample; the 32 slots are numbered 0-31 and real samples are 1-31</span></div>
        </div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the channel streams</span><span class="rspan">a byte code . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Each channel of each pattern is a stream of one-byte commands with the
        occasional operand: notes in 0x50-0xAF, volume in 0xC1-0xCF, ornament-and-sample in
        0xF0-0xFF followed by a sample byte, speed as 0xB1 and a byte, rest and release, and
        0x00 to end. The stream is decoded by playing it; the walk sizes it by the next pointer,
        which is what the pointers are for.</p>
        <div id="pt3-chan"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">Pro Tracker 2</span><span class="rspan">the same file, no signature</span></summary>
      <div class="rbody">
        <p class="note">PT2, the tracker before, is the same design with the header the other way
        round and nothing at offset zero to read:</p>
        <div class="kv">
          <div><span class="k">0x00</span><span class="v">delay, positions, loop: one byte each</span></div>
          <div><span class="k">0x03</span><span class="v">32 sample pointers, then 16 ornament pointers at 0x43</span></div>
          <div><span class="k">0x63</span><span class="v">pattern table pointer</span></div>
          <div><span class="k">0x65</span><span class="v">30-character name</span></div>
          <div><span class="k">0x83</span><span class="v">position list, plain pattern numbers, ended by 0xFF</span></div>
          <div><span class="k">sample row</span><span class="v">3 bytes, not 4</span></div>
        </div>
        <p class="note">With nothing to sniff, a PT2 is identified by its arithmetic: the position
        count and the list agree, every pointer lands inside the file, and the first pointed
        region begins exactly where the position list ends. A file that fails any of those is
        not called a PT2, which means a PT2 cut short is refused rather than guessed at.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">text at zero</span></summary>
      <div class="rbody">
        <p class="note"><code>ProTracker 3.</code> or <code>Vortex Tracker II</code> at offset
        zero. The older Spectrum trackers (PT2, Sound Tracker, ASC) have no text signature and are
        identified by their pointer arithmetic instead; this is the one that says its name.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / pt3 anatomy</span>
    <span>zx spectrum . ay-3-8910 . protracker 3 . absolute pointers</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the numbers after the 99 bytes of text: 0x63 to 0x72
  build("pt3-head","byte",
    [0x02, 0x05, 0x07, 0x04, 0xD1,0x00, 0x00,0x00, 0x18,0x05, 0x2A,0x05, 0x30,0x05, 0x62,0x05],
    [
      {label:"tone table",k:"enum",r:[0,0],val:"2 = ASM / PSC",
       body:"Which of four tuning tables the notes use. 0 is the original PT3.3 table, 1 Sound Tracker's, 2 the ASM/PSC one, 3 Real Sound's.",
       note:"at 0x63, after 99 bytes of text."},
      {label:"delay",k:"enum",r:[1,1],val:"5",
       body:"Ticks per row: the song's speed. Five 50 Hz frames per row.",
       note:"0x64."},
      {label:"positions",k:"size",r:[2,2],val:"7",
       body:"How many entries the position list holds. The list itself ends at 0xFF; the two have to agree.",
       note:"0x65."},
      {label:"loop",k:"enum",r:[3,3],val:"4",
       body:"The position to return to when the last one ends.",
       note:"0x66."},
      {label:"pattern table",k:"ptr",r:[4,5],val:"0x00D1 = 209",
       body:"Absolute offset of the pattern table. In this file it begins the moment the position list ends: 0xC9 + 7 + 1 = 0xD1.",
       note:"little-endian."},
      {label:"sample 0",k:"pad",r:[6,7],val:"0 = none",
       body:"The first of 32 sample pointers. Slot 0 is the empty sample and points nowhere.",
       note:"0x69."},
      {label:"sample 1",k:"ptr",r:[8,9],val:"0x0518 = 1304",
       body:"Absolute offset of the first real sample record, drawn below.",
       note:""},
      {label:"samples 2-4",k:"ptr",r:[10,15],val:"1322, 1328, 1378",
       body:"More sample pointers; 28 more follow, then 16 ornament pointers at 0xA9.",
       note:"32 + 16 pointers = 96 bytes."}
    ]);

  // the position list at 0xC9
  build("pt3-pos","byte",
    [0x00,0x03,0x06,0x09,0x0C,0x0F,0x12, 0xFF],
    [
      {label:"positions",k:"enum",r:[0,6],val:"0 3 6 9 12 15 18 = patterns 0-6",
       body:"Seven positions, each a pattern number times three. Divide by three to read them; a Z80 player did not, and used them as row offsets into the six-byte pattern table.",
       note:"pattern n stored as 3n."},
      {label:"end",k:"sync",r:[7,7],val:"0xFF",
       body:"The list's terminator, and the last byte of the header.",
       note:"at 0xD0; the pattern table starts at 0xD1."}
    ]);

  // the pattern table at 209: two patterns' worth
  build("pt3-ptab","byte",
    [0xFB,0x00, 0x25,0x01, 0x54,0x01, 0x7D,0x01, 0x9D,0x01, 0xCB,0x01],
    [
      {label:"pattern 0, ch A",k:"ptr",r:[0,1],val:"0x00FB = 251",
       body:"Where channel A's stream for pattern 0 begins: right after the table, since 209 + 7 x 6 = 251.",
       note:""},
      {label:"pattern 0, ch B",k:"ptr",r:[2,3],val:"0x0125 = 293",
       body:"Channel B's stream. Channel A's therefore runs 251 to 293: 42 bytes, sized by this pointer.",
       note:""},
      {label:"pattern 0, ch C",k:"ptr",r:[4,5],val:"0x0154 = 340",note:"",
       body:"Channel C's stream."},
      {label:"pattern 1",k:"ptr",r:[6,11],val:"381, 413, 459",
       body:"The next row. Rows continue for as many patterns as the position list names.",
       note:"six bytes per pattern."}
    ]);

  // sample 1 at 1304: loop, length, then rows
  build("pt3-smp","byte",
    [0x00, 0x04, 0x00,0x8F,0x00,0x00, 0x00,0x8F,0x00,0x00, 0x00,0x8D,0x02,0x00],
    [
      {label:"loop",k:"enum",r:[0,0],val:"0",
       body:"The row to return to after the last. Zero: the whole sample repeats.",
       note:""},
      {label:"length",k:"size",r:[1,1],val:"4 rows",
       body:"Four rows of four bytes follow: 18 bytes in all, which is what fits before the next pointer at 1322.",
       note:"2 + 4 x 4 = 18."},
      {label:"row 0",k:"enum",r:[2,5],val:"flags 00, volume 8F, tone +0",
       body:"One tick of the sound: a flag byte for the mixer and envelope, a byte holding the volume in its low nibble with accumulate bits above, and a signed 16-bit tone offset.",
       note:"four bytes per tick."},
      {label:"row 1",k:"enum",r:[6,9],val:"the same",note:"",
       body:"The tick after."},
      {label:"row 2",k:"enum",r:[10,13],val:"volume 8D, tone +2",
       body:"The volume drops by two and the tone rises by two: the start of a decay with vibrato, written out a tick at a time.",
       note:""}
    ]);

  // ornament 0 at 1960
  build("pt3-orn","byte",
    [0x00, 0x01, 0x00],
    [
      {label:"loop",k:"enum",r:[0,0],val:"0",body:"Return to the first offset.",note:""},
      {label:"length",k:"size",r:[1,1],val:"1",body:"One offset follows.",note:""},
      {label:"offset",k:"enum",r:[2,2],val:"+0 semitones",
       body:"The null ornament: a single zero, meaning the note plays at its own pitch. Most modules keep one of these in slot 0.",
       note:"signed."}
    ]);

  // the first 16 bytes of pattern 0, channel A, at 251
  build("pt3-chan","byte",
    [0xF0,0x2A, 0xC2, 0xB1,0x08, 0x74, 0xC3, 0xB1,0x04, 0x7B, 0x74, 0xC5, 0xB1,0x08, 0x72, 0xC8],
    [
      {label:"ornament + sample",k:"sync",r:[0,1],val:"0xF0, sample 0x2A",
       body:"0xF0-0xFF sets the ornament from the low nibble (0 here) and takes the next byte as the sample number times two: 0x2A is sample 21.",
       note:"two bytes."},
      {label:"volume",k:"enum",r:[2,2],val:"0xC2 = 2",
       body:"0xC1-0xCF sets the channel volume from the low nibble.",
       note:""},
      {label:"speed",k:"enum",r:[3,4],val:"0xB1, 8",
       body:"0xB1 takes the next byte as the new delay: eight ticks per row from here.",
       note:""},
      {label:"note",k:"enum",r:[5,5],val:"0x74",
       body:"A note: 0x50 is the lowest, so 0x74 is 36 semitones up, three octaves. The note starts the sample and ornament and ends this row.",
       note:"0x50-0xAF are notes."},
      {label:"more rows",k:"enum",r:[6,15],val:"volume, speed, notes ...",
       body:"The stream continues row by row until its 0x00, forty-two bytes from the start, where channel B's begins.",
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / pt3 anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / pt3 anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
