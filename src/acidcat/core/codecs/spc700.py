"""The SPC700: the SNES sound CPU, its 64 KB of RAM, its I/O ports and timers.

An .spc file is this machine frozen: the registers, all 64 KB, the DSP's
128 registers. To hear it, the program has to run again, and this is what
runs it. Written from the fullsnes hardware reference (nocash): the
instruction table with its cycle counts, the I/O port map at $F0-$FF, the
timer rates, the dummy reads a store makes.

WHAT IS MODELLED. All 256 opcodes, with the documented flag behaviour
(including DIV's overflow cases and the BCD adjusts); base cycle counts
plus two for a taken branch; the three timers (8 kHz, 8 kHz, 64 kHz, each
divided by its register, each a 4-bit counter cleared on read); the DSP
through $F2/$F3, which is caught up to the CPU's clock on every access so
a program that reads ENDX or ENVX sees the sample it would have seen; and
the 64 bytes at $FFC0 that CONTROL bit 7 swaps between RAM and ROM.

WHAT IS NOT. Waitstates from the TEST register (a program that changes
TEST is broken by design); the main SNES CPU, which in a snapshot has
stopped, so the four input ports hold whatever it last wrote; interrupts,
which the APU does not have. SLEEP and STOP halt the CPU and the DSP
keeps playing, as on hardware.

THE ROM. The 64-byte boot ROM is Nintendo's and is not carried here. When
a snapshot was taken with the ROM mapped in, the dumper saw the ROM's bytes
at $FFC0 and saved the RAM underneath separately, in the file's "extra
RAM"; so the ROM image used is the dump's own, and the RAM is the extra RAM.
Music code almost never calls the ROM once it is running.
"""

# PSW bits
_N, _V, _P, _B, _H, _I, _Z, _C = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01

CYCLES_PER_SAMPLE = 32                 # 1.024 MHz CPU, 32 kHz DSP
_T01_PERIOD = 128                      # 8 kHz timer base
_T2_PERIOD = 16                        # 64 kHz timer base

# Base cycles per opcode, from the fullsnes tables. A taken branch adds 2.
# SLEEP and STOP halt; their count only has to be non-zero.
CYCLES = [
    2, 8, 4, 5, 3, 4, 3, 6, 2, 6, 5, 4, 5, 4, 6, 8,   # 0x
    2, 8, 4, 5, 4, 5, 5, 6, 5, 5, 6, 5, 2, 2, 4, 6,   # 1x
    2, 8, 4, 5, 3, 4, 3, 6, 2, 6, 5, 4, 5, 4, 5, 4,   # 2x
    2, 8, 4, 5, 4, 5, 5, 6, 5, 5, 6, 5, 2, 2, 3, 8,   # 3x
    2, 8, 4, 5, 3, 4, 3, 6, 2, 6, 4, 4, 5, 4, 6, 6,   # 4x
    2, 8, 4, 5, 4, 5, 5, 6, 5, 5, 4, 5, 2, 2, 4, 3,   # 5x
    2, 8, 4, 5, 3, 4, 3, 6, 2, 6, 4, 4, 5, 4, 5, 5,   # 6x
    2, 8, 4, 5, 4, 5, 5, 6, 5, 5, 5, 5, 2, 2, 3, 6,   # 7x
    2, 8, 4, 5, 3, 4, 3, 6, 2, 6, 5, 4, 5, 2, 4, 5,   # 8x
    2, 8, 4, 5, 4, 5, 5, 6, 5, 5, 5, 5, 2, 2, 12, 5,  # 9x
    3, 8, 4, 5, 3, 4, 3, 6, 2, 6, 4, 4, 5, 2, 4, 4,   # Ax
    2, 8, 4, 5, 4, 5, 5, 6, 5, 5, 5, 5, 2, 2, 3, 4,   # Bx
    3, 8, 4, 5, 4, 5, 4, 7, 2, 5, 6, 4, 5, 2, 4, 9,   # Cx
    2, 8, 4, 5, 5, 6, 6, 7, 4, 5, 5, 5, 2, 2, 6, 3,   # Dx
    2, 8, 4, 5, 3, 4, 3, 6, 2, 4, 5, 3, 4, 3, 4, 3,   # Ex
    2, 8, 4, 5, 4, 5, 5, 6, 3, 4, 5, 4, 2, 2, 4, 3,   # Fx
]


