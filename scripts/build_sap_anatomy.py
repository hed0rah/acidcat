"""Generate docs/formats/sap-anatomy.html.

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
OUT = os.path.join(DOCS, "sap-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>SAP Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Atari 8-bit XL / XE</b>POKEY . 6502<br>rev 2026.08</div>
    </div>
    <div class="strip">
      <div>magic <b>SAP CR LF</b></div>
      <div>header <b>ASCII text</b></div>
      <div>payload <b>Atari executable</b></div>
      <div>endian <b>little</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.sap</b> is two files concatenated: a header of plain ASCII tag lines, then
    a standard <b>Atari executable</b> holding the 6502 player and its music data. The specification
    points out you can build one with <code>cat</code>. Like NSF and PSID it ships the program rather
    than the music, and the tags say which addresses to call and how often.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the sound hardware</div>
  <p class="note"><b>POKEY</b> is the chip every SAP exists to drive, and it is not a synthesiser so
  much as a bank of dividers. Four channels, each an <b>8-bit frequency divider</b> off the main
  clock with its own 4-bit volume. Pairs can be joined into <b>16-bit</b> channels, trading two voices
  for the pitch resolution that bass lines need -- an 8-bit divider is coarse enough that low notes
  land audibly out of tune.</p>
  <p class="note">Timbre comes from <b>polynomial counters</b> rather than from waveform shapes. A
  17-bit, a 5-bit and a 4-bit shift register can be switched into a channel's output to break up the
  square wave, giving everything from pure tone through buzzing pitched distortion to white noise.
  The same mechanism produces both the noise and the character, which is why POKEY music sounds the
  way it does.</p>
  <p class="note">Two further techniques matter for what the binary half will be doing. Setting a
  channel to <b>volume-only</b> mode turns its volume register into a 4-bit DAC, which is how
  digitised samples are played -- and why the <code>TYPE D</code> files below need cycle-exact timing.
  And a second POKEY may be fitted for stereo, declared by the <code>STEREO</code> tag.</p>

  <div class="sec">the signature</div>
  <p class="note">Five bytes, and that is the entire magic: the three characters <code>SAP</code>
  followed immediately by CR LF. It is a weak signature -- three common letters and a line ending --
  so identification should require a valid second line before accepting a file.</p>
  <div id="sap-magic"></div>

  <div class="sec">the text header</div>
  <p class="note">One tag per line, CR LF terminated. A line is an uppercase tag name, then
  optionally a single space and an argument. Arguments are a quoted string, a decimal integer, a
  hexadecimal address, or a single letter.</p>

  <table>
    <tr><td class="meth">AUTHOR</td><td>Composer. Real name with an optional scene handle in parentheses; multiple authors joined with <code>&amp;</code>.</td></tr>
    <tr><td class="meth">NAME</td><td>Title.</td></tr>
    <tr><td class="meth">DATE</td><td>Year, <code>DD/MM/YYYY</code>, or a range. <code>199?</code> is a legal way to say &#8220;some year in the nineties&#8221;.</td></tr>
    <tr><td class="meth">SONGS</td><td>Subsong count. Omitted when there is one.</td></tr>
    <tr><td class="meth">DEFSONG</td><td>Which subsong plays first, indexed from <b>zero</b>. Defaults to 0.</td></tr>
    <tr><td class="meth">TYPE</td><td>Player type: <code>B</code>, <code>C</code>, <code>D</code>, <code>S</code> or <code>R</code>. Determines which other tags are required.</td></tr>
    <tr><td class="meth">INIT</td><td>Address of the setup routine. Required for B, D and S; <b>invalid</b> for C.</td></tr>
    <tr><td class="meth">MUSIC</td><td>Address of the music data. Required for C; <b>invalid</b> for everything else.</td></tr>
    <tr><td class="meth">PLAYER</td><td>Address of the routine called on the timer.</td></tr>
    <tr><td class="meth">FASTPLAY</td><td>Scanlines between player calls. A scanline is 114 clock cycles; the default is one frame, 312 for PAL and 262 for NTSC.</td></tr>
    <tr><td class="meth">STEREO</td><td>Dual POKEY. Takes no argument.</td></tr>
    <tr><td class="meth">NTSC</td><td>Play as NTSC rather than the default PAL. Takes no argument.</td></tr>
    <tr><td class="meth">COVOX</td><td>COVOX expansion at the given address, which can only be <code>D600</code>.</td></tr>
    <tr><td class="meth">TIME</td><td>Duration as <code>M:SS.fff</code>, optionally followed by <code>LOOP</code>. <b>One line per subsong</b>, in order.</td></tr>
  </table>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the character set is narrower than ASCII</span><span class="rspan">0x20-0x5F . 0x61-0x7A . 0x7C</span></summary>
      <div class="rbody">
        <p class="note">Arguments may use only characters that mean the same thing in ASCII and in
        <b>ATASCII</b>, the Atari's own encoding: space through underscore, all lowercase letters, and
        the pipe. There is no backquote, tilde or curly brace in ATASCII, so those four cannot appear.</p>
        <p class="note">There is no escape mechanism, and doublequotes inside a quoted argument are
        best avoided because not every player copes. Arguments should stay within 120 characters plus
        the enclosing quotes.</p>
        <p class="note">The narrow set has a second consequence, used below: <b>0xFF cannot occur in a
        valid header</b>.</p>
      </div>
    </details>
  </div>

  <div class="sec">where the text stops</div>
  <p class="note">The specification defines <b>no end-of-header marker</b>. There is no terminating
  tag, no blank line, no length field. The boundary has to be inferred, and the inference is sound
  rather than quoted: the header cannot contain <code>0xFF</code>, and the binary half is required
  to begin with two of them.</p>
  <p class="note">So the first <code>FF FF</code> is the boundary. Checking that the byte before it is
  a line ending is what separates a real boundary from an <code>FF FF</code> that happens to fall
  inside a damaged file.</p>
  <div id="sap-boundary"></div>

  <div class="sec">the binary half</div>
  <p class="note">A standard Atari executable: one or more blocks, each naming where it loads. This is
  the same layout a <code>.xex</code> uses, and it is the one part of SAP with a second independent
  description.</p>
  <div id="sap-block"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the end address is inclusive</span><span class="rspan">end - start + 1</span></summary>
      <div class="rbody">
        <p class="note">A block holds <code>end - start + 1</code> bytes. Reading it as
        <code>end - start</code> loses the last byte of every block in the file, and the file still
        parses, so nothing complains and the damage is silent.</p>
        <p class="note">A block whose end address is below its start is malformed.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">FF FF is optional after the first block</span><span class="rspan">so you cannot resynchronise</span></summary>
      <div class="rbody">
        <p class="note">The two <code>0xFF</code> bytes are required only on the <b>first</b> block.
        Later blocks may omit them, which means a reader cannot recover from a bad length by scanning
        forward for the next <code>FF FF</code> -- there may not be one, and a run of two <code>FF</code>
        bytes inside block data is indistinguishable from a header.</p>
        <p class="note">Blocks must be walked in order from the first. One wrong length desynchronises
        everything after it.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">0x02E0 is not special here</span><span class="rspan">unlike a real Atari executable</span></summary>
      <div class="rbody">
        <p class="note">In an ordinary Atari executable, <code>0x02E0-0x02E1</code> holds the run
        address and <code>0x02E2-0x02E3</code> the initialisation address, and the loader jumps to
        them. In SAP those are <b>ordinary RAM</b> with no special treatment: the <code>INIT</code> and
        <code>PLAYER</code> tags supply the entry points instead.</p>
        <p class="note">A block loading data to <code>0x02E0</code> is therefore just a block, not an
        entry vector.</p>
      </div>
    </details>
  </div>

  <div class="sec">the player types</div>
  <p class="note">The <code>TYPE</code> tag does more than label the file: it determines the calling
  convention, and which other tags are legal.</p>

  <table>
    <tr><td class="meth">B</td><td>The standard. The subsong number goes in the accumulator, <code>INIT</code> is called and returns, then <code>PLAYER</code> is called every <code>FASTPLAY</code> scanlines and returns each time.</td></tr>
    <tr><td class="meth">C</td><td>A convenience type for Chaos Music Composer. <code>MUSIC</code> is required and <code>INIT</code> is invalid; setup is a fixed call sequence into <code>PLAYER+3</code>, after which <code>PLAYER+6</code> is called on the timer.</td></tr>
    <tr><td class="meth">D</td><td>Digitised audio. Like B except the <code>INIT</code> routine <b>never returns</b> -- it drives the audio itself using WSYNC, VCOUNT and POKEY timer interrupts. Registers are saved before each <code>PLAYER</code> call and restored back into the still-running routine.</td></tr>
    <tr><td class="meth">S</td><td>A convenience type for SoftSynth. <code>PLAYER</code> is not used; instead location <code>0x45</code> is decremented on the timer and <code>0xB07B</code> is incremented when it reaches zero. The default <code>FASTPLAY</code> for this type is 78, not 312.</td></tr>
    <tr><td class="meth">R</td><td>A raw dump of POKEY registers <code>0xD200-0xD208</code> at the <code>FASTPLAY</code> rate, <b>instead of</b> an executable. The binary half is therefore not block-structured, and the boundary rule above does not apply.</td></tr>
  </table>

  <p class="note">The specification is internally inconsistent about <code>R</code>: the description of
  the <code>TYPE</code> tag lists only B, C, D and S, while the player-types section documents R in
  full. Reading it as defined-but-unimplemented resolves the contradiction.</p>

  <div class="tree"><b>SAP file layout</b>
0x00  <b>text header</b>       ASCII, CR LF line endings
      "SAP" CR LF        the entire signature
      AUTHOR / NAME / DATE
      SONGS / DEFSONG
      TYPE               decides which tags below are legal
      INIT / MUSIC / PLAYER / FASTPLAY
      TIME               one line per subsong
      (no end marker -- the boundary is the first FF FF)

 ..   <b>binary half</b>       standard Atari executable
      FF FF              required on block 1, optional after
      start              u16 little-endian
      last               u16 little-endian, INCLUSIVE
      data               end - start + 1 bytes
      ...                further blocks, walked in order
</div>
</div>
"""

