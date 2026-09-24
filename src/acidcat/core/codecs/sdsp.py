"""The S-DSP: the SNES sound chip's eight voices, its mixer and its echo.

Sample-accurate rather than cycle-accurate: every register the CPU writes
takes effect at the next 32 kHz sample, where the hardware reads each at
its own slot within the sample. That is invisible to the ear, but a driver
that busy-waits on ENVX can see a value change a few CPU cycles early and
drift its timing by that much; it is the one known gap.

Written from the fullsnes hardware reference (nocash), which documents:

- BRR decoding, four samples at a time, into a 12-sample ring per voice,
  with the filters' exact integer forms and the 15-bit wrap;
- the pitch counter (bits 12-15 pick the sample, bits 4-11 the Gaussian
  row), capped at 0x7FFF, and pitch modulation from the previous voice;
- the 4-point Gaussian interpolation, with the hardware's own 512-entry
  table and its overflow behaviour;
- ADSR and GAIN envelopes stepped by the shared rate counter, whose
  periods and phase offsets make a step land on the sample the hardware's
  does;
- KON and KOFF polled every other sample, the five-sample key-on delay,
  FLG's soft reset, mute and echo-write bits, and the noise generator;
- the mixer, with 16-bit saturation after every addition, master volume,
  and the echo: a ring in the CPU's own RAM, an 8-tap FIR, feedback.

The order of events inside a sample (output, then the end-block check,
then key-on/off, then the envelope, then the pitch step and decode), the
key-on latch, and the rate counter's starting phase were settled by
comparing against blargg's Snes_Spc sample by sample; the file does not
carry the phases, so matching the player everyone already hears is the
honest choice.

The output is signed 16-bit stereo at 32 kHz, before the SNES's analog
stage; the final phase inversion the post-amp applies is not applied, so
the samples read as the DSP computes them.
"""

import array

SAMPLE_RATE = 32000