class Timer(object):
    __slots__ = ("period", "enabled", "div", "stage", "out", "last")

    def __init__(self, period):
        self.period = period
        self.enabled = False
        self.div = 256
        self.stage = 0
        self.out = 0
        self.last = 0

    def catch_up(self, now):
        # the base clock ticks at cycle 1, 1+period, ...: its phase is not in
        # the file, and a driver that seeds from a counter read hears it
        p = self.period
        ticks = (now + p - 1) // p - (self.last + p - 1) // p
        self.last = now
        if ticks <= 0 or not self.enabled:
            return
        stage = self.stage + ticks
        self.out = (self.out + stage // self.div) & 0x0F
        self.stage = stage % self.div


class SPC700(object):
    """The CPU and its memory map. `dsp` is anything with `run_to(cycle)`,
    `read(reg)` and `write(reg, value)`; the renderer passes the S-DSP."""

    def __init__(self, dsp=None):
        self.ram = bytearray(0x10000)
        self.rom = bytearray(64)
        self.rom_on = False
        self.a = self.x = self.y = 0
        self.sp = 0xEF
        self.pc = 0
        self.psw = 0
        self.cycles = 0
        self.halted = False
        self.dsp = dsp
        self.dsp_addr = 0
        self.inputs = bytearray(4)            # what the main CPU last wrote
        self.outputs = bytearray(4)           # what this CPU writes back
        self.timers = [Timer(_T01_PERIOD), Timer(_T01_PERIOD), Timer(_T2_PERIOD)]
        self._ops = [getattr(self, "_op_%02X" % op) for op in range(256)]

    # -- memory ---------------------------------------------------------

    def read(self, a):
        if 0x100 <= a < 0xFFC0 or a < 0xF0:
            return self.ram[a]
        if a >= 0xFFC0:
            return self.rom[a - 0xFFC0] if self.rom_on else self.ram[a]
        return self._io_read(a)

    def write(self, a, v):
        if 0xF0 <= a <= 0xFF:
            self._io_write(a, v)
        self.ram[a] = v                       # writes always land in RAM

    def _io_read(self, a):
        if a == 0xF2:
            return self.dsp_addr
        if a == 0xF3:
            if self.dsp is None:
                return 0
            self.dsp.run_to(self.cycles)
            return self.dsp.read(self.dsp_addr & 0x7F)
        if 0xF4 <= a <= 0xF7:
            return self.inputs[a - 0xF4]
        if a >= 0xFD:
            t = self.timers[a - 0xFD]
            t.catch_up(self.cycles)
            v = t.out
            t.out = 0                          # reading clears the counter
            return v
        if a in (0xF8, 0xF9):
            return self.ram[a]
        return 0                              # TEST, CONTROL, TnDIV read as 0

    def _io_write(self, a, v):
        if a == 0xF1:
            for i, t in enumerate(self.timers):
                t.catch_up(self.cycles)
                on = bool(v & (1 << i))
                if on and not t.enabled:
                    t.stage = 0
                    t.out = 0
                t.enabled = on
            if v & 0x10:
                self.inputs[0] = self.inputs[1] = 0
            if v & 0x20:
                self.inputs[2] = self.inputs[3] = 0
            self.rom_on = bool(v & 0x80)
        elif a == 0xF2:
            self.dsp_addr = v
        elif a == 0xF3:
            if self.dsp is not None and self.dsp_addr < 0x80:
                self.dsp.run_to(self.cycles)
                self.dsp.write(self.dsp_addr, v)
        elif 0xF4 <= a <= 0xF7:
            self.outputs[a - 0xF4] = v
        elif 0xFA <= a <= 0xFC:
            t = self.timers[a - 0xFA]
            t.catch_up(self.cycles)
            t.div = v or 256

    def _store(self, a, v):
        # most stores are read-modify-write inside and issue a dummy read,
        # which is visible only on the read-clear timer outputs
        if 0xFD <= a <= 0xFF:
            self._io_read(a)
        self.write(a, v)

    # -- state ----------------------------------------------------------

    def load_snapshot(self, ram, pc, a, x, y, psw, sp, extra_ram=None):
        """Set up from a 64 KB image and the saved registers, then apply the
        I/O state the image holds at $F0-$FF."""
        self.ram[:] = ram[:0x10000]
        self.pc, self.a, self.x, self.y, self.psw, self.sp = pc, a, x, y, psw, sp
        control = self.ram[0xF1]
        self.inputs[:] = self.ram[0xF4:0xF8]
        self.dsp_addr = self.ram[0xF2]
        for i, t in enumerate(self.timers):
            t.enabled = bool(control & (1 << i))
            t.div = self.ram[0xFA + i] or 256
            t.out = self.ram[0xFD + i] & 0x0F
            t.stage = 0
            t.last = 0
        self.rom_on = bool(control & 0x80)
        if self.rom_on and extra_ram is not None and len(extra_ram) == 64:
            self.rom[:] = self.ram[0xFFC0:0x10000]
            self.ram[0xFFC0:0x10000] = extra_ram

    def run(self, until):
        """Execute until the cycle counter reaches `until`."""
        ops = self._ops
        ram = self.ram
        read = self.read
        cyc = CYCLES
        while self.cycles < until:
            if self.halted:
                self.cycles = until
                return
            pc = self.pc
            op = ram[pc] if 0x100 <= pc < 0xFFC0 else read(pc)
            self.pc = (pc + 1) & 0xFFFF
            self.cycles += cyc[op]
            ops[op]()

    # -- operand fetch and addressing -------------------------------------

    def _imm(self):
        pc = self.pc
        self.pc = (pc + 1) & 0xFFFF
        return self.read(pc)

    def _imm16(self):
        lo = self._imm()
        return lo | (self._imm() << 8)

    def _dp(self, off):
        return (0x100 if self.psw & _P else 0) | (off & 0xFF)

    def _dp_word(self, off):
        base = 0x100 if self.psw & _P else 0
        return self.read(base | (off & 0xFF)) | (self.read(base | ((off + 1) & 0xFF)) << 8)

    def _a_dp(self):
        return self._dp(self._imm())

    def _a_dpx(self):
        return self._dp(self._imm() + self.x)

    def _a_dpy(self):
        return self._dp(self._imm() + self.y)

    def _a_abs(self):
        return self._imm16()

    def _a_absx(self):
        return (self._imm16() + self.x) & 0xFFFF

    def _a_absy(self):
        return (self._imm16() + self.y) & 0xFFFF

    def _a_ix(self):
        return self._dp(self.x)

    def _a_iy(self):
        return self._dp(self.y)

    def _a_indy(self):                          # [aa]+Y
        return (self._dp_word(self._imm()) + self.y) & 0xFFFF

    def _a_indx(self):                          # [aa+X]
        return self._dp_word(self._imm() + self.x)

    # -- flags ------------------------------------------------------------

    def _nz(self, v):
        self.psw = (self.psw & ~(_N | _Z)) | (v & _N) | (0 if v else _Z)
        return v

    def _nz16(self, v):
        self.psw = (self.psw & ~(_N | _Z)) | ((v >> 8) & _N) | (0 if v else _Z)

    def _setc(self, on):
        self.psw = (self.psw | _C) if on else (self.psw & ~_C)

    # -- ALU --------------------------------------------------------------

    def _or(self, a, b):
        return self._nz(a | b)

    def _and(self, a, b):
        return self._nz(a & b)

    def _eor(self, a, b):
        return self._nz(a ^ b)

    def _cmp(self, a, b):
        r = a - b
        self._setc(r >= 0)
        self._nz(r & 0xFF)
        return a

    def _adc(self, a, b):
        r = a + b + (self.psw & _C)
        psw = self.psw & ~(_V | _H | _C)
        if (a ^ b ^ r) & 0x10:
            psw |= _H
        if ~(a ^ b) & (a ^ r) & 0x80:
            psw |= _V
        if r > 0xFF:
            psw |= _C
        self.psw = psw
        return self._nz(r & 0xFF)

    def _sbc(self, a, b):
        return self._adc(a, b ^ 0xFF)

    def _asl(self, v):
        self._setc(v & 0x80)
        return self._nz((v << 1) & 0xFF)

    def _rol(self, v):
        c = self.psw & _C
        self._setc(v & 0x80)
        return self._nz(((v << 1) | c) & 0xFF)

    def _lsr(self, v):
        self._setc(v & 1)
        return self._nz(v >> 1)

    def _ror(self, v):
        c = 0x80 if self.psw & _C else 0
        self._setc(v & 1)
        return self._nz((v >> 1) | c)

    def _dec(self, v):
        return self._nz((v - 1) & 0xFF)

    def _inc(self, v):
        return self._nz((v + 1) & 0xFF)

    # -- stack and branches ------------------------------------------------

    def _push(self, v):
        self.ram[0x100 | self.sp] = v
        self.sp = (self.sp - 1) & 0xFF

    def _pop(self):
        self.sp = (self.sp + 1) & 0xFF
        return self.ram[0x100 | self.sp]

    def _call(self, target):
        pc = self.pc
        self._push(pc >> 8)
        self._push(pc & 0xFF)
        self.pc = target

    def _branch(self, take):
        rel = self._imm()
        if take:
            self.pc = (self.pc + (rel - 256 if rel & 0x80 else rel)) & 0xFFFF
            self.cycles += 2

    def _bit_operand(self):
        w = self._imm16()
        return w & 0x1FFF, w >> 13

    def _halt(self):
        self.halted = True


# The opcode handlers. The regular groups are generated; the rest are
# written out, one method per opcode, in table order.

def _install():
    cls = SPC700
    alu = [("_or", 0x00, True), ("_and", 0x20, True), ("_eor", 0x40, True),
           ("_cmp", 0x60, False), ("_adc", 0x80, True), ("_sbc", 0xA0, True)]
    for fn, base, store in alu:
        def mk(fn=fn, store=store):
            def a_mode(mode):
                def h(self):
                    self.a = getattr(self, fn)(self.a, self.read(getattr(self, mode)()))
                return h

            def imm(self):
                self.a = getattr(self, fn)(self.a, self._imm())

            def dp_dp(self):
                src = self.read(self._a_dp())
                dst = self._a_dp()
                r = getattr(self, fn)(self.read(dst), src)
                if store:
                    self.write(dst, r)

            def dp_imm(self):
                v = self._imm()
                dst = self._a_dp()
                r = getattr(self, fn)(self.read(dst), v)
                if store:
                    self.write(dst, r)

            def ix_iy(self):
                src = self.read(self._a_iy())
                dst = self._a_ix()
                r = getattr(self, fn)(self.read(dst), src)
                if store:
                    self.write(dst, r)
            return a_mode, imm, dp_dp, dp_imm, ix_iy
        a_mode, imm, dp_dp, dp_imm, ix_iy = mk()
        for off, mode in ((0x04, "_a_dp"), (0x05, "_a_abs"), (0x06, "_a_ix"),
                          (0x07, "_a_indx"), (0x14, "_a_dpx"), (0x15, "_a_absx"),
                          (0x16, "_a_absy"), (0x17, "_a_indy")):
            setattr(cls, "_op_%02X" % (base + off), a_mode(mode))
        setattr(cls, "_op_%02X" % (base + 0x08), imm)
        setattr(cls, "_op_%02X" % (base + 0x09), dp_dp)
        setattr(cls, "_op_%02X" % (base + 0x18), dp_imm)
        setattr(cls, "_op_%02X" % (base + 0x19), ix_iy)

    for fn, base in (("_asl", 0x00), ("_rol", 0x20), ("_lsr", 0x40),
                     ("_ror", 0x60), ("_dec", 0x80), ("_inc", 0xA0)):
        def mk2(fn=fn):
            def mem(mode):
                def h(self):
                    a = getattr(self, mode)()
                    self.write(a, getattr(self, fn)(self.read(a)))
                return h

            def acc(self):
                self.a = getattr(self, fn)(self.a)
            return mem, acc
        mem, acc = mk2()
        setattr(cls, "_op_%02X" % (base + 0x0B), mem("_a_dp"))
        setattr(cls, "_op_%02X" % (base + 0x0C), mem("_a_abs"))
        setattr(cls, "_op_%02X" % (base + 0x1B), mem("_a_dpx"))
        setattr(cls, "_op_%02X" % (base + 0x1C), acc)

    for n in range(16):
        def tcall(self, n=n):
            vec = 0xFFDE - n * 2
            self._call(self.read(vec) | (self.read(vec + 1) << 8))
        setattr(cls, "_op_%02X" % (n * 16 + 1), tcall)

    for b in range(8):
        def set1(self, b=b):
            a = self._a_dp()
            self.write(a, self.read(a) | (1 << b))

        def clr1(self, b=b):
            a = self._a_dp()
            self.write(a, self.read(a) & ~(1 << b) & 0xFF)

        def bbs(self, b=b):
            v = self.read(self._a_dp())
            self._branch(v & (1 << b))

        def bbc(self, b=b):
            v = self.read(self._a_dp())
            self._branch(not v & (1 << b))
        setattr(cls, "_op_%02X" % (b * 0x20 + 0x02), set1)
        setattr(cls, "_op_%02X" % (b * 0x20 + 0x12), clr1)
        setattr(cls, "_op_%02X" % (b * 0x20 + 0x03), bbs)
        setattr(cls, "_op_%02X" % (b * 0x20 + 0x13), bbc)

    for op, flag, want in ((0x10, _N, False), (0x30, _N, True), (0x50, _V, False),
                           (0x70, _V, True), (0x90, _C, False), (0xB0, _C, True),
                           (0xD0, _Z, False), (0xF0, _Z, True)):
        def br(self, flag=flag, want=want):
            self._branch(bool(self.psw & flag) == want)
        setattr(cls, "_op_%02X" % op, br)


_install()


def _op(code):
    def deco(fn):
        setattr(SPC700, "_op_%02X" % code, fn)
        return fn
    return deco


@_op(0x00)
def _nop(self):
    pass


@_op(0x0A)
def _or1(self):
    a, b = self._bit_operand()
    if (self.read(a) >> b) & 1:
        self.psw |= _C


@_op(0x2A)
def _or1n(self):
    a, b = self._bit_operand()
    if not (self.read(a) >> b) & 1:
        self.psw |= _C


@_op(0x4A)
def _and1(self):
    a, b = self._bit_operand()
    if not (self.read(a) >> b) & 1:
        self.psw &= ~_C


@_op(0x6A)
def _and1n(self):
    a, b = self._bit_operand()
    if (self.read(a) >> b) & 1:
        self.psw &= ~_C


@_op(0x8A)
def _eor1(self):
    a, b = self._bit_operand()
    if (self.read(a) >> b) & 1:
        self.psw ^= _C


@_op(0xAA)
def _mov1_c(self):
    a, b = self._bit_operand()
    self._setc((self.read(a) >> b) & 1)


@_op(0xCA)
def _mov1_m(self):
    a, b = self._bit_operand()
    v = self.read(a)
    v = (v | (1 << b)) if self.psw & _C else (v & ~(1 << b) & 0xFF)
    self.write(a, v)


@_op(0xEA)
def _not1(self):
    a, b = self._bit_operand()
    self.write(a, self.read(a) ^ (1 << b))


@_op(0x0D)
def _push_psw(self):
    self._push(self.psw)


@_op(0x2D)
def _push_a(self):
    self._push(self.a)


@_op(0x4D)
def _push_x(self):
    self._push(self.x)


@_op(0x6D)
def _push_y(self):
    self._push(self.y)


@_op(0x8E)
def _pop_psw(self):
    self.psw = self._pop()


@_op(0xAE)
def _pop_a(self):
    self.a = self._pop()


@_op(0xCE)
def _pop_x(self):
    self.x = self._pop()


@_op(0xEE)
def _pop_y(self):
    self.y = self._pop()


@_op(0x0E)
def _tset1(self):
    a = self._a_abs()
    v = self.read(a)
    self._nz((self.a - v) & 0xFF)
    self.write(a, v | self.a)


@_op(0x4E)
def _tclr1(self):
    a = self._a_abs()
    v = self.read(a)
    self._nz((self.a - v) & 0xFF)
    self.write(a, v & ~self.a & 0xFF)


@_op(0x0F)
def _brk(self):
    self._call(self.read(0xFFDE) | (self.read(0xFFDF) << 8))
    self._push(self.psw)
    self.psw = (self.psw | _B) & ~_I


@_op(0x1A)
def _decw(self):
    off = self._imm()
    v = (self._dp_word(off) - 1) & 0xFFFF
    self.write(self._dp(off), v & 0xFF)
    self.write(self._dp(off + 1), v >> 8)
    self._nz16(v)


@_op(0x3A)
def _incw(self):
    off = self._imm()
    v = (self._dp_word(off) + 1) & 0xFFFF
    self.write(self._dp(off), v & 0xFF)
    self.write(self._dp(off + 1), v >> 8)
    self._nz16(v)


@_op(0x5A)
def _cmpw(self):
    w = self._dp_word(self._imm())
    ya = (self.y << 8) | self.a
    r = ya - w
    self._setc(r >= 0)
    self._nz16(r & 0xFFFF)


def _addw16(self, w, carry):
    ya = (self.y << 8) | self.a
    r = ya + w + carry
    psw = self.psw & ~(_V | _H | _C)
    if (ya ^ w ^ r) & 0x1000:
        psw |= _H
    if ~(ya ^ w) & (ya ^ r) & 0x8000:
        psw |= _V
    if r > 0xFFFF:
        psw |= _C
    self.psw = psw
    r &= 0xFFFF
    self.a, self.y = r & 0xFF, r >> 8
    self._nz16(r)


@_op(0x7A)
def _addw(self):
    _addw16(self, self._dp_word(self._imm()), 0)


@_op(0x9A)
def _subw(self):
    _addw16(self, self._dp_word(self._imm()) ^ 0xFFFF, 1)


@_op(0xBA)
def _movw_ya(self):
    w = self._dp_word(self._imm())
    self.a, self.y = w & 0xFF, w >> 8
    self._nz16(w)


@_op(0xDA)
def _movw_dp(self):
    off = self._imm()
    lo = self._dp(off)
    if 0xFD <= lo <= 0xFF:
        self._io_read(lo)                       # the dummy read is on the low byte only
    self.write(lo, self.a)
    self.write(self._dp(off + 1), self.y)


@_op(0x1D)
def _dec_x(self):
    self.x = self._dec(self.x)


@_op(0x3D)
def _inc_x(self):
    self.x = self._inc(self.x)


@_op(0xDC)
def _dec_y(self):
    self.y = self._dec(self.y)


@_op(0xFC)
def _inc_y(self):
    self.y = self._inc(self.y)


@_op(0x1E)
def _cmp_x_abs(self):
    self._cmp(self.x, self.read(self._a_abs()))


@_op(0x3E)
def _cmp_x_dp(self):
    self._cmp(self.x, self.read(self._a_dp()))


@_op(0xC8)
def _cmp_x_imm(self):
    self._cmp(self.x, self._imm())


@_op(0x5E)
def _cmp_y_abs(self):
    self._cmp(self.y, self.read(self._a_abs()))


@_op(0x7E)
def _cmp_y_dp(self):
    self._cmp(self.y, self.read(self._a_dp()))


@_op(0xAD)
def _cmp_y_imm(self):
    self._cmp(self.y, self._imm())


@_op(0x1F)
def _jmp_absx(self):
    a = self._a_absx()
    self.pc = self.read(a) | (self.read((a + 1) & 0xFFFF) << 8)


@_op(0x5F)
def _jmp(self):
    self.pc = self._imm16()


@_op(0x2F)
def _bra(self):
    self._branch(True)
    self.cycles -= 2                            # BRA's 4 cycles are its base


@_op(0x3F)
def _call_abs(self):
    self._call(self._imm16())


@_op(0x4F)
def _pcall(self):
    self._call(0xFF00 | self._imm())


@_op(0x6F)
def _ret(self):
    lo = self._pop()
    self.pc = lo | (self._pop() << 8)


@_op(0x7F)
def _reti(self):
    self.psw = self._pop()
    lo = self._pop()
    self.pc = lo | (self._pop() << 8)


@_op(0x2E)
def _cbne_dp(self):
    v = self.read(self._a_dp())
    self._branch(self.a != v)


@_op(0xDE)
def _cbne_dpx(self):
    v = self.read(self._a_dpx())
    self._branch(self.a != v)


@_op(0x6E)
def _dbnz_dp(self):
    a = self._a_dp()
    v = (self.read(a) - 1) & 0xFF
    self.write(a, v)
    self._branch(v != 0)


@_op(0xFE)
def _dbnz_y(self):
    self.y = (self.y - 1) & 0xFF
    self._branch(self.y != 0)


@_op(0x20)
def _clrp(self):
    self.psw &= ~_P


@_op(0x40)
def _setp(self):
    self.psw |= _P


@_op(0x60)
def _clrc(self):
    self.psw &= ~_C


@_op(0x80)
def _setc(self):
    self.psw |= _C


@_op(0xED)
def _notc(self):
    self.psw ^= _C


@_op(0xE0)
def _clrv(self):
    self.psw &= ~(_V | _H)


@_op(0xA0)
def _ei(self):
    self.psw |= _I


@_op(0xC0)
def _di(self):
    self.psw &= ~_I


@_op(0x5D)
def _mov_x_a(self):
    self.x = self._nz(self.a)


@_op(0x7D)
def _mov_a_x(self):
    self.a = self._nz(self.x)


@_op(0xDD)
def _mov_a_y(self):
    self.a = self._nz(self.y)


@_op(0xFD)
def _mov_y_a(self):
    self.y = self._nz(self.a)


@_op(0x9D)
def _mov_x_sp(self):
    self.x = self._nz(self.sp)


@_op(0xBD)
def _mov_sp_x(self):
    self.sp = self.x


@_op(0x8D)
def _mov_y_imm(self):
    self.y = self._nz(self._imm())


@_op(0xCD)
def _mov_x_imm(self):
    self.x = self._nz(self._imm())


@_op(0xE8)
def _mov_a_imm(self):
    self.a = self._nz(self._imm())


def _load(reg, mode):
    def h(self):
        setattr(self, reg, self._nz(self.read(getattr(self, mode)())))
    return h


for _code, _reg, _mode in ((0xE4, "a", "_a_dp"), (0xF4, "a", "_a_dpx"), (0xE5, "a", "_a_abs"),
                           (0xF5, "a", "_a_absx"), (0xF6, "a", "_a_absy"), (0xE6, "a", "_a_ix"),
                           (0xF7, "a", "_a_indy"), (0xE7, "a", "_a_indx"),
                           (0xF8, "x", "_a_dp"), (0xF9, "x", "_a_dpy"), (0xE9, "x", "_a_abs"),
                           (0xEB, "y", "_a_dp"), (0xFB, "y", "_a_dpx"), (0xEC, "y", "_a_abs")):
    setattr(SPC700, "_op_%02X" % _code, _load(_reg, _mode))


def _store_op(reg, mode):
    def h(self):
        self._store(getattr(self, mode)(), getattr(self, reg))
    return h


for _code, _reg, _mode in ((0xC4, "a", "_a_dp"), (0xD4, "a", "_a_dpx"), (0xC5, "a", "_a_abs"),
                           (0xD5, "a", "_a_absx"), (0xD6, "a", "_a_absy"), (0xC6, "a", "_a_ix"),
                           (0xD7, "a", "_a_indy"), (0xC7, "a", "_a_indx"),
                           (0xD8, "x", "_a_dp"), (0xD9, "x", "_a_dpy"), (0xC9, "x", "_a_abs"),
                           (0xCB, "y", "_a_dp"), (0xDB, "y", "_a_dpx"), (0xCC, "y", "_a_abs")):
    setattr(SPC700, "_op_%02X" % _code, _store_op(_reg, _mode))


@_op(0xBF)
def _mov_a_xinc(self):
    self.a = self._nz(self.read(self._a_ix()))
    self.x = (self.x + 1) & 0xFF


@_op(0xAF)
def _mov_xinc_a(self):
    self.write(self._a_ix(), self.a)            # no dummy read
    self.x = (self.x + 1) & 0xFF


@_op(0x8F)
def _mov_dp_imm(self):
    v = self._imm()
    self._store(self._a_dp(), v)


@_op(0xFA)
def _mov_dp_dp(self):
    v = self.read(self._a_dp())
    self.write(self._a_dp(), v)                 # no dummy read


@_op(0x9E)
def _div(self):
    ya = (self.y << 8) | self.a
    x = self.x
    psw = self.psw & ~(_V | _H)
    if (x & 0x0F) <= (self.y & 0x0F):
        psw |= _H
    # the hardware's nine-step shift-subtract, which is what gives DIV its
    # documented results when the quotient does not fit in nine bits
    yva = ya
    xx = x << 9
    for _ in range(9):
        yva <<= 1
        if yva & 0x20000:
            yva = (yva & 0x1FFFF) | 1
        if yva >= xx:
            yva ^= 1
        if yva & 1:
            yva = (yva - xx) & 0x1FFFF
    if yva & 0x100:
        psw |= _V
    self.psw = psw
    self.a = yva & 0xFF
    self.y = (yva >> 9) & 0xFF
    self._nz(self.a)


@_op(0xCF)
def _mul(self):
    r = self.y * self.a
    self.a, self.y = r & 0xFF, r >> 8
    self._nz(self.y)


@_op(0x9F)
def _xcn(self):
    self.a = self._nz(((self.a >> 4) | (self.a << 4)) & 0xFF)


@_op(0xDF)
def _daa(self):
    a = self.a
    if self.psw & _C or a > 0x99:
        a += 0x60
        self.psw |= _C
    if self.psw & _H or (a & 0x0F) > 9:
        a += 6
    self.a = self._nz(a & 0xFF)


@_op(0xBE)
def _das(self):
    a = self.a
    if not self.psw & _C or a > 0x99:
        a -= 0x60
        self.psw &= ~_C
    if not self.psw & _H or (a & 0x0F) > 9:
        a -= 6
    self.a = self._nz(a & 0xFF)


@_op(0xEF)
def _sleep(self):
    self._halt()


@_op(0xFF)
def _stop(self):
    self._halt()


_missing = [op for op in range(256) if not hasattr(SPC700, "_op_%02X" % op)]
assert not _missing, "opcodes without a handler: %s" % _missing
