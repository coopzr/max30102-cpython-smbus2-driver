# MAX30102 CPython/smbus2 driver — datasheet conformance audit

**Scope:** `max30102/__init__.py`, `max30102/circular_buffer.py`, `examples/heart_rate.py`,
`examples/spo2.py`, `examples/basic_usage.py`, checked against the Maxim MAX30102 datasheet
(`MAX30102.pdf`, 32 pages, "19-7317; Rev 4; 10/21" per its footer). Page numbers below are the
datasheet's own printed page numbers.

## Method & limits

No MAX30102 sensor was available, and this audit ran on Windows (the driver targets Linux via
`smbus2`, which is Linux-only). Every finding below is therefore derived from **reading the
datasheet text against the driver's source**, not from measurement against real silicon. Where an
arithmetic or logical claim could be checked without hardware — FIFO bit-shift math, the SpO₂
curve fit, the buffer-eviction behavior, the temperature sign-extension failure — it was executed
in a throwaway interpreter and the actual output is shown, not just asserted. Findings that
*would* need a live sensor to confirm (e.g. real-world FIFO fill rate under an actual 400 sps
stream, actual AC signal amplitude on skin) are marked **[unverifiable without a sensor]** and
state only what the datasheet predicts, not an observed result.

Every finding carries two independent verdicts, because this port's stated goal is byte-for-byte
I²C parity with the upstream MicroPython driver (proven by `tests/test_equivalence.py`), which is
a different property from datasheet conformance:

- **Spec** — does this conform to or deviate from the MAX30102 datasheet.
- **Parity** — is this required by upstream-fidelity (fixing it would break byte-identical
  transaction logs against `MAX30102-MicroPython-driver`), or is it a port-introduced difference
  fixable at zero parity cost.

Neither verdict is weighted over the other here; which one should win is a decision for whoever
owns this port's goals, not this audit.

## Summary

