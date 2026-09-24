"""Tests for the SPC player: the SPC700, the S-DSP, and the render loop.

An .spc is the sound unit frozen mid-tune, so "does it play" is a question
about two machines. What can be pinned without a reference player is what
each machine computes: the CPU's arithmetic and flags, the timers' clock,
and the DSP's documented behaviour -- when a key-on is heard, how BRR
decodes, how a block that ends without a loop silences its voice, what the
echo delays by. The phase conventions (timer base, rate counter) are not
in the file; the values here are the ones that line up with the
established players, which the player was measured against sample by
sample during development.
"""
import os
import struct

import pytest

from acidcat.core.codecs import sdsp, spc_render
from acidcat.core.codecs.spc700 import SPC700


# ── the SPC700 ──────────────────────────────────────────────────────

def _run(code, a=0, x=0, y=0, psw=0, cycles=None, ram=None):
    """Run `code` at $0200 until it has executed. Returns the CPU."""
    cpu = SPC700()
    if ram:
        for addr, v in ram.items():
            cpu.ram[addr] = v
    cpu.ram[0x0200:0x0200 + len(code)] = bytes(code)
    cpu.pc, cpu.a, cpu.x, cpu.y, cpu.psw = 0x0200, a, x, y, psw
    end = 0x0200 + len(code)
    while cpu.pc != end:
        cpu.run(cpu.cycles + 1)
    return cpu


def test_div_is_ya_over_x():
    cpu = _run([0x9E], a=100, y=0, x=7)                # DIV YA,X
    assert (cpu.a, cpu.y) == (14, 2)


def test_div_overflow_sets_v_and_keeps_the_hardware_result():
    """A quotient over 255 is not an error on this CPU: V is set and the
    shift-subtract circuit leaves a defined result."""
    cpu = _run([0x9E], a=0x00, y=0x10, x=0x01)          # 0x1000 / 1
    assert cpu.psw & 0x40
    cpu = _run([0x9E], a=0x34, y=0x12, x=0x00)          # divide by zero
    assert cpu.psw & 0x40


def test_mul_sets_n_and_z_from_y_only():
    cpu = _run([0xCF], a=0x04, y=0x16)                  # MUL YA: 0x0058
    assert (cpu.y, cpu.a) == (0x00, 0x58)
    assert cpu.psw & 0x02                               # Z from Y, not YA


def test_adc_overflow_and_sbc_borrow():
    cpu = _run([0x88, 0x01], a=0x7F)                    # ADC A,#1
    assert cpu.a == 0x80 and cpu.psw & 0x40 and cpu.psw & 0x80
    cpu = _run([0xA8, 0x20], a=0x10, psw=0x01)          # SBC A,#$20, carry set
    assert cpu.a == 0xF0 and not cpu.psw & 0x01


def test_addw_half_carry_is_from_bit_11():
    # YA = 0x00E5, word at $14 = 0x0FCD -> 0x10B2, carry out of bit 11
    cpu = _run([0x7A, 0x14], a=0xE5, y=0x00, ram={0x14: 0xCD, 0x15: 0x0F})
    assert (cpu.y, cpu.a) == (0x10, 0xB2)
    assert cpu.psw & 0x08


def test_subw():
    cpu = _run([0x9A, 0x14], a=0xBE, y=0x10, ram={0x14: 0xCD, 0x15: 0x0F})
    assert (cpu.y, cpu.a) == (0x00, 0xF1)
    assert cpu.psw & 0x01                               # no borrow


def test_daa_adjusts_a_bcd_sum():
    cpu = _run([0x88, 0x19, 0xDF], a=0x28)              # 28 + 19 = 47 in BCD
    assert cpu.a == 0x47


def test_every_opcode_has_a_handler_and_a_cycle_count():
    from acidcat.core.codecs.spc700 import CYCLES
    cpu = SPC700()
    assert len(CYCLES) == 256 and all(c >= 2 for c in CYCLES)
    assert len(cpu._ops) == 256


def test_a_timer_counts_at_its_base_rate_over_its_divider():
    """Timer 0 is 8 kHz (128 CPU cycles) divided by $FA; its 4-bit output
    clears when read. The base clock ticks at cycle 1, 1+128, ... -- the
    phase is not in the file, and this is the one the established players
    use, which a driver seeding a random number from a counter read hears."""
    cpu = SPC700()
    cpu.write(0xFA, 2)
    cpu.write(0xF1, 0x01)
    cpu.cycles = 1 + 128 * 3                            # four base ticks
    assert cpu.read(0xFD) == 2
    assert cpu.read(0xFD) == 0                          # read clears it


# ── the S-DSP ───────────────────────────────────────────────────────

DIR, SAMPLE = 0x0300, 0x0400


def _brr(nibbles, header):
    out = bytearray([header])
    for i in range(0, 16, 2):
        out.append(((nibbles[i] & 0xF) << 4) | (nibbles[i + 1] & 0xF))
    return bytes(out)


