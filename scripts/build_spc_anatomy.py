"""Generate docs/formats/spc-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from a real file and decodes to the stated
value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "spc-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>SPC Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Super Nintendo</b>SPC700 . S-DSP snapshot<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>33 bytes</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own, fixed</b></div>
      <div>samples <b>BRR, inside the RAM</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">An <b>.spc</b> is a Super Nintendo's sound chip, frozen. The SNES has a second
    computer for audio -- the <b>SPC700</b>, with 64 KB of its own RAM and a DSP that plays
    compressed samples out of it -- and this file is that computer mid-tune: the CPU registers,
    every byte of RAM, and the DSP's 128 registers. A player loads the image and lets the program
    run. There is no score to parse. What there is: a tag, a machine state, and a bank of samples
    the DSP knows how to find.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the shape, always the same</div>
  <p class="note">Every SPC is 66,048 bytes before its optional tail: a 256-byte header, 65,536
  bytes of RAM, 128 bytes of DSP registers, 64 unused, 64 of the boot ROM area. Then, usually, an
  <code>xid6</code> chunk carrying the rest of the tag.</p>
  <div class="kv">
    <div><span class="k">0x00000</span><span class="v">header and ID666 tag, 256 bytes</span></div>
    <div><span class="k">0x00100</span><span class="v">SPC700 RAM, 65,536 bytes</span></div>
    <div><span class="k">0x10100</span><span class="v">DSP registers, 128 bytes</span></div>
    <div><span class="k">0x10180</span><span class="v">unused, 64 bytes</span></div>
    <div><span class="k">0x101C0</span><span class="v">IPL ROM area, 64 bytes</span></div>
    <div><span class="k-en">0x10200</span><span class="v">xid6, if present; in nearly every file made this century</span></div>
  </div>

  <div class="sec">the header, and the flag that is not one</div>
  <p class="note">Thirty-three bytes of magic, then three bytes the specification describes as a
  signature and a tag flag, then the CPU state, then the tag. The bytes are from a real file.</p>
  <div id="spc-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">what the spec says, and what files do</span><span class="rspan">three disagreements</span></summary>
      <div class="rbody">
        <p class="note">The SPC specification has travelled with every player since 1999, and real
        files disagree with it in three places. Each is the kind of thing only a pile of files can
        show.</p>
        <p class="note"><b>The tag flag.</b> The spec says byte <code>0x23</code> is <code>0x26</code>
        when an ID666 tag follows and <code>0x27</code> when it does not. Real files carry
        <code>0x1A</code> there -- bytes <code>0x21</code> to <code>0x23</code> are three DOS
        end-of-file markers, so a tune printed to a console shows its magic and stops -- and they
        carry a full tag regardless. A reader that obeys the flag reads every file as untagged.</p>
        <p class="note"><b>Telling the two spellings apart.</b> The tag's length and fade are text in
        one spelling and packed numbers in the other, and the spec suggests looking at the date to
        tell which. Real dumpers leave the date empty in both spellings. The <b>seconds slot</b> is
        the tell: digits means text, anything else means binary, and an empty slot is text with the
        length simply not written.</p>
        <p class="note"><b>The emulator byte.</b> In the text spelling it is written as the character
        <code>'0'</code>, not the number 0. The spec does not say so.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the two tag spellings</span><span class="rspan">text . binary</span></summary>
      <div class="rbody">
        <p class="note">The title, game, dumper and comment slots are the same in both. From the
        date onward they differ, and the artist moves by <b>one byte</b>:</p>
        <div class="kv">
          <div><span class="k">text</span><span class="v">date as 11 chars at 0x9E, seconds as 3 chars at 0xA9, fade as 5 chars at 0xAC, artist at <b>0xB1</b>, emulator as a digit at 0xD2</span></div>
          <div><span class="k">binary</span><span class="v">date packed at 0x9E, seconds as 3 bytes at 0xA9, fade as 4 bytes at 0xAC, artist at <b>0xB0</b>, emulator as a number at 0xD1</span></div>
        </div>
        <p class="note">Read the binary artist at the text offset and its first letter vanishes:
        "Akihiko Mori" becomes "kihiko Mori". A reader that is one byte off is always one byte off
        in the layout, never in the font.</p>
        <p class="note"><b>A third spelling, from one dumper.</b> Files with the <code>v0.20</code>
        magic and <code>0x1B</code> at byte 0x23, dumped in the summer of 1999, leave the text slots
        empty and put a <b>20-character title at 0x30</b> -- two bytes late -- and the date at
        <b>0xD0</b> as day, month and a little-endian year, exactly where the text spelling keeps
        its disable and emulator bytes. Some <code>v0.10</code> files use the same 0x30 title slot,
        space-padded, with a binary length. A reader that takes the title from 0x2E sees an empty
        slot and calls these untitled.</p>
        <div id="spc-tag"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">finding the samples</span><span class="rspan">DIR . SRCN</span></summary>
      <div class="rbody">
        <p class="note">DSP register <code>0x5D</code>, <b>DIR</b>, names a page of RAM holding the
        <b>sample directory</b>: 256 entries of four bytes, a start address and a loop address each.
        Every entry that points inside RAM looks like a sample.</p>
        <p class="note">Most of them are not. The directory is a table the hardware reads by index,
        and a sound engine leaves it full of whatever was there: addresses from other tunes, from
        the game's other banks, from nothing. Two thirds of real files have directory entries that
        overlap each other and the program code. What the DSP actually dereferences is named by the
        eight <b>SRCN</b> registers, one per voice at <code>0x04 + voice * 0x10</code>: the entry
        each voice is set to play. Those are the real samples, and those alone.</p>
        <div id="spc-dir"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">a BRR sample</span><span class="rspan">9-byte blocks</span></summary>
      <div class="rbody">
        <p class="note">Samples are <b>Bit Rate Reduction</b>, the SNES's own ADPCM: nine-byte
        blocks, each a header byte and sixteen 4-bit samples. The header carries the shift, the
        filter, and two flags: <b>END</b> and <b>LOOP</b>. A sample runs from its start address
        until a block with END set. Whether it loops is the LOOP bit of that same last block --
        the directory's loop address says where to, not whether.</p>
        <div id="spc-brr"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">xid6, where the tag actually is</span><span class="rspan">after the image</span></summary>
      <div class="rbody">
        <p class="note">The 256-byte header has room for a title, a game, an artist and a length.
        Everything else -- publisher, year, disc and track, intro and loop lengths to the
        1/64000th of a second -- lives in a chunk after the image: <code>xid6</code>, a size, then
        sub-chunks of a one-byte ID, a one-byte type, and a two-byte length or value, each padded
        to four bytes. The spec presents it as an optional extension. Nearly every real file has
        one, and it is where the metadata is.</p>
        <div id="spc-xid6"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">RSN</span><span class="rspan">not a format</span></summary>
      <div class="rbody">
        <p class="note">An <code>.rsn</code> is a RAR archive of SPC files, renamed so a player will
        open it. It works because every tune from one game shares its engine and most of its
        samples, and RAR's solid compression stores the shared bytes once.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">little-endian</span></summary>
      <div class="rbody">
        <p class="note">The SPC700 is little-endian and so is everything here: the program counter,
        the directory entries, the packed tag numbers, the xid6 lengths.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / spc anatomy</span>
    <span>super nintendo . spc700 . s-dsp . brr samples . id666 and xid6</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // bytes 0x21 through 0x3D of a real file: the three EOF markers, the CPU
  // state, and the start of the title
  build("spc-head","byte",
    [0x1A,0x1A,0x1A, 0x1E, 0x7E,0x08, 0x13, 0x00, 0x01, 0x00, 0xFB, 0x00,0x00,
     0x4D,0x61,0x69,0x6E,0x20,0x54,0x68,0x65,0x6D,0x65,0x00,0x00,0x00,0x00,0x00,0x00],
    [
      {label:"1A 1A 1A",k:"sync",r:[0,2],val:"three DOS EOF marks",
       body:"The specification describes bytes 0x21-0x22 as a signature 26 26 and byte 0x23 as a tag flag. Every real file measured has 1A 1A 1A: end-of-file markers, so that typing the file to a console shows the 33-byte magic and stops. The flag the spec describes is not something dumpers write.",
       note:"not a flag. Read the tag slots regardless."},
      {label:"version",k:"enum",r:[3,3],val:"0x1E = 30",
       body:"The minor version, 30, matching the v0.30 in the magic string.",
       note:""},
      {label:"PC",k:"enum",r:[4,5],val:"$087E",
       body:"The program counter at the moment of the dump: where the SPC700 was about to execute. A player restores this and the tune continues from exactly here.",
       note:"little-endian."},
      {label:"A",k:"sync",r:[6,6],val:"$13",body:"The accumulator.",note:""},
      {label:"X",k:"sync",r:[7,7],val:"$00",body:"",note:""},
      {label:"Y",k:"sync",r:[8,8],val:"$01",body:"",note:""},
      {label:"PSW",k:"sync",r:[9,9],val:"$00",body:"The processor status word: the flags.",note:""},
      {label:"SP",k:"sync",r:[10,10],val:"$FB",
       body:"The stack pointer's low byte. The SPC700's stack is always in page 1, so this is $01FB.",
       note:""},
      {label:"reserved",k:"rsv",r:[11,12],val:"00 00",body:"Two bytes the spec reserves.",note:""},
      {label:"title",k:"enum",r:[13,28],val:"Main Theme",
       body:"The first sixteen bytes of the 32-byte title slot. Text ends at the NUL; the slot does not. Game, dumper and comment follow in slots of their own.",
       note:"the slot is always 32 bytes."}
    ]);

  // the length, fade and emulator slots, in the text spelling
  build("spc-tag","byte",
    [0x31,0x34,0x30, 0x38,0x30,0x30,0x30,0x00, 0x00, 0x30],
    [
      {label:"seconds",k:"enum",r:[0,2],val:"'140'",
       body:"How long to play before fading, as three text digits. This is the slot that tells the spellings apart: digits means text, packed bytes means binary, all zero means text with nothing written.",
       note:"at 0xA9. The tell."},
      {label:"fade",k:"enum",r:[3,7],val:"'8000'",
       body:"The fade length in milliseconds, as up to five text digits.",
       note:"at 0xAC."},
      {label:"(0xD1)",k:"rsv",r:[8,8],val:"00",
       body:"In the text spelling this byte is the channel-disable mask, zero here. In the binary spelling it is where the emulator byte sits instead.",
       note:"the two spellings straddle this byte."},
      {label:"emulator",k:"enum",r:[9,9],val:"'0' = unknown",sel:0,branch:[
        ["'0' / 0","unknown"],["'1' / 1","ZSNES"],["'2' / 2","Snes9x"]],
       body:"Which emulator made the dump. Written as the CHARACTER '0' in the text spelling -- 0x30, not 0x00 -- which the spec does not mention and every text-tagged file does.",
       note:"at 0xD2 in text, 0xD1 in binary."}
    ]);

  // the first two directory entries, at the page DIR names
  build("spc-dir","byte",
    [0x68,0x2B, 0x68,0x2B, 0xB2,0x53, 0x0B,0x6D],
    [
      {label:"entry 0 start",k:"enum",r:[0,1],val:"$2B68",
       body:"Where sample 0 begins in SPC700 RAM. Voice 0's SRCN register names entry 0, so this one is real: the DSP is set to play it.",
       note:"little-endian; the directory is at page $01, so RAM $0100."},
      {label:"entry 0 loop",k:"enum",r:[2,3],val:"$2B68",
       body:"Where sample 0 loops back to. Equal to the start: a full-length loop. Whether it loops at all is a flag in the sample's last block, not this address.",
       note:"an address, not a flag."},
      {label:"entry 1 start",k:"sync",r:[4,5],val:"$53B2",body:"Sample 1, which voice 1 is set to play.",note:""},
      {label:"entry 1 loop",k:"sync",r:[6,7],val:"$6D0B",
       body:"A loop point well inside the sample: the tail repeats while the attack does not, which is how a sustained instrument is stored.",
       note:"254 more entries follow. Most are stale."}
    ]);

  // the first block of sample 0
  build("spc-brr","bit",[0x52],[
      {label:"shift",k:"enum",r:[0,3],val:"0101 = 5",
       body:"The range shift for this block's sixteen nibbles: each is shifted left by this much before the filter. Four bits, 0-12 are meaningful.",
       note:"the top nibble of the header."},
      {label:"filter",k:"enum",r:[4,5],val:"00 = 0",
       body:"Which of four prediction filters to apply. Filter 0 is none: the nibbles are the samples, shifted. The other three predict from the previous one or two samples.",
       note:""},
      {label:"LOOP",k:"flag",r:[6,6],val:"1",
       body:"Set: when this sample reaches its END block, jump to the directory's loop address. Only the last block's copy means anything, but most encoders set it on every block of a looping sample, which is why it is set on this first one.",
       note:"the loop flag lives HERE, not in the directory."},
      {label:"END",k:"flag",r:[7,7],val:"0",
       body:"Clear: this is not the last block. The sample continues into the next nine bytes.",
       note:"sixteen 4-bit samples follow the header."}
    ]);

  // the start of the xid6 chunk
  build("spc-xid6","byte",
    [0x78,0x69,0x64,0x36, 0x34,0x00,0x00,0x00,
     0x12,0x00,0x01,0x00,
     0x14,0x00,0xCA,0x07,
     0x13,0x01,0x15,0x00, 0x4D,0x65,0x72,0x69],
    [
      {label:"xid6",k:"sync",r:[0,3],val:"xid6",body:"The chunk's name. It sits at 0x10200, right after the base image.",note:""},
      {label:"size",k:"enum",r:[4,7],val:"0x34 = 52",body:"The chunk's payload length, not counting these eight bytes.",note:"little-endian."},
      {label:"OST track",k:"enum",r:[8,11],val:"id 0x12, type 0: 1",
       body:"A sub-chunk with its value IN the header: type 0 means the two-byte data field is the value itself. Track 1 of the soundtrack.",
       note:"id, type, then two bytes of value."},
      {label:"year",k:"enum",r:[12,15],val:"id 0x14, type 0: 0x07CA = 1994",
       body:"Copyright year, held in the header the same way.",
       note:""},
      {label:"publisher",k:"enum",r:[16,19],val:"id 0x13, type 1, 21 bytes",
       body:"A string sub-chunk: type 1 means the two-byte field is a LENGTH, and that many bytes of text follow, padded to a multiple of four.",
       note:"the text starts on the next byte."},
      {label:"text",k:"sync",r:[20,23],val:"Meri...",
       body:"The first four of 21 bytes: 'Merit Studios, Ocean'. Then padding to the next 4-byte boundary, then the intro and fade lengths as type-4 integers.",
       note:"1/64000 s units for the lengths."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / spc anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / spc anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
