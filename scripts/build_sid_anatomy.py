"""Generate docs/formats/sid-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "sid-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>SID Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>PSID / RSID</b>C64 sidtune<br>rev 2026.08</div>
    </div>
    <div class="strip">
      <div>magic <b>PSID / RSID</b></div>
      <div>endian <b>big (header)</b></div>
      <div>container <b>own</b></div>
      <div>sample <b>50 53 49 44</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.sid</b> file contains no audio. It is a fixed <b>big-endian</b> header
    describing a 6502 machine-code music player, followed by that player and its data as a raw
    <b>Commodore 64 memory image</b>. Playback means loading the image into a C64, calling an
    <b>init</b> routine with the subtune number in the accumulator, then calling a <b>play</b>
    routine at interrupt rate while the code writes the SID chip's registers itself.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">byte order</div>
  <p class="note">Every multi-byte field in the header is <b>Motorola big-endian</b>, on a file
  format that describes a little-endian 6502. The format was designed on the Amiga and the byte
  order followed the host rather than the target. The C64 image after <code>dataOffset</code> is
  native little-endian.</p>

  <div class="sec">header v1 -- the fixed numeric block</div>
  <p class="note">Offsets 0x00 through 0x15. Identical in every header version; v2 and later only
  append.</p>
  <div id="sid-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the two magics</span><span class="rspan">0x00 . 4 bytes</span></summary>
      <div class="rbody">
        <p class="note"><b>RSID</b> is <b>PSID</b> with four fields pinned and a
        stricter runtime contract, because the tune needs a real C64 rather than the shortcuts
        early emulators took. A reader that meets an RSID breaking any of these must reject it.</p>
        <div class="kv">
          <div><span class="k">PSID</span><span class="v">0x50534944 -- permissive; runs on PlaySID and libsidplay1 era emulators</span></div>
          <div><span class="k">RSID</span><span class="v">0x52534944 -- requires a true C64 environment</span></div>
          <div><span class="k-en">RSID: version</span><span class="v">2, 3 or 4 only -- never 1</span></div>
          <div><span class="k-en">RSID: loadAddress</span><span class="v">must be 0, so the address comes from the data</span></div>
          <div><span class="k-en">RSID: playAddress</span><span class="v">must be 0, so init installs its own interrupt handler</span></div>
          <div><span class="k-en">RSID: speed</span><span class="v">must be 0, so the tune configures its own timing</span></div>
          <div><span class="k-en">RSID: load floor</span><span class="v">the effective load address must not be below $07E8, which is $0400 plus the 1,000 bytes of default screen RAM: the first byte a tune can occupy without landing on the screen</span></div>
          <div><span class="k-en">RSID: init</span><span class="v">must not point into $A000-$BFFF or $D000-$FFFF; both ROMs are banked in</span></div>
        </div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">where the load address lives</span><span class="rspan">0x08 . 2 bytes</span></summary>
      <div class="rbody">
        <p class="note">A <code>loadAddress</code> of <b>0</b> does not mean address zero. It means
        the C64 data is an ordinary C64 binary whose <b>first two bytes are the load address,
        little-endian</b> -- the layout <code>SAVE</code> writes and <code>LOAD"FILE",8,1</code>
        expects. Those two bytes are then not code, and the image begins after them.</p>
        <p class="note">A non-zero <code>loadAddress</code> means the opposite: the data is all
        code and carries no address of its own. Nothing in the bytes distinguishes the two cases,
        which is why the field exists and why writing it wrongly displaces an entire tune by two
        bytes.</p>
        <div id="sid-data"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">init, play and the speed bits</span><span class="rspan">0x0A . 0x12</span></summary>
      <div class="rbody">
        <p class="note"><code>initAddress</code> is called once per subtune with the subtune number
        in the accumulator. 0 means it equals the effective load address.</p>
        <p class="note"><code>playAddress</code> is called repeatedly to produce sound. 0 means
        init installs its own interrupt handler and the player must not call anything --
        always the case for RSID.</p>
        <p class="note"><code>speed</code> is one bit per subtune, bit 0 being subtune 1: a
        <b>0</b> bit selects the vertical blank interrupt (50 Hz PAL, 60 Hz NTSC), a <b>1</b> bit
        the CIA 1 timer. Past 32 subtunes the two header generations disagree, and only
        the header says which rule applies.</p>
        <p class="note">This is not the field's original design. The specification carries its
        own warning that <code>speed</code> never worked as intended on PlaySID for Amiga, and
        that what is documented today is a <b>redefinition</b> of the v2NG field. The
        <code>clock</code> bits exist because of that failure: the speed field alone cannot
        express NTSC.</p>
        <p class="note">The bits stay meaningful even when nothing reads them. With
        <code>playAddress</code> at 0 they should still be set, for players old enough to act on
        them; a player running in a real C64 environment ignores them in that case.</p>
        <div class="kv">
          <div><span class="k">v1, or PlaySID-specific set</span><span class="v">bits wrap -- subtune 33 reuses bit 0</span></div>
          <div><span class="k">v2NG with that flag clear</span><span class="v">bit 31 repeats for every subtune above 32</span></div>
        </div>
        <p class="note">The wrapping rule above is the self-consistent reading, not the literal
        one. The format description states that "the speed specified for tune 32 is the same as
        tune 1", but its own preceding sentence gives subtune 1 to bit 0, which leaves subtune 32
        holding bit 31. Taking the later sentence literally would strand bit 31 and shift every
        subtune above it by one. The wrap therefore begins at subtune <b>33</b>.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the three text fields</span><span class="rspan">0x16 . 96 bytes</span></summary>
      <div class="rbody">
        <p class="note">Three 32-byte strings in <b>Windows-1252</b>, not ASCII, not Latin-1 and
        not UTF-8. Latin-1 agrees everywhere except 0x80-0x9F, where cp1252 carries typographic
        characters and Latin-1 has C1 control codes.</p>
        <p class="note">A field holding exactly 32 characters <b>has no terminator</b>. A C-string
        read that does not stop at 32 runs straight into the next field and produces a plausible,
        wrong string.</p>
        <div class="kv">
          <div><span class="k">0x16</span><span class="v">name -- 32 bytes</span></div>
          <div><span class="k">0x36</span><span class="v">author -- 32 bytes</span></div>
          <div><span class="k">0x56</span><span class="v">released -- 32 bytes, once called copyright</span></div>
        </div>
        <p class="note">Version 1 of the header ends at <b>0x76</b>, and the C64 data begins there.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the v2 / v3 / v4 tail</span><span class="rspan">0x76 . 6 bytes</span></summary>
      <div class="rbody">
        <p class="note">There are only two header sizes. v1 is <b>0x76</b> bytes; v2, v3 and v4 are
        all <b>0x7C</b> and differ only in which of these trailing bytes carry meaning.</p>
        <div id="sid-tail"></div>
        <div class="kv">
          <div><span class="k">startPage 0x00</span><span class="v">the tune is clean: it writes nothing outside its own data range</span></div>
          <div><span class="k">startPage 0xFF</span><span class="v">not one free page; a driver cannot be relocated</span></div>
          <div><span class="k">otherwise</span><span class="v">start of the largest free page range, e.g. 0x1E means $1E00</span></div>
          <div><span class="k-en">pageLength</span><span class="v">free pages after startPage; must be 0 at both sentinel values</span></div>
        </div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">flags, bit by bit</span><span class="rspan">0x76 . 16 bits</span></summary>
      <div class="rbody">
        <p class="note">Drawn most-significant bit first, the way a 16-bit word is written. The
        specification numbers these from the least significant end, so bit 0 of the spec is the
        rightmost cell here.</p>
        <div id="sid-flags"></div>
        <p class="note">Bit 1 carries two different meanings depending on the magic. In a PSID it
        marks a tune as <b>PlaySID specific</b> -- typically one using PlaySID volume samples,
        which no longer play on a real C64. In an RSID the same bit is the <b>C64 BASIC</b> flag,
        and when it is set <code>initAddress</code> must be 0 and the subtune number is written to
        $030C instead.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">second and third SID</span><span class="rspan">0x7A . 0x7B</span></summary>
      <div class="rbody">
        <p class="note">Each byte encodes the middle of <b>$Dxx0</b>: 0x42 means <b>$D420</b>,
        0xFE means <b>$DFE0</b>. Only <b>even</b> values in 0x42-0x7F or 0xE0-0xFE are legal, and
        any other value -- including 0x00 -- means no chip at that position.</p>
        <p class="note">0x00-0x41 would collide with the first SID
        and the VIC-II; 0x80-0xDF lands in colour RAM and the CIAs. The third SID may not share
        the second SID's address.</p>
        <div class="kv">
          <div><span class="k">secondSIDAddress</span><span class="v">a v3 field; 0 in v2NG</span></div>
          <div><span class="k">thirdSIDAddress</span><span class="v">a v4 field; 0 in v2NG and v3</span></div>
          <div><span class="k-en">model bits unknown</span><span class="v">an extra chip inherits the first SID's model</span></div>
        </div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the environment the file assumes</span><span class="rspan">not in the file</span></summary>
      <div class="rbody">
        <p class="note">Loading the image is not enough. The machine around it has to be in a defined state or the tune's timing is
        wrong from the first frame.</p>
        <div class="kv">
          <div><span class="k">$02A6</span><span class="v">1 for PAL, 0 for NTSC, from the clock bits</span></div>
          <div><span class="k">CIA 1 timer A</span><span class="v">$4025 PAL, $4295 NTSC</span></div>
          <div><span class="k-en">PSID bank register</span><span class="v">set per call from the target address: 0x37, 0x36, 0x35 or 0x34</span></div>
          <div><span class="k-en">RSID bank register</span><span class="v">always 0x37, which is why init may not sit under ROM</span></div>
          <div><span class="k">PSID VIC</span><span class="v">raster IRQ below 0x100, enabled only when the speed bit is 0</span></div>
          <div><span class="k">RSID VIC</span><span class="v">raster IRQ at 0x137, not enabled; the CIA runs instead</span></div>
        </div>
        <p class="note">A tune written for one video standard and played on the other is detuned,
        because the frame rates differ. Playing an NTSC tune on a PAL machine wants a CIA latch of
        $3FFB; a PAL tune on NTSC wants $5021.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">what the payload can be instead</span><span class="rspan">flags bit 0</span></summary>
      <div class="rbody">
        <p class="note">When bit 0 of <code>flags</code> is set the data is not a player at all. It
        is <b>Compute!'s Sidplayer MUS</b> music data with no player attached, and an external
        player must be merged with it before anything can be replayed.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the two chip revisions</span><span class="rspan">flags bits 4-9</span></summary>
      <div class="rbody">
        <p class="note">The <b>MOS6581</b> and <b>MOS8580</b> are not interchangeable, which is why
        the header names one. Combined waveforms are far louder on the 8580, to the point that
        combinations clearly audible on one are silent on the other. The 8580's internal DC levels
        are small enough that volume-register samples need a hardware or software trick. The two
        analog filters have different characteristics, so a filter sweep written for
        one is a different sound on the other.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / sid anatomy</span>
    <span>psid . rsid . big-endian header over a little-endian machine</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = r"""  // v1 header, the fixed numeric block: 0x00 - 0x15
  build("sid-head","byte",
    [0x50,0x53,0x49,0x44, 0x00,0x02, 0x00,0x7C, 0x00,0x00, 0x10,0x00,
     0x10,0x03, 0x00,0x01, 0x00,0x01, 0x00,0x00,0x00,0x00],
    [
      {label:"magicID",k:"enum",r:[0,3],val:'\'"PSID"\'',sel:0,branch:[
        ["PSID","runs on emulators too"],["RSID","requires a real C64"]],
       body:"Four ASCII bytes. RSID is not a separate format but a restricted PSID: loadAddress, playAddress and speed are all pinned to zero and the version must be 2 or higher.",
       note:"50 53 49 44 / 52 53 49 44."},
      {label:"version",k:"enum",r:[4,5],val:"2",sel:2,branch:[
        ["1","header ends at 0x76"],["2","v2NG, header 0x7C"],
        ["3","adds a second SID"],["4","adds a third SID"]],
       body:"Big-endian. Only the value 1 changes the header SIZE; 2, 3 and 4 share one layout and differ in which trailing bytes mean anything.",
       note:"RSID is never version 1."},
      {label:"dataOffset",k:"sync",r:[6,7],val:"0x007C",
       body:"Where the C64 memory image begins, and the exact point at which the file stops being big-endian. 0x0076 for a v1 header, 0x007C for every later one.",
       note:"only two legal values."},
      {label:"loadAddress",k:"enum",r:[8,9],val:"$0000",sel:0,branch:[
        ["0","address is in the data"],["non-zero","data is all code"]],
       body:"Zero means the first two bytes of the C64 data hold the load address, little-endian. Non-zero means the data carries no address and this is it. Required to be zero in an RSID.",
       note:"00 00 here does not mean $0000."},
      {label:"initAddress",k:"sync",r:[10,11],val:"$1000",
       body:"Entry point called once per subtune with the subtune number in the 6510 accumulator. Zero means it equals the effective load address.",
       note:"in an RSID this may not point under ROM."},
      {label:"playAddress",k:"sync",r:[12,13],val:"$1003",
       body:"Entry point called at interrupt rate to produce continuous sound. Zero means init installs its own interrupt handler and nothing should call in.",
       note:"required to be zero in an RSID."},
      {label:"songs",k:"sync",r:[14,15],val:"1",
       body:"How many subtunes the init routine can be asked for. Minimum 1, maximum 256.",
       note:"0x0001 - 0x0100."},
      {label:"startSong",k:"sync",r:[16,17],val:"1",
       body:"Which subtune to play by default, typically the one heard first in the program the rip came from. Defaults to 1.",
       note:"1 through songs."},
      {label:"speed",k:"flag",r:[18,21],val:"0x00000000",
       body:"One bit per subtune, bit 0 for subtune 1: clear selects the vertical blank interrupt, set selects the CIA 1 timer. Required to be zero in an RSID.",
       note:"32 bits, big-endian; behaviour past 32 subtunes depends on the header generation."}
    ]);

  // the v2/v3/v4 tail: 0x76 - 0x7B
  build("sid-tail","byte",
    [0x00,0xA4, 0x00, 0x00, 0x42, 0x00],
    [
      {label:"flags",k:"flag",r:[0,1],val:"0x00A4",
       body:"Sixteen bits of bitfields: payload kind, PlaySID/BASIC, video standard, and up to three SID chip models. Drawn bit by bit in its own map.",
       note:"bits 10-15 are reserved and must be zero."},
      {label:"startPage",k:"enum",r:[2,2],val:"0x00",sel:0,branch:[
        ["0x00","clean, no relocation needed"],["0xFF","no free page at all"],
        ["other","start of the free range"]],
       body:"The start page of the single largest free memory range within the driver ranges, used to relocate a player without colliding with the tune.",
       note:"0x1E would mean $1E00."},
      {label:"pageLength",k:"sync",r:[3,3],val:"0",
       body:"How many free pages follow startPage. Must be zero when startPage is 0x00 or 0xFF, because neither of those names a range.",
       note:"the relocation range must not overlap the load image."},
      {label:"secondSIDAddress",k:"enum",r:[4,4],val:"0x42",sel:0,branch:[
        ["0x42","$D420"],["0x50","$D500"],["0xFE","$DFE0"],["0x00","no second SID"]],
       body:"The middle of $Dxx0 for a second SID chip. Even values in 0x42-0x7F or 0xE0-0xFE only; anything else means no chip. A v3 field.",
       note:"0x00-0x41 and 0x80-0xDF are reserved for other hardware."},
      {label:"thirdSIDAddress",k:"enum",r:[5,5],val:"0x00",sel:3,branch:[
        ["0x42","$D420"],["0x50","$D500"],["0xFE","$DFE0"],["0x00","no third SID"]],
       body:"The same encoding for a third chip, and a v4 field. It may not name the same address as the second SID.",
       note:"zero in v2NG and v3."}
    ]);

  // flags, MSB first across the 16-bit word
  build("sid-flags","bit",[0x00,0xA4],[
    {label:"reserved",k:"rsv",r:[0,5],val:"0",
     body:"Spec bits 15 down to 10. Reserved and required to be zero; a reader seeing anything here is looking at a header it does not fully understand.",
     note:"six bits, always clear."},
    {label:"third SID model",k:"enum",r:[6,7],val:"0 = unknown",sel:0,branch:[
      ["0","unknown - inherit the first SID"],["1","MOS6581"],["2","MOS8580"],["3","both"]],
     body:"Spec bits 9-8, a v4 field. Unknown means the third chip takes the first SID's model rather than being undefined.",
     note:"only meaningful when a third SID address is set."},
    {label:"second SID model",k:"enum",r:[8,9],val:"2 = MOS8580",sel:2,branch:[
      ["0","unknown - inherit the first SID"],["1","MOS6581"],["2","MOS8580"],["3","both"]],
     body:"Spec bits 7-6, a v3 field. Same inheritance rule as the third.",
     note:"a stereo SID rig can mix chip revisions."},
    {label:"SID model",k:"enum",r:[10,11],val:"2 = MOS8580",sel:2,branch:[
      ["0","unknown"],["1","MOS6581"],["2","MOS8580"],["3","both"]],
     body:"Spec bits 5-4. Which chip revision the music was written for. The two differ in combined-waveform level, sample technique and filter response, so this is not a cosmetic preference.",
     note:"a v2NG field."},
    {label:"clock",k:"enum",r:[12,13],val:"1 = PAL",sel:1,branch:[
      ["0","unknown"],["1","PAL"],["2","NTSC"],["3","PAL and NTSC"]],
     body:"Spec bits 3-2, the video standard. Added because the speed field alone cannot express NTSC, and the two machines' frame rates differ enough to detune a tune and break its envelopes.",
     note:"a v2NG field."},
    {label:"psidSpecific / C64BASIC",k:"flag",r:[14,14],val:"0",sel:0,branch:[
      ["0","C64 compatible"],["1","PlaySID specific, or BASIC in an RSID"]],
     body:"Spec bit 1, and the one bit whose meaning depends on the magic. In a PSID it marks PlaySID-specific content such as volume samples. In an RSID it is the C64 BASIC flag, and then initAddress must be zero.",
     note:"two meanings, one bit."},
    {label:"musPlayer",k:"flag",r:[15,15],val:"0",sel:0,branch:[
      ["0","built-in player"],["1","Compute!'s Sidplayer MUS data"]],
     body:"Spec bit 0. When set the payload is MUS music data with no player in it, and an external player must be merged before it can be replayed.",
     note:"a structural difference, not a tonal one: there is no player to call."}
  ]);

  // the head of the C64 data when loadAddress is 0
  build("sid-data","byte",
    [0xF9,0x0F, 0x78,0xA9,0x00,0x8D,0x18,0xD4],
    [
      {label:"loadAddress",k:"sync",r:[0,1],val:"$0FF9",
       body:"LITTLE-endian, unlike every field in the header above it. These two bytes are the address the rest of the image is loaded to, and they are not part of the code.",
       note:"F9 0F reads as $0FF9, not $F90F."},
      {label:"C64 code and data",k:"enum",r:[2,7],val:"6510 machine code",sel:0,branch:[
        ["player","init and play routines"],["MUS","music data, if flags bit 0 is set"]],
       body:"The memory image proper: 6510 machine code and its tables, loaded at the address above and executed on the machine. Nothing here is audio in any decodable sense.",
       note:"78 A9 00 8D 18 D4 = SEI, LDA #$00, STA $D418 - silencing the SID's volume register."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / sid anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / sid anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
