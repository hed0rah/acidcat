"""Generate docs/formats/dsd-anatomy.html.

The page shell -- CSS, favicon, the byte-map engine, the theme toggle -- is
lifted byte-for-byte from an existing anatomy page so every page in the set
stays identical below the content. Only the body and the build() calls are
written here.

Every byte in the maps is taken from a real specimen and decodes to the stated
value. Two specimens, because the page covers two containers: a DSD64 stereo
DSF and a DSD64 stereo DSDIFF, both commercial SACD rips written by different
tools, which are also what the walkers were built and verified against.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs", "formats")
TEMPLATE = os.path.join(DOCS, "caf-anatomy.html")
OUT = os.path.join(DOCS, "dsd-anatomy.html")

BODY = """<div class="sheet">
  <div class="head">
    <div class="row">
      <div class="title"><div class="tt"><div class="sysmark">ACIDCAT . FILE FORMAT REFERENCE</div><h1>DSD Anatomy</h1></div></div>
      <div class="stamp"><b>DSF &amp; DSDIFF</b>Super Audio CD . 1999<br>rev 2026.09</div>
    </div>
    <div class="strip">
      <div>magic <b>DSD&nbsp;/ FRM8</b></div>
      <div>sample <b>1 bit</b></div>
      <div>sizes <b>u64</b></div>
      <div>endian <b>little / big</b></div>
    </div>
  </div>

  <div class="intro">
    <p class="lede">One bit per sample, at sixty-four times the CD rate. <b>Direct Stream Digital</b>
    is what a Super Audio CD holds, and it does not store amplitudes at all: each sample is a single
    bit from a sigma-delta modulator, and the signal lives in the local <b>density</b> of ones. Two
    containers carry it, one from each company behind the disc, and they agree about almost
    nothing.</p>
    <aside class="sig" aria-label="color key">
      <div class="legrows">
        <div class="row"><span class="sw dark k-enum">mauve</span><span class="swsep">&#8594;</span><span class="sw light k-enum">value</span></div>
        <div class="row"><span class="sw dark k-sync">slate</span><span class="swsep">&#8594;</span><span class="sw light k-sync">structural</span></div>
        <div class="row"><span class="sw dark k-rsv">grey</span><span class="swsep">&#8594;</span><span class="sw light k-rsv">payload</span></div>
      </div>
      <div class="siglabel">color key</div>
    </aside>
  </div>

  <div class="sec">the one fact that breaks every other reader</div>
  <p class="note"><b>bits per sample is 1.</b> Every duration and size formula written for PCM
  assumes at least eight, and the assumption is usually invisible because it is spelled
  <code>bits / 8</code> somewhere in a helper. The sample count is a count of <b>bits per
  channel</b>. The byte count is that divided by eight, times the channel count. Reading the two as
  the same number is not a small error; it is a factor of eight, and it produces a duration that
  looks entirely plausible.</p>
  <p class="note">At <b>2,822,400&nbsp;Hz</b> -- the DSD64 rate, sixty-four times 44,100 -- one
  second of stereo is 5,644,800 bits, which is <b>705,600 bytes</b>. A reader that assumes 16-bit
  samples expects <b>11,289,600</b> for that same second and finds a sixteenth of it. The duration
  survives, because seconds are still samples over rate; it is every calculation that touches
  <i>bytes</i> that breaks -- the buffer size, the seek target, the offset of the next block.</p>

  <div class="sec">DSF: the DSD chunk (28 bytes)</div>
  <p class="note">Sony's container. Flat, little-endian, four blocks in fixed order and no nesting
  at all. The header says how long the file is and where its tag lives.</p>
  <div id="dsd-dsf-head"></div>

  <div class="sec">DSF: the fmt block (first 32 of 52 bytes)</div>
  <p class="note">Everything about the audio, and two fields that look like the same number and are
  not: <b>channel type</b> is a layout id, <b>channel num</b> is a count.</p>
  <div id="dsd-dsf-fmt"></div>

  <details class="deep"><summary><span class="chev">&#9656;</span><b>channel type is not a channel count</b></summary><div class="dbody">Type <b>4</b> is quad -- front left, front right, back left, back right. Type <b>5</b> is also four channels, and they are front left, front right, centre and LFE. The count is the same and the speakers are not, so a reader that derives one field from the other silently puts the centre channel in the back of the room. The two are separate fields precisely because they are separate facts, and a file whose type and count disagree is a file to distrust.<br><br>The full table: <b>1</b> mono, <b>2</b> stereo, <b>3</b> three channels, <b>4</b> quad, <b>5</b> four channels, <b>6</b> five channels, <b>7</b> 5.1.</div></details>

  <details class="deep"><summary><span class="chev">&#9656;</span><b>the last block is padded, not short</b></summary><div class="dbody"><b>Block size per channel is fixed at 4,096 bytes</b> and the spec is explicit about the tail: "Block size per channel is fixed, so please fill ZERO(0x00) for unused sample data area in the block." The final block is therefore full-length and zero-filled, not truncated. A reader that treats the whole final block as audio emits a burst of silence rather than noise, which is the merciful failure -- but a reader that computes the length from the block count rather than from the sample count overstates the duration.</div></details>

  <div class="sec">DSDIFF: FRM8 and the version chunk (28 bytes)</div>
  <p class="note">Philips' container, and an IFF file in every respect but one: the size is
  <b>64-bit</b>. The spec says so plainly -- "this FORM chunk is slightly different from EA IFF 85
  (the ckDataSize is not a long but a double ulong)". Big-endian throughout, and the even-length pad
  byte is kept.</p>
  <div id="dsd-frm8"></div>

  <details class="deep"><summary><span class="chev">&#9656;</span><b>FRM8 counts from byte 12, and Wave64 does not</b></summary><div class="dbody">Three formats widened IFF to survive past 4&nbsp;GB and each drew the line somewhere different. <b>DSDIFF</b> keeps the IFF convention: the size counts everything after the id and the size itself, so the file is <b>size + 12</b>. <b>Wave64</b> counts its own 24-byte header <i>inside</i> the size, so the chunk is exactly <code>size</code> bytes. <b>RF64</b> keeps 32-bit sizes and moves the real length into a separate <code>ds64</code> chunk, leaving <code>0xFFFFFFFF</code> as a sentinel behind.<br><br>All three are reasonable and no two are the same, which is why a reader written for one of them is wrong about the others in a way that still produces a number.</div></details>

  <div class="sec">DSDIFF: PROP and its first local chunk (28 bytes)</div>
  <p class="note">DSDIFF nests. <b>PROP</b> is a container whose payload opens with a property type
  -- <code>SND&nbsp;</code> for sound -- followed by local chunks that describe the audio. The
  sample rate, the channel list, the compression type and the absolute start time all live in
  here rather than at the top level.</p>
  <div id="dsd-prop"></div>

  <div class="sec">the local chunks inside PROP</div>
  <div class="kv">
    <div><span class="k">FS</span><span class="v">sample rate, u32. 2,822,400 is DSD64; each higher rate doubles</span></div>
    <div><span class="k">CHNL</span><span class="v">a u16 count then one 4-character id per channel: SLFT SRGT for stereo, MLFT MRGT C&nbsp;&nbsp;&nbsp; LFE&nbsp; LS&nbsp;&nbsp; RS&nbsp;&nbsp; for multichannel</span></div>
    <div><span class="k">CMPR</span><span class="v">a 4-character compression id, a length byte, then a human-readable name. DSD&nbsp; is uncompressed; DST&nbsp; is the lossless codec</span></div>
    <div><span class="k">ABSS</span><span class="v">absolute start time: hours u16, minutes u8, seconds u8, samples u32. Where this file sits in the original recording</span></div>
    <div><span class="k">LSCO</span><span class="v">loudspeaker configuration, u16. 0 stereo, 3 five channels, 4 five-point-one</span></div>
  </div>

  <details class="deep"><summary><span class="chev">&#9656;</span><b>DST, and why a DSDIFF can be lossless-compressed</b></summary><div class="dbody">A Super Audio CD holds 4.7&nbsp;GB and a stereo DSD64 stream runs at 5.6&nbsp;Mbit/s, so an uncompressed disc would hold well under two hours and a multichannel one far less. <b>Direct Stream Transfer</b> is the lossless codec that closes the gap, and a DSDIFF carrying it replaces the <code>DSD&nbsp;</code> sound chunk with <code>DST&nbsp;</code>, holding <code>DSTF</code> frames, an optional <code>DSTC</code> CRC per frame and a <code>DSTI</code> index.<br><br>The compression type in <code>CMPR</code> is what says which of the two a file is, and it is the only thing that does: both look identical at the container level.</div></details>

  <div class="sec">where the tag lives</div>
  <p class="note">Both containers embed <b>ID3v2</b>, the tag format MP3 made ubiquitous, and they
  put it in completely different places. <b>DSF</b> stores a 64-bit <b>pointer</b> in its header to
  a tag at the end of the file, and sets it to zero when there is none. <b>DSDIFF</b> carries an
  <code>ID3&nbsp;</code> chunk, and in practice writes it <i>inside</i> PROP alongside the sample
  rate rather than at the top level.</p>
  <p class="note">This is the same tag a RIFF file carries in an <code>id3&nbsp;</code> chunk and an
  AIFF in an <code>ID3&nbsp;</code> chunk. Five containers, one tag format, and five different
  opinions about where it goes.</p>

  <div class="sec">the rate family</div>
  <div class="kv">
    <div><span class="k">DSD64</span><span class="v">2,822,400 Hz -- 64 x 44,100. The SACD rate</span></div>
    <div><span class="k">DSD128</span><span class="v">5,644,800 Hz</span></div>
    <div><span class="k">DSD256</span><span class="v">11,289,600 Hz</span></div>
    <div><span class="k">DSD512</span><span class="v">22,579,200 Hz</span></div>
    <div><span class="k">48 kHz base</span><span class="v">3,072,000 and its doublings. Legal, rarer, and the reason a rate should be named from a table rather than divided by 44,100</span></div>
  </div>
