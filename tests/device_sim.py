"""A tiny deterministic model of the MAX30102 register file + FIFO.

This is not a full simulation of the physical sensor: it gives the driver
something to talk to in tests. Starting register values don't need to match
real silicon, but what the model does (reset, sampling, LED current) follows
the datasheet, so tests can only stage states the real chip can reach.

Addressing mirrors the real device: a 1-byte write sets the "current
register" pointer, a 2-byte write sets the pointer *and* writes a value to
it, and a read serves bytes starting at the pointer, auto-incrementing
except at FIFO_DATA (0x07), which serves FIFO samples byte by byte
(pag. 14, 15).

The FIFO is 32 slots addressed by FIFO_WR_PTR / FIFO_RD_PTR. RD_PTR
advances on the first byte of each sample, and writing either pointer
restarts at a sample boundary (the behaviour check()'s error recovery relies
on; TODO.md lists it for checking on hardware). With FIFO_ROLLOVER_EN a
sample arriving while the FIFO is full replaces the oldest and moves both
pointers; without it the new sample is discarded. Either way OVF_COUNTER
counts it (saturating at 31) until the next sample is popped (pag. 13, 17).
"""

REG_INT_STAT_1 = 0x00
REG_INT_STAT_2 = 0x01
REG_INT_ENABLE_1 = 0x02
REG_INT_ENABLE_2 = 0x03
REG_FIFO_WRITE_PTR = 0x04
REG_FIFO_OVERFLOW = 0x05
REG_FIFO_READ_PTR = 0x06
REG_FIFO_DATA = 0x07
REG_FIFO_CONFIG = 0x08
REG_MODE_CONFIG = 0x09
REG_PARTICLE_CONFIG = 0x0A
REG_LED1_PULSE_AMP = 0x0C
REG_LED2_PULSE_AMP = 0x0D
REG_LED3_PULSE_AMP = 0x0E
REG_LED_PROX_AMP = 0x10
REG_MULTI_LED_CONFIG_1 = 0x11
REG_MULTI_LED_CONFIG_2 = 0x12
REG_DIE_TEMP_INT = 0x1F
REG_DIE_TEMP_FRAC = 0x20
REG_DIE_TEMP_CONFIG = 0x21
REG_PROX_INT_THRESH = 0x30
REG_REVISION_ID = 0xFE
REG_PART_ID = 0xFF

PWR_RDY_BIT = 0x01
ROLLOVER_BIT = 0x10
SHDN_BIT = 0x80
RESET_BIT = 0x40
TEMP_EN_BIT = 0x01

# MODE[2:0] values that take readings (datasheet pag. 18, Table 4)
MODE_HR = 0x02
MODE_SPO2 = 0x03
MODE_MULTI_LED = 0x07

SAMPLE_RATES = (50, 100, 200, 400, 800, 1000, 1600, 3200)  # Table 6
SAMPLE_AVERAGES = (1, 2, 4, 8, 16, 32, 32, 32)  # Table 3
PULSE_WIDTHS = (69, 118, 215, 411)  # Table 7

# Highest allowed sample rate for each pulse width (datasheet pag. 23)
MAX_SAMPLE_RATE_SPO2 = {69: 1600, 118: 1000, 215: 800, 411: 400}  # Table 11
MAX_SAMPLE_RATE_HR = {69: 3200, 118: 1600, 215: 1600, 411: 1000}  # Table 12

# Registers that never change, regardless of resets or writes.
_FIXED_REGISTERS = {
    REG_PART_ID: 0x15,
    REG_REVISION_ID: 0x03,
}

# Fixed "measurement" the simulated die-temperature conversion reports.
# 25 + 8 * 0.0625 = 25.5 degC. INT_STAT_2's DIE_TEMP_RDY bit (0x02) is never
# set by this model, so read_temperature()'s ready-poll loop always exits
# after exactly one read.
_SIMULATED_DIE_TEMP_INT = 25
_SIMULATED_DIE_TEMP_FRAC = 8


