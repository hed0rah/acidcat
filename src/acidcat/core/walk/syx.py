"""SysEx walker: every message named by its manufacturer, DX7 voices decoded.

A .syx is a transcript of a synth talking, one System Exclusive message after
another (core/formats/syx.py has the layout). Each message is a chunk. A Yamaha
DX7 voice dump is decoded: its six-byte header, then one chunk per voice with
the front-panel parameters (algorithm, feedback, LFO, transpose, and per
operator the envelope, output level and frequency), then the checksum. Any
other message, from Yamaha or anyone else, is named and measured, not decoded.
"""

from acidcat.core.formats import syx as syxmod
from acidcat.core.infra.findings import defect, info
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f

# A DX7 bank is 4,104 bytes; a sample dump over SysEx runs to megabytes. The
# cap stops a huge file from being held whole and says so when it bites.
_SYX_READ_CAP = 16 * 1024 * 1024
# Messages and stray runs listed as chunks. A MIDI Sample Dump sends one
# 127-byte packet per message, so a long sample is thousands of them.
_SYX_MESSAGE_LIST_CAP = 4096


def inspect_syx(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _SYX_READ_CAP))
    if bytes([syxmod.SOX]) not in raw:
        raise _Unsupported("no System Exclusive message (no F0 byte)")
    warns = []
    if size > _SYX_READ_CAP:
        warns.append(hit("read_bytes", _SYX_READ_CAP, size,
                         "file is %d bytes; parsed the first %d" % (size, len(raw))))
    spans, complete = syxmod.messages(raw, _SYX_MESSAGE_LIST_CAP)
    chunks, shallow, state = [], {}, {"msg": 0, "voice": 0, "stray": 0}
    for sp in spans:
        if sp["kind"] == "stray":
            n = sp["end"] - sp["start"]
            chunks.append({"id": "stray[%d]" % state["stray"],
                           "offset": sp["start"], "size": n,
                           "summary": "%d bytes outside any SysEx message" % n,
                           "fields": [], "warnings": [], "payload_base": sp["start"]})
            state["stray"] += 1
            warns.append(defect("bytes.stray",
                                "%d bytes at 0x%X are not part of any SysEx message"
                                % (n, sp["start"])))
            continue
        k = state["msg"]
        state["msg"] += 1
        _message(raw, sp, k, chunks, warns, shallow, state)
    if not complete:
        pos = chunks[-1]["offset"] + chunks[-1]["size"]
        warns.append(hit("list_rows", _SYX_MESSAGE_LIST_CAP, len(spans),
                         "listing the first %d messages and runs; the %d bytes "
                         "after them are not walked" % (len(spans), len(raw) - pos)))
        chunks.append({"id": "unwalked", "offset": pos, "size": len(raw) - pos,
                       "summary": "%d bytes past the message listing cap" % (len(raw) - pos),
                       "fields": [], "warnings": [], "payload_base": pos})
    if shallow:
        warns.append(info("decode.partial",
                          "%d message(s) named and measured, not decoded: %s" % (
                              sum(shallow.values()),
                              ", ".join("%s (%d)" % kv for kv in shallow.items()))))
    return chunks, warns


def _ends(raw, sp, k):
    """The findings for a message that no F7 closes."""
    if sp["end_by"] == "status":
        return [defect("required.missing",
                       "message %d at 0x%X has no F7: the status byte 0x%02X at 0x%X "
                       "cuts it short" % (k, sp["start"], raw[sp["end"]], sp["end"]))]
    if sp["end_by"] == "eof":
        return [defect("required.missing",
                       "message %d at 0x%X has no F7: the file ends first"
                       % (k, sp["start"]))]
    return []


def _head_fields(body, y):
    """The fields of a message's F0, manufacturer id and, for a Yamaha bulk
    dump, its four header bytes. Offsets from the F0."""
    mid, mname = syxmod.manufacturer(body)
    fields = [_f(0, 1, "start", "0xF0", "System Exclusive status byte"),
              _f(1 if body else None, syxmod.id_len(body) if body else 0,
                 "manufacturer", "%s (%s)" % (mname, mid) if mid else mname)]
    if y:
        fields += [
            _f(2, 1, "device", y["device"] + 1, "low nibble + 1, the MIDI channel; "
               "high nibble 0 is a bulk dump", enc="bits:0:1:4:4:-1", raw=y["device"] + 1),
            _f(3, 1, "format", y["format"],
               syxmod.YAMAHA_FORMATS.get(y["format"], "a format acidcat does not name")),
            _f(4, 2, "byte_count", format(y["count"], ","),
               "seven bits per byte: high << 7 | low"),
        ]
    return fields


