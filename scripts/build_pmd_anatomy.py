"""Generate docs/formats/pmd-anatomy.html.

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
OUT = os.path.join(DOCS, "pmd-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>PMD Anatomy</h1></div></div>
      <div class="stamp"><b>NEC PC-9801</b>Professional Music Driver . YM2608<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>3 weak bytes</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own</b></div>
      <div>samples <b>separate .PPC / .PPS / .PZI</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.M</b> file is a compiled score for <b>Professional Music Driver</b>, the sound
    driver M. Kajihara wrote for the NEC PC-9801 and its Yamaha <b>YM2608</b> FM chip. Most of the
    PC-98's game and doujin music was written for it. A composer writes <b>MML</b>, a text language,
    and a compiler turns it into this: a table of eleven part streams, the FM instruments, and a
    memo block where the score's own <code>#Title</code> and <code>#Composer</code> survive.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">source and binary</div>
  <p class="note">MML is the source. It is plain text: <code>c d e f g</code> for notes,
  <code>t120</code> for tempo, <code>@1</code> to pick an instrument, and a block of
  <code>#</code>-directives at the top for the title, composer and sample banks. <b>MC.EXE</b>
  compiles it to the binary on this page, and the driver, <b>PMD.COM</b>, plays the binary. So a
  <code>.M</code> is to its <code>.MML</code> what an object file is to its source, and the memo
  block is the part of the source the compiler chose to keep.</p>

  <div class="sec">the header, 27 bytes</div>
  <p class="note">A flag byte, then twelve little-endian words, then one more. Every offset in
  the file is measured <b>from byte 1</b>, not byte 0: the driver loads the file and then points
  its buffer one byte in, and never looks back.</p>
  <div id="pmd-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the part table</span><span class="rspan">12 words . little-endian</span></summary>
      <div class="rbody">
        <p class="note">Eleven part streams and one address table, each a word offset counted from
        byte 1. The parts are lettered the way the MML names them:</p>
        <div class="kv">
          <div><span class="k">A &ndash; F</span><span class="v">six FM voices on the YM2608</span></div>
          <div><span class="k">G &ndash; I</span><span class="v">three SSG channels, the chip's square-wave half</span></div>
          <div><span class="k">J</span><span class="v">ADPCM, played from the .PPC or .P86 sample bank</span></div>
          <div><span class="k">K</span><span class="v">rhythm, the chip's six built-in drum sounds</span></div>
          <div><span class="k-en">twelfth word</span><span class="v">the rhythm pattern address table, not a part</span></div>
        </div>
        <p class="note"><b>Twelve words, not eleven.</b> The driver walks the eleven parts and then
        takes one more for the rhythm table. A reader that counts eleven finds the tone offset two
        bytes early and leaves a two-byte hole in front of every first part; the numbers still look
        plausible, which is why it is worth saying.</p>
        <p class="note">A part whose stream opens with <code>0x80</code> is <b>silent</b>: the MML
        wrote nothing for it. A stream's length is not stored anywhere. A part runs from its own
        offset to whatever begins next in the file, so the extents come from sorting every offset
        the header gives you.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification, with no magic to speak of</span><span class="rspan">3 bytes</span></summary>
      <div class="rbody">
        <p class="note">There is no signature. What the driver checks before it will play a file
        is three bytes:</p>
        <div class="kv">
          <div><span class="k">byte 0</span><span class="v">at most <code>0x0F</code>; 0 is PC-98, 1 is X68000</span></div>
          <div><span class="k">byte 1</span><span class="v"><code>0x1A</code> or <code>0x18</code></span></div>
          <div><span class="k">byte 2</span><span class="v"><code>0x00</code> or <code>0xE6</code></span></div>
        </div>
        <p class="note">Bytes 1 and 2 are just the low and high halves of part A's offset, which
        is 26 or 24 in every file the compiler produces because the header is that long. So the
        "magic" is a consequence of the layout, not a mark, and a random file passes it roughly
        once in a few thousand. That is why a reader checks the extension too.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the memo anchor</span><span class="rspan">4 bytes, just before the tones</span></summary>
      <div class="rbody">
        <p class="note">The memo is found backwards. The word at offset <code>0x18</code> of the
        stream is the tone offset; four bytes before where it points sit a word pointer, a tag
        byte, and <code>0xFE</code>. That is the anchor, and it is not a region of its own: it is
        the tail of whatever precedes the tone block, usually the rhythm table.</p>
        <div id="pmd-anchor"></div>
        <p class="note">The tag byte says which sample-bank slots the memo table carries in front
        of its text. <b>The PCM-file slot is always first.</b> A tag of <code>0x42</code> or above
        puts a PPS-file slot in front of it; <code>0x48</code> or above puts a PPZ-file slot in
        front of both. That is the driver's arithmetic reproduced: a caller asks for slot &minus;2,
        &minus;1 or 0 and the driver adds one per threshold. Get the order wrong and the composer
        lands in the arranger field.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the memo table</span><span class="rspan">word pointers, then strings</span></summary>
      <div class="rbody">
        <p class="note">A list of word pointers, each to a NUL-terminated <b>Shift-JIS</b> string,
        ending with a zero word. The slots are the MML directives the compiler read them from, as
        the driver's author lists them:</p>
        <div class="kv">
          <div><span class="k">[#PPZFile]</span><span class="v">PPZ8 sample bank, if the tag says so</span></div>
          <div><span class="k">[#PPSFile]</span><span class="v">SSG-PCM sample bank, if the tag says so</span></div>
          <div><span class="k">#PCMFile</span><span class="v">the ADPCM bank part J plays from</span></div>
          <div><span class="k">#Title</span><span class="v">the tune's name</span></div>
          <div><span class="k">#Composer</span><span class="v">who wrote it</span></div>
          <div><span class="k">#Arranger</span><span class="v">who arranged it</span></div>
          <div><span class="k-en">#Memo ...</span><span class="v">free lines, up to 128, until the zero word</span></div>
        </div>
        <p class="note">A string that is just <code>/</code> means the directive was never written.
        So does an empty one. A PMD file names its sample banks the way an X68000 MDX names its
        PDX: the samples live elsewhere, and a tune separated from its bank has an ADPCM part with
        nothing to play.</p>
        <div id="pmd-memo"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the tone block</span><span class="rspan">FM instruments, 26 bytes each</span></summary>
      <div class="rbody">
        <p class="note">A list of instruments, each an <b>instrument number</b> and then 25 bytes of
        YM2608 operator registers, ended by <code>00 FF</code>. It is a list and not an array: the
        driver finds instrument <code>@5</code> by walking the records comparing numbers, so they need
        not be in order and need not be contiguous. The memo strings begin on the byte after the
        terminator.</p>
        <div id="pmd-tone"></div>
        <p class="note">They are here only if the MML was compiled with <b><code>MC /V</code></b>.
        Without that flag the compiler writes none, the tone offset simply equals the header size,
        and the driver expects a separate <code>.FF</code> instrument file. A file with no tones is a
        real thing the compiler produces, not damage; so is a list holding only its terminator,
        which is what an SSG-only tune has.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the family</span><span class="rspan">PC-98 . PC-88 . X68000 . FM Towns</span></summary>
      <div class="rbody">
        <p class="note">The driver was ported, and the compiler's output followed. Byte 0 is the
        platform flag: <code>0</code> for the PC-98 and <code>1</code> for the X68000, whose files
        otherwise share this layout exactly. The part letters map to different chips on each
        machine, which the MML manual notes and the file does not.</p>
        <p class="note">Other extensions the compiler writes: <code>.M2</code> and <code>.M86</code>
        are the same format under a different preprocessor setting; <code>.MZ</code> is compressed.
        The sample banks are their own formats: <code>.PPC</code> and <code>.P86</code> for ADPCM,
        <code>.PPS</code> for SSG-PCM, <code>.PZI</code> and <code>.PVI</code> for PPZ8.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">little-endian</span></summary>
      <div class="rbody">
        <p class="note">Every word is little-endian, as the 8086 in the PC-9801 is. The X68000
        variant keeps it that way despite running on a 68000, because the compiler and driver
        were ported together and the format did not change.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / pmd anatomy</span>
    <span>nec pc-9801 . professional music driver . ym2608 . compiled mml</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the whole 27-byte header of a real PC-98 tune
  build("pmd-head","byte",
    [0x00,
     0x1A,0x00, 0x3E,0x03, 0xDB,0x05, 0x10,0x0A, 0xEF,0x0C, 0xBA,0x0F,
     0xBB,0x0F, 0x0D,0x14, 0xB2,0x17,
     0xB3,0x17,
     0xB4,0x17,
     0xF5,0x17,
     0x08,0x1B],
    [
      {label:"flag",k:"enum",r:[0,0],val:"0 = PC-98",sel:0,branch:[
        ["0","PC-98"],["1","X68000"]],
       body:"The platform byte, which the driver calls x68_flg. It is also the one byte that is NOT part of the stream the driver works in: every offset below is measured from the byte after this one.",
       note:"the origin of every offset in the file is here + 1."},
      {label:"part A",k:"enum",r:[1,2],val:"0x001A = 26",
       body:"Where FM part A's command stream begins, counted from byte 1. It is 26 because that is exactly the size of the twelve words and the tone offset that follow -- so part A starts the instant the header ends. These two bytes are also two thirds of the file's identification.",
       note:"1A 00, little-endian."},
      {label:"parts B - F",k:"sync",r:[3,12],val:"5 more FM parts",
       body:"830, 1499, 2576, 3311, 4026. Each part runs from its own offset to whatever begins next, because no length is stored anywhere.",
       note:"one word each."},
      {label:"parts G - I",k:"sync",r:[13,18],val:"SSG",
       body:"4027, 5133, 6066. Part G starts one byte after part F: F is silent here, and a silent part is a single 0x80.",
       note:"the chip's square-wave channels."},
      {label:"part J",k:"sync",r:[19,20],val:"ADPCM",
       body:"6067: another single byte, so this tune plays no samples and names no bank.",
       note:""},
      {label:"part K",k:"sync",r:[21,22],val:"rhythm",
       body:"6068. The YM2608's six built-in drum sounds, driven by pattern number.",
       note:""},
      {label:"rhythm table",k:"sync",r:[23,24],val:"0x17F5 = 6133",
       body:"The twelfth word, and not a part: where the rhythm PATTERN address table lives. A reader that counts eleven words takes this for the tone offset and is two bytes out for the rest of the file.",
       note:"the word that makes the table twelve."},
      {label:"tone offset",k:"enum",r:[25,26],val:"0x1B08 = 6920",
       body:"Where the FM instrument list begins. If this equals 26 -- the header size -- the compiler wrote no instruments, which is what MC without /V produces. Four bytes BEFORE where this points is the memo anchor.",
       note:"the word at stream offset 0x18."}
    ]);

  // the four-byte memo anchor, just before the tone block
  build("pmd-anchor","byte",
    [0xB4,0x1B, 0x48, 0xFE],
    [
      {label:"table",k:"sync",r:[0,1],val:"0x1BB4 = 7092, file 7093",
       body:"A pointer to the memo's pointer table, which sits at the very end of the file. The strings it points to come BEFORE it, immediately after the instruments.",
       note:"little-endian, counted from byte 1."},
      {label:"tag",k:"enum",r:[2,2],val:"0x48",sel:2,branch:[
        ["0x40","PCM slot only"],["0x42 +","PPS slot in front"],["0x48 +","PPZ slot in front of that"]],
       body:"Which sample-bank slots the table carries before its text. This one is 0x48, so the table opens PPZ, PPS, PCM, then Title, Composer, Arranger. The driver's own arithmetic: a caller asks for slot -2, -1 or 0 and one is added per threshold.",
       note:"0x40 is the minimum; below it there is no memo."},
      {label:"end",k:"sync",r:[3,3],val:"0xFE",
       body:"The anchor's terminator. Checked by the driver only when the tag is above 0x40.",
       note:""}
    ]);

  // one FM instrument, then the terminator
  build("pmd-tone","byte",
    [0x2B,
     0x02,0x04,0x08,0x04, 0x1C,0x00,0x00,0x00, 0x1F,0x1F,0x1F,0x1F,
     0x00,0x0E,0x0E,0x0E, 0x00,0x03,0x03,0x03, 0x00,0x3F,0x3F,0x3F,
     0x3D,
     0x00,0xFF],
    [
      {label:"number",k:"enum",r:[0,0],val:"0x2B = @43",
       body:"The instrument number the MML refers to as @43. The driver walks the list comparing this byte, so instruments need not be in order.",
       note:"one byte; 256 is the most a file can hold."},
      {label:"DT / ML",k:"sync",r:[1,4],val:"02 04 08 04",
       body:"Detune and multiple, one byte per operator. The registers are grouped BY PARAMETER across the four operators, not by operator, and the four come in the chip's own order: operators 1, 3, 2, 4. The driver reads them straight into the register file at 0x30, stepping four per operator.",
       note:"in the order 1, 3, 2, 4."},
      {label:"TL",k:"sync",r:[5,8],val:"1C 00 00 00",body:"Total level, the operator's attenuation, in the same 1, 3, 2, 4 order. Operator 1 is turned down here; the other three are at full.",note:""},
      {label:"KS / AR",k:"sync",r:[9,12],val:"1F 1F 1F 1F",body:"Key scale and attack rate. All four attack at the fastest rate.",note:""},
      {label:"AM / DR",k:"sync",r:[13,16],val:"00 0E 0E 0E",body:"Amplitude-modulation enable and decay rate.",note:""},
      {label:"SR",k:"sync",r:[17,20],val:"00 03 03 03",body:"Sustain rate.",note:""},
      {label:"SL / RR",k:"sync",r:[21,24],val:"00 3F 3F 3F",body:"Sustain level and release rate.",note:""},
      {label:"FB / ALG",k:"enum",r:[25,25],val:"0x3D",
       body:"Feedback and algorithm, packed: the algorithm in the low three bits selects how the four operators are wired, feedback in the next three. This is the one byte that is not per-operator.",
       note:"algorithm 5, feedback 7."},
      {label:"terminator",k:"sync",r:[26,27],val:"00 FF",
       body:"A number of 0 with no registers behind it: the end of the list. The memo strings begin on the very next byte.",
       note:"in every file measured."}
    ]);

  // the memo pointer table, at the end of the file
  build("pmd-memo","byte",
    [0x8C,0x1B, 0x8D,0x1B, 0x8E,0x1B, 0x8F,0x1B, 0xA6,0x1B, 0xAA,0x1B, 0x00,0x00],
    [
      {label:"#PPZFile",k:"sync",r:[0,1],val:"-> 7053",
       body:"Present because the tag is 0x48. Points at an empty string here: this tune uses no PPZ8 bank.",
       note:"an empty string and a lone / both mean not written."},
      {label:"#PPSFile",k:"sync",r:[2,3],val:"-> 7054",body:"Present because the tag is at least 0x42. Empty.",note:""},
      {label:"#PCMFile",k:"sync",r:[4,5],val:"-> 7055",body:"Always present. Empty: part J was a single 0x80, so there is nothing to name.",note:""},
      {label:"#Title",k:"enum",r:[6,7],val:"-> 7056",
       body:"The tune's title, as a NUL-terminated Shift-JIS string: Hartmann's Youkai Girl.",
       note:"the first thing most readers want."},
      {label:"#Composer",k:"enum",r:[8,9],val:"-> 7079",body:"ZUN.",note:""},
      {label:"#Arranger",k:"enum",r:[10,11],val:"-> 7083",body:"pedipanol. A field that only comes out right if the slot order above is right.",note:""},
      {label:"end",k:"sync",r:[12,13],val:"0000",
       body:"A zero pointer ends the table. Any #Memo lines would sit between the arranger and here.",
       note:"up to 128 of them, by the MML manual."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / pmd anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / pmd anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