# 4-point Gaussian interpolation table, measured from hardware (fullsnes)
GAUSS = [
    0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000,
    0x001, 0x001, 0x001, 0x001, 0x001, 0x001, 0x001, 0x001, 0x001, 0x001, 0x001, 0x002, 0x002, 0x002, 0x002, 0x002,
    0x002, 0x002, 0x003, 0x003, 0x003, 0x003, 0x003, 0x004, 0x004, 0x004, 0x004, 0x004, 0x005, 0x005, 0x005, 0x005,
    0x006, 0x006, 0x006, 0x006, 0x007, 0x007, 0x007, 0x008, 0x008, 0x008, 0x009, 0x009, 0x009, 0x00A, 0x00A, 0x00A,
    0x00B, 0x00B, 0x00B, 0x00C, 0x00C, 0x00D, 0x00D, 0x00E, 0x00E, 0x00F, 0x00F, 0x00F, 0x010, 0x010, 0x011, 0x011,
    0x012, 0x013, 0x013, 0x014, 0x014, 0x015, 0x015, 0x016, 0x017, 0x017, 0x018, 0x018, 0x019, 0x01A, 0x01B, 0x01B,
    0x01C, 0x01D, 0x01D, 0x01E, 0x01F, 0x020, 0x020, 0x021, 0x022, 0x023, 0x024, 0x024, 0x025, 0x026, 0x027, 0x028,
    0x029, 0x02A, 0x02B, 0x02C, 0x02D, 0x02E, 0x02F, 0x030, 0x031, 0x032, 0x033, 0x034, 0x035, 0x036, 0x037, 0x038,
    0x03A, 0x03B, 0x03C, 0x03D, 0x03E, 0x040, 0x041, 0x042, 0x043, 0x045, 0x046, 0x047, 0x049, 0x04A, 0x04C, 0x04D,
    0x04E, 0x050, 0x051, 0x053, 0x054, 0x056, 0x057, 0x059, 0x05A, 0x05C, 0x05E, 0x05F, 0x061, 0x063, 0x064, 0x066,
    0x068, 0x06A, 0x06B, 0x06D, 0x06F, 0x071, 0x073, 0x075, 0x076, 0x078, 0x07A, 0x07C, 0x07E, 0x080, 0x082, 0x084,
    0x086, 0x089, 0x08B, 0x08D, 0x08F, 0x091, 0x093, 0x096, 0x098, 0x09A, 0x09C, 0x09F, 0x0A1, 0x0A3, 0x0A6, 0x0A8,
    0x0AB, 0x0AD, 0x0AF, 0x0B2, 0x0B4, 0x0B7, 0x0BA, 0x0BC, 0x0BF, 0x0C1, 0x0C4, 0x0C7, 0x0C9, 0x0CC, 0x0CF, 0x0D2,
    0x0D4, 0x0D7, 0x0DA, 0x0DD, 0x0E0, 0x0E3, 0x0E6, 0x0E9, 0x0EC, 0x0EF, 0x0F2, 0x0F5, 0x0F8, 0x0FB, 0x0FE, 0x101,
    0x104, 0x107, 0x10B, 0x10E, 0x111, 0x114, 0x118, 0x11B, 0x11E, 0x122, 0x125, 0x129, 0x12C, 0x130, 0x133, 0x137,
    0x13A, 0x13E, 0x141, 0x145, 0x148, 0x14C, 0x150, 0x153, 0x157, 0x15B, 0x15F, 0x162, 0x166, 0x16A, 0x16E, 0x172,
    0x176, 0x17A, 0x17D, 0x181, 0x185, 0x189, 0x18D, 0x191, 0x195, 0x19A, 0x19E, 0x1A2, 0x1A6, 0x1AA, 0x1AE, 0x1B2,
    0x1B7, 0x1BB, 0x1BF, 0x1C3, 0x1C8, 0x1CC, 0x1D0, 0x1D5, 0x1D9, 0x1DD, 0x1E2, 0x1E6, 0x1EB, 0x1EF, 0x1F3, 0x1F8,
    0x1FC, 0x201, 0x205, 0x20A, 0x20F, 0x213, 0x218, 0x21C, 0x221, 0x226, 0x22A, 0x22F, 0x233, 0x238, 0x23D, 0x241,
    0x246, 0x24B, 0x250, 0x254, 0x259, 0x25E, 0x263, 0x267, 0x26C, 0x271, 0x276, 0x27B, 0x280, 0x284, 0x289, 0x28E,
    0x293, 0x298, 0x29D, 0x2A2, 0x2A6, 0x2AB, 0x2B0, 0x2B5, 0x2BA, 0x2BF, 0x2C4, 0x2C9, 0x2CE, 0x2D3, 0x2D8, 0x2DC,
    0x2E1, 0x2E6, 0x2EB, 0x2F0, 0x2F5, 0x2FA, 0x2FF, 0x304, 0x309, 0x30E, 0x313, 0x318, 0x31D, 0x322, 0x326, 0x32B,
    0x330, 0x335, 0x33A, 0x33F, 0x344, 0x349, 0x34E, 0x353, 0x357, 0x35C, 0x361, 0x366, 0x36B, 0x370, 0x374, 0x379,
    0x37E, 0x383, 0x388, 0x38C, 0x391, 0x396, 0x39B, 0x39F, 0x3A4, 0x3A9, 0x3AD, 0x3B2, 0x3B7, 0x3BB, 0x3C0, 0x3C5,
    0x3C9, 0x3CE, 0x3D2, 0x3D7, 0x3DC, 0x3E0, 0x3E5, 0x3E9, 0x3ED, 0x3F2, 0x3F6, 0x3FB, 0x3FF, 0x403, 0x408, 0x40C,
    0x410, 0x415, 0x419, 0x41D, 0x421, 0x425, 0x42A, 0x42E, 0x432, 0x436, 0x43A, 0x43E, 0x442, 0x446, 0x44A, 0x44E,
    0x452, 0x455, 0x459, 0x45D, 0x461, 0x465, 0x468, 0x46C, 0x470, 0x473, 0x477, 0x47A, 0x47E, 0x481, 0x485, 0x488,
    0x48C, 0x48F, 0x492, 0x496, 0x499, 0x49C, 0x49F, 0x4A2, 0x4A6, 0x4A9, 0x4AC, 0x4AF, 0x4B2, 0x4B5, 0x4B7, 0x4BA,
    0x4BD, 0x4C0, 0x4C3, 0x4C5, 0x4C8, 0x4CB, 0x4CD, 0x4D0, 0x4D2, 0x4D5, 0x4D7, 0x4D9, 0x4DC, 0x4DE, 0x4E0, 0x4E3,
    0x4E5, 0x4E7, 0x4E9, 0x4EB, 0x4ED, 0x4EF, 0x4F1, 0x4F3, 0x4F5, 0x4F6, 0x4F8, 0x4FA, 0x4FB, 0x4FD, 0x4FF, 0x500,
    0x502, 0x503, 0x504, 0x506, 0x507, 0x508, 0x50A, 0x50B, 0x50C, 0x50D, 0x50E, 0x50F, 0x510, 0x511, 0x511, 0x512,
    0x513, 0x514, 0x514, 0x515, 0x516, 0x516, 0x517, 0x517, 0x517, 0x518, 0x518, 0x518, 0x518, 0x518, 0x519, 0x519,
]
assert len(GAUSS) == 512

