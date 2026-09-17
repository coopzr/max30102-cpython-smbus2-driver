# SPEC_AUDIT triage — is each fix worth making?

**Question answered here:** for every finding in `SPEC_AUDIT.md`, does fixing it *materially* improve
measurement accuracy or driver usability? Upstream-parity cost is deliberately ignored; the only
question is whether the fix changes what a user of this driver would actually observe.

**Basis:** each finding was re-checked against the driver source, the upstream MicroPython source,
and the datasheet text (extracted from `MAX30102.pdf`) before a verdict was assigned. Where the
audit's description of a mechanism turned out to be inaccurate, that is noted under the finding,
because it changes the severity even when the conclusion survives. Still no hardware: anything that
depends on real-silicon timing is labeled as such.

**Scale used:**

- **High** — the fix changes the printed number, or turns a silent failure into a visible one, for
  the shipped examples or the documented usage loop.
- **Medium** — real improvement, but only for non-default configurations or for a specific misuse
  the API invites.
- **Low** — correct in principle, but the practical effect is small or the trigger is rare.
- **None** — no observable effect on accuracy or usability.

Recommendations use **Fix**, **Fix (hygiene)** (cheap, correct, not important), **Document**,
**Leave**, **No action**.

## Summary

Findings are listed in the order they appear in the audit body.

| ID | Finding | Materiality | Recommendation | Effort |
|---|---|---|---|---|
| D1 | STOP+START instead of repeated START | None | Leave | — |
| D2 | Temperature read as unsigned | Low (accuracy) | Fix (hygiene) | 2 lines |
| D3 | DIE_TEMP_RDY poll inverted | Low (usability) | Fix (hygiene) | ~8 lines |
| D4 | Writes to 0x0E / 0x10 / 0x30 | None | Leave (or drop for hygiene) | — |
| D5 | `led_mode=3` desyncs FIFO | Low (usability) | Document + warn | ~5 lines |
| D6 | No rate/pulse-width validation | Medium (usability) | Fix | ~25 lines |
| D7 | OVF_COUNTER never read | Low (usability) | Fix (hygiene): additive accessor, and have `check()` return the count | ~6 lines |
| D8 | FIFO-full vs FIFO-empty ambiguity | Low | Document; no code | — |
| D9 | 4-deep storage buffer | **High** (accuracy + usability) | **Fix** | 1 constant |
| D10 | RED/IR comments swapped | Cosmetic | Fix (hygiene) | 2 comments |
| D11 | `_pulse_width` stale after `soft_reset()` | Low (usability) | Fix (hygiene) | ~6 lines |
| D12 | "Current draw" comment | Cosmetic | Fix (hygiene) | 1 comment |
| A1 | Drain-time timestamps | **High** once D9 is fixed | **Fix** (with D9) | ~10 lines |
| A2 | No refractory period | Medium–High (accuracy) | **Fix** | ~4 lines |
| A3 | No DC removal before thresholding | Medium–High (accuracy) | **Fix** | ~10 lines |
| A4 | Intervals span dropped samples | — (consequence of D9/A1) | No separate action | — |
| A6 | R is scale-invariant (positive) | — | No action | — |
| A7 | AN6409 quadratic correct (positive) | — | No action | — |
| A5 | Peak-to-peak used as AC | Medium (accuracy) | Fix (with A3) | ~15 lines |
| A9 | No signal-quality gate | **High** (usability) | **Fix** | ~6 lines + hardware tuning |
| A8 | No temperature compensation | None (not achievable) | Leave | — |
| A10 | Window cleared, not slid | Low–Medium (usability) | Fix (hygiene) | ~5 lines |
| A11 | `get_red()`/`get_ir()` not a pair | Medium (usability) | Document; optionally add a paired accessor | docstrings + ~8 lines |
| A12 | Idle before first poll | None | Leave (or one `clear_fifo()` call) | 1 line |

**The short version.** Six changes matter: D9, A1, A2, A3, A5, A9. Together they are under 100
lines, touch no I2C traffic except one constant, and are the difference between "prints numbers"
and "prints numbers that track the finger." D9 and A1 must be done together (see A1 for why
fixing D9 alone can introduce a crash). Everything else is either free hygiene or not worth doing.

---

## D1 — Register reads use STOP+START, not repeated START

**Verified:** yes. Datasheet p.30 shows a single transaction with a repeated START; the driver
issues two `i2c_rdwr()` calls.

