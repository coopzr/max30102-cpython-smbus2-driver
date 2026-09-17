"""A tiny deterministic model of the MAX30102 register file + FIFO.

This is not a datasheet-accurate simulation of the physical sensor -- it
exists purely to give the ported driver and the unmodified upstream
MicroPython driver something to talk to, so their I2C transaction logs can
be compared byte-for-byte (see test_equivalence.py). Because both drivers
drive their own fresh instance of this exact same deterministic model, any
starting register values we pick are fine: what matters is that the same
starting state produces the same read-modify-write arithmetic in both
drivers, not that the values match real silicon.

Addressing mirrors the real device: a 1-byte write sets the "current
register" pointer, a 2-byte write sets the pointer *and* writes a value to
it, and a read serves bytes starting at the pointer (auto-advancing through
the FIFO queue for register 0x07, the only register the driver ever reads
more than one byte from).
"""
from collections import deque

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

RESET_BIT = 0x40
TEMP_EN_BIT = 0x01

# Registers that never change, regardless of resets or writes.
_FIXED_REGISTERS = {
    REG_PART_ID: 0x15,
    REG_REVISION_ID: 0x03,
}

# Fixed "measurement" the simulated die-temperature conversion reports.
# 25 + 8 * 0.0625 = 25.5 degC. INT_STAT_2's DIE_TEMP_RDY bit (0x02) is never
# set by this model, so read_temperature()'s ready-poll loop always exits
# after exactly one read -- for both drivers, identically.
_SIMULATED_DIE_TEMP_INT = 25
_SIMULATED_DIE_TEMP_FRAC = 8


class DeviceSim:
    """Register-file + FIFO model addressed like the real MAX30102."""

    def __init__(self):
        self._registers = {}
        self._addr_ptr = 0
        self._fifo_queue = deque()
        self._reset_to_defaults()

    def _reset_to_defaults(self):
        self._registers = dict(_FIXED_REGISTERS)
        self._registers[REG_DIE_TEMP_INT] = _SIMULATED_DIE_TEMP_INT
        self._registers[REG_DIE_TEMP_FRAC] = _SIMULATED_DIE_TEMP_FRAC
        self._fifo_queue.clear()

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
                # power-on state, and the bit self-clears once done. We
                # simulate that completing instantly (deterministic, and
                # avoids an infinite poll loop in soft_reset()).
                self._reset_to_defaults()
                value &= ~RESET_BIT & 0xFF
            elif reg == REG_DIE_TEMP_CONFIG and (value & TEMP_EN_BIT):
                # Datasheet: TEMP_EN self-clears once the temperature
                # conversion completes. Modeled as completing instantly,
                # same rationale as RESET above -- this also means
                # read_temperature()'s new TEMP_EN poll (see
                # SPEC_AUDIT_TRIAGE.md D3) exits on its first check rather
                # than running out its 100ms timeout in every test.
                value &= ~TEMP_EN_BIT & 0xFF
            self._set(reg, value)
        else:
            raise ValueError(
                "MAX30102 registers are only ever written as [reg] or "
                "[reg, value]; got {0} bytes".format(len(data))
            )

    def read(self, n_bytes):
        """Handle a read transaction of n_bytes starting at the pointer."""
        reg = self._addr_ptr
        if reg == REG_FIFO_DATA:
            data = self._pop_fifo(n_bytes)
            # On real silicon, reading FIFO_DATA advances the device's own
            # internal read pointer (that's how the driver's check() avoids
            # re-reading samples it already consumed). The driver always
            # issues exactly one FIFO_DATA read transaction per sample
            # (of active_leds*3 bytes), so one transaction == one sample,
            # regardless of how many bytes it spans.
            read_ptr = self._get(REG_FIFO_READ_PTR)
            self._registers[REG_FIFO_READ_PTR] = (read_ptr + 1) & 0x1F
            return data
        if n_bytes != 1:
            raise ValueError(
                "Only FIFO_DATA (0x07) is ever read as a multi-byte "
                "register; got n_bytes={0} for register 0x{1:02X}".format(
                    n_bytes, reg
                )
            )
        return bytes([self._get(reg)])

    # -- test-setup helpers (not real I2C traffic) -----------------------
    def set_write_ptr(self, value):
        self._registers[REG_FIFO_WRITE_PTR] = value & 0x1F

    def set_read_ptr(self, value):
        self._registers[REG_FIFO_READ_PTR] = value & 0x1F

    def push_fifo_bytes(self, data):
        """Queue raw bytes to be served by subsequent FIFO_DATA reads."""
        self._fifo_queue.extend(data)

    def set_die_temperature(self, int_reg_value, frac_reg_value=0):
        """Poke raw TINT/TFRAC register bytes (e.g. 0xF6 for -10 degC, per
        Table 10's two's-complement encoding)."""
        self._registers[REG_DIE_TEMP_INT] = int_reg_value & 0xFF
        self._registers[REG_DIE_TEMP_FRAC] = frac_reg_value & 0xFF

    def set_overflow_counter(self, value):
        self._registers[REG_FIFO_OVERFLOW] = value & 0xFF

    def _pop_fifo(self, n_bytes):
        out = bytearray()
        for _ in range(n_bytes):
            out.append(self._fifo_queue.popleft() if self._fifo_queue else 0x00)
        return bytes(out)
