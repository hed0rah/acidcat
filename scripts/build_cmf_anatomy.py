"""Generate docs/formats/cmf-anatomy.html.

The page shell is lifted from an existing anatomy page so every page stays
identical below the content. Every byte in the maps is from one real file
(a 2,054-byte CMF 1.1, six instruments) and decodes to the stated value.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "rmid-anatomy.html")
OUT = os.path.join(DOCS, "cmf-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>CMF Anatomy</h1></div><acidcat-toggle aria-label="Cycle theme: light, dark, acid, house light, house dark"></acidcat-toggle></div>
      <div class="stamp"><b>Creative Labs</b>Creative Music File<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>CTMF</b></div>
      <div>endian <b>little header, MIDI track</b></div>
      <div>container <b>own; offsets</b></div>
      <div>samples <b>none; OPL2 patches</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">A <b>.cmf</b> is a Standard MIDI track with its instruments packed in front. Creative
    made it in 1991 for the Sound Blaster's AdLib-compatible OPL2: instead of a General MIDI bank the
    player needs sixteen bytes of FM registers per patch, and the file carries them. The header is
    little-endian offsets to the patches, the music and three optional strings; the music is one
    MTrk's worth of events, running status and all, with four controller numbers of Creative's own,
    ending on End of Track.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">file regions</div>

    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the header</span><span class="rspan">0x00 . drawn below</span></summary>
      <div class="rbody">
        <div id="cmf-head"></div>
        <p class="note"><b>1.0 and 1.1.</b> A 1.0 header stops at 0x24: no instrument count and no
        tempo, and the count is what fits between the two offsets. 1.1 adds both. The three string
        offsets are zero when absent, and they point wherever the writer put the text, usually
        between the header and the patches.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">an instrument</span><span class="rspan">16 bytes . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Two OPL2 operators, <b>interleaved</b>: byte 0 is the modulator's first register,
        byte 1 the carrier's, byte 2 the modulator's second, and so on for five registers each. Then
        the feedback and connection byte, then five reserved. The registers are the chip's own:
        AM/VIB/EG/KSR/multiplier, KSL/level, attack/decay, sustain/release, waveform.</p>
        <div id="cmf-inst"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the music</span><span class="rspan">SMF track events . drawn below</span></summary>
      <div class="rbody">
        <p class="note">Delta time as a variable-length quantity, then a MIDI event, with running
        status. Program changes select the patches above by index. Controllers 0x66 to 0x69 are
        Creative's: a marker, rhythm mode on or off, pitch-bend range, transpose. The stream ends
        on <code>FF 2F 00</code>, and most writers leave one more <code>0xFF</code> after it.</p>
        <div id="cmf-music"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">identification</span><span class="rspan">CTMF</span></summary>
      <div class="rbody">
        <p class="note">Four bytes at zero.</p>
      </div>
    </details>

  <footer>
    <span>acidcat / cmf anatomy</span>
    <span>creative labs . adlib opl2 . midi track with patches</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""

BUILDS = """
  // the 40-byte 1.1 header of a real file
  build("cmf-head","byte",
    [0x43,0x54,0x4D,0x46, 0x01,0x01, 0x28,0x00, 0x88,0x00, 0x30,0x00, 0x60,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x00,
     0x01,0x01,0x00,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00, 0x06,0x00, 0x78,0x00],
    [
      {label:"magic",k:"sync",r:[0,3],val:"\\"CTMF\\"",body:"Four bytes.",note:""},
      {label:"version",k:"enum",r:[4,5],val:"0x0101 = 1.1",body:"BCD, low byte first: 01 01. 1.0 is 00 01.",note:""},
      {label:"instruments at",k:"ptr",r:[6,7],val:"0x0028 = 40",body:"Right after this header.",note:""},
      {label:"music at",k:"ptr",r:[8,9],val:"0x0088 = 136",body:"40 + 6 patches of 16 = 136.",note:""},
      {label:"ticks per quarter",k:"enum",r:[10,11],val:"48",body:"The MIDI division.",note:""},
      {label:"ticks per second",k:"enum",r:[12,13],val:"96",body:"The clock the player runs the track at.",note:"120 BPM at 48/quarter."},
      {label:"title, composer, remarks",k:"pad",r:[14,19],val:"0, 0, 0",body:"No strings in this file.",note:"offsets when present."},
      {label:"channels used",k:"enum",r:[20,35],val:"1, 2, 4, 5",body:"One byte per MIDI channel, non-zero for used. A player can skip the others.",note:""},
      {label:"instrument count",k:"size",r:[36,37],val:"6",body:"1.1 only.",note:""},
      {label:"tempo",k:"enum",r:[38,39],val:"120 BPM",body:"1.1 only.",note:""}
    ]);

  // instrument 0
  build("cmf-inst","byte",
    [0x01,0x11, 0x4F,0x00, 0xF1,0xD2, 0x53,0x74, 0x00,0x00, 0x06, 0x00,0x00,0x00,0x00,0x00],
    [
      {label:"AVEKM mod/car",k:"enum",r:[0,1],val:"0x01 / 0x11",body:"Tremolo, vibrato, sustain, KSR and frequency multiplier. Modulator x1; carrier x1 with sustain on.",note:"interleaved: mod, car."},
      {label:"KSL/level",k:"enum",r:[2,3],val:"0x4F / 0x00",body:"Modulator attenuated 15 steps; carrier at full.",note:""},
      {label:"attack/decay",k:"enum",r:[4,5],val:"0xF1 / 0xD2",body:"Fast attacks, slow decays.",note:""},
      {label:"sustain/release",k:"enum",r:[6,7],val:"0x53 / 0x74",body:"",note:""},
      {label:"waveform",k:"enum",r:[8,9],val:"0 / 0",body:"Both sine.",note:""},
      {label:"feedback/conn",k:"enum",r:[10,10],val:"0x06 = feedback 3, FM",body:"Bit 0 clear: the modulator modulates the carrier. Bits 1-3: feedback 3.",note:""},
      {label:"reserved",k:"pad",r:[11,15],val:"0",body:"Five bytes.",note:""}
    ]);

  // the first events of the track
  build("cmf-music","byte",
    [0x00,0xB0,0x67,0x01, 0x00,0xC0,0x00, 0x00,0xC1,0x00, 0x00,0xC3,0x00, 0x00,0xC4,0x00, 0x00,0x93,0x3C,0x32],
    [
      {label:"controller",k:"sync",r:[0,3],val:"delta 0, ch 1 CC 0x67 = 1",body:"Creative's rhythm-mode controller, on: the OPL2's five percussion voices are in play.",note:"a Creative controller."},
      {label:"program",k:"enum",r:[4,6],val:"ch 1 program 0",body:"Select instrument 0 above for channel 1.",note:""},
      {label:"programs",k:"enum",r:[7,15],val:"ch 2, 4, 5 program 0",body:"The other used channels.",note:""},
      {label:"note on",k:"enum",r:[16,19],val:"delta 0, ch 4 note 60 vel 50",body:"The first note, middle C. The track continues to FF 2F 00 at the end of the file.",note:"0x93: note on, channel 4."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / cmf anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / cmf anatomy</title>",
                  page, count=1, flags=re.S)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
