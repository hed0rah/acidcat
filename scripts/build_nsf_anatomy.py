"""Generate docs/formats/nsf-anatomy.html.

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
OUT = os.path.join(DOCS, "nsf-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>NSF Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Nintendo NES / Famicom</b>2A03 APU . 6502<br>rev 2026.08</div>
    </div>
    <div class="strip">
      <div>magic <b>NESM 1A</b></div>
      <div>endian <b>little</b></div>
      <div>header <b>128 bytes</b></div>
      <div>payload <b>6502 code</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">An <b>.nsf</b> does not describe music. It carries the original <b>6502 program</b>
    that produced it, lifted out of a cartridge, behind a 128-byte header saying where to put that
    code and which two addresses to call. Playing one means running it. The header is the only part
    a reader can parse; everything after <code>0x80</code> is a ROM image whose meaning belongs to
    the console.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
        <div class="row"><span class="sw dark k-flag">ochre</span><span class="swsep">&#8594;</span><span class="sw light k-flag">flag</span></div>
        <div class="row"><span class="sw dark k-rsv">grey</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">reserved</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">lineage</div>
  <p class="note">The format's author put it plainly: NSF is <i>&#8220;somewhat sorta based on the PSID
  file format for C64 music/sound&#8221;</i>. The inheritance shows in the shape -- a fixed header in
  front of a raw machine-code image, an init entry point and a play entry point, a subsong index --
  and in small conventions like the literal <code>&lt;?&gt;</code> for an unknown author, which NSF,
  SAP and PSID all share.</p>

  <div class="sec">the sound hardware</div>
  <p class="note">The console's own audio lives inside the <b>2A03</b>, the chip that is also the
  CPU: a 6502 with decimal mode disabled and an audio unit bolted alongside. It offers five voices --
  <b>two pulse</b> channels with four duty cycles each, one <b>triangle</b> fixed at 4-bit and
  stepped through 32 values, one <b>noise</b> channel driven by a shift register with two tap modes,
  and a <b>DMC</b> channel playing 7-bit delta-modulated samples. That is the whole palette every
  stock NSF is written for.</p>
  <p class="note">Everything in the expansion byte below is a <b>cartridge</b> adding a sixth voice
  and upward. That was a Famicom capability rather than an NES one: the Famicom's cartridge connector
  carries an audio-in pin that mixes cartridge sound back into the console's output, and the western
  NES cartridge slot does not route it. Expansion audio therefore plays on a Famicom, on an emulator,
  or on a modified NES -- which is why so much of the music below was never heard outside Japan on
  real hardware.</p>

  <div class="sec">the header &#8212; identification and entry points</div>
  <p class="note">Offsets <code>0x00</code> through <code>0x0D</code>. Every multi-byte value in an
  NSF header is <b>little-endian</b>, matching the 6502 it describes. This is worth stating only
  because its sibling formats do not: a SID header is big-endian on a little-endian target.</p>
  <div id="nsf-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the version byte</span><span class="rspan">0x05 . 1 byte</span></summary>
      <div class="rbody">
        <p class="note">Two values have ever been defined: <b>1</b> for NSF and <b>2</b> for NSF2.
        There is no 1.01, 1.02 or 1.03. Those numbers belong to the revision history of the
        specification <i>document</i>, which was edited seven times between 1999 and 2000 while the
        header layout never changed; and to PSID, which does have a genuine version ladder with
        fields reserved in v1 and given meaning later.</p>
        <p class="note">A version byte of 3 or higher is therefore not a newer file. It is damage,
        or a different format that happened to match five bytes.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">songs are 1-based here and 0-based in NSFe</span><span class="rspan">0x06 . 0x07</span></summary>
      <div class="rbody">
        <p class="note"><code>songs</code> is a count: 1 means one song. <code>startSong</code> is an
        index into it, also 1-based, so a valid file satisfies
        <code>1 &lt;= startSong &lt;= songs</code>.</p>
        <p class="note">NSFe stores the same two numbers in its <code>INFO</code> chunk and makes the
        starting song <b>zero-based</b>. Converting between the two containers without adjusting is
        the most common off-by-one in tooling that reads both.</p>
      </div>
    </details>
  </div>

  <div class="sec">the tail &#8212; timing, banks, hardware</div>
  <p class="note">Offsets <code>0x6E</code> through <code>0x7F</code>. The two speed words are
  <b>microseconds between calls to the play routine</b>, not a frequency: <code>16666</code> is
  60&#8239;Hz and <code>20000</code> is 50&#8239;Hz. A player uses the word matching the region it is
  emulating and ignores the other.</p>
  <div id="nsf-tail"></div>

  <div class="sec">the load address is not always an address</div>
  <p class="note">This is the header's one real trap. When bankswitching is in use, the low twelve
  bits of <code>loadAddress</code> stop being a location and become a <b>count of padding bytes</b>
  at the start of the ROM image. <code>load &amp; 0x0FFF</code> is that count.</p>
  <p class="note">Nothing flags the change. There is no bit anywhere saying &#8220;banked&#8221;: the
  signal is the eight bank bytes at <code>0x70</code> being <b>not all zero</b>. A file whose bank
  array is entirely zero is unbanked and its load address means what it says.</p>

  <div>
    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the bank registers</span><span class="rspan">0x70 . 8 bytes</span></summary>
      <div class="rbody">
        <p class="note">Byte <code>0x70 + i</code> is the initial value written to bank register
        <code>0x5FF8 + i</code>. The eight registers window 4&#8239;KB pages into
        <code>0x8000-0x8FFF</code> through <code>0xF000-0xFFFF</code> in order, so the array is a
        map of the address space at the moment the tune starts.</p>
        <p class="note">FDS files are the exception. Two further registers at <code>0x5FF6</code> and
        <code>0x5FF7</code> map <code>0x6000-0x6FFF</code> and <code>0x7000-0x7FFF</code>, and header
        bytes <code>0x76</code> and <code>0x77</code> initialise <b>both</b> those and the top of the
        address space. The same bank appears in two places, so a bank-to-address diagram drawn
        without that exception is wrong for every FDS rip.</p>
      </div>
    </details>
  </div>

  <div class="sec">the region byte</div>
  <div id="nsf-region"></div>

  <div class="sec">the expansion byte</div>
  <p class="note">Each bit claims one cartridge sound chip, on top of the five stock voices. More
  than one bit may be set: no cartridge ever carried two of these, but the container allows it and
  files built to exercise emulators do it deliberately.</p>
  <div id="nsf-chips"></div>

  <div class="sec">the three text fields</div>
  <p class="note">Title, artist and copyright each occupy a <b>fixed 32-byte slot</b> at
  <code>0x0E</code>, <code>0x2E</code> and <code>0x4E</code>. The text inside is NUL-terminated, so
  at most 31 characters, but the slot is always 32 bytes wide regardless.</p>
  <p class="note">Both halves of that sentence matter. Treating the field as fixed-width alone loses
  the terminator that separates text from padding; treating it as NUL-terminated alone lets a
  malformed slot with 32 non-NUL bytes run the title straight into the artist.</p>

  <div>
    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">what the bytes after the terminator mean</span><span class="rspan">nothing, and that is useful</span></summary>
      <div class="rbody">
        <p class="note">Padding after the NUL is unspecified. It is usually zero, but nothing requires
        it, and non-zero bytes there are frequently the remains of a longer string a tool wrote
        earlier. That residue is not damage and does not affect playback.</p>
        <p class="note">No byte anywhere declares the <b>encoding</b>. The fields are nominally ASCII,
        but files carrying Japanese titles in Shift-JIS exist and so, more rarely, do CP-1252 ones.
        There is no way to tell from the file which you are holding. NSF2 and NSFe both recommend
        UTF-8 for new files, which does not help with old ones.</p>
        <p class="note">Three fields all reading <code>&lt;?&gt;</code> is a convention meaning
        unknown, not corruption.</p>
      </div>
    </details>
  </div>

  <div class="sec">NSF2 &#8212; the reserved bytes given meaning</div>
  <p class="note">The original specification ended the header with <i>&#8220;4 extra bytes for
  expansion (must be 00h)&#8221;</i> at <code>0x7C</code>. Those four were later split: one byte of
  feature flags, and a 24-bit length.</p>
  <p class="note">This was done without a version bump, and safely, because the rule is explicit: <b>if
  the version byte is 1, the flags at <code>0x7C</code> must be ignored</b>. The length at
  <code>0x7D</code> is the interesting half -- a version-1 file may legitimately carry a non-zero
  length there to mark where its program data ends and appended metadata begins. Old players load
  that metadata as part of the ROM and are unharmed by it.</p>
  <div id="nsf-nsf2"></div>

  <div class="sec">the appended metadata</div>
  <p class="note">When <code>dataLength</code> is non-zero, NSFe chunks may follow the program data
  at <code>0x80 + dataLength</code>. Two differences from a standalone NSFe: there is <b>no
  <code>NSFE</code> signature</b> -- the first bytes are the first chunk's length -- and the
  <code>INFO</code>, <code>DATA</code>, <code>BANK</code> and <code>NSF2</code> chunks must not
  appear, because that information is already in the header above.</p>
  <p class="note">Bit 7 of the NSF2 flags is the one that changes a reader's obligations: it says the
  trailer may contain a chunk that is <b>required</b> for correct playback. A player that cannot
  parse the trailer cannot claim to have understood the file.</p>

  <div class="tree"><b>NSF file layout</b>
0x00  <b>header</b>            128 bytes, little-endian
      0x00  magic         "NESM" 0x1A
      0x05  version       1 or 2, nothing else
      0x06  songs / start 1-based count, 1-based index
      0x08  load / init / play
      0x0E  title / artist / copyright   3 x 32-byte slots
      0x6E  NTSC speed    microseconds per play call
      0x70  bank init     8 bytes; all zero means unbanked
      0x78  PAL speed
      0x7A  region        bit0 PAL, bit1 dual
      0x7B  expansion     one bit per cartridge sound chip
      0x7C  NSF2 flags    reserved when version is 1
      0x7D  data length   24-bit; 0 means "to end of file"
0x80  <b>program</b>           6502 code and its data
      ...
      <b>metadata</b>          NSFe chunks, when data length is set
</div>
</div>
"""