The wire protocol's addressing, register map, and bitfield encodings are, with one exception
(D1, a deliberate and documented upstream-parity choice), correctly transcribed from the
datasheet — see [What conforms](#what-conforms). The SpO₂ ratio-of-ratios math is also sound in
one specific, provable sense: R is scale-invariant, so several raw-value bugs elsewhere (D11,
D12) cannot corrupt it (A6). Everything else in the "turn raw counts into a number a person reads"
path has real problems, several severe enough that the printed BPM and SpO₂ numbers should not be
trusted even under ideal contact conditions:

| ID | Finding | Severity | Spec | Parity | Cite |
|---|---|---|---|---|---|
| A1 | HR example times peaks with host clock at drain time, not sample cadence | **Critical** | Deviates | Port-introduced (example, not driver) | p.9 (SR), p.19 Table 6 |
| D9 | 4-deep storage buffer silently drops up to 88% of a FIFO backlog | **Critical** | Deviates | Port-introduced | p.14 (32-deep FIFO) |
| A11 | `get_red()`/`get_ir()` each independently re-poll → not a valid RED/IR pair | **Critical** | Deviates | Port-introduced | p.15 Fig. 2 (paired samples) |
| D2 | Die temperature read as unsigned; sub-zero reads as ~130–255 °C | High | Deviates | Port-introduced | p.22 Table 10, p.4 (−40…+85°C) |
| D5 | `led_mode=3` desyncs the FIFO permanently on this part | High | Deviates | Port-introduced | p.21 Table 9 |
| A9 | No perfusion/signal-quality gate; noise reports as a % SpO₂ | High | N/A (app-level) | Port-introduced (example) | — |
| A5 | Peak-to-peak window swing used as "AC"; no bandpass/DC removal | High | N/A (app-level) | Port-introduced (example) | AN6409 method |
| D6 | No validation of (rate, pulse width, mode) vs. Tables 11/12; silent chip-side clamp | Medium | Deviates | Port-introduced | p.19, p.23 |
| D8 | FIFO-full indistinguishable from FIFO-empty; rollover can lap pointers | Medium | Deviates (edge case) | Port-introduced (default config choice) | p.17 |
| A2 | No refractory period in peak detection → double-counted beats | Medium | N/A (app-level) | Port-introduced (example) | — |
| A8 | SpO₂ temperature compensation never applied | Medium | Deviates (recommendation) | Port-introduced (example) | p.9, p.24, p.26 Table 15 |
| D4 | Writes to reserved/absent registers 0x0E, 0x10, 0x30 (green LED / proximity) | Low–Medium | Deviates | **Faithful to upstream** | p.10–11 |
| D3 | DIE_TEMP_RDY poll condition is inverted (works only by accident) | Low | Deviates | **Faithful to upstream** | p.12 |
| D1 | Register reads use STOP+START, not the datasheet's repeated START | Low | Deviates | **Faithful to upstream** (deliberate) | p.30 Fig. 10/11 |
| D7 | OVF_COUNTER cleared but never read; sample loss is invisible | Low | Deviates (best-practice) | Port-introduced | p.13 |
| A10 | SpO₂ window is cleared, not slid, despite the comment | Low | N/A (app-level) | Port-introduced (example) | — |
| A12 | ~2s idle before first `check()`, no `clear_fifo()` before the loop | Low | N/A (app-level) | Port-introduced (example) | p.14 |
| D10 | Comments swap RED/IR on 0x0C/0x0D (code itself is correct) | Cosmetic | Deviates (comment only) | N/A | p.20 Table 8, p.21 Table 9 |
| D11 | `_pulse_width` goes stale across `soft_reset()` | Low | Deviates | Port-introduced | p.18 |
| D12 | ADC-range comment calls LSB size "current draw" (mislabeled units) | Cosmetic | Deviates (comment only) | N/A | p.18 Table 5 |
| A7 | AN6409 quadratic itself is transcribed correctly | Positive | Conforms | — | AN6409 |
| A6 | R is scale-invariant: SpO₂ math is immune to D11/D12 | Positive | Conforms | — | — |

---

## What conforms

The register-level wire protocol is, for the most part, a faithful transcription of the datasheet.
Kept brief since this is the larger and less interesting half of the audit:

- **C1** I²C address `0x57` matches Table 17 (p.29): write address `0xAE`, read `0xAF`, 7-bit
  address `0x57`.
- **C2** All register addresses used (`0x00`–`0x0D`, `0x11`, `0x12`, `0x1F`–`0x21`, `0xFE`, `0xFF`)
  match the register map (pp.10–11) exactly.
- **C3** Part ID check (`MAX_30105_EXPECTED_PART_ID = 0x15`) matches p.11 (`PART_ID 0xFF → 0x15`).
- **C4** `MAX30105_MODE_RED_ONLY/RED_IR_ONLY/MULTI_LED` = `0x02/0x03/0x07` match Table 4 (p.18)
  exactly, including that modes `000, 001, 100–110` are correctly never emitted by this driver.
- **C5** ADC range encoding (`MAX30105_ADC_RANGE_*`) matches Table 5 (p.18) bit-for-bit.
- **C6** Sample-rate encoding (`MAX30105_SAMPLERATE_*`) matches Table 6 (p.19) bit-for-bit.
- **C7** Pulse-width encoding (`MAX30105_PULSE_WIDTH_*`) matches Table 7 (p.19) bit-for-bit.
- **C8** `MAX30105_SAMPLE_AVG_*` (SMP_AVE[2:0]) matches Table 3 (p.17): 1/2/4/8/16/32, with 0x06 and
  0x07 both correctly unused (Table 3 maps both to 32, and the driver's `set_fifo_average` only
  ever emits the canonical codes for 1/2/4/8/16/32).
- **C9** `MAX30105_ROLLOVER_ENABLE/DISABLE` on bit 4 of `0x08` matches p.17's FIFO_ROLLOVER_EN
  description exactly.
- **C10** `clear_fifo()` writes `FIFO_WR_PTR`, `OVF_COUNTER`, `FIFO_RD_PTR` to zero — precisely the
  three registers and order p.14 recommends ("it is recommended to first clear the FIFO_WR_PTR,
  OVF_COUNTER, and FIFO_RD_PTR registers to all zeroes").
- **C11** `check()`'s FIFO-backlog math (`write_pointer - read_pointer`, `+= 32` on wraparound)
  matches the pseudo-code on p.16 exactly, including the explicit wrap-around note.
- **C12** `check()` reads `read_pointer` *before* `write_pointer`. This is the race-safe order: if a
  sample arrives between the two reads, the code under-counts by one (caught on the next poll)
  rather than reading past real data. Not explicitly specified by the datasheet, but consistent
  with its "read pointer only advances on a completed transaction" model (p.13–14).
  **[unverifiable without a sensor — this is a race that only manifests under live timing]**
- **C13** FIFO reads are one `i2c_rdwr` transaction per sample, sized `active_leds * 3` bytes, which
  respects p.14's rule that `FIFO_DATA` reads do **not** auto-increment the register pointer (only
  the internal `FIFO_RD_PTR` does) — the driver never tries to read `FIFO_DATA` across a register
  boundary.
- **C14** Byte order within a sample is RED first, then IR (`fifo_bytes[0:3]` → red, `[3:6]` → IR),
  matching the p.16 pseudo-code (`Save LED1[...]` before `Save LED2[...]`) and Table 9 (p.21,
  SLOT=001 → LED1/Red, SLOT=010 → LED2/IR), given `enable_slot(1, SLOT_RED_LED)` /
  `enable_slot(2, SLOT_IR_LED)` in `set_led_mode`.
- **C15** 400 sps at an 18-bit (411 µs) pulse width, the `setup_sensor()` default, is a legal
  combination per Table 11 (SpO₂ mode allowed settings, p.23) — the "O" is present at that
  intersection.
- **C16** `read_temperature()`'s `sleep_ms(100)` comfortably covers the datasheet's 29 ms
  temperature-ADC acquisition time (p.4, `T_T`, `T_A = +25°C`).
- **C17** Temperature fraction resolution: `tempFrac * 0.0625` matches p.22 exactly ("increments of
  0.0625°C") and the datasheet's stated sensor resolution (p.9, "inherent resolution of
  0.0625°C").
- **C18 (positive)** `fifo_bytes_to_int`'s `>> (3 - pulse_width_code)` is exactly the
  datasheet-correct `>> (18 - ADC_resolution_bits)` shift that undoes Table 1's left-justification
  (p.14: "the MSB bit is always in the bit 17 data position regardless of ADC resolution
  setting"). Verified for all four pulse widths against a raw 18-bit value:

  ```
  pw_code=0 (15-bit): driver shift=3  == 18-15=3   0x2abcd >> 3  = 0x5579   match
  pw_code=1 (16-bit): driver shift=2  == 18-16=2   0x2abcd >> 2  = 0xaaf3   match
  pw_code=2 (17-bit): driver shift=1  == 18-17=1   0x2abcd >> 1  = 0x155e6  match
  pw_code=3 (18-bit): driver shift=0  == 18-18=0   0x2abcd >> 0  = 0x2abcd  match
  ```

  This is worth calling out because it's *more* spec-correct than the SparkFun library this driver
  ultimately descends from, which omits the shift entirely and returns the raw masked 18-bit value
  regardless of configured resolution.

---

## Deviations from the datasheet

Each entry: what the datasheet specifies → what the code does → a concrete failure scenario → the
two verdicts → a fix, offered as a reference implementation rather than a prescription (its parity
cost, if any, is stated alongside it).

### D1 — Register reads use two STOP-terminated transactions, not a repeated START

**Datasheet (p.30, Fig. 10/11):** a register read is `START; write slave-ID+reg addr; REPEATED
START (Sr); read slave-ID; read data; STOP`. There is exactly one STOP, at the very end — the
repeated START is what tells the device "keep the same register pointer, now switch to reading."

**Code (`i2c_read_register`, `max30102/__init__.py:645-649`):**
```python
def i2c_read_register(self, REGISTER, n_bytes=1):
    self._i2c.i2c_rdwr(i2c_msg.write(self.i2c_address, bytes([REGISTER])))
    read = i2c_msg.read(self.i2c_address, n_bytes)
    self._i2c.i2c_rdwr(read)
    return bytes(read)
```
Two separate `i2c_rdwr()` calls means two separate STOP-terminated transactions — there is a bus
STOP and idle time between the address write and the data read, not a repeated START.

**Failure scenario:** on a *shared* I²C bus with another master, or in the presence of another
slave device active between the two transactions, the STOP releases the bus and the sequence is no
longer atomic — another master could interject and change device state between the register-select
write and the data read. In the common case (this device is the only bus master's only concern),
this makes no observable difference.

**Spec:** deviates from p.30's documented format. **Parity:** this is a **deliberate,
upstream-faithful choice** — `machine.SoftI2C.writeto()`/`.readfrom()` on the MicroPython side
default to `stop=True`, so upstream itself issues two STOP-terminated transactions, and this port
exists specifically to reproduce that exact shape (see the code comment directly above this
function and the README's "Deviations from upstream" section). Note the port had a
datasheet-correct option already at hand and consciously didn't take it: `smbus2.SMBus.
read_i2c_block_data()`/`read_byte_data()` *do* emit a single combined transaction with a repeated
START — i.e. the spec-correct behavior was one call away and was deliberately not used, in favor of
upstream parity.

**Reference fix** (breaks upstream parity — not recommended given this port's stated goal, shown
for completeness):
```python
def i2c_read_register(self, REGISTER, n_bytes=1):
    write = i2c_msg.write(self.i2c_address, bytes([REGISTER]))
    read = i2c_msg.read(self.i2c_address, n_bytes)
    self._i2c.i2c_rdwr(write, read)  # single transaction, repeated START
    return bytes(read)
```

### D2 — Die temperature integer register read as unsigned

**Datasheet (p.22, Table 10):** `TINT[7:0]` is explicitly **2's complement**, with the table
showing `0x80 → -128 °C` and `0xFF → -1 °C`. The sensor's documented range is −40 °C to +85 °C
(p.4, `T_MIN`/`T_MAX`).

**Code (`read_temperature`, `max30102/__init__.py:583-589`):**
```python
tempInt = ord(self.i2c_read_register(MAX30105_DIE_TEMP_INT))
tempFrac = ord(self.i2c_read_register(MAX30105_DIE_TEMP_FRAC))
return float(tempInt) + (float(tempFrac) * 0.0625)
```
`ord()` on a single byte yields an unsigned value 0–255. Nothing sign-extends it.

**Verified failure:** simulating a real, in-range −10 °C reading (well within the documented
−40…+85 °C sensor range):
```
register byte on wire for -10 degC: 0xf6 (246)
driver interprets tempInt as (unsigned): 246
driver reports temperature as: 246.0 degC   (actual: -10 degC)
```
Any ambient temperature the sensor is rated to measure that happens to be below 0 °C is reported as
somewhere in 129–255 °C instead. This directly undermines A8 below (SpO₂ temperature
compensation), since a wildly wrong temperature is worse than no compensation at all.

**Spec:** deviates from Table 10. **Parity:** port-introduced — upstream's own `readTemperature()`
has the identical bug (same `ord()`-only pattern), so a spec-correct fix here *would* break byte
parity with upstream's *logic*, though not its I²C wire traffic (this is pure post-read arithmetic,
zero bytes on the wire). Fixable without touching the transaction log.

**Reference fix:**
```python
tempInt = ord(self.i2c_read_register(MAX30105_DIE_TEMP_INT))
if tempInt > 127:
    tempInt -= 256
tempFrac = ord(self.i2c_read_register(MAX30105_DIE_TEMP_FRAC))
return float(tempInt) + (float(tempFrac) * 0.0625)
```

### D3 — DIE_TEMP_RDY poll condition is inverted

**Datasheet (p.12):** "When an internal die temperature conversion is finished, this interrupt is
triggered" — i.e., the flag is **set (1) when the reading is ready**. It also states: "The
interrupts are cleared whenever the interrupt status register is read" (p.13).

**Code (`read_temperature`, `max30102/__init__.py:576-581`):**
```python
reading = ord(self.i2c_read_register(MAX30105_INT_STAT_2))
sleep_ms(100)
while (reading & MAX30105_INT_DIE_TEMP_RDY_ENABLE) > 0:
    reading = ord(self.i2c_read_register(MAX30105_INT_STAT_2))
    sleep_ms(1)
```
This loops *while the ready bit is set*, i.e. it's written as if `1` means "not ready yet, keep
waiting." That's backwards from p.12. In practice this makes the loop a no-op in the common case
(the flag reads 0 before the conversion is even likely to have started, since it's checked
immediately with no prior delay), so the function's correctness rests entirely on the blind
`sleep_ms(100)` beforehand — not on this loop.

**Failure scenario:** if the flag *did* happen to read 1 on entry (e.g. a stale/uncleared bit from
a prior read cycle), the loop would spin — but the very act of reading `INT_STAT_2` clears
`DIE_TEMP_RDY` per p.13, so the second read inside the loop returns 0 regardless of real conversion
state, and the loop exits after exactly one extra iteration no matter what. The loop can never
actually detect "conversion genuinely still in progress," because reading the status also destroys
the status.

**Spec:** deviates from p.12's documented polarity. **Parity:** **faithful to upstream** — this is
a direct, unmodified transliteration of upstream's identical polling logic; upstream has the same
inverted condition. A spec-correct fix is pure post-read logic (no wire-protocol change), so it
*can* be fixed without breaking transaction-log parity, at the cost of diverging from upstream's
logic.

**Reference fix** (also needs the read-clears-flag issue accounted for — poll `INT_STAT_2` without
reading `FIFO_DATA` or anything else that also clears it in between):
```python
sleep_ms(1)  # give the conversion a moment to start
while True:
    reading = ord(self.i2c_read_register(MAX30105_INT_STAT_2))
    if reading & MAX30105_INT_DIE_TEMP_RDY_ENABLE:
        break
    sleep_ms(1)
```

### D4 — `setup_sensor()` writes to registers the MAX30102 doesn't document

**Datasheet (pp.10–11):** the register map lists `0x0C`/`0x0D` as `LED1_PA`/`LED2_PA`, `0x0E` and
`0x0F` as `RESERVED`, and jumps straight to `0x11`/`0x12` for multi-LED control — **register
`0x10` does not appear in the map at all**. There is no green-LED or proximity-detection hardware
documented anywhere in this datasheet (the MAX30102 has two LEDs — Table 8's `LED1_PA`/`LED2_PA` —
not three; proximity mode is a MAX30105-family feature this part's datasheet never mentions).

**Code:**
```python
MAX30105_LED3_PULSE_AMP = 0x0E   # comment: "GREEN (when available)"
MAX30105_LED_PROX_AMP = 0x10
...
MAX30105_PROX_INT_THRESH = 0x30
```
and `setup_sensor()` unconditionally calls `set_pulse_amplitude_green(led_power)` and
`set_pulse_amplitude_proximity(led_power)`, writing to `0x0E` and `0x10` on every single
`setup_sensor()` call regardless of `led_mode`.

**Failure scenario:** writing `0x0E` (documented RESERVED, POR state `0x00`) is a write to a
reserved register — the datasheet gives no guarantee about what a write there does; on the closely
related MAX30105 it is the actual green-LED amplitude register, so on a MAX30102 it is, at best, a
no-op and, per the "do not write to reserved registers" convention implicit in the datasheet
listing them as reserved rather than documenting behavior, unspecified. `0x10` isn't even reserved
— it's simply absent from this part's map.

**Spec:** deviates — targets registers this datasheet doesn't document for this part. **Parity:**
**faithful to upstream** — this is inherited directly from the SparkFun MAX3010x library (which
covers the whole family, including the 3-LED, proximity-capable MAX30105) via upstream's
MicroPython port; the MAX30102-specific driver never trimmed the family-wide register set down to
what this part actually implements. Removing the writes would diverge from upstream's setup
sequence (a different number of I²C transactions).

**Reference fix** (breaks parity — upstream issues these writes too):
```python
def setup_sensor(self, ...):
    ...
    self.set_pulse_amplitude_red(led_power)
    self.set_pulse_amplitude_ir(led_power)
    if self._active_leds > 2:
        self.set_pulse_amplitude_green(led_power)  # only meaningful on MAX30105
    # drop set_pulse_amplitude_proximity(led_power) entirely on MAX30102
```

### D5 — `led_mode=3` silently desyncs the FIFO on the MAX30102

**Datasheet (p.21, Table 9):** `SLOTx[2:0]` encodes which LED is active in each multi-LED time
slot: `001` = LED1 (Red), `010` = LED2 (IR), and **`011` = "None"** — there is no third LED to
select on this part.

**Code (`set_led_mode`, `max30102/__init__.py:377-400`):**
```python
elif LED_mode == 3:
    self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_MODE_MASK, MAX30105_MODE_MULTI_LED)
...
self.enable_slot(1, SLOT_RED_LED)
if LED_mode > 1:
    self.enable_slot(2, SLOT_IR_LED)
if LED_mode > 2:
    self.enable_slot(3, SLOT_GREEN_LED)   # SLOT_GREEN_LED = 0x03 = "None" per Table 9
...
self._active_leds = LED_mode          # 3
self._multi_led_read_mode = LED_mode * 3   # 9
```
With `led_mode=3`, slot 3 is programmed to `0b011`, which Table 9 defines as "None" — the device
does **not** emit a third 3-byte channel. The FIFO sample size for this configuration is `2 × 3 =
6` bytes (RED + IR only), but the driver computes `_multi_led_read_mode = 9` and `check()` issues a
9-byte `FIFO_DATA` read per iteration of its sample-count loop.

**Failure scenario:** every `check()` call reads 3 bytes past the end of the real 6-byte sample —
those 3 extra bytes are actually the *first 3 bytes of the next sample* (or of the sample after
that in the FIFO's circular layout), interleaved into what the driver treats as the "green"
channel. Every subsequent call is then reading from a position offset by one channel-width from
where the device's own `FIFO_RD_PTR` logic assumes, and the reported RED/IR values become a
mixture of adjacent samples that never resynchronizes on its own.

**Spec:** deviates — programs a slot value Table 9 documents as inactive, then reads FIFO data as
if it were active. **Parity:** port-introduced in the sense that upstream has the identical
`enable_slot(3, SLOT_GREEN_LED)` call and the identical `LED_mode * 3` arithmetic — so this is
actually **faithful to upstream** too; it's a shared, unexercised-until-now bug (`led_mode=3` is
never invoked by either project's own examples). Listed as port-introduced-in-spirit only because
the port could add a mode-3 guard without touching a single byte on the wire for modes 1 and 2.

**Reference fix** (raises at config time instead of desyncing at read time; no wire-protocol change
for modes 1/2, and mode 3 was never usable on this part regardless):
```python
def set_led_mode(self, LED_mode):
    if LED_mode == 3:
        raise ValueError(
            "led_mode=3 (green LED) is not supported on MAX30102; "
            "this part has only Red and IR LEDs"
        )
    ...
```

### D6 — No validation of (sample rate, pulse width, mode) against Tables 11/12

**Datasheet (p.19):** "If the user selects a sample rate that is too high for the selected LED_PW
setting, the highest possible sample rate is programmed instead into the register" — i.e. an
illegal combination is **silently clamped by the chip itself**, not rejected. Tables 11/12 (p.23)
give the legal combinations per mode (e.g. 1600 sps is illegal at 411 µs / 18-bit in either mode;
3200 sps is illegal at every pulse width except 69 µs, and only in HR mode at that).

**Code:** `set_sample_rate()` and `set_pulse_width()` each validate their own argument against
their own enum independently (`max30102/__init__.py:421-471`) but never cross-check the pair
against Table 11/12, and never know which mode (`led_mode`/`MODE[2:0]`) is active while doing so.
`update_acquisition_frequency()` then computes `self._acq_frequency = self._sample_rate /
self._sample_avg` purely from the values the caller *requested*, with no way to learn that the
chip silently substituted a different rate.

**Compounding issue:** `setup_sensor()`'s call order is `set_sample_rate(sample_rate)` *then*
`set_pulse_width(pulse_width)` (`max30102/__init__.py:252-255`) — so when the chip evaluates the
legality of the requested sample rate (per p.19's clamp rule), it does so against whatever pulse
width/resolution was in the register *before* this call, i.e. the sensor's just-reset default
(69 µs / 15-bit, POR state per p.10) — not the pulse width `setup_sensor()` is about to set. The
two writes are shipped in the order least likely to reflect the caller's actual intended pair.

**Failure scenario:** call `setup_sensor(sample_rate=1600, pulse_width=411)` (both individually
valid arguments): per Table 11, 1600 sps is illegal at 411 µs/18-bit. The chip clamps to whatever
its highest legal rate is at 69 µs/15-bit (the resolution in effect at the time `set_sample_rate`
runs, per the ordering issue above) — silently. `get_acquisition_frequency()` still reports
`1600 / sample_avg`, feeding a fabricated time base directly into both `heart_rate.py`'s BPM
arithmetic (A1) and `spo2.py`'s window-size assumption ("100 samples ≈ 2 seconds"). Every derived
number downstream is now computed against a sample rate the device isn't actually running at, with
no error, warning, or observable symptom other than wrong output.
**[the exact clamped rate for any specific combination is undocumented beyond "highest possible" —
confirming which rate results requires a live device]**

**Spec:** deviates — the datasheet's own escape hatch (silent clamping) is exactly what makes this
dangerous to not check for. **Parity:** port-introduced — upstream has the identical gap (no
cross-validation, same call order in its `setup_sensor` equivalent), so a fix here is
upstream-faithful in the sense of "upstream doesn't validate either," but flagged as
port-fixable since validation is pure Python with zero wire impact.

**Reference fix** (adds a lookup table from Table 11/12; no change to existing valid calls):
```python
_SPO2_LEGAL = {  # (sample_rate, pulse_width) pairs marked "O" in Table 11, p.23
    50: {69, 118, 215, 411}, 100: {69, 118, 215, 411}, 200: {69, 118, 215, 411},
    400: {69, 118, 215, 411}, 800: {69, 118, 215}, 1000: {69, 118},
    1600: {69}, 3200: set(),
}
def set_sample_rate(self, sample_rate, pulse_width=None):
    pw = pulse_width if pulse_width is not None else self._pulse_width_us
    if pw is not None and pw not in _SPO2_LEGAL.get(sample_rate, ()):
        raise ValueError(f"{sample_rate} sps is not legal at {pw}us pulse width (Table 11)")
    ...
```

### D7 — OVF_COUNTER is cleared but never read

**Datasheet (p.13):** "When the FIFO is full, samples are not pushed on to the FIFO, samples are
lost. OVF_COUNTER counts the number of samples lost." This is the datasheet's only built-in
mechanism for detecting sample loss.

**Code:** `clear_fifo()` (`max30102/__init__.py:539-544`) writes `MAX30105_FIFO_OVERFLOW` to 0, and
`MAX30105_FIFO_OVERFLOW`/`0x05` is defined as a register constant, but nothing in the driver ever
*reads* it. There is no `get_overflow_count()` method.

**Failure scenario:** combined with D9 (4-deep storage buffer) and D8 (rollover ambiguity) below,
samples can be silently lost at three different layers, and none of them is instrumented — the
datasheet gives a direct, cheap signal for one of those layers (device-side FIFO overflow) and the
driver discards it on every reset without ever consulting it.

**Spec:** deviates from best-practice implied by the register's existence (not a hard requirement —
nothing mandates reading it). **Parity:** port-introduced — upstream also never reads this
register, so adding a read is purely additive and cannot affect existing transaction-log parity
tests (it would only append new transactions when explicitly called).

**Reference fix** (additive, zero parity cost — no existing call site changes):
```python
def get_overflow_count(self):
    """Number of samples lost to FIFO overflow since the last read-pointer advance (p.13)."""
    return ord(self.i2c_read_register(MAX30105_FIFO_OVERFLOW))
```

### D8 — FIFO-full is indistinguishable from FIFO-empty in `check()`

**Datasheet (p.16 pseudo-code; p.17 FIFO_ROLLOVER_EN):** sample count is computed as
`FIFO_WR_PTR - FIFO_RD_PTR` (mod 32). With `FIFO_ROLLOVER_EN` set, p.17 states the write pointer
keeps advancing and wrapping once the FIFO is completely full, overwriting unread data.

**Code:** `setup_sensor()` calls `enable_fifo_rollover()` unconditionally
(`max30102/__init__.py:242`), and `check()`'s backlog math
(`max30102/__init__.py:734-746`) is exactly the datasheet's pointer-difference formula — which is
structurally unable to distinguish "0 samples waiting" from "32 samples waiting, and the write
pointer has lapped the read pointer exactly once" (`write_pointer == read_pointer` in both cases).

**Failure scenario:** at the default config (400 sps / 8-avg ≈ 50 Hz acquisition), the 32-sample
FIFO fills in 640 ms of un-polled time. If the host goes uninterrupted for ≥640 ms (GC pause,
`sleep_ms` inside another call, scheduling jitter, or — per A12 — the examples' own ~2s idle
sleeps before the first `check()`), and the write pointer laps the read pointer by exactly a
multiple of 32, `check()` reports **zero new samples** when in fact a full FIFO's worth (or more,
now silently overwritten per FIFO_ROLLOVER_EN) was available. Because D7 means OVF_COUNTER is never
consulted either, this failure mode leaves no trace.
**[the exact timing threshold depends on real acquisition rate and host scheduling —
unverifiable without a sensor]**

**Spec:** this is an inherent ambiguity in the datasheet's own pointer-difference scheme when
rollover is enabled, not something p.16's pseudo-code accounts for — the datasheet's example
assumes the host polls often enough that a full wrap never happens between polls. **Parity:**
port-introduced in the sense that enabling rollover (vs. leaving it disabled, p.17's alternative
"FIFO is not updated until FIFO_DATA is read") is a `setup_sensor()` default choice this port
inherited unchanged from upstream — so also faithful to upstream's default.

**Reference fix** (poll more defensively — reading OVF_COUNTER from D7 turns the silent case into
a detectable one at zero parity cost, since it's an additive read):
```python
def check(self):
    read_pointer = ord(self.get_read_pointer())
    write_pointer = ord(self.get_write_pointer())
    if read_pointer == write_pointer:
        overflowed = ord(self.i2c_read_register(MAX30105_FIFO_OVERFLOW))
        if overflowed:
            # FIFO was full at some point since the last read-pointer advance;
            # treat as "32 new samples", not "0 new samples".
            ...
        return False
    ...
```

### D9 — 4-deep storage buffer vs. a 32-deep device FIFO

**Datasheet (p.14):** "The circular FIFO depth is 32 and can hold up to 32 samples of data."

**Code:** `STORAGE_QUEUE_SIZE = 4` (`max30102/__init__.py:168`), and `SensorData` builds
`self.red`/`self.IR`/`self.green` as `CircularBuffer(STORAGE_QUEUE_SIZE)` — a
`collections.deque(maxlen=4)`. `check()`'s loop (`max30102/__init__.py:748-768`) `.append()`s
*every* sample it reads from the device FIFO into this 4-deep buffer in one pass, before the caller
ever gets a chance to call `available()` or drain anything.

**Verified failure** — simulating exactly what one `check()` sweep does after a FIFO backlog has
built up (32 samples pushed into a fresh 4-deep buffer, oldest first, matching `check()`'s own
append order):
```
pushed 32 samples (values 0..31), 4-deep buffer survivors: [28, 29, 30, 31]
lost 28 of 32 = 88%
```
This is not a rare edge case — it is `deque(maxlen=4)`'s documented, correct behavior, triggered
by design every time `check()` observes more than 4 unread device-FIFO samples in one call, which
per D8 happens routinely under normal polling jitter at 50 Hz.

**Failure scenario:** this sits directly upstream of *every* derived metric in the codebase. It is
non-uniform decimation (always evicts the *oldest* of a burst, keeps only the *most recent* 4) —
worse than uniform downsampling for peak-interval timing (A1) because the discarded samples are not
evenly spaced in time, and worse for the SpO₂ AC/DC window (A5) because bursts correlate with
exactly the polling gaps (D8) most likely to also contain real signal.

**Spec:** deviates from the datasheet's 32-sample FIFO model — a consumer designed to track it
should hold at least as many samples as the producer can emit between polls in the worst realistic
case, not a fixed 4. **Parity:** port-introduced difference in *severity*, though the constant and
mechanism (`STORAGE_QUEUE_SIZE = 4`) are inherited unchanged from upstream — upstream has the
identical value and the identical bug; this port doesn't introduce it, but doesn't need to keep it
either, since `CircularBuffer`'s `max_size` is pure Python bookkeeping (see README: "Pure buffer
bookkeeping, zero I2C traffic on either side of it" — the same is true of *changing* its size).

**Reference fix** (zero parity cost — no wire traffic involved):
```python
STORAGE_QUEUE_SIZE = 32  # match the device's own FIFO depth (datasheet p.14)
```

### D10 — Register-map comments swap RED and IR

**Datasheet:** Table 8 (p.20) and Table 9 (p.21) both agree: `LED1_PA` (`0x0C`) drives LED1 = Red,
`LED2_PA` (`0x0D`) drives LED2 = IR.

**Code (`max30102/__init__.py:54-55`):**
```python
MAX30105_LED1_PULSE_AMP = 0x0C  # IR
MAX30105_LED2_PULSE_AMP = 0x0D  # RED
```
The comments are swapped — the constant *names* and their actual register addresses are correct
and match the datasheet; only the inline `# IR`/`# RED` annotations are backwards. This doesn't
affect behavior (nothing reads these comments at runtime), but it's a live landmine for anyone
manually rebalancing RED vs. IR LED current to fix a poor SpO₂ ratio in A5/A9 (exactly the person
most likely to read these two lines closely).

**Spec:** deviates (comment-only). **Parity:** N/A — comments carry no wire traffic either way.

**Reference fix:**
```python
MAX30105_LED1_PULSE_AMP = 0x0C  # RED
MAX30105_LED2_PULSE_AMP = 0x0D  # IR
```

### D11 — `_pulse_width` cache goes stale across `soft_reset()`

**Datasheet (p.18):** "When the RESET bit is set to one, all configuration, threshold, and data
registers are reset to their power-on-state" — which per Table 7 (p.19)/p.10's POR column means
`LED_PW[1:0]` returns to `00` (69 µs / 15-bit).

**Code:** `self._pulse_width` (used by `fifo_bytes_to_int`'s shift amount) is only ever written
inside `set_pulse_width()`. `soft_reset()` (`max30102/__init__.py:353-362`) resets the physical
register but never touches `self._pulse_width`, and it is `None` until the first
`set_pulse_width()` call ever happens.

**Failure scenario:** any code path that calls `soft_reset()` directly (rather than exclusively
through `setup_sensor()`, which happens to call `soft_reset()` then `set_pulse_width()` in that
order) — or that calls `fifo_bytes_to_int` indirectly via `check()` before `set_pulse_width()` has
ever run — either shifts by a stale, no-longer-matching amount (silently wrong resolution
decoding) or raises `TypeError` on `3 - None`. `setup_sensor()`'s own internal ordering happens to
avoid this, so the bug is latent, not currently triggered by the shipped examples.

**Spec:** deviates from the implicit contract that cached register state should track actual
register state across a documented reset. **Parity:** port-introduced — this cache
(`self._pulse_width`) exists specifically to support this port's `fifo_bytes_to_int`; upstream's
equivalent MicroPython code has the analogous field with the same gap, so arguably faithful too,
but fixable with zero wire impact (pure Python state).

**Reference fix:**
```python
def soft_reset(self):
    self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_RESET_MASK, MAX30105_RESET)
    curr_status = -1
    while not ((curr_status & MAX30105_RESET) == 0):
        sleep_ms(10)
        curr_status = ord(self.i2c_read_register(MAX30105_MODE_CONFIG))
    self._pulse_width = MAX30105_PULSE_WIDTH_69  # POR default per Table 7/p.10
```

### D12 — ADC-range comment mislabels LSB size as "current draw"

**Datasheet (p.18, Table 5):** `SPO2_ADC_RGE[1:0]` sets LSB size in **pA** and full-scale range in
**nA** — these are *ADC quantization units for the photodiode current signal*, specified at the
part's native **18-bit** resolution.

**Code (`max30102/__init__.py:123-127`):**
```python
# ADC range: set the range of the conversion
# Options: 2048, 4096, 8192, 16384
# Current draw: 7.81pA. 15.63pA, 31.25pA, 62.5pA per LSB.
```
"Current draw" is a misleading label for an LSB *quantization* size — it reads as if it were LED
supply current draw (which is what `MAX30105_PULSE_AMP_*`'s mA comments, correctly, describe a few
lines below). It's also silent about the fact that Table 5's pA/LSB figures are specified at
18-bit resolution — the *effective* LSB size after `fifo_bytes_to_int`'s right-shift scales by
`2^(18 - actual_resolution_bits)` when a shorter pulse width is configured, and nothing in the
driver exposes a counts→physical-units conversion at all (raw ADC counts are all that's ever
returned).

**Spec:** deviates (comment-only, no behavioral effect). **Parity:** N/A.

**Reference fix:**
```python
# ADC full-scale range: set the SpO2 photodiode-current ADC's full-scale range
# (datasheet Table 5, p.18). LSB sizes there (7.81/15.63/31.25/62.5 pA) are
# specified at 18-bit resolution; the effective LSB scales by 2**(18 - bits)
# when a shorter pulse width (lower resolution) is configured.
```

---

## Turning raw counts into heart rate

`examples/heart_rate.py`'s `HeartRateMonitor` takes the IR channel only (correct choice per p.24:
"In the Heart Rate mode, only the Red LED is used" for *hardware* HR mode — but this driver runs in
`led_mode=2`/SpO₂ mode throughout and uses IR by software convention instead, which the code
comment correctly acknowledges: "based on the skin color, the red, IR or green LED can be used").
Independent of that reasonable choice, the pipeline that turns those IR counts into a BPM number
has several defects:

### A1 — Peaks are timestamped with host wall-clock time at drain time, not sample cadence [Critical]

**What the datasheet establishes:** the sensor clocks a new sample into its FIFO at a fixed,
known rate — `sample_rate / sample_avg` (p.9's ADC output rate, divided by Table 3's on-chip
averaging) — completely decoupled from when the host happens to read it out. The device does not
timestamp samples; their time is implicit in their position in the fixed-rate stream.

**Code (`heart_rate.py:27-31`):**
```python
def add_sample(self, sample):
    timestamp = time.monotonic_ns() // 1_000_000
    self.samples.append(sample)
    self.timestamps.append(timestamp)
```
`add_sample()` is called once per sample **inside the drain loop**
(`while sensor.available(): ... hr_monitor.add_sample(ir_reading)`, `heart_rate.py:157-163`), and
its timestamp is `time.monotonic_ns()` at the moment that particular sample is popped from the
*host-side* storage buffer — not when the *device* actually acquired it.

**Failure scenario:** `sensor.check()` is called once per outer-loop iteration, then *all* samples
currently sitting in the (already too-small, per D9) 4-deep storage buffer are drained in a tight
inner `while` loop with no sleep between iterations. Every sample drained in that inner loop gets a
timestamp within microseconds of every other sample in that same batch — batches of samples that
were actually acquired 20 ms apart (at 50 Hz) are stamped as if acquired instantaneously together,
while the *gap* between batches (however long the outer loop's Python-level overhead + `check()`'s
own I²C round-trip takes) is attributed as if it were real inter-sample time. `find_peaks()` and
`calculate_heart_rate()` then compute BPM entirely from these fabricated intervals
(`heart_rate.py:69-94`) — the reported BPM reflects host scheduling jitter, not cardiac timing.

**Corrected approach:** derive each sample's time from its position in the fixed-rate stream, not
the host clock:
```python
# t_sample[n] = n / f_acq, where f_acq = sample_rate / sample_avg (driver's
# own get_acquisition_frequency(), datasheet p.9 + Table 3)
sample_index = 0
def add_sample(self, sample):
    timestamp_ms = 1000.0 * sample_index / self.sensor_acq_frequency_hz
    sample_index += 1
    ...
```
This requires zero I²C changes — it's a pure host-side accounting fix, and it depends on D6 being
addressed too (an unvalidated, chip-clamped rate feeds a wrong `f_acq` into this formula just as
readily as it does into the current code's assumptions).

### A2 — No refractory period in peak detection [Medium]

**Code (`find_peaks`, `heart_rate.py:63-68`):** flags sample `i` as a peak whenever it's a local
maximum above a threshold set at 50% between the recent window's min and max — with no constraint
on how close consecutive flagged peaks may be in time.

**Failure scenario:** a real PPG waveform commonly has a secondary bump (the dicrotic notch,
reflecting the arterial pressure wave's reflection) that can itself cross a 50%-of-range threshold,
especially with the flat 5-tap moving average this code uses (A3) rather than a proper pulse-shape
filter. Two flagged "peaks" per actual heartbeat inflates the reported BPM, often by close to 2×.

**Corrected approach:** enforce a minimum inter-peak interval consistent with physiologically
possible heart rates (≤ ~220 BPM at rest-to-max exertion ⇒ ≥ ~270 ms between beats; 300 ms is a
common conservative choice):
```python
MIN_PEAK_INTERVAL_MS = 300
...
if peaks and (peak_time - peaks[-1][0]) < MIN_PEAK_INTERVAL_MS:
    continue  # too close to the last accepted peak; almost certainly the same beat
peaks.append((peak_time, self.filtered_samples[i]))
```

### A3 — Moving average is not a substitute for AC/DC separation [Medium]

**Code (`add_sample`, `heart_rate.py:33-40`):** a 5-sample simple moving average is the only
filtering applied before peak detection.

**What's wrong:** the cardiac pulsatile ("AC") component this method is trying to isolate is
typically on the order of 0.1–2% of the DC baseline (a physiological fact reflected in why SpO₂
algorithms bother with a separate AC/DC decomposition at all — see A5/A6 below). A 5-tap moving
average is a weak low-pass filter; it does essentially nothing to remove the *low-frequency*
baseline wander (breathing, motion, ambient-light drift via the ALC circuit described on p.9) that
dominates the signal's min/max range. `find_peaks()`'s threshold — 50% between the window's min and
max — is computed against whatever that wandering baseline's range happens to be, not against the
actual pulse amplitude, so the threshold drifts with baseline rather than tracking the heartbeat.

**Corrected approach:** remove the DC/baseline component before thresholding, e.g. a simple
high-pass (subtract a slower-moving average of e.g. 1–2s from the raw signal) or a proper 0.5–4 Hz
bandpass (covers 30–240 BPM) before peak detection, so the threshold is computed against pulsatile
amplitude, not raw signal range.

### A4 — Peak intervals silently span dropped samples

Because D9 (4-deep buffer) and D8 (FIFO wrap ambiguity) can each cause samples to be dropped
without any signal to the caller, `calculate_heart_rate()`'s "average interval between consecutive
peaks" (`heart_rate.py:81-87`) has no way to know when two consecutive *detected* peaks were
separated by one or more *undetected* beats in between (because the samples spanning them were
evicted). An interval that should span 2 beats gets treated as if it spans 1, silently doubling the
reported BPM for that interval. This is a direct downstream consequence of D9/D8, not a separate
bug — fixing D9 removes the routine case; A1's index-based timestamping (rather than drain-time
stamping) at least makes any *remaining* gap show up as an anomalously long interval instead of
being invisibly absorbed into drain-time noise.

---

## Turning raw counts into blood oxygen (SpO₂)

`examples/spo2.py` implements the classic "ratio of ratios" method (AN6409): AC/DC per channel,
then RED-ratio ÷ IR-ratio, fed through an empirical quadratic. Two parts of this are provably
correct; the rest has real gaps.

### A6 (positive) — R is scale-invariant, so D11/D12 cannot corrupt SpO₂

```python
r = (red_ac / red_dc) / (ir_ac / ir_dc)
```
`red_ac`, `red_dc`, `ir_ac`, `ir_dc` are all raw ADC counts from the *same* channel's
`fifo_bytes_to_int` output, computed with the *same* resolution shift and the *same* ADC range for
every sample in a window. Any constant multiplicative factor applied uniformly to a channel's
counts — a wrong resolution shift (D11), an unlabeled/misconfigured ADC range (D12), or simply a
different `pulse_width` between two runs — cancels exactly in `red_ac/red_dc` (same channel, same
factor top and bottom) and therefore in `r` as a whole. This is provable algebraically, not just
empirically: if `red_ac' = k·red_ac` and `red_dc' = k·red_dc` for any constant `k`,
`red_ac'/red_dc' = red_ac/red_dc` exactly. **This means D11 and D12, while real defects elsewhere,
cannot be the cause of a wrong SpO₂ reading** — worth stating explicitly so a reader doesn't spend
effort "fixing" resolution-shift bugs in pursuit of a bad SpO₂ number; the actual causes are below.

### A7 (positive) — AN6409 quadratic is transcribed correctly

**Code:**
```python
spo2 = -45.060 * r * r + 30.354 * r + 94.845
```
This matches the commonly-cited Maxim AN6409 empirical fit. Verified numerically against known
reference points for this exact curve:
```
R=0.4: SpO2=99.78%
R=0.5: SpO2=98.76%
R=0.7: SpO2=94.01%
R=1.0: SpO2=80.14%
```
These match the standard published shape of this curve (near-100% at low R, ~98% around R=0.4,
crossing into clinically-implausible-low territory by R≈1.0 — consistent with this being a fit
valid only over the R≈0.4–1.0 range typical of real perfused tissue, not a general-purpose
formula). The code's own docstring correctly flags this as "an approximation, not a calibrated
conversion" and that "real pulse oximeters are calibrated against a reference device per unit" —
this caveat is accurate and should stay prominent.

### A5 — Peak-to-peak window swing is not a valid AC estimate [High]

**Code (`compute_spo2`, `spo2.py:41-42`):**
```python
red_ac = max(red_samples) - min(red_samples)
ir_ac = max(ir_samples) - min(ir_samples)
```
**What's wrong:** as in A3, this conflates *pulsatile amplitude* with *raw signal range*. A single
motion artifact, a brief pressure change, or slow ambient-light drift over the 100-sample (~2s)
window can dominate `max - min` just as easily as an actual heartbeat can — the method has no way
to distinguish "the signal swung because of a pulse" from "the signal swung because of anything
else." Since `r` (and thus reported SpO₂) is a *ratio* of these two channels' swings, correlated
noise across both channels (e.g. motion, which affects both RED and IR similarly since they share
the same optical path through tissue) partially cancels, but *uncorrelated* noise (ambient light
flicker affecting one channel's ALC circuit differently, per p.9) does not, and directly perturbs
`r`.

**Corrected approach:** isolate the actual pulsatile component per beat rather than per arbitrary
window — bandpass/high-pass filter each channel first (same fix as A3), then use each channel's
mean per-beat peak-to-trough swing (using beat boundaries detected the *same* way for both
channels, e.g. from IR peak detection applied to both RED and IR at the same sample offsets) rather
than a single global peak-to-peak over an arbitrary fixed window.

### A9 — No perfusion/signal-quality gate before reporting a number [High]

**Code (`compute_spo2`, `spo2.py:37-44`):**
```python
if red_dc == 0 or ir_dc == 0:
    return None
red_ac = max(red_samples) - min(red_samples)
ir_ac = max(ir_samples) - min(ir_samples)
if ir_ac == 0:
    return None  # "almost certainly no finger present"
```
**What's wrong:** `ir_ac == 0` requires every one of 100 consecutive raw ADC samples to be
*exactly, bit-for-bit* identical — that essentially never happens with a live ADC and any amount of
electrical or ambient noise, finger on the sensor or not. This "no finger" gate is, in practice,
unreachable, meaning `spo2.py` prints a numeric SpO₂ percentage even when there's no meaningful
pulsatile signal — no finger, poor contact, or pure noise all produce *some* nonzero peak-to-peak
swing and thus *some* value of `r`, fed straight through the AN6409 curve as if it were a real
measurement, silently.
**[what real noise-floor swing looks like on actual hardware is unverifiable without a sensor;
this finding is about the gate's logical unreachability, not a measured noise figure]**

**Corrected approach:** gate on a perfusion index (AC/DC ratio, which the code already computes the
ingredients for) against a floor that reflects real-world "no usable pulse" conditions, not exact
zero — a commonly used minimum in consumer PPG designs is roughly PI ≳ 0.2–0.5%:
```python
ir_pi = ir_ac / ir_dc if ir_dc else 0
if ir_pi < 0.002:  # perfusion index floor — tune against real hardware
    return None
```

### A8 — SpO₂ temperature compensation documented but never applied [Medium]

**Datasheet:** p.9 ("An SpO2 algorithm used with the MAX30102 output signal can compensate for the
associated SpO2 error with ambient temperature changes"), p.24 ("the red LED's wavelength is
critical to correct interpretation of the data... Use Table 13 to estimate the temperature[-driven
wavelength shift]"), and p.26's Table 15 event sequence for SpO₂ mode explicitly opens with
"Initiate a Temperature measurement" as step 1 of its documented reference flow, noting "The
temperature does not need to be sampled very often – once a second or every few seconds should be
sufficient."

**Code:** `read_temperature()` exists and is correctly implemented (module-arithmetic bugs aside —
see D2/D3), but `spo2.py` never calls it. The RED channel's temperature-dependent wavelength shift
(Table 13, p.24) is never accounted for anywhere in the SpO₂ pipeline.

**Impact:** this is exactly the kind of systematic-bias source that an uncalibrated fit like AN6409
(A7) is least equipped to absorb — Table 13 shows temperature rise as a function of LED current and
duty cycle reaching several degrees C, which per p.24 directly shifts red LED wavelength and thus
`r`. Note per D2 this can't be safely bolted on without fixing the sign-extension bug first, since a
wrongly-signed temperature would inject a compensation error larger than the effect being
corrected for.

**Corrected approach:** call `read_temperature()` roughly once per second (matching p.26's guidance
and Table 15's documented event sequence) alongside the SpO₂ loop and apply whatever temperature
compensation curve the deployment's calibration data supports — this driver exposes the raw
ingredient (`read_temperature()`) but implements no compensation model itself, and the datasheet
doesn't give a ready-made formula either (Table 13 is guidance for building one, not one itself).

### A10 — Window is cleared, not slid, despite the comment [Low]

**Code (`spo2.py:91-94`):**
```python
# Slide the window forward instead of recomputing on every
# single new sample.
red_window.clear()
ir_window.clear()
```
The comment describes a sliding window; `.clear()` on both deques empties them completely, so the
*next* SpO₂ estimate has to wait for a fresh 100 samples (~2s at 50 Hz) to accumulate from scratch,
rather than the window advancing by however many new samples arrived. This is a real behavior —
non-overlapping 2-second blocks — just not the one the comment describes, and it means SpO₂ updates
are unnecessarily coarse (a fresh 2s wait between every reading, rather than every reading using the
most recent 2s of data).

### A11 — `get_red()`/`get_ir()` do not produce a valid RED/IR pair [Critical]

**Datasheet (p.15, Fig. 2):** each FIFO sample is inherently a *pair* — Sample N's RED channel and
Sample N's IR channel are written to the FIFO together, from the same LED pulse cycle.

**Code:**
```python
def get_red(self):
    if self.safe_check(250):
        return self.sense.red.pop_head()
    else:
        return 0

def get_ir(self):
    if self.safe_check(250):
        return self.sense.IR.pop_head()
    ...
```
Two separate problems compound here:
1. **`available()` only reports `len(self.sense.red)`** (`max30102/__init__.py:672-674`) — in
   `led_mode=1` (Red-only HR mode), `self.sense.IR` is never populated at all (per `check()`'s
   `if self._active_leds > 1:` gate, `max30102/__init__.py:760-763`), so `get_ir()` would poll
   forever against a channel `check()` never fills.
2. Even in `led_mode=2`, `get_red()` and `get_ir()` are two **independent** calls, each running its
   own `safe_check(250)` (which itself calls `check()`, which pulls from the *device*), and each
   popping from its *own* buffer via `pop_head()` — which per the README's documented deviation
   "return[s] the newest sample and discard[s] the stale backlog behind it." Two sequential,
   independent calls to a function that "return newest, discard the rest" on two *different*
   buffers gives no guarantee whatsoever that the RED value returned by `get_red()` and the IR
   value returned by `get_ir()` came from the same device sample index — each call can trigger its
   own intervening `check()`, advancing both buffers by different, uncorrelated amounts.

**Failure scenario:** any code that calls `get_red()` then `get_ir()` (or vice versa) expecting a
matched pair — which is exactly what SpO₂'s ratio math requires (A6 depends on `red_ac`/`red_dc`
and `ir_ac`/`ir_dc` referring to the *same* underlying samples) — gets two values from
*independently-advanced* buffers. This method pair is unusable for SpO₂ or any RED/IR-correlated
purpose by construction, not by misuse.

**Mitigating factor:** this appears to be recognized in practice — both shipped examples
(`heart_rate.py`, `spo2.py`) correctly avoid `get_red()`/`get_ir()` entirely and use
`check()` + `pop_red_from_storage()`/`pop_ir_from_storage()` instead, which *does* preserve
pairing (both pop from buffers advanced together by the same `check()` call, one index at a time).
The bug is real and unfixed, just not currently exercised by the driver's own example code.

**Spec:** deviates from the datasheet's fundamental sample model (RED+IR as an inseparable pair,
p.15 Fig. 2). **Parity:** port-introduced in *consequence* — `get_red()`/`get_ir()`'s reliance on
`pop_head()` inherits this port's own D9-adjacent fix to `pop_head()` (see README's documented
deviation #1); upstream's *original* `pop_head()` is outright broken (raises `AttributeError`/
`IndexError`), so upstream's `get_red()`/`get_ir()` don't silently mis-pair — they crash instead.
This port made them *callable* without also making them *correct* for paired use.

**Reference fix:** either document these two methods as single-channel-only (HR mode, `led_mode=1`,
where pairing is moot) and steer SpO₂ users exclusively toward
`check()`+`pop_red_from_storage()`/`pop_ir_from_storage()`, or implement a genuinely paired
accessor:
```python
def get_red_ir_pair(self):
    """Returns (red, ir) from the same device sample index, or None if none available."""
    if self.safe_check(250) or self.available():
        if len(self.sense.red) and len(self.sense.IR):
            return self.sense.red.pop(), self.sense.IR.pop()
    return None
```

### A12 — Idle time before the first poll exceeds the FIFO's un-polled horizon [Low]

**Code:** both `heart_rate.py` and `spo2.py` call `sensor.setup_sensor()` then `time.sleep(1)` (in
`heart_rate.py`, twice — `heart_rate.py:128,134`) or `time.sleep(1)` (`spo2.py:74`) *before*
entering their polling loop, with no `clear_fifo()` call immediately beforehand. `setup_sensor()`
does call `clear_fifo()` internally as its last step, but the LED amplitude/mode configuration and
the ~1-2s of sleep all happen *after* that clear, during which the sensor (already sampling at
~50 Hz per the defaults) fills its 32-sample FIFO in 640 ms and — with rollover enabled by
`setup_sensor()` per D8 — begins overwriting it.

**Impact:** the very first batch of samples `check()` sees at loop start is not a clean start-of-
acquisition set but whatever's left in a FIFO that's already wrapped one or more times, aggravating
D9's eviction on the very first read.

**Corrected approach:** call `sensor.clear_fifo()` immediately before entering the polling loop, or
move the setup sleeps to before `setup_sensor()`'s internal `clear_fifo()` runs (i.e., don't sleep
after clearing).

---

## Why the existing test suite doesn't catch any of this

`tests/device_sim.py` says so itself: "This is not a datasheet-accurate simulation of the physical
sensor -- it exists purely to give the ported driver and the unmodified upstream MicroPython driver
something to talk to, so their I2C transaction logs can be compared byte-for-byte." The suite's
central claim (`test_equivalence.py::test_transaction_logs_are_byte_identical`) is a strong,
valuable, and correctly-proven property — but it is orthogonal to spec conformance by construction:
if upstream has a bug (D3, D4, D5, D9's severity, A11's mis-pairing potential all trace back to
upstream), byte-identical transaction logs *prove* this port reproduces that bug precisely, not
that the bug doesn't exist.

Of the unit tests in `tests/test_driver.py`, exactly one encodes a fact from the datasheet rather
than a fact about the code's own internal consistency:
`test_fifo_bytes_to_int_uses_pulse_width_shift` (and its sibling
`test_fifo_bytes_to_int_matches_manual_unpack`) — these are the closest thing in the repo to a
conformance test today, and C18 above independently re-derives the same shift from Table 1 as a
gut-check.

A conformance suite worth adding — sketched, not built as part of this audit per its scope (a
report only) — would assert things like:
- `fifo_bytes_to_int` against Table 1's shift for all four documented resolutions (extends the
  existing test's coverage with the explicit `18 - resolution_bits` derivation shown in C18).
- The die-temperature sign-extension boundary (`0x7F` → `+127`, `0x80` → `-128`, `0xFF` → `-1`)
  directly against Table 10 (currently untested — D2 is a real, live gap in the suite, not just
  the driver).
- `set_sample_rate`/`set_pulse_width` combinations against Tables 11/12's legal-combination grid
  (D6) — currently there is no cross-parameter validation to test at all.
- `STORAGE_QUEUE_SIZE`'s eviction behavior under a full 32-sample burst (the exact scenario
  reproduced ad hoc for D9 above) as a first-class, named test rather than an audit-time script.

---

## Appendix: datasheet page index used in this audit

| Topic | Page(s) | Table(s) |
|---|---|---|
| General description, functional diagram | 1, 9 | — |
| Electrical characteristics (temperature sensor spec) | 3–5 | — |
| Register map | 10–11 | — |
| Interrupt status/enable | 12–13 | — |
| FIFO registers, structure, pseudo-code | 13–17 | 1, 2 |
| FIFO configuration (SMP_AVE, rollover, A_FULL) | 17 | 3 |
| Mode configuration | 18 | 4 |
| SpO₂ configuration (ADC range, sample rate, pulse width) | 18–19 | 5, 6, 7 |
| LED pulse amplitude | 20 | 8 |
| Multi-LED mode control | 21 | 9 |
| Temperature data registers | 22 | 10 |
| Sample-rate/pulse-width legality per mode | 23 | 11, 12 |
| SpO₂ temperature compensation | 9, 24 | 13 |
| Slot/channel timing | 25 | 14 |
| SpO₂/HR mode timing diagrams and event sequences | 26–27 | 15, 16 |
| I²C slave address, ACK, write format | 29 | 17 |
| I²C read format (repeated-START requirement) | 30 | — |