# Rates 1..31 are the number of samples between steps; rate 0 never steps.
# The offsets place each rate's steps against a single counter shared by
# every voice and the noise generator, which is how the hardware derives
# them all from one clock.
_NEVER = 1 << 30
RATE_PERIOD = [_NEVER, 2048, 1536, 1280, 1024, 768, 640, 512, 384, 320, 256, 192,
               160, 128, 96, 80, 64, 48, 40, 32, 24, 20, 16, 12, 10, 8, 6, 5, 4,
               3, 2, 1]
RATE_OFFSET = [1] + [0, 1040, 536] * 9 + [0, 1040, 0, 0]
COUNTER_RANGE = 0x7800                        # the LCM of the periods

ATTACK, DECAY, SUSTAIN, RELEASE = 0, 1, 2, 3

# registers
R_MVOLL, R_MVOLR, R_EVOLL, R_EVOLR = 0x0C, 0x1C, 0x2C, 0x3C
R_KON, R_KOFF, R_FLG, R_ENDX = 0x4C, 0x5C, 0x6C, 0x7C
R_EFB, R_PMON, R_NON, R_EON, R_DIR, R_ESA, R_EDL = 0x0D, 0x2D, 0x3D, 0x4D, 0x5D, 0x6D, 0x7D


def _s8(v):
    return v - 256 if v & 0x80 else v


S8 = [_s8(v) for v in range(256)]


def _clamp16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


class Voice(object):
    __slots__ = ("n", "base", "bit", "buf", "buf_pos", "interp", "brr_addr",
                 "brr_off", "p1", "p2", "env", "hidden", "mode", "kon_delay",
                 "out")

    def __init__(self, n):
        self.n = n
        self.base = n << 4
        self.bit = 1 << n
        self.buf = [0] * 12
        self.buf_pos = 0
        self.interp = 0
        self.brr_addr = 0
        self.brr_off = 1
        self.p1 = self.p2 = 0
        self.env = 0
        self.hidden = 0
        self.mode = RELEASE
        self.kon_delay = 0
        self.out = 0


