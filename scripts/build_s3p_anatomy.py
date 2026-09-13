"""Generate docs/formats/s3p-anatomy.html.

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
OUT = os.path.join(DOCS, "s3p-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>S3P Anatomy</h1></div></div>
      <div class="stamp"><b>Akai S1000 / S3000</b>program dump . SysEx<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>PSYSSS30</b></div>
      <div>endian <b>big + little</b></div>
      <div>container <b>MIDI SysEx</b></div>
      <div>samples <b>by name only</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">An <b>.s3p</b> is an Akai S1000 or S3000 program. It is not a file layout: it is a
    <b>recording of a conversation</b>. The sampler had no program file format, only a MIDI System
    Exclusive dump, so the program was captured message by message and each message written to disk
    with its length in front of it. Everything strange about the format follows from that.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">a file wrapped around a MIDI transcript</div>
  <p class="note">Two layers, written by two different things. The <b>outer</b> layer is a file:
  a magic, a count, then a length in front of each message, all <b>big-endian</b>. The
  <b>inner</b> layer is MIDI, and the multi-byte values inside it are <b>little-endian</b>, because
  that is the byte order of the processor inside the sampler. A reader that picks one endianness
  for the whole file gets one of the two layers wrong.</p>

  <div class="sec">the container header, and the first length</div>
  <p class="note">Twelve bytes of header, then the first message with its length in front. There is
  no index and no total size: the messages are walked, not looked up.</p>
  <div id="s3p-head"></div>

  <div>
    <details class="region" open>
      <summary><span class="chev">&#9656;</span><span class="rname">the message</span><span class="rspan">F0 47 . 48 . F7</span></summary>
      <div class="rbody">
        <p class="note">Every message is a complete MIDI System Exclusive frame, exactly as it came
        down the cable.</p>
        <div class="kv">
          <div><span class="k">F0</span><span class="v">exclusive message start</span></div>
          <div><span class="k">47</span><span class="v">Akai's manufacturer id</span></div>
          <div><span class="k">cc</span><span class="v">exclusive channel</span></div>
          <div><span class="k">ff</span><span class="v">function: 07 = program common, 09 = keygroup</span></div>
          <div><span class="k">48</span><span class="v">the S1000 model byte</span></div>
          <div><span class="k-en">F7</span><span class="v">end of exclusive, always the last byte</span></div>
        </div>
        <p class="note">A program is <b>one</b> function-07 message followed by <b>one function-09
        message per keygroup</b>, in that order. The selector bytes between the model byte and the
        payload say which program and which keygroup, which is what a sampler needs and a file does
        not.</p>
        <div id="s3p-frame"></div>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the nibble split</span><span class="rspan">2 bytes on the wire = 1 byte of data</span></summary>
      <div class="rbody">
        <p class="note">A System Exclusive message may not contain a byte with <b>bit 7 set</b>,
        because that is how MIDI marks the start of a new message. So a data byte cannot travel as
        itself. Each one is sent as <b>two bytes, low nibble first</b>:</p>
        <div class="kv">
          <div><span class="k">on the wire</span><span class="v">06 09</span></div>
          <div><span class="k">low nibble</span><span class="v">06</span></div>
          <div><span class="k">high nibble</span><span class="v">09</span></div>
          <div><span class="k-en">the data byte</span><span class="v">0x96 = 150</span></div>
        </div>
        <p class="note">This is the format's one real trap. A reader that skips the step does not
        crash and does not read zeros: it reads a stream of small numbers that look like plausible
        parameters, and names that decode to strings of the digit zero. The failure is quiet, which
        is the worst kind.</p>
        <p class="note">A block is <b>192 bytes</b> once rejoined and the sampler fills the first
        <b>150</b>; the rest is padding on the wire.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the program common block</span><span class="rspan">150 bytes</span></summary>
      <div class="rbody">
        <p class="note">One per program. It opens with <code>01</code>, the block identifier, and
        carries what applies to the whole program rather than to one key range.</p>
        <div class="kv">
          <div><span class="k">0</span><span class="v">PRIDENT, 1 = program common block</span></div>
          <div><span class="k">1-2</span><span class="v">KGRP1@, first keygroup address</span></div>
          <div><span class="k">3-14</span><span class="v">PRNAME, 12 characters</span></div>
          <div><span class="k">15-18</span><span class="v">program number, MIDI channel, polyphony, priority</span></div>
          <div><span class="k">19-20</span><span class="v">play range low and high</span></div>
          <div><span class="k">22-28</span><span class="v">output, level, pan, loudness and its modulators</span></div>
          <div><span class="k">29-38</span><span class="v">pan LFO and the main LFO</span></div>
          <div><span class="k-en">42</span><span class="v">GROUPS, how many keygroups the program has</span></div>
        </div>
        <p class="note"><b>GROUPS is the honest check on the whole format.</b> It sits inside a
        block, and the number of keygroup messages is a property of the container around it. The
        two are written by different parts of the sampler and have no reason to agree unless the
        block layout is being read correctly.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">a keygroup, and its four zones</span><span class="rspan">150 bytes</span></summary>
      <div class="rbody">
        <p class="note">One per key range. It opens with <code>02</code> and holds the key span,
        the filter, two envelopes, and then <b>four velocity zones</b>.</p>
        <div class="kv">
          <div><span class="k">0</span><span class="v">KGIDENT, 2 = keygroup block</span></div>
          <div><span class="k">3-4</span><span class="v">key range low and high, 24-127 = C0 to G8</span></div>
          <div><span class="k">7-11</span><span class="v">filter frequency and its modulators</span></div>
          <div><span class="k">12-19</span><span class="v">amplitude envelope: attack, decay, sustain, release</span></div>
          <div><span class="k">20-27</span><span class="v">filter envelope, same four</span></div>
          <div><span class="k-en">34, 58, 82, 106</span><span class="v">velocity zones 1 to 4, 24 bytes each</span></div>
        </div>
        <p class="note">A zone opens with a <b>12-character sample name</b> and then its velocity
        range and offsets. The name is all there is: the sample data lives elsewhere entirely, so a
        program without its bank is a set of instructions referring to things that are not there.
        An unused zone is a name of twelve spaces.</p>
        <div class="kv">
          <div><span class="k">+0</span><span class="v">SNAME, 12 characters</span></div>
          <div><span class="k">+12, +13</span><span class="v">velocity range low and high</span></div>
          <div><span class="k">+16, +17, +18</span><span class="v">loudness, filter and pan offsets</span></div>
          <div><span class="k-en">+19</span><span class="v">what to do with the sample's own loop</span></div>
        </div>
        <p class="note">The commonest use of two zones is not velocity layering at all. It is
        <b>stereo</b>: one zone panned hard left and one hard right, over the same velocity range,
        naming the two halves of a stereo sample.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">names are not ASCII</span><span class="rspan">41 characters</span></summary>
      <div class="rbody">
        <p class="note">The sampler has its own character set, and it is not a superset of anything.
        There are forty-one characters and no lower case.</p>
        <div class="kv">
          <div><span class="k">0 - 9</span><span class="v">the digits 0 to 9</span></div>
          <div><span class="k">10</span><span class="v">space</span></div>
          <div><span class="k">11 - 36</span><span class="v">A to Z</span></div>
          <div><span class="k-en">37 - 40</span><span class="v"># + - .</span></div>
        </div>
        <p class="note">Read as ASCII the codes are control characters, so a name comes out as
        invisible junk rather than as wrong text. Read correctly, code 11 is <code>A</code>: the
        set is an index, not an encoding.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">the pointers that are not offsets</span><span class="rspan">KGRP1@, NXTKG@, SBADD</span></summary>
      <div class="rbody">
        <p class="note">Three fields look like they point at something and none of them point at
        anything in the file. <code>KGRP1@</code>, <code>NXTKG@</code> and <code>SBADD</code> are
        addresses in the <b>sampler's own memory</b>, captured along with everything else because
        the dump is a memory dump.</p>
        <p class="note"><code>KGRP1@</code> reads as <b>150</b> in ordinary files, which is exactly
        the block size and looks convincingly like an offset to the next block. It is not. Following
        it lands on nothing, and the only correct use of these fields is to notice they are there.</p>
      </div>
    </details>

    <details class="region">
      <summary><span class="chev">&#9656;</span><span class="rname">byte order</span><span class="rspan">both</span></summary>
      <div class="rbody">
        <p class="note">The container's lengths and counts are <b>big-endian</b>. The words inside a
        block, once the nibbles are rejoined, are <b>little-endian</b>. The two layers were written
        by different machines and neither was asked to agree with the other.</p>
      </div>
    </details>
  </div>

  <footer>
    <span>acidcat / s3p anatomy</span>
    <span>akai s1000 . s3000 . midi system exclusive . nibble-split</span>
  <span><a href="https://hed0rah.github.io">hed0rah</a> &middot; <a href="https://x.com/r3l0z">r3l0z</a></span></footer>

</div>

"""