BUILDS = """
  build("sap-magic","byte",[0x53,0x41,0x50,0x0D,0x0A],[
    {label:"SAP",k:"sync",r:[0,2],val:"S A P",
     body:"Three ASCII characters. On their own they are far too weak to identify a file, which is why a reader should also require the next line to parse as a tag.",
     note:"53 41 50."},
    {label:"CR",k:"sync",r:[3,3],val:"0x0D",
     body:"Carriage return. The specification asks for CR LF throughout and gives no allowance for either character alone.",
     note:"part of the signature."},
    {label:"LF",k:"sync",r:[4,4],val:"0x0A",
     body:"Line feed, closing the signature line. Everything after this is tag text until the binary half begins.",
     note:"part of the signature."}
  ]);

  // real bytes: the last two characters of a TIME line, then the boundary
  build("sap-boundary","byte",[0x4F,0x50,0x0D,0x0A,0xFF,0xFF,0x00,0x40],[
    {label:"text",k:"sync",r:[0,1],val:"O P",
     body:"The tail of the final tag line. Ordinary header characters, inside the ASCII-and-ATASCII set.",
     note:"the end of a TIME line."},
    {label:"CR LF",k:"sync",r:[2,3],val:"0x0D 0x0A",
     body:"The line ending that closes the last tag. Checking for this immediately before the FF FF is what makes the boundary trustworthy rather than a guess.",
     note:"the last bytes of the header."},
    {label:"FF FF",k:"enum",r:[4,5],val:"boundary",
     body:"Two 0xFF bytes. Because the header's character set stops at 0x7C, neither byte can occur in valid header text, so the first occurrence is unambiguously the start of the binary half.",
     note:"required on the first block."},
    {label:"start",k:"enum",r:[6,7],val:"$4000",
     body:"The load address of the first block, and the first byte of the executable proper.",
     note:"00 40 little-endian."}
  ]);

  // real bytes: the first block header of a type B tune
  build("sap-block","byte",[0xFF,0xFF,0x00,0x40,0x49,0x4D,0x52,0x4D],[
    {label:"header",k:"sync",r:[0,1],val:"FF FF",
     body:"Marks the start of a block. Required on the first block only; later blocks may begin straight at their start address, which is why a reader cannot resynchronise by searching for this pattern.",
     note:"2 bytes."},
    {label:"start",k:"enum",r:[2,3],val:"$4000",
     body:"Where this block's data loads in the 6502 address space.",
     note:"00 40 = 0x4000."},
    {label:"last",k:"enum",r:[4,5],val:"$4D49",
     body:"The address of the LAST byte of the block, not one past it. The block therefore holds end minus start plus one bytes: 0x4D49 - 0x4000 + 1 = 3402.",
     note:"49 4D = 0x4D49, inclusive."},
    {label:"data",k:"rsv",r:[6,7],val:"52 4D",
     body:"The first two bytes of the block's contents, loading to 0x4000 and 0x4001. From here the bytes are 6502 code and music data, meaningful only to the machine.",
     note:"3402 bytes follow in total."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / sap anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / sap anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