class SDSP(object):
    """The DSP. `ram` is the CPU's 64 KB, shared: samples are read from it
    and the echo is written into it."""

    def __init__(self, ram):
        self.ram = ram
        self.regs = bytearray(128)
        self.voices = [Voice(n) for n in range(8)]
        # the rate counter's phase is not in an .spc; this start lines its
        # steps up with the established players', so a driver that reads
        # ENVX back sees the values they give it
        self.counter = 2032
        self.noise = 0x4000
        self.every_other = True
        self.new_kon = 0                      # KON as last written
        self.kon = 0                          # KON as last polled
        self.koff = 0
        self.echo_off = 0
        self.echo_len = 0
        self.fir_l = [0] * 8
        self.fir_r = [0] * 8
        self.fir_pos = 0
        self.samples = 0                      # samples produced so far
        self.mute_mask = 0                    # voices left out of the mix
        self.out = array.array("h")

    # -- register interface -------------------------------------------------

    def load(self, regs):
        """Registers from a snapshot. Internal voice state is not in an .spc,
        so every voice starts silent. A KON the snapshot holds is taken as
        pending: some are frozen the instant after a key-on, with a driver
        that will not write it again, and without this they play nothing."""
        self.regs[:] = regs[:128]
        self.new_kon = self.regs[R_KON]

    def read(self, r):
        return self.regs[r]

    def write(self, r, v):
        self.regs[r] = v
        if r == R_KON:
            self.new_kon = v
        elif r == R_ENDX:
            self.regs[R_ENDX] = 0               # any write clears every bit

    def run_to(self, cycle):
        target = cycle // 32
        n = target - self.samples
        if n > 0:
            self.render(n)

    # -- one sample -----------------------------------------------------------

    def _fires(self, rate):
        return rate and (self.counter + RATE_OFFSET[rate]) % RATE_PERIOD[rate] == 0

    def render(self, n):
        regs = self.regs
        ram = self.ram
        out = self.out
        voices = self.voices
        gauss = GAUSS
        for _ in range(n):
            self.counter -= 1
            if self.counter < 0:
                self.counter = COUNTER_RANGE - 1
            flg = regs[R_FLG]
            if self._fires(flg & 0x1F):
                nz = self.noise
                self.noise = ((nz >> 1) & 0x3FFF) | (((nz ^ (nz >> 1)) & 1) << 14)
            noise16 = self.noise - 0x8000 if self.noise & 0x4000 else self.noise
            noise16 <<= 1

            # KON and KOFF are polled every other sample. A KON bit is taken
            # at one poll only: the next poll drops it even if it was
            # written again in between.
            self.every_other = not self.every_other
            if self.every_other:
                self.new_kon &= ~self.kon & 0xFF
                self.kon = self.new_kon
                self.koff = regs[R_KOFF]
            poll = self.every_other
            kon = self.kon
            koff = self.koff

            pmon = regs[R_PMON]
            non = regs[R_NON]
            eon = regs[R_EON]
            dir_base = regs[R_DIR] << 8
            main_l = main_r = echo_l = echo_r = 0
            prev_out = 0
            for v in voices:
                base = v.base
                bit = v.bit
                header = ram[v.brr_addr]
                pitch = regs[base + 2] | ((regs[base + 3] & 0x3F) << 8)
                if pmon & bit and v.n:
                    pitch += ((prev_out >> 5) * pitch) >> 10

                if v.kon_delay:
                    v.kon_delay -= 1
                    if v.kon_delay == 4:
                        entry = (dir_base + (regs[base + 4] << 2)) & 0xFFFF
                        v.brr_addr = ram[entry] | (ram[(entry + 1) & 0xFFFF] << 8)
                        v.brr_off = 1
                        v.buf_pos = 0
                        v.p1 = v.p2 = 0
                        header = 0                      # not read on this sample
                    v.env = 0
                    v.hidden = 0
                    v.interp = 0x4000 if v.kon_delay & 3 else 0
                    pitch = 0

                # interpolate the four samples under the pitch counter
                env = v.env
                if not env:
                    output = 0                      # silent: nothing to mix
                else:
                    if non & bit:
                        output = noise16
                    else:
                        interp = v.interp
                        i = (interp >> 4) & 0xFF
                        p = (interp >> 12) + v.buf_pos
                        buf = v.buf
                        s = ((gauss[255 - i] * buf[p % 12]) >> 11)                             + ((gauss[511 - i] * buf[(p + 1) % 12]) >> 11)                             + ((gauss[256 + i] * buf[(p + 2) % 12]) >> 11)
                        s = ((s + 0x8000) & 0xFFFF) - 0x8000   # wraps, as the chip does
                        s += (gauss[i] * buf[(p + 3) % 12]) >> 11
                        output = (32767 if s > 32767 else -32768 if s < -32768 else s) & ~1
                    output = ((output * env) >> 11) & ~1
                v.out = output
                prev_out = output
                regs[base + 8] = env >> 4
                regs[base + 9] = (output >> 8) & 0xFF

                if output and not self.mute_mask & bit:
                    vl = (output * S8[regs[base]]) >> 7
                    vr = (output * S8[regs[base + 1]]) >> 7
                    x = main_l + vl
                    main_l = 32767 if x > 32767 else -32768 if x < -32768 else x
                    x = main_r + vr
                    main_r = 32767 if x > 32767 else -32768 if x < -32768 else x
                    if eon & bit:
                        x = echo_l + vl
                        echo_l = 32767 if x > 32767 else -32768 if x < -32768 else x
                        x = echo_r + vr
                        echo_r = 32767 if x > 32767 else -32768 if x < -32768 else x

                # soft reset, or a block that ends without looping
                if flg & 0x80 or header & 3 == 1:
                    v.mode = RELEASE
                    v.env = 0
                if poll:
                    if koff & bit:
                        v.mode = RELEASE
                    if kon & bit:
                        v.kon_delay = 5
                        v.mode = ATTACK
                        regs[R_ENDX] &= ~bit & 0xFF

                if not v.kon_delay:
                    self._envelope(v)

                # advance the pitch counter; four more samples are decoded
                # when it has used a group
                old = v.interp
                v.interp = (old & 0x3FFF) + pitch
                if v.interp > 0x7FFF:
                    v.interp = 0x7FFF
                if old >= 0x4000:
                    self._decode(v, header, dir_base)

            # echo: read the oldest entry, filter it, mix, write the newest
            esa = regs[R_ESA] << 8
            addr = (esa + self.echo_off) & 0xFFFF
            el = ram[addr] | (ram[(addr + 1) & 0xFFFF] << 8)
            er = ram[(addr + 2) & 0xFFFF] | (ram[(addr + 3) & 0xFFFF] << 8)
            el = (el - 0x10000 if el & 0x8000 else el) >> 1
            er = (er - 0x10000 if er & 0x8000 else er) >> 1
            fp = self.fir_pos = (self.fir_pos + 1) & 7
            self.fir_l[fp] = el
            self.fir_r[fp] = er
            fl = fr = 0
            for k in range(7):
                c = _s8(regs[0x0F + (k << 4)])
                j = (fp + 1 + k) & 7                  # oldest first
                fl += (self.fir_l[j] * c) >> 6
                fr += (self.fir_r[j] * c) >> 6
            fl = ((fl + 0x8000) & 0xFFFF) - 0x8000
            fr = ((fr + 0x8000) & 0xFFFF) - 0x8000
            c7 = _s8(regs[0x7F])
            fl = _clamp16(fl + ((self.fir_l[fp] * c7) >> 6)) & ~1
            fr = _clamp16(fr + ((self.fir_r[fp] * c7) >> 6)) & ~1

            left = _clamp16(((main_l * _s8(regs[R_MVOLL])) >> 7)
                            + ((fl * _s8(regs[R_EVOLL])) >> 7))
            right = _clamp16(((main_r * _s8(regs[R_MVOLR])) >> 7)
                             + ((fr * _s8(regs[R_EVOLR])) >> 7))
            if flg & 0x40:
                left = right = 0

            efb = _s8(regs[R_EFB])
            if not flg & 0x20:
                wl = _clamp16(echo_l + ((fl * efb) >> 7)) & 0xFFFE
                wr = _clamp16(echo_r + ((fr * efb) >> 7)) & 0xFFFE
                ram[addr] = wl & 0xFF
                ram[(addr + 1) & 0xFFFF] = wl >> 8
                ram[(addr + 2) & 0xFFFF] = wr & 0xFF
                ram[(addr + 3) & 0xFFFF] = wr >> 8
            if self.echo_off == 0:
                self.echo_len = ((regs[R_EDL] & 0x0F) << 11) or 4
            self.echo_off += 4
            if self.echo_off >= self.echo_len:
                self.echo_off = 0

            out.append(left)
            out.append(right)
        self.samples += n

    # -- BRR ------------------------------------------------------------------

    def _decode(self, v, header, dir_base):
        ram = self.ram
        addr = v.brr_addr
        shift = header >> 4
        filt = (header >> 2) & 3
        p1, p2 = v.p1, v.p2
        buf = v.buf
        pos = v.buf_pos
        for k in range(2):
            byte = ram[(addr + v.brr_off + k) & 0xFFFF]
            for nib in (byte >> 4, byte & 0x0F):
                s = nib - 16 if nib & 8 else nib
                if shift <= 12:
                    s = (s << shift) >> 1
                else:
                    s = -2048 if s < 0 else 0
                if filt == 1:
                    s += p1 + ((-p1) >> 4)
                elif filt == 2:
                    s += (p1 << 1) + ((-(p1 * 3)) >> 5) - p2 + (p2 >> 4)
                elif filt == 3:
                    s += (p1 << 1) + ((-(p1 * 13)) >> 6) - p2 + ((p2 * 3) >> 4)
                s = _clamp16(s)
                s = ((s + 0x4000) & 0x7FFF) - 0x4000          # the 15-bit wrap
                p2, p1 = p1, s
                buf[pos] = s << 1
                pos += 1
        v.p1, v.p2 = p1, p2
        v.buf_pos = pos % 12
        v.brr_off += 2
        if v.brr_off >= 9:
            # leaving the block: an end flag jumps to the loop point and
            # sets ENDX
            if header & 1:
                entry = (dir_base + (self.regs[v.base + 4] << 2) + 2) & 0xFFFF
                v.brr_addr = ram[entry] | (ram[(entry + 1) & 0xFFFF] << 8)
                if not v.kon_delay:
                    self.regs[R_ENDX] |= v.bit
            else:
                v.brr_addr = (addr + 9) & 0xFFFF
            v.brr_off = 1

    # -- envelope ---------------------------------------------------------------

    def _envelope(self, v):
        env = v.env
        if v.mode == RELEASE:
            env -= 8
            v.env = env if env > 0 else 0
            return
        regs = self.regs
        adsr1 = regs[v.base + 5]
        if adsr1 & 0x80:
            data = regs[v.base + 6]
            if v.mode == ATTACK:
                rate = ((adsr1 & 0x0F) << 1) + 1
                env += 0x400 if rate == 31 else 0x20
            else:
                env -= 1
                env -= env >> 8
                rate = (((adsr1 >> 3) & 0x0E) + 0x10) if v.mode == DECAY else (data & 0x1F)
        else:
            data = regs[v.base + 7]
            mode = data >> 5
            if mode < 4:
                env = data << 4
                rate = 31
            else:
                rate = data & 0x1F
                if mode == 4:
                    env -= 0x20
                elif mode == 5:
                    env -= 1
                    env -= env >> 8
                else:
                    env += 0x20
                    if mode == 7 and v.hidden >= 0x600:
                        env += 0x08 - 0x20
        if v.mode == DECAY and (env >> 8) == (data >> 5):
            v.mode = SUSTAIN
        v.hidden = env
        if env < 0 or env > 0x7FF:
            env = 0 if env < 0 else 0x7FF
            if v.mode == ATTACK:
                v.mode = DECAY
        if self._fires(rate):
            v.env = env
