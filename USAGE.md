# Using the MAX30102 driver

This guide walks through using this driver on a Raspberry Pi (or similar Linux single-board
computer), starting from "read some numbers off the sensor" and building up to a heart-rate
reading and a blood-oxygen (SpO2) estimate. No prior I2C knowledge assumed.

> **Disclaimer:** this driver and the examples in this guide are for hobbyist and educational
> use. Nothing here is a medical device or a calibrated measurement. Don't use it to make health
> decisions -- see [README.md](README.md) for the full disclaimer.

## 0. One-time setup

**Wire the sensor up.** MAX30102 breakout boards typically have 4 pins: `VCC`, `GND`, `SDA`,
`SCL`. On a Raspberry Pi's 40-pin header:

| Sensor pin | Pi pin | Pi GPIO |
| --- | --- | --- |
| VCC | Pin 1 (3.3V) | -- |
| GND | Pin 6 (GND) | -- |
| SDA | Pin 3 | GPIO 2 |
| SCL | Pin 5 | GPIO 3 |

**I2C**, in one sentence: it's the 2-wire protocol (`SDA` = data, `SCL` = clock) the sensor uses
to talk to the Pi. You don't need to understand it beyond this guide -- the driver handles all of
it for you.

**Enable I2C and install dependencies:**

```bash
sudo raspi-config nonint do_i2c 0     # enable the I2C interface (one-time)
sudo apt install -y i2c-tools
pip install smbus2
```

**Confirm the sensor is detected:**

```bash
i2cdetect -y 1
```

You should see a device at address `57` in the grid. If you instead see a `PermissionError` when
running the scripts below (rather than from `i2cdetect`, which uses `sudo` internally), add
yourself to the `i2c` group and log out/in once: `sudo usermod -aG i2c $USER`.

Then get the driver itself:

```bash
pip install .[hrspo2]   # from this project's root; [hrspo2] adds numpy + scipy for heart rate / SpO2
# or copy this project's max30102/ directory into yours
```

---

## 1. MVP: read raw sensor values

The sensor measures two things continuously: how much RED light and how much infrared (IR)
light reflects back off whatever's pressed against it (normally a fingertip). Those two raw
numbers are the foundation for everything else below.

```python
import time

from max30102 import MAX30102

with MAX30102(bus=1) as sensor:
    sensor.setup_sensor()  # load sensible defaults and start sampling
    sensor.clear_fifo()    # start the loop from an empty buffer

    while True:
        time.sleep(0.1)    # let a few readings pile up
        sensor.check()     # ask the sensor for the new readings
        while sensor.available():
            red = sensor.pop_red_from_storage()
            ir = sensor.pop_ir_from_storage()
            print(red, ir)
```

Run it (with a fingertip resting gently on the sensor) and you should see a stream of number
pairs, each in the low-to-mid thousands to hundreds-of-thousands range depending on lighting and
finger pressure. If both numbers stay near zero, no light is being detected -- check your wiring
and that a finger is actually covering the sensor.

That's the whole loop every example in this guide builds on:

1. `sensor.check()` -- pulls any new readings out of the sensor's internal buffer.
2. `sensor.available()` -- how many readings are waiting to be read.
3. `sensor.pop_red_from_storage()` / `pop_ir_from_storage()` -- take one reading each.

Why the `sleep()`: the sensor keeps up to 32 readings in its own buffer (0.64 seconds' worth with
the defaults), so there's no need to ask it constantly -- every 0.1 to 0.2 seconds is plenty and
leaves the CPU idle. Don't wait *much* longer than that, though, or the buffer fills up and the
oldest readings are lost; `sensor.lost_samples` counts any that were. That's also why the loop
starts with `clear_fifo()`: the sensor starts measuring at `setup_sensor()`, and anything slow in
between (printing, reading the temperature, a `sleep()`) would otherwise leave a full,
overflowing buffer for the first `check()`.

A fuller version of this script -- with a sensor-detection check and live acquisition-rate
reporting -- is at [`examples/basic_usage.py`](examples/basic_usage.py).

### `bus=1` vs. `i2c=...`