</div>
"""

MAPS = """
  // DSF: the header block, which is also a chunk named 'DSD '
  build("dsd-dsf-head","byte",
    [0x44,0x53,0x44,0x20,0x1C,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
     0xD6,0x00,0x6E,0x04,0x00,0x00,0x00,0x00,
     0x5C,0x00,0x6D,0x04,0x00,0x00,0x00,0x00],
    [
      {label:"ckID",k:"sync",r:[0,3],val:'"DSD "',
       body:"Four characters, and the last one is a SPACE. It names the file and its first chunk at the same time -- there is no separate form type the way RIFF and IFF have one.",
       note:"'DSD ' is four common characters and a weak magic on its own. The declared chunk size below is what makes the test safe: it is always 28, and a file whose first bytes merely spell DSD will not agree."},
      {label:"ckSize",k:"enum",r:[4,11],val:"28",
       body:"64-bit, LITTLE-endian, and fixed: this block is always 28 bytes. Every size in a DSF is a u64, because a one-bit stream outgrows 32 bits quickly.",
       note:"little-endian here, big-endian in DSDIFF. The two containers for the same audio disagree on byte order, which is the single most common way to misread one as the other."},
      {label:"total file size",k:"enum",r:[12,19],val:"74,318,038",
       body:"The length of the whole file, stated by the file. A reader can check it against what is actually on disk, and a mismatch is the same class of damage as a RIFF whose size lies.",
       note:"this is the file length, not a payload length. RIFF's outer size counts everything after the first eight bytes; this counts everything."},
      {label:"metadata pointer",k:"rsv",r:[20,27],val:"74,252,380",
       body:"A 64-bit absolute offset to an ID3v2 tag, or ZERO when the file carries no tag. The tag sits at the end of the file, after the audio.",
       note:"a pointer rather than a chunk, which means a DSF can be retagged by rewriting the tail and one 8-byte field -- no chunk shuffling, no rewriting 200 MB of audio."}
    ]);
  // DSF: the fmt block. 52 bytes; the first 32 are drawn.
  build("dsd-dsf-fmt","byte",
    [0x66,0x6D,0x74,0x20,0x34,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
     0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
     0x02,0x00,0x00,0x00,0x02,0x00,0x00,0x00,
     0x00,0x11,0x2B,0x00],
    [
      {label:"ckID",k:"sync",r:[0,3],val:'"fmt "',
       body:"Borrowed from RIFF, down to the trailing space, and holding a completely different structure."},
      {label:"ckSize",k:"enum",r:[4,11],val:"52",
       body:"Always 52. The block is fixed-length, so there is no extension mechanism and no cbSize."},
      {label:"format version",k:"enum",r:[12,15],val:"1",
       body:"One version has ever been defined."},
      {label:"format id",k:"enum",r:[16,19],val:"0 = DSD raw",
       body:"Zero is DSD raw, and it is the only value the specification defines. There is no compressed DSF: where DSDIFF has DST, Sony's container simply does not."},
      {label:"channel type",k:"enum",r:[20,23],val:"2 = stereo",sel:0,
       branch:[["1 mono, 2 stereo, 3 three","the small layouts"],["4 quad vs 5 four-channel","same count, different speakers"],["6 five, 7 five-point-one","the surround layouts"]],
       body:"A LAYOUT id, not a count. It says which speakers the channels drive, and two different values can describe the same number of them.",
       note:"type 4 is quad (front pair, back pair) and type 5 is four channels (front pair, centre, LFE). Deriving the count from this field works; deriving this field from the count does not."},
      {label:"channel num",k:"enum",r:[24,27],val:"2",
       body:"How many channels there are. Stated separately because the layout does not determine it and it does not determine the layout.",
       note:"a file whose channel type and channel num disagree is making two claims about itself that cannot both be true."},
      {label:"sampling frequency",k:"enum",r:[28,31],val:"2,822,400 Hz",
       body:"The DSD64 rate: sixty-four times the 44,100 Hz of a CD, which is where the name comes from. The higher rates double from here.",
       note:"name it from a table rather than dividing by 44,100. The 48 kHz-derived family -- 3,072,000 and its doublings -- is legal and does not divide cleanly."}
    ]);
  // DSDIFF: FRM8 and the mandatory version chunk
  build("dsd-frm8","byte",
    [0x46,0x52,0x4D,0x38,0x00,0x00,0x00,0x00,0x08,0x09,0x26,0x52,
     0x44,0x53,0x44,0x20,
     0x46,0x56,0x45,0x52,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x04],
    [
      {label:"ckID",k:"sync",r:[0,3],val:'"FRM8"',
       body:"FORM, with an 8 where IFF has nothing. The digit is the size width in bytes: eight, not four.",
       note:"FRM8 is the container, not the format. DSDIFF is one thing it can hold, and the form type below is what says so."},
      {label:"ckDataSize",k:"enum",r:[4,11],val:"134,817,362",
       body:"BIG-endian, 64-bit, and counting everything after this twelve-byte header -- so the file is this plus 12.",
       note:"the spec is explicit that this is the deviation: 'the ckDataSize is not a long but a double ulong'. Wave64 made the same widening and counts its header IN. Neither is wrong; they are just not the same."},
      {label:"formType",k:"enum",r:[12,15],val:'"DSD "',
       body:"What kind of FRM8 this is, exactly as IFF's form type works. The same four characters that name a Sony DSF, doing a different job.",
       note:"checking FRM8 alone is not enough to claim DSDIFF, in the same way that checking FORM alone does not claim AIFF."},
      {label:"FVER ckID",k:"sync",r:[16,19],val:'"FVER"',
       body:"The format version chunk, and it is MANDATORY: the spec requires it to be the first local chunk in the form."},
      {label:"FVER ckDataSize",k:"enum",r:[20,27],val:"4",
       body:"Four bytes of version follow. Every chunk in a DSDIFF carries a 64-bit size, including the ones that hold four bytes."}
    ]);
  // DSDIFF: PROP, and the first local chunk inside it
  build("dsd-prop","byte",
    [0x01,0x05,0x00,0x00,
     0x50,0x52,0x4F,0x50,0x00,0x00,0x00,0x00,0x00,0x00,0x01,0x7C,
     0x53,0x4E,0x44,0x20,
     0x46,0x53,0x20,0x20,0x00,0x00,0x00,0x00],
    [
      {label:"version",k:"enum",r:[0,3],val:"1.5.0.0",
       body:"Four bytes, most significant first: major, minor, and two more that have always been zero. 1.5 is the published specification, dated 2004.",
       note:"the FVER payload from the previous map. A file claiming anything else is claiming to be a format nobody has documented."},
      {label:"PROP ckID",k:"sync",r:[4,7],val:'"PROP"',
       body:"A container chunk. DSDIFF nests, which IFF always allowed and RIFF only does through LIST."},
      {label:"PROP ckDataSize",k:"enum",r:[8,15],val:"380",
       body:"The whole property block, including the type below and every local chunk after it."},
      {label:"propType",k:"enum",r:[16,19],val:'"SND "',
       body:"What these properties describe. Sound is the only type the specification defines, and the field exists so another could be added without moving anything.",
       note:"a property type reads exactly like a chunk id and is not one. The local chunks start after it."},
      {label:"FS ckID",k:"sync",r:[20,23],val:'"FS  "',
       body:"Sample rate, and the id is two characters padded to four with SPACES. Ids are always four bytes; the padding is part of the id.",
       note:"trimming the id before comparing is a common shortcut that works until a format uses 'FS' and 'FS  ' to mean different things."},
      {label:"FS ckDataSize",k:"enum",r:[24,27],val:"4",
       body:"A u32 sample rate follows: 2,822,400 for DSD64."}
    ]);
"""


def main():
    shell = io.open(TEMPLATE, encoding="utf-8").read()
    # body: between the opening of the sheet and the closing script block
    start = shell.index('<div class="sheet">')
    end = shell.index("</div>\n\n<script>") if "</div>\n\n<script>" in shell \
        else shell.index("<script>")
    out = shell[:start] + BODY + "\n" + shell[end:]

    # swap the build() calls for ours: everything between the first build(
    # and the closing of the IIFE that holds them
    first = out.index("\n  // ", out.index("<script>"))
    last = out.index("})();", first)
    out = out[:first] + "\n" + MAPS + out[last:]

    out = out.replace("CAF Anatomy", "DSD Anatomy")
    out = re.sub(r"<title>[^<]*</title>", "<title>DSD Anatomy</title>", out, 1)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(out)
    print(f"wrote {OUT} ({len(out):,} bytes)")


if __name__ == "__main__":
    main()
