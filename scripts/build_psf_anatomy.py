"""Generate docs/formats/psf-anatomy.html.

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
OUT = os.path.join(DOCS, "psf-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>PSF Anatomy</h1></div></div>
      <div class="stamp"><b>Portable Sound Format</b>eight consoles . one container<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>PSF + version</b></div>
      <div>endian <b>little</b></div>
      <div>container <b>own</b></div>
      <div>program <b>zlib, checksummed</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>PSF</b> file is a console's sound program and the memory it runs in, compressed,
    with a checksum and a tag block. Neill Corlett made it in 2002 for PlayStation dumps, and then
    everyone who needed to ship the same thing for a different chip borrowed it: one <b>version
    byte</b> names the machine, and nothing else about the container changes. So a
    <code>.psf</code>, a <code>.minigsf</code>, a <code>.ssf</code> and a <code>.usf</code> are the
    same file with a different byte 3.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">a whole file, 221 bytes</div>
  <p class="note">This is a complete Game Boy Advance mini: sixteen bytes of header, a 22-byte
  compressed program, and a tag block. Nothing is left out. The program inflates to fourteen bytes,
  and twelve of those are a header, which is the point of a mini -- the tune lives in a
  library file the tag names, and this file is just the song number.</p>
  <div id="psf-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the version byte</span><span class="rspan">which machine</span></summary>
      <div class="rbody">
        <p class="note">Three letters and then the only byte that differs between platforms.</p>
        <div class="kv">
          <div><span class="k">0x01</span><span class="v">PSF1, PlayStation</span></div>
          <div><span class="k">0x02</span><span class="v">PSF2, PlayStation 2</span></div>
          <div><span class="k">0x11</span><span class="v">SSF, Sega Saturn</span></div>
          <div><span class="k">0x12</span><span class="v">DSF, Sega Dreamcast</span></div>
          <div><span class="k">0x21</span><span class="v">USF, Nintendo 64</span></div>
          <div><span class="k">0x22</span><span class="v">GSF, Game Boy Advance</span></div>
          <div><span class="k">0x23</span><span class="v">SNSF, Super Nintendo</span></div>
          <div><span class="k">0x24</span><span class="v">2SF, Nintendo DS</span></div>
          <div><span class="k-en">0x25</span><span class="v">NCSF, Nintendo DS (Nitro Composer)</span></div>
          <div><span class="k-en">0x41</span><span class="v">QSF, Capcom QSound arcade boards</span></div>
        </div>
        <p class="note">The letters alone are not enough to identify a file: <code>PSF</code> opens
        ordinary text too. A reader checks the version byte with them.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the program, and its checksum</span><span class="rspan">zlib . CRC32</span></summary>
      <div class="rbody">
        <p class="note">After the header and the reserved area comes the program, <b>zlib-compressed</b>,
        of the size the header states. What the program IS depends on the machine: a PlayStation
        executable, an N64 ROM image, a GBA ROM. The container does not say, and a reader that
        does not know the platform's program layout should not guess at it.</p>
        <p class="note">What the container does say is whether the program is <b>intact</b>. The
        header carries a CRC32 of the compressed bytes. Most formats give a reader nothing to check
        against; this one hands over a checksum, so "the program is undamaged" is a fact rather than
        a hope.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">minis and libraries</span><span class="rspan">_lib</span></summary>
      <div class="rbody">
        <p class="note">A soundtrack shares one sound engine and one sample set across every track.
        Shipping all of it in every file would multiply a game's music by its track count, so the
        format splits it: the shared part goes in a <b>library</b> (<code>.psflib</code>,
        <code>.gsflib</code>, <code>.ssflib</code>...) and each track is a <b>mini</b> whose program is
        a few bytes patched over the library's, and whose <code>_lib</code> tag names the library.</p>
        <div class="kv">
          <div><span class="k">library</span><span class="v">the engine and the samples; carries no tags, because it is data</span></div>
          <div><span class="k">mini</span><span class="v">the song number, and the tags; loads on top of the library</span></div>
          <div><span class="k-en">_lib, _lib2 ...</span><span class="v">a mini may name several, loaded in order</span></div>
        </div>
        <p class="note">A mini separated from its library is a song number with nothing to play. The
        library sits beside it by convention, and a reader can check.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the GBA program header</span><span class="rspan">GSF only . 12 bytes</span></summary>
      <div class="rbody">
        <p class="note">Once the program is inflated, a <b>GSF</b> program opens with three
        little-endian words and then exactly that much ROM:</p>
        <div id="psf-gba"></div>
        <p class="note">A library's ROM is the whole game's sound side, up to sixteen megabytes. A
        mini's is one or two bytes at an offset inside the library's image: the song number, written
        where the engine reads it. This header is decoded because it is documented and holds on every
        file measured; the other machines' programs are reported as a size that inflates and
        checksums, and nothing more is claimed.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the DS program and its SAVE block</span><span class="rspan">2SF only . 8 bytes + a reserved area</span></summary>
      <div class="rbody">
        <p class="note">A <b>2SF</b> program is the GBA layout without the entry point: two little-endian
        words, <b>offset</b> and <b>length</b>, then that many bytes of DS ROM. A library holds the
        whole cartridge image, up to 64 MB inflated; a mini holds two bytes, the song number, at an
        offset inside it.</p>
        <p class="note">2SF is the one machine that uses the header's <b>reserved area</b>. When present
        it is a <b>SAVE</b> block: the four letters, a little-endian zlib size, the CRC32 of the zlib
        stream, then the stream. Inflated, it has the same shape as the program: <b>offset</b>,
        <b>length</b>, data. It is a patch into the emulator's save state; a mini's is four bytes, and
        a mini may carry a SAVE patch and a tag and <i>no program at all</i>, the library holding
        everything else.</p>
        <div class="kv">
          <div><span class="k">program</span><span class="v">u32 offset, u32 length, ROM</span></div>
          <div><span class="k">reserved</span><span class="v">"SAVE", u32 zlib size, u32 CRC32, zlib &rarr; u32 offset, u32 length, data</span></div>
        </div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the tag block</span><span class="rspan">[TAG] . key=value</span></summary>
      <div class="rbody">
        <p class="note">Optional, at the end, marked by five literal bytes. Then one <code>key=value</code>
        per line, LF-separated, UTF-8. A key that repeats is a value continued on the next line.</p>
        <div class="kv">
          <div><span class="k">title, artist, game</span><span class="v">what a player shows</span></div>
          <div><span class="k">year, genre, copyright</span><span class="v">catalogue fields</span></div>
          <div><span class="k">length, fade</span><span class="v">how long to play, then how long to fade, as <code>m:ss</code> or seconds</span></div>
          <div><span class="k">volume</span><span class="v">a gain to apply</span></div>
          <div><span class="k">psfby, gsfby, ssfby ...</span><span class="v">who made the rip, named per platform</span></div>
          <div><span class="k-en">_lib</span><span class="v">the library; the one tag that changes what plays rather than what is shown</span></div>
        </div>
        <div id="psf-tag"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">little-endian</span></summary>
      <div class="rbody">
        <p class="note">The three header words are little-endian, as is the GBA header inside a GSF
        program. Other machines' programs follow their own machine; the container does not care what
        is inside the zlib.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / psf anatomy</span>
    <span>portable sound format . eight consoles . zlib . crc32 . minis and libraries</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the header and the compressed program of a real GBA mini: 38 bytes
  build("psf-head","byte",
    [0x50,0x53,0x46, 0x22,
     0x00,0x00,0x00,0x00,
     0x16,0x00,0x00,0x00,
     0x42,0xCE,0xD9,0xAE,
     0x78,0xDA,0x63,0x60,0x60,0xE0,0x58,0xA3,0x24,0xC7,0xC1,0xC4,0xC0,0xC0,0x20,0xC0,0x00,0x00,0x09,0xA4,0x01,0x0F],
    [
      {label:"magic",k:"sync",r:[0,2],val:"PSF",
       body:"Three letters. Not enough on their own -- ordinary text can open this way -- so the version byte is part of the identification.",
       note:"50 53 46."},
      {label:"version",k:"enum",r:[3,3],val:"0x22 = GSF",sel:5,branch:[
        ["0x01","PlayStation"],["0x02","PlayStation 2"],["0x11","Saturn"],["0x12","Dreamcast"],["0x21","Nintendo 64"],["0x22","Game Boy Advance"],["0x23","Super Nintendo"],["0x24","Nintendo DS"],["0x25","Nintendo DS (Nitro Composer)"],["0x41","QSound"]],
       body:"Which machine the program runs on. This is the only byte in the container that differs between platforms; everything after it is laid out the same for all eight.",
       note:"the whole difference between a .psf and a .minigsf."},
      {label:"reserved size",k:"sync",r:[4,7],val:"0",
       body:"How many bytes of platform-specific data sit between this header and the program. Zero in every GBA file measured; other platforms use it.",
       note:"little-endian."},
      {label:"program size",k:"enum",r:[8,11],val:"0x16 = 22",
       body:"The COMPRESSED size of the program that follows. Twenty-two bytes here, which inflates to fourteen.",
       note:"compressed, not inflated."},
      {label:"CRC32",k:"enum",r:[12,15],val:"0xAED9CE42",
       body:"A checksum of the compressed program. A reader computes its own and compares, and gets a yes or a no rather than a guess. Every file measured agrees with its own.",
       note:"of the 22 bytes below, as they sit in the file."},
      {label:"program",k:"sync",r:[16,37],val:"zlib, 22 bytes",
       body:"78 DA is zlib's header at best compression. Inflated, this is the twelve-byte GBA header on the next map and two bytes of ROM.",
       note:"decompress before reading anything in it."}
    ]);

  // the same program, inflated: the GBA header and the two-byte patch
  build("psf-gba","byte",
    [0x00,0x00,0x00,0x08,
     0xAC,0x22,0x1E,0x08,
     0x02,0x00,0x00,0x00,
     0x10,0x00],
    [
      {label:"entry point",k:"sync",r:[0,3],val:"0x08000000",
       body:"Where the GBA starts executing. 0x08000000 is the start of cartridge ROM on every GBA, so this is the same in every file.",
       note:"little-endian."},
      {label:"load offset",k:"enum",r:[4,7],val:"0x081E22AC",
       body:"Where the ROM bytes below are placed in the GBA's address space. For a library that is the base of the image; for a mini like this one it is an address INSIDE the library's image, where the engine reads its song number.",
       note:"the patch address."},
      {label:"length",k:"enum",r:[8,11],val:"2",
       body:"How many bytes of ROM follow. Two. A library says something in the megabytes here; a mini says one or two. Every file measured has exactly this many bytes after the header, with no exceptions.",
       note:"12 + length = the inflated size, always."},
      {label:"ROM",k:"enum",r:[12,13],val:"10 00 = 16",
       body:"The song number, 16, patched over the library at the offset above. This is the entire musical content of the file: everything else comes from target.gsflib.",
       note:"the tune is two bytes; the library is the rest."}
    ]);

  // the first forty bytes of the tag block
  build("psf-tag","byte",
    [0x5B,0x54,0x41,0x47,0x5D,
     0x5F,0x6C,0x69,0x62,0x3D,0x74,0x61,0x72,0x67,0x65,0x74,0x2E,0x67,0x73,0x66,0x6C,0x69,0x62,0x0A,
     0x67,0x73,0x66,0x62,0x79,0x3D,0x53,0x61,0x70,0x54,0x61,0x70,0x70,0x65,0x72,0x2F],
    [
      {label:"marker",k:"sync",r:[0,4],val:"[TAG]",
       body:"Five literal bytes. If they are not here, there is no tag block, and a library legitimately has none.",
       note:"no length; the block runs to the end of the file."},
      {label:"_lib",k:"enum",r:[5,22],val:"_lib=target.gsflib",
       body:"The one tag that changes what PLAYS rather than what is shown: the library this mini loads on top of. A second library would be _lib2, and so on, loaded in order.",
       note:"key, equals, value, LF."},
      {label:"LF",k:"sync",r:[23,23],val:"0x0A",body:"Lines end in a bare line feed.",note:""},
      {label:"gsfby",k:"sync",r:[24,39],val:"gsfby=SapTapper/",
       body:"Who made the rip, named per platform: psfby for PlayStation, gsfby here. The line continues past this map with the rest of the name.",
       note:"title, length, game, artist and year follow."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / psf anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / psf anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