def _message(raw, sp, k, chunks, warns, shallow, state):
    s, e = sp["start"], sp["end"]
    body = raw[s + 1:e - 1] if sp["end_by"] == "eox" else raw[s + 1:e]
    y = syxmod.yamaha_bulk(body)
    want = {syxmod.VMEM: syxmod.VMEM_LEN, syxmod.VCED: syxmod.VCED_LEN}
    if y and want.get(y["format"]) == y["count"]:
        _dx7(raw, sp, k, y, body, chunks, warns, state)
        return
    mid, mname = syxmod.manufacturer(body)
    what = mname
    if y:
        what = "Yamaha " + syxmod.YAMAHA_FORMATS.get(y["format"], "format %d" % y["format"])
    shallow[what] = shallow.get(what, 0) + 1
    fields = _head_fields(body, y)
    cw = _ends(raw, sp, k)
    if y and sp["end_by"] == "eox":
        held = len(body) - (syxmod.YAMAHA_HEADER - 1)    # data plus checksum
        if held != y["count"] + 1:
            cw.append(defect("count.mismatch",
                             "message %d: the header counts %d data bytes and the "
                             "message holds %d" % (k, y["count"], max(held - 1, 0))))
        else:
            cw += _checksum(raw, s + syxmod.YAMAHA_HEADER, y["count"], k, fields, s)
    if sp["end_by"] == "eox":
        fields.append(_f(e - 1 - s, 1, "end", "0xF7", "End of Exclusive"))
    chunks.append({"id": "sysex[%d]" % k, "offset": s, "size": e - s,
                   "summary": "%s SysEx, %s bytes%s" % (
                       what, format(e - s, ","), "" if sp["end_by"] == "eox" else ", no F7"),
                   "fields": fields, "warnings": cw, "payload_base": s})
    warns.extend(cw)


def _checksum(raw, data_at, count, k, fields, base):
    """Check a Yamaha bulk dump's checksum, adding its field. Returns findings."""
    stored = raw[data_at + count]
    want = syxmod.checksum(raw[data_at:data_at + count])
    fields.append(_f(data_at + count - base, 1, "checksum", stored,
                     "matches" if stored == want else "computed 0x%02X" % want))
    if stored == want:
        return []
    return [defect("checksum.mismatch",
                   "message %d: the checksum is 0x%02X and the data sums to 0x%02X"
                   % (k, stored, want))]


def _dx7(raw, sp, k, y, body, chunks, warns, state):
    s, e = sp["start"], sp["end"]
    packed = y["format"] == syxmod.VMEM
    count = y["count"]
    data_at = s + syxmod.YAMAHA_HEADER
    length = e - s
    cw = []
    region_end = e - 2 if sp["end_by"] == "eox" else e      # the bytes before checksum and F7
    usable = max(0, min(data_at + count, region_end) - data_at)
    block = syxmod.PACKED_VOICE if packed else syxmod.VCED_LEN
    n = usable // block
    hfields = _head_fields(body, y)
    tail_fields = []
    exact = length == count + 8 or (sp["end_by"] != "eox" and length == count + 7)
    if exact:
        cw += _checksum(raw, data_at, count, k, tail_fields, data_at + count)
    elif data_at + count > e:
        cw.append(defect("size.overrun",
                         "message %d: the header counts %s data bytes and %s" % (
                             k, format(count, ","),
                             ("only %s remain in the file" if sp["end_by"] == "eof"
                              else "the message holds %s")
                             % format(max(region_end - data_at, 0), ","))))
    else:
        cw.append(defect("count.mismatch",
                         "message %d: the header counts %s data bytes and the message "
                         "holds %s" % (k, format(count, ","), format(max(e - data_at - 2, 0), ","))))
    if not (sp["end_by"] == "eof" and data_at + count > e):
        cw[:0] = _ends(raw, sp, k)                  # a cut-short dump already says why
    names, vw = [], []
    head = {"id": "sysex[%d]" % k, "offset": s, "size": syxmod.YAMAHA_HEADER,
            "summary": "", "fields": hfields, "warnings": cw, "payload_base": s}
    chunks.append(head)
    for i in range(n):
        at = data_at + i * block
        state["voice"] += 1
        v = _voice(raw[at:at + block], packed, state["voice"], at)
        names.append("%d %s" % (state["voice"], v["name"]))
        chunks.append(v["chunk"])
        vw.extend(v["chunk"]["warnings"])
    pos = data_at + n * block
    if pos < e:
        if exact:
            if sp["end_by"] == "eox":
                tail_fields.append(_f(e - 1 - pos, 1, "end", "0xF7", "End of Exclusive"))
            summary = "checksum%s" % (" and F7" if sp["end_by"] == "eox" else ", no F7")
        else:
            summary = "%d bytes no whole voice fills" % (e - pos)
        chunks.append({"id": "end[%d]" % k, "offset": pos, "size": e - pos,
                       "summary": summary, "fields": tail_fields, "warnings": [],
                       "payload_base": pos})
    kind = "32-voice bank" if packed else "single voice"
    head["summary"] = "Yamaha DX7 %s, device %d, %d voice%s%s" % (
        kind, y["device"] + 1, n, "" if n == 1 else "s",
        (": " + names[0].split(" ", 1)[1]) if n == 1 else "")
    hfields.append(_f(None, 0, "voices", ", ".join(names) or "none",
                      "number and name of every voice the dump holds"))
    warns.extend(cw + vw)