class DeviceSim:
    """Register-file + FIFO model addressed like the real MAX30102."""

    def __init__(self):
        self._registers = {}
        self._addr_ptr = 0
        self._reset_to_defaults()
        # Power-up raises PWR_RDY (pag. 12); a soft reset doesn't (pag. 18)
        self._registers[REG_INT_STAT_1] = PWR_RDY_BIT

    def _reset_to_defaults(self):
        self._registers = dict(_FIXED_REGISTERS)
        self._registers[REG_DIE_TEMP_INT] = _SIMULATED_DIE_TEMP_INT
        self._registers[REG_DIE_TEMP_FRAC] = _SIMULATED_DIE_TEMP_FRAC
        self._slots = [None] * 32
        self._count = 0  # unread samples: 32 when full, though WR_PTR == RD_PTR
        self._latched = b""  # the sample being read out of FIFO_DATA

    def _get(self, reg):
        return self._registers.get(reg, 0x00)

    def _set(self, reg, value):
        if reg in _FIXED_REGISTERS:
            return  # read-only
        self._registers[reg] = value & 0xFF

    # -- bus-facing API --------------------------------------------------
    def write(self, data):
        """Handle a write transaction: 1 byte points, 2 bytes points+writes."""
        if len(data) == 0:
            return  # bus-scan "quick write" probe: nothing to address or write
        elif len(data) == 1:
            self._addr_ptr = data[0]
        elif len(data) == 2:
            reg, value = data
            self._addr_ptr = reg
            if reg == REG_MODE_CONFIG and (value & RESET_BIT):
                # Datasheet: setting RESET reloads all registers to their
                # power-on state (MODE_CONFIG included, so the other bits of
                # this write are lost), and the bit self-clears once done.
                # We simulate that completing instantly (deterministic, and
                # avoids an infinite poll loop in soft_reset()).
                self._reset_to_defaults()
                return
            elif reg == REG_DIE_TEMP_CONFIG and (value & TEMP_EN_BIT):
                # Datasheet: TEMP_EN self-clears once the temperature
                # conversion completes. Simulated as completing instantly,
                # like RESET above, so read_temperature()'s TEMP_EN poll
                # exits on its first check.
                value &= ~TEMP_EN_BIT & 0xFF
            self._set(reg, value)
            if reg in (REG_FIFO_WRITE_PTR, REG_FIFO_READ_PTR):
                self._registers[reg] = value & 0x1F
                # Equal pointers after a write: the FIFO is empty (pag. 14)
                self._count = (self._get(REG_FIFO_WRITE_PTR) - self._get(REG_FIFO_READ_PTR)) & 0x1F
                self._latched = b""
        else:
            raise ValueError(
                "MAX30102 registers are only ever written as [reg] or "
                "[reg, value]; got {0} bytes".format(len(data))
            )

    def read(self, n_bytes):
        """Handle a read transaction of n_bytes starting at the pointer."""
        out = bytearray()
        for _ in range(n_bytes):
            reg = self._addr_ptr
            if reg == REG_FIFO_DATA:
                out.append(self._fifo_byte())
                continue
            out.append(self._get(reg))
            if reg in (REG_INT_STAT_1, REG_INT_STAT_2):
                self._registers[reg] = 0  # reading clears the flags (pag. 12)
            if reg != REG_PART_ID:
                self._addr_ptr = (reg + 1) & 0xFF
        return bytes(out)

    def _sample_size(self):
        return 3 * max(1, self._channels())

    def _fifo_byte(self):
        if not self._latched:
            if self._count == 0:
                return 0x00  # nothing unread: the pointers don't move
            read_ptr = self._get(REG_FIFO_READ_PTR)
            self._latched = self._slots[read_ptr] or bytes(self._sample_size())
            self._registers[REG_FIFO_READ_PTR] = (read_ptr + 1) & 0x1F
            self._count -= 1
            self._registers[REG_FIFO_OVERFLOW] = 0
        byte, self._latched = self._latched[0], self._latched[1:]
        return byte

    # -- test-setup helpers (not real I2C traffic) -----------------------
    def load_fifo(self, samples, read_ptr=0, overflow=0):
        """Fill the FIFO with raw samples (one bytes-like per sample), as if
        the chip had just written them starting at `read_ptr`. 32 samples
        make WR_PTR == RD_PTR, i.e. a full FIFO."""
        self._slots = [None] * 32
        for i, sample in enumerate(samples):
            self._slots[(read_ptr + i) & 0x1F] = bytes(sample)
        self._registers[REG_FIFO_READ_PTR] = read_ptr & 0x1F
        self._registers[REG_FIFO_WRITE_PTR] = (read_ptr + len(samples)) & 0x1F
        self._registers[REG_FIFO_OVERFLOW] = overflow
        self._count = len(samples)
        self._latched = b""

    def power_cycle(self):
        """Supply drops below UVLO and comes back: every register returns to
        its power-on value, the FIFO is lost and PWR_RDY is set (pag. 12)."""
        self._reset_to_defaults()
        self._registers[REG_INT_STAT_1] = PWR_RDY_BIT

    def unread(self):
        return self._count

    def set_die_temperature(self, int_reg_value, frac_reg_value=0):
        """Poke raw TINT/TFRAC register bytes (e.g. 0xF6 for -10 degC, per
        Table 10's two's-complement encoding)."""
        self._registers[REG_DIE_TEMP_INT] = int_reg_value & 0xFF
        self._registers[REG_DIE_TEMP_FRAC] = frac_reg_value & 0xFF

    def set_overflow_counter(self, value):
        self._registers[REG_FIFO_OVERFLOW] = value & 0xFF

    # -- the chip at work (datasheet behavior, not I2C traffic) ----------
    def _mode(self):
        return self._get(REG_MODE_CONFIG) & 0x07

    def _slots_config(self):
        cfg1 = self._get(REG_MULTI_LED_CONFIG_1)
        cfg2 = self._get(REG_MULTI_LED_CONFIG_2)
        return [cfg1 & 0x07, (cfg1 >> 4) & 0x07, cfg2 & 0x07, (cfg2 >> 4) & 0x07]

    def is_sampling(self):
        """Readings are taken only in HR, SpO2 or multi-LED mode, and not
        while SHDN is set (pag. 18)."""
        if self._get(REG_MODE_CONFIG) & SHDN_BIT:
            return False
        return self._mode() in (MODE_HR, MODE_SPO2, MODE_MULTI_LED)

    def pulsed_leds(self):
        """The LEDs (1, 2) the chip is pulsing right now (Table 4, Table 9)."""
        if not self.is_sampling():
            return set()
        if self._mode() == MODE_HR:
            return {1}
        if self._mode() == MODE_SPO2:
            return {1, 2}
        return {slot for slot in self._slots_config() if slot in (1, 2)}

    def led_current_ma(self, led):
        """Pulse current of LED1 or LED2: 0.2mA per LEDx_PA step (Table 8),
        or 0 when the chip isn't pulsing that LED."""
        if led not in self.pulsed_leds():
            return 0.0
        reg = REG_LED1_PULSE_AMP if led == 1 else REG_LED2_PULSE_AMP
        return round(self._get(reg) * 0.2, 1)

    def sample_rate(self):
        return SAMPLE_RATES[(self._get(REG_PARTICLE_CONFIG) >> 2) & 0x07]

    def pulse_width_us(self):
        return PULSE_WIDTHS[self._get(REG_PARTICLE_CONFIG) & 0x03]

    def fifo_rate_hz(self):
        """Samples per second pushed to the FIFO: SPO2_SR / SMP_AVE."""
        return self.sample_rate() / SAMPLE_AVERAGES[(self._get(REG_FIFO_CONFIG) >> 5) & 0x07]

    def rate_allowed(self):
        """Whether the sample rate is allowed at this pulse width in the
        current mode (Tables 11 and 12, pag. 23)."""
        if self._mode() == MODE_HR:
            max_rate = MAX_SAMPLE_RATE_HR
        elif self._mode() in (MODE_SPO2, MODE_MULTI_LED):
            max_rate = MAX_SAMPLE_RATE_SPO2
        else:
            return True  # not sampling
        return self.sample_rate() <= max_rate[self.pulse_width_us()]

    def a_full_unread(self):
        """Unread samples in the FIFO when A_FULL triggers (pag. 17)."""
        return 32 - (self._get(REG_FIFO_CONFIG) & 0x0F)

    def _channels(self):
        if self._mode() == MODE_HR:
            return 1
        if self._mode() == MODE_SPO2:
            return 2
        # Each non-zero slot adds a 3-byte channel (pag. 21)
        return sum(1 for slot in self._slots_config() if slot)

    def produce(self, samples):
        """The chip takes readings: write `samples` (each a tuple of ADC
        counts, one per active channel) to the FIFO. Returns the number
        taken: 0 when the chip isn't sampling.

        Raises ValueError for samples this configuration can't produce.
        """
        if not self.is_sampling():
            return 0
        channels = self._channels()
        bits = 15 + (self._get(REG_PARTICLE_CONFIG) & 0x03)  # Table 7
        rollover = self._get(REG_FIFO_CONFIG) & ROLLOVER_BIT
        for sample in samples:
            if len(sample) != channels:
                raise ValueError("this mode produces {0}-channel samples".format(channels))
            data = bytearray()
            for value in sample:
                if not 0 <= value < (1 << bits):
                    raise ValueError("{0} doesn't fit a {1}-bit ADC".format(value, bits))
                # Left-justified in 18 bits (Table 1), MSB first (Table 2)
                word = value << (18 - bits)
                data += bytes(((word >> 16) & 0x03, (word >> 8) & 0xFF, word & 0xFF))
            write_ptr = self._get(REG_FIFO_WRITE_PTR)
            if self._count == 32:
                ovf = self._get(REG_FIFO_OVERFLOW)
                self._registers[REG_FIFO_OVERFLOW] = min(31, ovf + 1)
                if not rollover:
                    continue
                # The oldest sample is overwritten: both pointers move on
                self._registers[REG_FIFO_READ_PTR] = (write_ptr + 1) & 0x1F
                self._count -= 1
            self._slots[write_ptr] = bytes(data)
            self._registers[REG_FIFO_WRITE_PTR] = (write_ptr + 1) & 0x1F
            self._count += 1
        return len(samples)
