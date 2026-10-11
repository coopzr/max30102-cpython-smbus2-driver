# TODO: checks that need a real MAX30102

Everything in this driver has been tested against simulated sensors only: the unit tests in `tests/`, a byte-level
model of the chip with fault injection, and offline PPG recordings for the heart rate / SpO2 estimator. These checks
can only be answered on hardware. They are ordered by how much depends on them.

The register-level snippets assume `import time` and an open sensor `s` (`s = MAX30102(bus=1)`), and the register
addresses from the datasheet (0x04 FIFO_WR_PTR, 0x05 OVF_COUNTER, 0x06 FIFO_RD_PTR, 0x07 FIFO_DATA, 0x09
MODE_CONFIG).

## 1. FIFO reader assumptions (`check()`)

- [ ] **Rewinding FIFO_RD_PTR restarts at a sample boundary.** `check()` recovers from a FIFO read that fails
  part-way by writing RD_PTR back. That only works if a pointer write also resets the chip's position *within* a
  sample. This is the one datasheet gap the driver relies on.

  ```python
  s.setup_sensor(sample_rate=100, sample_avg=1)    # 100 samples/s, SpO2 mode: 6 bytes per sample
  s.clear_fifo(); time.sleep(0.2)                  # ~20 samples waiting
  rd = s.i2c_read_burst(0x06, 1)[0]
  a = s.i2c_read_burst(0x07, 12)                   # samples k, k+1 (reference)
  s.i2c_set_register(0x06, rd)
  half = s.i2c_read_burst(0x07, 3)                 # first half of sample k
  b = s.i2c_read_burst(0x07, 6)
  s.i2c_set_register(0x06, rd)
  c = s.i2c_read_burst(0x07, 6)
  print(c == a[0:6], b == a[3:9], b == a[6:12])
  ```

  Pass: `c == a[0:6]`. `b` tells which way the chip goes when a read stops mid-sample: `a[3:9]` = byte position
  persists, `a[6:12]` = restarts at the next sample (either is fine for this driver). If `c` doesn't match, the
  rewind can't restore alignment. In that case, after a cut read, discard the FIFO (`clear_fifo()`) instead of
  rewinding.

- [ ] **What a full FIFO with rollover looks like.** The driver reads 32 samples when WR_PTR == RD_PTR and
  OVF_COUNTER > 0.

  ```python
  s.setup_sensor()                                 # 50 samples/s, rollover on
  s.clear_fifo(); time.sleep(1.0)                  # 50 samples: 18 more than fit
  p1 = s.i2c_read_burst(0x04, 3); time.sleep(0.1); p2 = s.i2c_read_burst(0x04, 3)
  print(list(p1), list(p2))                        # (WR, OVF, RD)
  print(s.check(), s.lost_samples)
  ```

  Expected: either WR == RD in both reads, both advancing ~5 between them ("model A"), or RD fixed and WR − RD small
  ("model B"). OVF should be ~18, then ~23. `check()` should return 32 under model A, or WR − RD under model B, and
  `lost_samples` should be about 23 in both. The driver handles both models; this pins down which loss figures to
  expect.

- [ ] **OVF_COUNTER is reset when a sample is read, and counts while rollover is on.** `lost_samples` adds
  OVF_COUNTER on every `check()`. If the counter doesn't reset after a read, losses are counted twice; if it doesn't
  count with rollover enabled, a full FIFO goes unnoticed. After the test above, call `s.check()` again: `lost_samples`
  must not grow, and `s.get_overflow_count()` must read 0.

- [ ] **Exactly 32 unread samples.** With 32 unread and nothing lost yet, the pointers can't tell full from empty
  (WR == RD, OVF == 0). The driver expects the next sample to overflow it and then reads it as full, losing 1. At
  50 samples/s, `clear_fifo()`, sleep 0.645 s, `check()`: expect either 0 (caught at exactly 32), or 32 with
  `lost_samples == 1`. If it was 0, `check()` again 30 ms later: expect 32 and `lost_samples == 1`.

- [ ] **Long combined reads on the Pi's I2C controller.** Each poll is one 7-byte read and one read of up to
  32 x 6 = 192 bytes, each as a single `i2c_rdwr` transaction (repeated START). Check that full 32-sample reads come
  back intact, at 100 kHz and at 400 kHz (`dtparam=i2c_arm_baudrate=400000`). On a Pi 5 the I2C controller is in
  the RP1 chip, not the BCM2835 one older Pis use. Test: 50 samples/s, `clear_fifo()`, sleep 0.6 s, `check()` returns
  ~30, with no `OSError` and no jumps in the values. Repeat for a few minutes.