BUILDS = """
  // real bytes: a cartridge-audio tune, VRC6
  build("nsf-head","byte",[0x4E,0x45,0x53,0x4D,0x1A,0x01,0x1C,0x01,
                           0x00,0x80,0x00,0xE2,0xD0,0xE0],[
    {label:"magic",k:"sync",r:[0,4],val:"NESM 1A",
     body:"Four characters and a 0x1A. The 0x1A is part of the signature, not a version or a separator, and a reader that checks only the four letters will accept files that are not NSFs.",
     note:"5 bytes, fixed."},
    {label:"version",k:"enum",r:[5,5],val:"1",sel:0,branch:[
      ["1","NSF"],["2","NSF2, flags at 0x7C become meaningful"]],
     body:"The only two values ever defined. Anything higher is damage rather than a newer format.",
     note:"01 = NSF."},
    {label:"songs",k:"enum",r:[6,6],val:"28",
     body:"How many subsongs the file holds. A count, so 1 means one song and 0 is meaningless.",
     note:"0x1C = 28."},
    {label:"startSong",k:"enum",r:[7,7],val:"1",
     body:"Which subsong to play first, indexed from 1. NSFe stores this same number from 0.",
     note:"01 = the first."},
    {label:"loadAddress",k:"enum",r:[8,9],val:"$8000",
     body:"Where the ROM image is placed in the 6502 address space. When the bank array at 0x70 is not all zero this stops being an address and its low twelve bits become a padding count.",
     note:"00 80 little-endian = 0x8000."},
    {label:"initAddress",k:"enum",r:[10,11],val:"$E200",
     body:"Called once per subsong, with the subsong number in the accumulator, to set the tune up. It returns.",
     note:"00 E2 = 0xE200."},
    {label:"playAddress",k:"enum",r:[12,13],val:"$E0D0",
     body:"Called repeatedly on a timer at the rate given by the speed words. Everything the tune does happens here.",
     note:"D0 E0 = 0xE0D0."}
  ]);

  build("nsf-tail","byte",[0xFF,0x40,0x00,0x01,0x02,0x03,0x0B,0x0C,0x0A,0x0A,
                           0x1D,0x4E,0x00,0x01,0x00,0x00,0x00,0x00],[
    {label:"ntscSpeed",k:"enum",r:[0,1],val:"16,639 us",
     body:"Microseconds between play calls on an NTSC machine. Not a frequency: 16666 would be exactly 60 Hz.",
     note:"FF 40 = 0x40FF = 16639."},
    {label:"bankInit",k:"sync",r:[2,9],val:"00 01 02 03 0B 0C 0A 0A",
     body:"Initial values for the eight bank registers at 0x5FF8. Any non-zero byte means the file is banked, which changes what the load address means. This one is banked.",
     note:"8 bytes, one per 4 KB window."},
    {label:"palSpeed",k:"enum",r:[10,11],val:"19,997 us",
     body:"The same number for a PAL machine. A player uses whichever matches the region it emulates and ignores the other, so a zero here is harmless on an NTSC-only tune.",
     note:"1D 4E = 0x4E1D = 19997."},
    {label:"region",k:"flag",r:[12,12],val:"NTSC",
     body:"Which machine the music was written for. Broken out below.",
     note:"00 = NTSC, no dual bit."},
    {label:"expansion",k:"flag",r:[13,13],val:"VRC6",
     body:"Which extra sound chips the cartridge carried. Broken out below.",
     note:"01 = bit 0 set."},
    {label:"nsf2Flags",k:"rsv",r:[14,14],val:"0",
     body:"Feature flags in an NSF2. In a version-1 file like this one the specification says to ignore this byte entirely, so a non-zero value here would be a writer fingerprint rather than a feature.",
     note:"reserved at version 1."},
    {label:"dataLength",k:"enum",r:[15,17],val:"0",
     body:"A 24-bit length of the program data, marking where appended NSFe metadata starts. Zero means the data runs to the end of the file. Legal in a version-1 file, where old players harmlessly load the trailer as ROM.",
     note:"3 bytes, little-endian. 0 = to EOF."}
  ]);

  // the region byte, MSB first
  build("nsf-region","bit",[0x00],[
    {label:"reserved",k:"rsv",r:[0,5],val:"0",
     body:"Bits 2 through 7. The specification says these must be zero.",
     note:"six bits, clear."},
    {label:"dual",k:"flag",r:[6,6],val:"0",
     body:"Set when the tune runs correctly on both machines. When it is set, bit 0 may be read as a preference rather than a statement, though support for that reading is thin enough that setting it is a compatibility risk.",
     note:"bit 1."},
    {label:"PAL",k:"flag",r:[7,7],val:"0",
     body:"Clear for an NTSC tune, set for a PAL one. The two machines differ in clock rate and in frame timing, so a tune written for one plays at the wrong speed on the other.",
     note:"bit 0, clear = NTSC."}
  ]);

  // the expansion byte, MSB first
  build("nsf-chips","bit",[0x01],[
    {label:"reserved",k:"rsv",r:[0,0],val:"0",
     body:"Bit 7. Must be zero.",
     note:"one bit."},
    {label:"VT02+",k:"flag",r:[1,1],val:"0",
     body:"A later addition covering NES-on-a-chip variants. The original specification declared this bit reserved, so it is a modern assignment to space that was once required to be clear.",
     note:"bit 6."},
    {label:"Sunsoft 5B",k:"flag",r:[2,2],val:"0",
     body:"The Sunsoft FME-7 audio expansion: three square channels, a General Instrument AY design.",
     note:"bit 5."},
    {label:"Namco 163",k:"flag",r:[3,3],val:"0",
     body:"Wavetable synthesis with up to eight channels sharing 128 bytes of RAM. The channels are time-shared, so enabling all eight divides the update rate between them and audibly coarsens the sound -- the count is a budget, not eight free voices. Named Namco 106 in the original specification.",
     note:"bit 4."},
    {label:"MMC5",k:"flag",r:[4,4],val:"0",
     body:"Nintendo's own mapper, adding two more square channels and an 8-bit PCM output.",
     note:"bit 3."},
    {label:"FDS",k:"flag",r:[5,5],val:"0",
     body:"The Famicom Disk System's wavetable channel. Files using it also change how the bank registers work.",
     note:"bit 2."},
    {label:"VRC7",k:"flag",r:[6,6],val:"0",
     body:"Konami's FM chip, a cut-down Yamaha OPLL with six channels. Fifteen of its instruments are burned in and one is user-definable, so a composer wanting a sound of their own had exactly one slot to put it in.",
     note:"bit 1."},
    {label:"VRC6",k:"flag",r:[7,7],val:"1",
     body:"Konami's expansion: two pulse channels with more duty cycles than the stock APU, plus a sawtooth. Set in this file.",
     note:"bit 0, set."}
  ]);

  // an NSF2 flags byte, MSB first
  build("nsf-nsf2","bit",[0x90],[
    {label:"mandatory",k:"flag",r:[0,0],val:"1",
     body:"The appended metadata may contain a chunk that is required for playback. A reader that cannot parse the trailer must not claim it understood the file.",
     note:"bit 7, set."},
    {label:"no PLAY",k:"flag",r:[1,1],val:"0",
     body:"The play routine will not be used at all; the tune drives itself from the init routine.",
     note:"bit 6."},
    {label:"non-returning INIT",k:"flag",r:[2,2],val:"0",
     body:"The init routine never returns. It runs as the body of the program rather than as setup, with play called around it.",
     note:"bit 5."},
    {label:"IRQ",k:"flag",r:[3,3],val:"1",
     body:"The tune may use the interrupt features NSF2 adds: a programmable cycle timer, plus explicit access to the DMC and frame-counter interrupts.",
     note:"bit 4, set."},
    {label:"reserved",k:"rsv",r:[4,7],val:"0",
     body:"Bits 0 through 3. Must be zero.",
     note:"four bits, clear."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / nsf anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / nsf anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
