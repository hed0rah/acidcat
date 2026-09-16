"""Generate docs/formats/gbs-anatomy.html.

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
OUT = os.path.join(DOCS, "gbs-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>GBS Anatomy</h1></div></div>
      <div class="stamp"><b>Nintendo Game Boy</b>Game Boy Sound System<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>GBS + version</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own</b></div>
      <div>samples <b>none; four channels of chip</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.gbs</b> is a Game Boy's music, cut out of the game: the sound engine and its
    data, with a 112-byte header saying where to load it and which routine to call. It is the same
    idea as an NSF for the NES, and it is simpler, because the Game Boy has no expansion chips and
    nothing in the header is reserved for anything. A player loads the code, calls
    <code>init</code> with a song number, then calls <code>play</code> sixty times a second.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the header, 112 bytes</div>
  <p class="note">Sixteen bytes of numbers, then three 32-byte text slots. Every multi-byte value
  is little-endian, as the Game Boy's processor is. The code follows immediately and runs to the
  end of the file.</p>
  <div id="gbs-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">three addresses</span><span class="rspan">load . init . play</span></summary>
      <div class="rbody">
        <p class="note">The code is placed at <b>load</b>. <b>init</b> is called once per song with
        the song number in register A. <b>play</b> is called on every tick, and is the routine that
        actually writes the sound registers.</p>
        <div class="kv">
          <div><span class="k">$0400 - $7FFF</span><span class="v">the cartridge ROM window once the boot ROM is paged out</span></div>
          <div><span class="k-en">the rule</span><span class="v">all three must land inside it, and the spec says so in as many words</span></div>
        </div>
        <p class="note">A file whose code runs past <code>$7FFF</code> is not wrong: a player maps
        the excess as switched banks, the way a cartridge does. One that loads at <code>$0070</code>
        is unusual and real -- the code sits over the interrupt vectors -- and a reader names it
        rather than refusing it.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the timer</span><span class="rspan">TMA . TAC</span></summary>
      <div class="rbody">
        <p class="note">Two bytes that say how often <code>play</code> is called. Both zero means
        <b>every VBlank</b>, 59.7 times a second, which is what nearly every game did. Non-zero values
        program the Game Boy's hardware timer instead, for engines that ran faster than the frame.</p>
        <div class="kv">
          <div><span class="k">TMA</span><span class="v">the timer's modulo, what it reloads to; the period is 256 &minus; TMA ticks, so 0 is the slowest, not off</span></div>
          <div><span class="k">TAC</span><span class="v">bit 2 enables the timer; bits 0-1 pick the divider: 1024, 16, 64, 256</span></div>
          <div><span class="k">rate</span><span class="v">4194304 / (divider &times; (256 &minus; TMA)) Hz when enabled; VBlank when not</span></div>
        </div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the text slots</span><span class="rspan">3 x 32 bytes</span></summary>
      <div class="rbody">
        <p class="note">Title, author, copyright. Each slot is <b>always 32 bytes</b>; a NUL ends the
        text inside it without ending the field. A reader that scans for the NUL instead of stopping
        at the slot runs a full 32-byte title into the author, which is how the composer's name ends
        up appended to the game's.</p>
        <p class="note">Nothing declares the encoding. Nominally ASCII; a Japanese rip may carry
        Shift-JIS, and no byte says so.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">what is not here</span><span class="rspan">and why</span></summary>
      <div class="rbody">
        <p class="note">No per-song titles, no lengths, no expansion-chip flags, no bankswitching
        table. An NSF header has all four. The Game Boy's sound is four channels on one chip with no
        variants, and banking needs no table: the code is loaded from the load address up to $7FFF,
        and whatever is left fills 16 KB ROM banks from bank 1, which the engine maps in at
        $4000-$7FFF by writing a bank number to $2000, the way an MBC1 cartridge does. A file
        larger than the address space is the normal shape of a whole game's music. The song count
        and the first song are the whole of the per-song information.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">GBS + 01</span></summary>
      <div class="rbody">
        <p class="note">Three letters and a version byte. The letters alone open ordinary text; the
        version byte is checked with them, and every file ever made is version 1.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">little-endian</span></summary>
      <div class="rbody">
        <p class="note">The Game Boy's processor is an 8-bit Z80 relative and stores words low byte
        first. Every address in the header follows it.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / gbs anatomy</span>
    <span>nintendo game boy . game boy sound system . 112-byte header . code follows</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the first 16 bytes of a real header, then the start of the title slot
  build("gbs-head","byte",
    [0x47,0x42,0x53, 0x01, 0x10, 0x01,
     0xE0,0x3F, 0xE0,0x3F, 0x00,0x40, 0xE0,0xDF,
     0x00, 0x00,
     0x41,0x73,0x74,0x65,0x72,0x69,0x78,0x20,0x26,0x20,0x4F,0x62,0x65,0x6C,0x69,0x78,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    [
      {label:"magic",k:"sync",r:[0,2],val:"GBS",
       body:"Three letters. Ordinary text can open the same way, so the version byte is part of the identification.",
       note:"47 42 53."},
      {label:"version",k:"enum",r:[3,3],val:"1",
       body:"The only version there has ever been.",
       note:"checked with the magic."},
      {label:"songs",k:"enum",r:[4,4],val:"16",
       body:"How many songs the code can play. A player offers this many; the init routine is called with a number from 1 to this.",
       note:"1 to 255."},
      {label:"first song",k:"enum",r:[5,5],val:"1",
       body:"Which song to start on. One-based, and almost always 1.",
       note:""},
      {label:"load",k:"enum",r:[6,7],val:"$3FE0",
       body:"Where the code that follows the header is placed in the Game Boy's address space. This engine loads just below $4000, which is where the switchable bank begins.",
       note:"little-endian: E0 3F."},
      {label:"init",k:"enum",r:[8,9],val:"$3FE0",
       body:"Called once per song, with the song number in register A. Here it is the very first byte of the loaded code.",
       note:"often equals load."},
      {label:"play",k:"enum",r:[10,11],val:"$4000",
       body:"Called on every tick. This one sits at $4000, which the load address puts 32 bytes into the code.",
       note:"the routine that writes the sound registers."},
      {label:"stack",k:"sync",r:[12,13],val:"$DFE0",
       body:"Where the stack pointer is set before init is called. $DFE0 is near the top of work RAM, which is the conventional place.",
       note:""},
      {label:"TMA",k:"sync",r:[14,14],val:"0",
       body:"Timer modulo. Zero here, and with the control byte also zero, play is called every VBlank: 59.7 Hz.",
       note:"both zero = VBlank."},
      {label:"TAC",k:"sync",r:[15,15],val:"0",body:"Timer control. Zero: the timer is not used.",note:""},
      {label:"title",k:"enum",r:[16,47],val:"Asterix & Obelix",
       body:"The first of three 32-byte text slots. The text ends at its NUL; the slot does not, and a reader that forgets runs this slot into the next one, which is the author.",
       note:"always 32 bytes; author and copyright follow, the same size."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / gbs anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / gbs anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