BUILDS = """
  // the container header of a real program, plus the first message's length
  build("s3p-head","byte",
    [0x50,0x53,0x59,0x53,0x53,0x53,0x33,0x30,
     0x00,0x00,0x00,0x0E,
     0x00,0x00,0x01,0x88],
    [
      {label:"magic",k:"sync",r:[0,7],val:"PSYSSS30",
       body:"Eight fixed bytes, and the only signature in the file. Nothing else about an .s3p is self-describing, so this is what identification rests on.",
       note:"the one thing here that is not a number."},
      {label:"keygroups",k:"enum",r:[8,11],val:"0x0000000E = 14",
       body:"How many keygroup messages follow the program message. Big-endian, like everything else in the outer layer. The program block repeats this number in its own GROUPS field, and the two agreeing is what proves the block is being read correctly.",
       note:"14 keygroups, so 15 messages in total."},
      {label:"length",k:"sync",r:[12,15],val:"0x00000188 = 392",
       body:"The length of the message that follows, not counting these four bytes. Every message carries one of these in front of it, which is the only reason the file can be walked at all.",
       note:"392 bytes: a 192-byte block, doubled by the nibble split, plus the frame."}
    ]);

  // the first message: a SysEx frame, and the first data bytes of the program
  build("s3p-frame","byte",
    [0xF0,0x47,0x00,0x07,0x48,
     0x00,0x00,
     0x01,0x00,
     0x06,0x09,0x00,0x00,
     0x00,0x02,
     0x03,0x01,
     0x0A,0x00,
     0x03,0x01],
    [
      {label:"F0",k:"sync",r:[0,0],val:"exclusive start",
       body:"MIDI's start-of-exclusive byte. Its bit 7 is set, which is exactly why no data byte inside the message may have bit 7 set, and therefore why the payload is nibble-split.",
       note:"the cause of the format's one real trap."},
      {label:"manufacturer",k:"enum",r:[1,1],val:"0x47 = Akai",
       body:"Akai's registered MIDI manufacturer id. Every message in the file carries it.",
       note:""},
      {label:"channel",k:"sync",r:[2,2],val:"0",
       body:"The exclusive channel the dump came in on. A property of the cable, not of the program.",
       note:""},
      {label:"function",k:"enum",r:[3,3],val:"0x07 = PDATA",sel:0,branch:[
        ["07","program common data"],["09","keygroup data"]],
       body:"Which kind of block this message carries. 07 is the program common block and appears once, first; 09 is a keygroup and appears once per keygroup.",
       note:"the only two that occur in a program file."},
      {label:"model",k:"enum",r:[4,4],val:"0x48 = S1000",
       body:"The model byte. It says S1000 in S3000 files too, because the S3000 speaks the S1000's exclusive language.",
       note:""},
      {label:"selector",k:"sync",r:[5,6],val:"program 0",
       body:"Which program the message is about. A keygroup message carries three selector bytes here instead of two, because it also has to say which keygroup.",
       note:"the sampler needs this; a file does not."},
      {label:"PRIDENT",k:"enum",r:[7,8],val:"01 00 -> 0x01",
       body:"The first data byte, and the first nibble pair: low nibble 01, high nibble 00, giving 1. One means this is a program common block.",
       note:"two bytes on the wire, one byte of data."},
      {label:"KGRP1@",k:"rsv",r:[9,12],val:"-> 0x0096 = 150",
       body:"Four bytes on the wire, two bytes of data, read little-endian: 150. It is an address in the sampler's memory, and 150 is also the block size, which makes it look exactly like an offset to the next block. It is not one.",
       note:"06 09 -> 0x96, then 00 00 -> 0x00."},
      {label:"name[0]",k:"enum",r:[13,14],val:"-> 0x20 = 'V'",
       body:"The program name starts here, twelve characters in the sampler's own character set. Code 0x20 is 32, and 32 in that set is the letter V -- not the ASCII space it would be anywhere else.",
       note:"the charset is an index, not an encoding."},
      {label:"name[1]",k:"sync",r:[15,16],val:"-> 0x13 = 'I'",body:"Code 19, the tenth letter.",note:""},
      {label:"name[2]",k:"sync",r:[17,18],val:"-> 0x0A = ' '",body:"Code 10 is the only space the set has.",note:""},
      {label:"name[3]",k:"sync",r:[19,20],val:"-> 0x13 = 'I'",body:"And the name continues for eight more characters.",note:"'VI I SUS F V' in full."}
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
    page = page.replace("acidcat / rmid anatomy", "acidcat / s3p anatomy")
    page = re.sub(r"<title>.*?</title>", "<title>acidcat / s3p anatomy</title>",
                  page, count=1, flags=re.S)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return OUT


if __name__ == "__main__":
    print("wrote %s" % build())