**Materiality: None.** The MAX30102 keeps its register pointer across a STOP, so the two-transaction
read returns identical bytes. The only scenario where it differs is a multi-master bus interleaving
between the two halves, which does not describe a Raspberry Pi with a breakout board. There is no
accuracy effect, and the usability effect (two `ioctl` calls instead of one) is unmeasurable at 50
samples per second.

**Recommendation: Leave.** If parity ever stops being a goal, the one-line combined-transaction
form is cleaner and matches the datasheet, but there is nothing to gain today.

## D2 — Die temperature integer read as unsigned

**Verified:** yes. Table 10 (p.22) is explicit that TINT is two's complement.

**Materiality: Low (accuracy).** The bug is real and the failure is total for the affected range
(−10 °C reads as 246 °C). But the affected range is *die* temperature below 0 °C, and the die runs
above ambient because the LEDs heat it (Table 13 gives +2 °C to +8 °C rise at 50 mA). A hobbyist on
a desk will never see it. Someone logging temperature outdoors in winter will, and the value they
get is not merely off but absurd.

**Recommendation: Fix (hygiene).** Two lines, no wire change, removes a documented-range failure.
Also mask TFRAC to its 4 defined bits (`& 0x0F`) while there. This is a prerequisite for A8, but A8
is not recommended, so the prerequisite argument carries no weight; fix it because it's free.

## D3 — DIE_TEMP_RDY poll condition is inverted

**Verified:** yes, and the audit's explanation of why the loop can never work is confirmed: p.12
says the flag is cleared by reading Interrupt Status 2, so the loop destroys the thing it polls.
Upstream has the identical loop. The SparkFun original polls for the bit to be *set* with a 100 ms
timeout; upstream inverted it and dropped the timeout.

**Materiality: Low (usability).** The function returns correct values today because the blind
100 ms sleep exceeds the 29 ms conversion time. The cost of the bug is a 100 ms block per call
instead of ~30 ms. That matters only if temperature is read inside the acquisition loop, where a
100 ms block is five samples' worth of FIFO fill (harmless with a 32-deep host buffer, one lost
sample with the current 4-deep one, which is another argument for D9).

**Recommendation: Fix (hygiene).** Do not use the audit's reference fix as written: polling
INT_STAT_2 depends on the interrupt-enable state and still races with the read-clears semantics.
Poll the `TEMP_EN` bit in register 0x21 instead, which p.22 says self-clears when the conversion
completes, and add a timeout. That is what SparkFun's commented-out "original way" did and it
needs no interrupt enable. Either way, delete the dead loop and fix the comment so the next reader
isn't misled.

## D4 — Writes to registers 0x0E, 0x10, 0x30

**Verified:** yes. The register map (pp.10–11) lists 0x0E as RESERVED, and neither 0x10 nor 0x30
appears at all.

**Materiality: None.** These writes are inherited from the SparkFun family library, which has been
run against MAX30102 parts by a very large number of users for years without reported side effects.
The MAX30102 and MAX30105 share a die family; the registers are almost certainly present and
simply unconnected on this part. There is no observable accuracy or usability consequence.

**Recommendation: Leave.** Dropping the two writes from `setup_sensor()` would be tidier and saves
two transactions at startup, but that is hygiene, not improvement. If it is done, note that the
equivalence-test script exercises these writes explicitly and would need updating.

## D5 — `led_mode=3` desyncs the FIFO on the MAX30102

**Verified:** yes. Table 9 (p.21) maps SLOT setting `011` to "None", and p.21 states a sample
comprises only the *active* slots, so the sample is 6 bytes while the driver reads 9. Page 15 adds
that the read pointer advances after the first byte of each sample, so a 9-byte read consumes two
FIFO slots and the loop over `number_of_samples` then reads past the write pointer. Exact
consequence (interleaved garbage vs. every-other-sample loss) is **unverifiable without a sensor**,
but neither outcome is usable.

**Materiality: Low (usability).** Nobody with a MAX30102 has a reason to pass `led_mode=3`; the
examples don't; and the code comment already says the third mode is MAX30105-only. The
population that hits this is people who read the API, see "3", and try it. For them the failure is
silent, which is the worst kind, but they are few.