- [ ] **Power-cycle detection.** `check()` treats PWR_RDY plus a zeroed MODE_CONFIG as a power-on reset and writes
  the configuration back. Run `examples/heart_rate_spo2.py`, unplug the sensor's VCC for ~1 s and reconnect it.
  Expect "I2C error, retrying" while it's off, readings resuming afterwards, and `sensor.power_resets == 1`. If
  readings don't resume, print `s.get_int_1()` and `s.i2c_read_register(0x09)` right after power-up: PWR_RDY
  should be set (bit 0) and MODE_CONFIG 0x00.

- [ ] **Bus recovery after a loose wire.** Pull SDA (then SCL) for ~1 s while an example runs. Expect `OSError`s,
  then normal readings, with `lost_samples` going up only if the outage outlasted the FIFO (1.28 s at
  25 samples/s). If the bus stays stuck (SDA held low), note the adapter: the Linux driver may need a bus recovery,
  which is outside this driver.

- [ ] **Soak test.** Run `examples/heart_rate_spo2.py` for an hour on the target Pi, with its normal other load.
  `lost_samples`, `dropped_samples` and `power_resets` should stay 0, and memory flat.

## 2. Board and sensor setup

- [ ] **LED order of this board.** `examples/heart_rate_spo2.py` has `SWAP_RED_IR = True`, chosen for a board that
  gave R ~ 2.2. Confirm it: `s.setup_sensor(led_mode=1)` pulses only LED1, which the datasheet says is red. If the
  LED that lights is visibly red, the board follows the datasheet (`SWAP_RED_IR = False`); if it is invisible to the
  eye but shows on a phone camera, it's IR and the board is swapped (`SWAP_RED_IR = True`). With the right setting, a
  healthy person at rest should give `estimate.r` around 0.4-0.7 (SpO2 ~95-99 %).

- [ ] **Actual sample rate.** The heart rate is computed from the sample count, so a sample-rate error is a heart
  rate error of the same size. Count samples over 60 s at `sample_rate=100, sample_avg=4`: expect 1500 (25.0/s).
  If it's off by more than ~1 %, pass the measured rate to `HrSpO2Estimator(sample_rate=...)`.

- [ ] **Signal level.** With a finger on the sensor at the example settings (25.4 mA, ADC range 16384 nA), print
  some raw IR/red values. IR should sit well inside the 18-bit range (roughly 30-70 % of 262143) and never at
  262143. Adjust `led_power` (`setup_sensor(led_power=...)`) if it saturates or sits very low. A saturated channel
  is flat, and the estimator returns no reading for it.

## 3. Heart rate and SpO2 on the real sensor

The estimator's figures in README.md come from a MAX30101 in a finger clip. A bare breakout board pressed by a
finger sees more pressure changes and ambient light.

- [ ] **Heart rate at rest.** Sit still for 5 minutes with a reference: a pulse oximeter, a chest strap, or beats
  counted over 60 s. Expect a reading in most seconds, nearly all within 5 BPM / 10 % of the reference. Note how
  finger pressure and ambient light change the share of "No reading". When a heart rate comes without SpO2,
  `estimate.correlation` below 0.8 means the red channel was too noisy for the ratio.

- [ ] **Heart rate changes.** Read again right after exercise (100-160 BPM) and while it falls back, seated. The
  estimator should follow the change within about 6 s (one window), with no half-rate readings (e.g. ~70 shown
  while the reference says ~140).

- [ ] **Above 180 BPM.** Readings from 180 to 220 BPM come from a search beyond RF's own range and have only been
  tested on simulated pulses. If a pulse simulator (finger phantom) is available, or a reference device during hard
  exercise, check that rates above 180 read correctly and not at about half.

- [ ] **"Heart rate below 40 BPM?"** Seated, breathing normally, it should never appear. Then, finger on the sensor,
  breathe deliberately at ~30 breaths a minute for a minute: breathing at that pace on a weak pulse signal is the
  known case that can raise it, and only simulated breathing has been tested. Note whether it appears and how
  finger pressure changes that. If a finger phantom (pulse simulator) is available, check that 33-38 BPM gives the
  message and 42 BPM and above a heart rate.

- [ ] **No finger / no pulse.** Sensor face-up on a table, then covered with paper, then pressed with a moving
  non-living object (e.g. a pencil eraser). Expect "No reading" essentially always, and never "Heart rate below
  40 BPM?".

- [ ] **SpO2 offset.** Compare the mean SpO2 over a few minutes at rest with a reference oximeter. On the offline
  dataset it read ~2 percentage points high. If this board's offset is consistent, subtract it in your
  application; a proper fix is to fit the curve's coefficients to paired readings.

## Not needed for this driver

These open datasheet questions only matter for designs this driver doesn't use:

- Whether A_FULL re-asserts on every new sample while the FIFO stays above the threshold. This only matters if you
  add INT-pin support. Even then, poll the INT *level* with a timed `check()` as a fallback, rather than relying on
  edges.
- Whether a multi-byte write starting at 0x06 auto-increments into 0x08. The driver only writes one register at a
  time.