def _voice(b, packed, number, at):
    """One DX7 voice block as a chunk. Field offsets are from the block."""
    fields = []

    def pf(name, op=None, bias=0, label=None, note=""):
        byte, lsb, width = syxmod.where(packed, name, op)
        stored = (b[byte] >> lsb) & ((1 << width) - 1)
        v = stored - bias
        fields.append(_f(byte, 1, ("op%d_%s" % (op, name)) if op else name,
                         label(stored) if label else v, note,
                         enc="bits:0:1:%d:%d:%d" % (8 - lsb - width, width, bias), raw=v))
        return stored

    def group(name, first, op=None, note=""):
        byte = syxmod.where(packed, first, op)[0]
        fields.append(_f(byte, 4, ("op%d_%s" % (op, name)) if op else name,
                         " ".join(str(x) for x in b[byte:byte + 4]), note))

    name = syxmod.voice_name(b, packed).rstrip()
    nat = syxmod.VMEM_NAME if packed else syxmod.VCED_NAME
    fields.append(_f(nat, syxmod.NAME_LEN, "name", name or "(blank)", text=True))
    alg = pf("algorithm", bias=-1, note="1-32")
    fb = pf("feedback", note="0-7")
    pf("osc_key_sync", label=_onoff)
    pf("transpose", bias=syxmod.TRANSPOSE_C3, note="semitones from C3 (stored 24)")
    pf("lfo_wave", label=_wave)
    pf("lfo_speed")
    pf("lfo_delay")
    pf("lfo_pitch_depth")
    pf("lfo_amp_depth")
    pf("lfo_sync", label=_onoff)
    pf("pitch_mod_sens", note="0-7")
    group("pitch_eg_rates", "pr1", note="rates 1-4")
    group("pitch_eg_levels", "pl1", note="levels 1-4, 50 is no shift")
    levels = []
    for op in range(1, syxmod.OPS + 1):
        group("rates", "r1", op, "EG rates 1-4")
        group("levels", "l1", op, "EG levels 1-4")
        levels.append(pf("level", op, note="output level 0-99"))
        mode = pf("mode", op, label=lambda x: "fixed" if x else "ratio")
        coarse = pf("coarse", op)
        fine = pf("fine", op)
        fields.append(_f(None, 0, "op%d_frequency" % op, syxmod.frequency(mode, coarse, fine),
                         derived_from=["op%d_mode" % op, "op%d_coarse" % op, "op%d_fine" % op]))
        pf("detune", op, bias=syxmod.DETUNE_CENTRE, note="-7 to +7")
        pf("velocity_sens", op, note="0-7")
    cw = []
    bad = syxmod.out_of_range(b, packed)
    if bad:
        cw.append(defect("value.invalid",
                         "voice %d %r: %d parameter(s) above their maximum: %s" % (
                             number, name, len(bad), ", ".join(bad[:4])
                             + (", ..." if len(bad) > 4 else ""))))
    chunk = {"id": "voice[%d]" % number, "offset": at, "size": len(b),
             "summary": "%r algorithm %d, feedback %d, op1-6 levels %s" % (
                 name, alg + 1, fb, "/".join(str(x) for x in levels)),
             "fields": fields, "warnings": cw, "payload_base": at}
    return {"name": name or "(blank)", "chunk": chunk}


def _onoff(x):
    return "on" if x else "off"


def _wave(x):
    return syxmod.LFO_WAVES[x] if x < len(syxmod.LFO_WAVES) else "undefined (%d)" % x