**Recommendation: Document + warn.** A prominent docstring on `setup_sensor()`/`set_led_mode()`
plus a `warnings.warn` when mode 3 is selected. A hard `ValueError` (the audit's fix) is also
defensible, but it removes green-channel support for MAX30105 owners, whom the examples explicitly
claim to support and whom the part ID cannot distinguish. Not worth a hard break for a rarely-hit
path.

## D6 — No validation of (sample rate, pulse width, mode)

**Verified:** yes, with one correction to the audit. Page 19 says an illegal rate is clamped
*when the sample-rate register is written*, evaluated against the pulse width in the register at
that moment. `setup_sensor()` writes the rate first, while the post-reset pulse width is 69 µs, at
which **every** rate is legal, so no clamp fires. The illegal pair enters the register via the
*later* pulse-width write, and the datasheet does not say what the chip does then. The audit's
"clamps to the highest legal rate at 69 µs" narrative is therefore backwards, but the conclusion
stands: the driver's idea of the sample rate can silently diverge from what the chip runs at.

**Materiality: Medium (usability).** Zero for the defaults (400 sps at 411 µs is legal per Table
11). Real for anyone who tunes: 800 sps at 411 µs, 1000 sps at 215 µs, 1600 sps at anything but
69 µs, and 3200 sps in SpO₂ mode at all are individually-valid arguments that produce an illegal
pair. Every downstream time base (A1, the SpO₂ window size, `get_acquisition_frequency()`) is then
wrong with no error. Turning that into an immediate `ValueError` is a genuine usability gain for
the tuning population.

**Recommendation: Fix.** Three parts: (1) a mode-aware legality table from Tables 11 and 12
(HR mode allows more than SpO₂ mode; the audit's table is SpO₂-only); (2) write pulse width
*before* sample rate in `setup_sensor()` so the chip's own clamp sees the intended pulse width;
(3) optionally read back register 0x0A after the writes and compare, which catches the clamp
directly and costs one read at setup. Part 2 changes the transaction order, which matters for
parity but not for this triage.

## D7 — OVF_COUNTER cleared but never read

**Verified:** yes. Note p.13 defines OVF_COUNTER for the FIFO-full-and-not-rolling case; whether it
also increments with `FIFO_ROLLOVER_EN` set (the driver's default) is not stated.
**Unverifiable without a sensor.**

**Materiality: Low (usability).** It is a diagnostic, not a correction. Its value is letting a user
on real hardware confirm "my polling loop keeps up" without guessing.

**Recommendation: Fix (hygiene), two small additive changes.** Add `get_overflow_count()` as the
audit suggests. More useful and equally cheap: make `check()` return the number of samples it
read instead of `True`. Truthiness is preserved (0 is falsy, N is truthy), so no caller changes,
and the caller can now see backlog size directly, which is a better polling-cadence signal than
OVF_COUNTER and needs no extra I2C traffic.

## D8 — FIFO-full indistinguishable from FIFO-empty

**Verified:** yes, the pointer-difference ambiguity is inherent to the scheme (p.16/p.17).
Working through the rollover case: after the write pointer laps the read pointer, the next poll
reads only the newest sample and the 31 behind it are abandoned, but the stream *resynchronises*
on the next sample. It is data loss, not corruption, and it only occurs when the host has been
away ≥ 640 ms at the defaults, in which case the host has already lost data by any measure.

**Materiality: Low.** With the 4-deep buffer (D9) the driver discards most of that backlog anyway.
With a 32-deep buffer the loss becomes visible as an anomalously long inter-peak interval, which
A1's index-based timing makes detectable. The audit's reference fix relies on OVF_COUNTER
behaviour in rollover mode that the datasheet does not specify, so it cannot be recommended
without hardware.

**Recommendation: Document, no code.** State the polling horizon (32 / acquisition rate, i.e.
640 ms at the defaults) in the README. If detection is wanted later, the datasheet's intended
mechanism is the A_FULL interrupt or a lower `FIFO_A_FULL` threshold, not pointer arithmetic.

## D9 — 4-deep storage buffer vs. 32-deep device FIFO

**Verified:** yes, and reproduced: 32 samples into `deque(maxlen=4)` keeps the last 4.

**Materiality: High (accuracy and usability).** One qualification to the audit's "happens
routinely": in the shipped examples the loop is tight (no sleep), a `check()` takes a millisecond
or two, and the buffer almost never sees more than one sample per poll, so the examples mostly get
away with it. The problem is everything *except* that tight loop:

| Host poll interval | Samples per poll at 50 Hz | Kept | Lost |
|---|---|---|---|
| 20 ms or less | 1 | 1 | 0 % |
| 100 ms | 5 | 4 | 20 % |
| 200 ms | 10 | 4 | 60 % |
| 640 ms | 32 | 4 | 88 % |

A CPython user who adds `time.sleep(0.1)` to stop the loop pinning a core, which is the first
thing most people do, silently loses a fifth of their samples, non-uniformly. The entire reason the
chip has a 32-deep FIFO (p.9: so the host "is not reading continuously") is defeated by the host
buffer. The 4 came from upstream's MicroPython memory budget and has no justification on a Pi.

**Recommendation: Fix.** Set `STORAGE_QUEUE_SIZE = 32`. One constant, no wire change, and it
makes the driver correct for any polling cadence up to the device's own horizon. Do it together
with A1 (see below). A larger or unbounded buffer is not needed: `check()` cannot return more than
32 samples, and an unbounded deque would leak for callers who never drain.

## D10 — Register-map comments swap RED and IR

**Verified:** yes. Tables 8 and 9 say LED1 = Red, LED2 = IR; the comments say the opposite. The
constants and behaviour are correct.

**Materiality: Cosmetic.** No runtime effect. It does matter to exactly one reader: the person
adjusting one LED's current to balance the SpO₂ ratio, who would raise the wrong LED.

**Recommendation: Fix (hygiene).** Two comment edits.

## D11 — `_pulse_width` goes stale across `soft_reset()`

**Verified:** yes, and it is broader than the audit says: `_sample_rate`, `_sample_avg`,
`_active_leds` and `_multi_led_read_mode` all go stale too, since reset returns every register to
POR (p.18) and none of the caches are touched.

**Materiality: Low (usability).** Triggered only by calling `soft_reset()` directly and then
`check()` without reconfiguring. The symptom is a `TypeError` on `3 - None` (first use) or a
uniformly scaled value (later use). Scaling does not affect SpO₂ (A6) or peak detection, so there
is no accuracy effect; the usability effect is one confusing traceback.

**Recommendation: Fix (hygiene).** Have `soft_reset()` set every cached field to its POR
equivalent (pulse width 69 µs, sample rate 50, averaging 1, mode/LED count unset). Six lines, no
wire change, and it makes the cache honest.

## D12 — ADC-range comment calls LSB size "current draw"

**Verified:** yes; Table 5 (p.18) defines these as LSB sizes in pA at 18-bit resolution.

**Materiality: Cosmetic.** The driver never converts counts to physical units, so nothing computes
with the mislabeled number.

**Recommendation: Fix (hygiene).** Replace the comment as the audit suggests. Do not add a
counts-to-pA conversion; nothing in the pipeline needs it.

## A1 — Peaks timestamped at drain time, not at sample cadence

**Verified:** yes. Attribution correction: the upstream example does exactly the same
(`ticks_ms()` in `add_sample`), so this is inherited, not port-introduced. That does not change
the verdict.

**Materiality: High, but conditional.** In the shipped tight loop, each sample's drain-time stamp
is within a couple of milliseconds of its true time, so the BPM error is under 1 % and the
"Critical" label overstates the current effect. The materiality comes from coupling with D9:

- With the 4-deep buffer, the worst case is 4 samples sharing a timestamp. Bad but bounded.
- With a 32-deep buffer (D9 fixed) and a slow poller, 32 samples share one timestamp. Two peaks in
  the same batch give an interval of 0; if all intervals in a window are 0, `60000 /
  average_interval` raises `ZeroDivisionError` and the example crashes.

So fixing D9 alone makes A1 worse. Fixing A1 (sample index divided by acquisition rate) makes the
timing independent of host scheduling entirely, which is the only way the BPM can be trusted from
a host that does anything else. It also makes any residual loss from D8 visible as a long interval
rather than absorbed.

**Recommendation: Fix, together with D9.** Use `get_acquisition_frequency()` for the time base
rather than the example's hard-coded 400/8, so the value stays correct if the user changes the
configuration (and so D6's validation protects it).

## A2 — No refractory period in peak detection

**Verified:** yes; `find_peaks()` accepts any local maximum above the threshold.

**Materiality: Medium–High (accuracy).** A dicrotic-notch double count is the classic PPG failure
and it produces a BPM near 2× the true value, which is the kind of error a user notices and
distrusts the driver for. A 5-tap average at 50 Hz (100 ms) is too short to suppress the notch. It
also removes the zero-interval crash path described under A1 as a side effect.

**Recommendation: Fix.** Reject any peak closer than 300 ms (200 BPM) to the previously accepted
one. Four lines. Express it in samples via the acquisition rate once A1 is done.

## A3 — Moving average is not DC removal

**Verified:** yes; the 50 %-of-range threshold is computed over a 3 s window's raw min/max.

**Materiality: Medium–High (accuracy).** The pulsatile component is on the order of 1 % of DC.
Any baseline movement over 3 s larger than that (finger pressure settling, slow motion, ambient
light drift through the ALC) sets the threshold from the drift rather than the pulse, and peaks
are then missed for whole seconds at a time. This is the main reason hobbyist PPG code "works when
you hold perfectly still and not otherwise."

**Recommendation: Fix.** Subtract a slow moving average (roughly 1 s, so 50 samples at the
defaults) from each sample before the existing 5-tap smoothing, and run the threshold on that
high-passed signal. Ten lines, no dependencies. A proper 0.5–4 Hz bandpass is better still but
not required for a material improvement.

## A4 — Peak intervals silently span dropped samples

**Verified:** yes, as a consequence of D9 and D8.

**Materiality: not separately assessable.** Fixing D9 removes the routine cause; fixing A1 makes
the residual case show up as an implausibly long interval, which the refractory logic from A2 can
be extended to reject on the upper side (for example, discard intervals over 2 s).

**Recommendation: No separate action.** Covered by D9 + A1, with an optional upper bound on
accepted intervals.

## A6 — R is scale-invariant (positive finding)

**Verified:** yes, algebraically. Any per-channel constant factor cancels in AC/DC.

**Recommendation: No action.** Worth keeping in the audit because it tells a reader chasing a bad
SpO₂ number not to look at D11 or D12.

## A7 — AN6409 quadratic transcribed correctly (positive finding)

**Verified:** yes; coefficients match the widely reproduced fit.

**Recommendation: No action.** Keep the "uncalibrated approximation" caveat prominent; it is
accurate.

## A5 — Peak-to-peak window swing used as AC

**Verified:** yes; `max - min` over a 100-sample window.

**Materiality: Medium (accuracy).** Min-minus-max is the least outlier-tolerant amplitude
estimator possible: one motion spike in either channel sets that channel's "AC" for the whole
window, and since R is a ratio of the two, uncorrelated spikes swing the printed SpO₂ by many
points. Rated Medium rather than High only because the AN6409 curve is uncalibrated regardless,
so the fix improves *stability* (fewer wild readings) more than absolute accuracy.

**Recommendation: Fix, with A3.** After removing DC from each channel (same high-pass as A3),
estimate AC as the RMS of the high-passed signal, or as the 5th-to-95th percentile spread, instead
of min/max. Either is a few lines and both are far more robust than peak-to-peak. Per-beat
amplitude, as the audit suggests, is better again but requires the beat detector from the heart
rate example to be shared; not necessary for a material improvement.

## A9 — No perfusion or signal-quality gate

**Verified:** yes; the `ir_ac == 0` gate requires 100 bit-identical ADC samples and is
unreachable with a live ADC.

**Materiality: High (usability).** This is the single most visible defect to a user: with no
finger on the sensor the example prints a confident SpO₂ percentage, and the "No finger detected"
branch that the README promises never runs. Everything else in the SpO₂ pipeline is a matter of
degree; this is the driver saying something false with no signal that it is false.

**Recommendation: Fix.** Two gates, both cheap: (1) a DC floor on the IR channel (no finger means
very little reflected light; the SparkFun examples use roughly 50 000 counts at their settings),
and (2) a perfusion-index floor (`ir_ac / ir_dc` below roughly 0.2 %). Both thresholds need
tuning on real hardware (**unverifiable without a sensor**), so ship them as named constants with
that note attached. Also emit the perfusion index alongside the SpO₂ so a user can judge the
reading's quality.

## A8 — SpO₂ temperature compensation never applied

**Verified:** yes; the datasheet recommends it (p.9, p.24, Table 15) and provides Table 13 for
estimating LED temperature, but no formula relating temperature to SpO₂ error.

**Materiality: None, as a practical matter.** Compensation requires a wavelength-to-R model that
only calibration against a reference oximeter can supply, and this project has none. The effect
being corrected (a few °C of LED heating, hence a sub-nanometre-scale red wavelength shift) is
smaller than the uncorrected errors from A5 and A9 and smaller than the uncalibrated fit's own
uncertainty. Bolting on an invented correction would add noise, not accuracy.

**Recommendation: Leave.** At most, log the die temperature next to each SpO₂ estimate so that a
future calibration effort has the data. D2 must be fixed first for that to be worth anything.

## A10 — Window is cleared, not slid

**Verified:** yes; both deques are cleared, so estimates are independent 2 s blocks.

**Materiality: Low–Medium (usability).** Independent blocks are statistically fine; the cost is a
2 s refresh cadence and no ability to average across overlapping windows. The comment is simply
wrong about what the code does.

**Recommendation: Fix (hygiene).** Keep the `maxlen=100` deques, drop the `clear()` calls, and
recompute every N new samples (for example every 25, i.e. twice a second). Fix the comment.

## A11 — `get_red()` / `get_ir()` do not return a matched pair

**Verified:** the core claim is correct: each call runs its own `safe_check()`, and each pops the
newest of its own buffer, so the two values can come from different device samples. Mechanism
correction: in `led_mode=1`, `get_ir()` does not "poll forever"; `safe_check()` returns as soon as
`check()` finds any red data, and `pop_head()` on the empty IR buffer returns 0. The result is a 0
that is indistinguishable from the timeout return, which is arguably worse than a hang because it
looks like data.

**Materiality: Medium (usability).** The shipped examples avoid these methods, so nothing shipped
is wrong. But they are the most obviously named methods in the API, a new user will reach for them
first, and any SpO₂ built on them is quietly mis-paired. That is an attractive nuisance, not a
bug in current output.

**Recommendation: Document, optionally extend.** Docstrings on all three `get_*` methods stating
they return the newest single-channel value, discard the backlog, and must not be paired; point to
`check()` + `pop_*_from_storage()`. Optionally add a `read_sample()` returning `(red, ir)` popped
together after one `check()`, which gives the "just give me a reading" user a correct one-liner.

## A12 — Idle time before the first poll

**Verified:** yes; the examples sleep 1–2 s after `setup_sensor()`'s `clear_fifo()`, so the FIFO
has wrapped before the first `check()`.

**Materiality: None.** The first poll returns the most recent 32 (or, today, 4) samples and the
stream is clean from then on. There is no accuracy consequence and no visible symptom; the audit's
own "Low" label is generous.

**Recommendation: Leave.** A `clear_fifo()` immediately before the loop is a harmless one-liner
if someone wants the first batch to be a true start-of-acquisition, but nothing depends on it.

---

## Suggested order of work

1. **D9 + A1 + A2** together (buffer size, index-based timing, refractory). These are coupled:
   D9 alone can crash the heart-rate example; A1 alone changes nothing while the buffer drops
   samples.
2. **A9** (signal gate) so the SpO₂ example stops printing numbers with no finger present.
3. **A3 + A5** (DC removal, robust AC) for both examples' stability under drift.
4. **D6** (rate/pulse-width validation, write order, readback) for anyone tuning the configuration.
5. Hygiene in one pass: D2, D3, D7, D10, D11, D12, A10, A11 docstrings, D5 warning.
6. Leave D1, D4, D8 (document only), A8, A12.

None of steps 1–3 touch a single byte of I2C traffic. Step 4 changes transaction *order* at setup
and adds one read; step 5 adds no traffic except D7's opt-in accessor and D3's shorter poll.

## Corrections to the audit, for the record

These do not change any verdict above but should be fixed in `SPEC_AUDIT.md` so it does not
mislead the next reader:

- **A1** is inherited from upstream's example (`ticks_ms()` per sample), not port-introduced.
- **D6**'s ordering narrative is backwards: at the post-reset 69 µs pulse width every rate is
  legal, so the chip's write-time clamp never fires in `setup_sensor()`; the illegal pair is
  introduced by the *later* pulse-width write, whose effect the datasheet does not specify.
- **A11**'s `led_mode=1` case returns 0, it does not hang.
- **D9**'s "happens routinely under normal polling jitter" is true for any host that sleeps
  between polls and false for the shipped tight loop; the severity is right, the trigger
  description is not.
- **D11** applies to all five cached configuration fields, not just `_pulse_width`.