`MAX30102(bus=1)` is the easiest way to start: it opens I2C bus 1 (the Pi's default), and when
the `with` block ends it turns the sensor's LEDs off (shutdown) and closes the bus for you. If
you're managing the I2C connection yourself elsewhere in a bigger program, you can instead hand
the driver an already-open bus:

```python
from smbus2 import SMBus
from max30102 import MAX30102

with SMBus(1) as bus, MAX30102(i2c=bus) as sensor:
    sensor.setup_sensor()
    ...
```

The sensor's `with` block (or `sensor.close()`) turns the LEDs off but leaves your bus open.
Close the sensor before the bus, as above: once the bus is closed, the LEDs can't be turned off
and stay lit after your program exits.

---

## 2. Heart rate and blood oxygen (SpO2)

Every heartbeat pushes a small pulse of blood through your fingertip, which briefly changes how
much light gets absorbed. That shows up as a small, regular wobble on top of the RED and IR
readings. The time it takes the wobble to repeat is one heartbeat; the size of the wobble on the
RED channel compared to the IR channel tells you how much oxygen the blood carries (oxygenated
blood absorbs more infrared, deoxygenated blood more red).

The driver ships an estimator that does both, in `max30102.hr_spo2` (it needs numpy and scipy:
`pip install .[hrspo2]`). Run the ready-made example:

```bash
python examples/heart_rate_spo2.py
```

Sit down, rest a fingertip gently on the sensor and hold still. After about 6 seconds it starts
printing a heart rate and an SpO2 estimate once a second, or "No reading" when the signal isn't
clean enough to trust.

The core of it:

```python
from max30102.hr_spo2 import HrSpO2Estimator

sensor.setup_sensor(sample_rate=100, sample_avg=4, pulse_width=411)  # 25 readings/s
estimator = HrSpO2Estimator(sample_rate=sensor.get_acquisition_frequency())

# in the loop, for every reading:
estimate = estimator.add_sample(red, ir)
if estimate is not None:  # once a second, from the last 6 seconds
    print(estimate.heart_rate, estimate.spo2)  # either can be None: "no reading"
```

**How it works, briefly** (see [`max30102/hr_spo2.py`](max30102/hr_spo2.py) for the full code):

1. **Filter.** Each channel goes through a band-pass filter that keeps only 0.5-4 Hz (30-240
   BPM). That removes the slow drift from breathing and finger pressure, and fast noise.
2. **Heart rate.** It takes the last 6 seconds of IR, slides the waveform against a copy of
   itself (autocorrelation) and finds the shift at which the two line up best: that shift is one
   heartbeat. A pulse often has a second, smaller bump (the dicrotic notch); simple peak-counting
   mistakes that for an extra beat, but it doesn't change how often the whole waveform repeats.
   If the waveform doesn't repeat clearly enough, or RED and IR don't move together at all, no
   reading. Six seconds is several beats, which makes the answer steadier while you sit still;
   it also means one movement spoils several seconds of readings.
3. **SpO2.** The ratio R = (RED wobble / RED level) / (IR wobble / IR level) goes into an
   empirical curve from Maxim's application note to give a percentage. It needs RED and IR to
   rise and fall closely together (a correlation of at least 0.8), because the ratio assumes
   both channels see the same pulse. The RED pulse is weaker compared with its noise, so
   sometimes you get a heart rate with "SpO2: no reading".
4. **Slower than 40 BPM.** The heart-rate search stops at 40 BPM. A pulse also repeats every
   second beat, so below that a normal pulse whose single-beat repeat is weak could pass for
   one at half its rate. Instead, when the IR repeats 30-40 times a minute and its spectrum
   shows that rhythm is its own (a normal pulse has no energy at half its rate),
   `estimate.below_range` is True. The example prints "Heart rate below 40 BPM?" once that has
   held for 7 estimates in a row: consecutive estimates share most of their samples, and 7 spans
   two windows with no samples in common. It's a prompt to check, not a measurement, and only
   while sitting still: breathing or finger pressure changing 30-40 times a minute can look the
   same when the pulse is weak, walking can too, and a pulse below 30 BPM isn't detected at all.

The estimator only answers when it is confident, so expect "No reading" often while you find a
good finger position, and whenever you move. That's deliberate: a wrong number looks just as
believable as a right one.

> **Accuracy note:** real pulse oximeters are individually calibrated against a reference device.
> The SpO2 curve here is a generic approximation, not a calibration for your sensor: expect it to
> land a few percentage points off a real oximeter even when your finger is perfectly still, and
> treat it as a rough estimate, not a medical reading.

---

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `Sensor not found.` | Check wiring (SDA/SCL not swapped, VCC/GND connected), confirm `i2cdetect -y 1` shows `57`. |
| `PermissionError` opening the bus | Add your user to the `i2c` group (`sudo usermod -aG i2c $USER`) and log back in, or run with `sudo`. |
| `FileNotFoundError: [Errno 2] ... /dev/i2c-1` | I2C isn't enabled, or you're on a board where it's a different bus number -- try `bus=0` and check `ls /dev/i2c*`. |
| Readings stuck near zero | No finger on the sensor, or it's pressed against something opaque with no skin contact. |
| A steady heart rate, but SpO2 far too low or never shown | Some clone boards (e.g. MH-ET LIVE) have the red and IR LEDs reversed vs. the datasheet. Set `swap_red_ir` to match your board (`SWAP_RED_IR` in `examples/heart_rate_spo2.py`). To check: in LED mode 1 (`setup_sensor(led_mode=1)`) only the red LED should glow; if the one that lights is invisible to the eye but shows on a phone camera, it's IR and your board is swapped. |
| "No reading" most of the time | Sit still, use light-but-full finger contact, and keep the sensor away from direct sunlight or bright indoor lighting. |
| A heart rate, but "SpO2: no reading" in many lines | The RED pulse is too noisy to match the IR one: `estimate.correlation` is below 0.8. Adjust finger pressure, or raise `led_power`. If the correlation is above 0.8 and SpO2 is still missing, see the swapped-LED row above. |
| "Heart rate below 40 BPM?" | A rhythm of 30-40 a minute was found that isn't a normal pulse. If your pulse isn't that slow (count it for 30 seconds), the usual cause is breathing or finger pressure changing at that pace while the pulse signal is weak: breathe normally, keep still and adjust finger contact. |
| `I2C error, retrying` | A loose wire or a glitch on the bus. The examples keep going and lose no readings if it recovers within about a second. |
| `ModuleNotFoundError: No module named 'numpy'` (or `scipy`) | Heart rate / SpO2 needs them: `pip install .[hrspo2]`. |

## Where to go next

- [`README.md`](README.md) -- API reference and install details.
- [`examples/basic_usage.py`](examples/basic_usage.py) -- MVP script with sensor detection and
  acquisition-rate reporting.
- [`examples/heart_rate_spo2.py`](examples/heart_rate_spo2.py) -- full heart rate and SpO2 example.