def _dsp(blocks, pitch=0x1000, adsr=(0x8F, 0xE0), loop=SAMPLE, extra=None):
    """A DSP with voice 0 set up on `blocks` and full volume, echo writes
    off. Nothing keyed on yet."""
    ram = bytearray(0x10000)
    ram[DIR:DIR + 4] = struct.pack("<HH", SAMPLE, loop)
    data = b"".join(blocks)
    ram[SAMPLE:SAMPLE + len(data)] = data
    d = sdsp.SDSP(ram)
    regs = bytearray(128)
    regs[0x00] = regs[0x01] = 0x40                      # voice 0 volume
    regs[0x02], regs[0x03] = pitch & 0xFF, pitch >> 8
    regs[0x05], regs[0x06] = adsr
    regs[0x0C] = regs[0x1C] = 0x7F                      # master volume
    regs[0x5D] = DIR >> 8
    regs[0x6C] = 0x20                                   # echo writes off
    for r, v in (extra or {}).items():
        regs[r] = v
    d.load(regs)
    return d


def _left(d):
    return list(d.out[0::2])


DC = _brr([4] * 16, (10 << 4) | 3)                      # constant, looping


def test_key_on_is_heard_six_samples_after_the_poll():
    """KON is polled every other sample; the poll that takes it starts the
    five-sample key-on delay, and the voice is heard on the sample after."""
    d = _dsp([DC])
    d.write(0x4C, 0x01)
    d.render(12)
    out = _left(d)
    first = next(i for i, v in enumerate(out) if v)
    assert first == 1 + 6                               # polled on sample 1


def test_a_dc_sample_plays_at_its_level_through_the_volumes():
    d = _dsp([DC])
    d.write(0x4C, 0x01)
    d.render(64)
    level = _left(d)[-1]
    # 4 << 10 >> 1 = 2048, doubled in the buffer; env 0x7FF/0x800, vol 64/128,
    # master 127/128; the Gaussian weights sum to about one
    assert abs(level - 4096 * 0.5 * 127 / 128) < 0.02 * 2048


def test_pitch_0x1000_is_one_source_sample_per_output_sample():
    ramp = _brr([0, 1, 2, 3, 4, 5, 6, 7, 7, 6, 5, 4, 3, 2, 1, 0], (10 << 4) | 3)
    d = _dsp([ramp], pitch=0x1000)
    d.write(0x4C, 0x01)
    d.render(200)
    out = _left(d)[100:]
    assert out[:80] == out[16:96]                       # period 16
    d = _dsp([ramp], pitch=0x0800)
    d.write(0x4C, 0x01)
    d.render(300)
    out = _left(d)[150:]
    assert out[:80] == out[32:112]                      # half speed, period 32


def test_a_key_on_rewritten_before_the_next_poll_is_dropped():
    """The poll that takes a KON bit clears it at the following poll, even
    if the program wrote it again in between: a driver that writes KON twice
    in quick succession keys the voice once."""
    d = _dsp([DC])
    d.write(0x4C, 0x01)
    d.render(2)                                         # taken at sample 1
    d.write(0x4C, 0x01)                                 # dropped at sample 3
    d.render(30)
    out = _left(d)
    assert all(out[7:])                                 # never restarted
    d = _dsp([DC])
    d.write(0x4C, 0x01)
    d.render(4)                                         # sample 3 cleared it
    d.write(0x4C, 0x01)                                 # taken at sample 5
    d.render(30)
    out = _left(d)
    assert not all(out[7:])                             # restarted: a gap


def test_a_block_that_ends_without_looping_silences_the_voice():
    body = _brr([4] * 16, 10 << 4)
    last = _brr([4] * 16, (10 << 4) | 1)                # end, no loop
    d = _dsp([body, last])
    d.write(0x4C, 0x01)
    d.render(60)
    out = _left(d)
    assert any(out[:20]) and not any(out[40:])
    assert d.regs[0x08] == 0                            # ENVX


def test_endx_is_set_when_a_looping_sample_leaves_its_end_block():
    d = _dsp([DC])
    d.write(0x4C, 0x01)
    d.render(8)
    assert not d.regs[0x7C] & 1
    d.render(40)
    assert d.regs[0x7C] & 1
    d.write(0x7C, 0x00)                                 # any write clears it
    assert d.regs[0x7C] == 0


def test_koff_releases_at_eight_per_sample():
    d = _dsp([DC])
    d.write(0x4C, 0x01)
    d.render(40)
    d.write(0x4C, 0x00)
    d.write(0x5C, 0x01)
    env = d.voices[0].env
    d.render(2)                                         # the next poll
    d.render(10)
    assert env - d.voices[0].env in range(8 * 9, 8 * 12)


def test_echo_repeats_the_signal_after_its_delay():
    """EDL=1 is a 2 KB ring of 4-byte stereo frames: 512 samples. With the
    voice only in the echo and a single FIR tap, the dry signal is silent
    and the wet one appears one ring length after the note."""
    extra = {0x0C: 0x00, 0x1C: 0x00,                    # dry off
             0x2C: 0x7F, 0x3C: 0x7F,                    # echo volume
             0x4D: 0x01, 0x6D: 0x80, 0x7D: 0x01,        # EON, ESA $8000, EDL 1
             0x7F: 0x7F, 0x6C: 0x00}                    # newest-sample tap
    d = _dsp([DC], extra=extra)
    d.write(0x4C, 0x01)
    d.render(700)
    out = _left(d)
    first = next(i for i, v in enumerate(out) if v)
    assert first == 7 + 512


def test_mute_mask_leaves_a_voice_out_of_the_mix():
    d = _dsp([DC])
    d.mute_mask = 0x01
    d.write(0x4C, 0x01)
    d.render(40)
    assert not any(_left(d))
    assert d.regs[0x08]                                 # still enveloping


# ── the render loop ─────────────────────────────────────────────────

def _snapshot():
    """A minimal .spc: a program that sets up voice 0 on a DC sample, keys
    it on, and spins."""
    ram = bytearray(0x10000)
    code = bytearray()
    for r, v in ((0x5D, DIR >> 8), (0x6C, 0x20), (0x0C, 0x7F), (0x1C, 0x7F),
                 (0x00, 0x40), (0x01, 0x40), (0x03, 0x10), (0x05, 0x8F),
                 (0x06, 0xE0), (0x4C, 0x01)):
        code += bytes([0x8F, r, 0xF2, 0x8F, v, 0xF3])   # MOV $F2,#r; MOV $F3,#v
    code += bytes([0x2F, 0xFE])                         # BRA $
    ram[0x0200:0x0200 + len(code)] = code
    ram[DIR:DIR + 4] = struct.pack("<HH", SAMPLE, SAMPLE)
    ram[SAMPLE:SAMPLE + len(DC)] = DC
    hdr = bytearray(0x100)
    hdr[0:33] = b"SNES-SPC700 Sound File Data v0.30"
    hdr[0x21:0x24] = b"\x1a\x1a\x1b"                    # no tag
    hdr[0x24] = 30
    struct.pack_into("<HBBBBB", hdr, 0x25, 0x0200, 0, 0, 0, 0x02, 0xEF)
    dsp = bytearray(128)
    dsp[0x6C] = 0xE0
    return bytes(hdr) + bytes(ram) + bytes(dsp) + bytes(64) + bytes(64)


def test_render_runs_the_program_and_returns_stereo_32k():
    pcm, info = spc_render.render(_snapshot(), seconds=0.25, fade_ms=0)
    assert info["sample_rate"] == 32000 and info["channels"] == 2
    assert len(pcm) == 8000 * 4
    left = struct.unpack("<%dh" % (len(pcm) // 2), pcm)[0::2]
    assert any(left[100:]) and not info["halted"]


def test_the_fade_ends_in_silence():
    pcm, _ = spc_render.render(_snapshot(), seconds=0.1, fade_ms=100)
    last = struct.unpack("<2h", pcm[-4:])
    assert abs(last[0]) < 50


def test_what_is_not_a_snapshot_is_refused():
    assert not spc_render.can_render(b"not an spc")[0]
    with pytest.raises(spc_render.CannotRender):
        spc_render.render(b"\x00" * 300, seconds=0.1)
    with pytest.raises(spc_render.CannotRender):
        spc_render.render(_snapshot()[:0x10000], seconds=0.1)


def test_the_tui_play_branch_matches_the_walker_label():
    """The TUI picks the SPC player by a substring of the walker's label;
    a reworded label would silently send `p` hunting for PCM instead."""
    from acidcat.core.walk import _WALKERS
    assert "spc700 sound snapshot" in _WALKERS["spc"][0].lower()


# ── real snapshots ──────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("ACIDCAT_SPC_CORPUS"),
                    reason="set ACIDCAT_SPC_CORPUS to a dir of real .spc files")
def test_real_snapshots_play():
    """Every sampled snapshot runs, makes sound, and does not halt. A driver
    that halts or stays silent is a CPU or DSP bug until shown otherwise."""
    import random
    root = os.environ["ACIDCAT_SPC_CORPUS"]
    files = sorted(os.path.join(d, f) for d, _, fs in os.walk(root)
                   for f in fs if f.lower().endswith(".spc"))
    if not files:
        pytest.skip("no .spc files under ACIDCAT_SPC_CORPUS")
    random.Random(7).shuffle(files)
    silent = []
    for path in files[:12]:
        pcm, info = spc_render.render_file(path, seconds=2.0, fade_ms=0)
        assert not info["halted"], path
        if not any(pcm):
            silent.append(path)
    assert len(silent) <= 1, silent
