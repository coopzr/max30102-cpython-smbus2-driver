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
git clone https://github.com/n-elia/MAX30102-MicroPython-driver   # for reference; not used directly
# copy this project's max30102/ directory into yours, or:
pip install .   # from this project's root, if you cloned it
```

---

## 1. MVP: read raw sensor values

The sensor measures two things continuously: how much RED light and how much infrared (IR)
light reflects back off whatever's pressed against it (normally a fingertip). Those two raw
numbers are the foundation for everything else below.

```python
from max30102 import MAX30102

with MAX30102(bus=1) as sensor:
    sensor.setup_sensor()  # load sensible defaults and start sampling

    while True:
        sensor.check()  # ask the sensor if new readings are ready
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

A fuller version of this script -- with a sensor-detection check and live acquisition-rate
reporting -- is at [`examples/basic_usage.py`](examples/basic_usage.py).

### `bus=1` vs. `i2c=...`

`MAX30102(bus=1)` is the easiest way to start: it opens I2C bus 1 (the Pi's default) and closes
it for you when the `with` block ends. If you're managing the I2C connection yourself elsewhere
in a bigger program, you can instead hand the driver an already-open bus:

```python
from smbus2 import SMBus
from max30102 import MAX30102

with SMBus(1) as bus:
    sensor = MAX30102(i2c=bus)
    sensor.setup_sensor()
    ...
```

---

## 2. Heart rate

Every heartbeat pushes a small pulse of blood through your fingertip, which briefly changes how
much light gets absorbed. That shows up as a small, regular wobble on top of the IR reading --
"regular wobble" is exactly what a heart rate monitor looks for: it watches the IR signal for
repeating peaks and turns the time between peaks into beats-per-minute (BPM).

```python
from max30102 import MAX30102

with MAX30102(bus=1) as sensor:
    sensor.setup_sensor()

    ir_samples = []
    while True:
        sensor.check()
        while sensor.available():
            sensor.pop_red_from_storage()  # not needed for heart rate; discard
            ir_samples.append(sensor.pop_ir_from_storage())
            if len(ir_samples) > 100:
                ir_samples.pop(0)  # keep a rolling window of the last ~2 seconds

        # ... peak-finding and BPM math goes here (see below)
```

Finding peaks reliably (and ignoring noise) takes a bit more than a few lines, so this driver
ships a ready-to-run version at [`examples/heart_rate.py`](examples/heart_rate.py):

```bash
python examples/heart_rate.py
```

Rest a fingertip gently on the sensor and hold still; after a few seconds it will start printing
a BPM reading roughly every 2 seconds. If readings look wildly off or don't appear, press a bit
more firmly (too light a touch lets ambient light interfere) but not hard enough to cut off blood
flow (too hard flattens the pulse signal).

**How it works, briefly:** `examples/heart_rate.py` defines a `HeartRateMonitor` class that
smooths the incoming IR samples (to reduce noise), looks for local peaks above a threshold set
from the recent min/max range, and averages the time between consecutive peaks to get BPM
(`60000 / average_interval_ms`).

---

## 3. Blood oxygen (SpO2)

Blood oxygen estimation compares how RED and IR light are absorbed differently by
oxygen-rich vs. oxygen-poor blood: oxygenated blood absorbs more infrared, deoxygenated blood
absorbs more red. By comparing the pulsing ("AC") part of each channel's signal against its
steady ("DC") background level, you can compute a ratio that correlates with SpO2 percentage.

Run the ready-made example:

```bash
python examples/spo2.py
```

Rest a fingertip gently on the sensor and hold still; it will print an estimated SpO2 percentage
roughly every 2 seconds, and "No finger detected / signal too weak" when there's no usable
pulsatile signal.

**How it works, briefly** (see [`examples/spo2.py`](examples/spo2.py) for the full code):

```python
def compute_spo2(red_samples, ir_samples):
    red_dc = sum(red_samples) / len(red_samples)
    ir_dc = sum(ir_samples) / len(ir_samples)

    red_ac = max(red_samples) - min(red_samples)
    ir_ac = max(ir_samples) - min(ir_samples)

    r = (red_ac / red_dc) / (ir_ac / ir_dc)

    # Empirical curve fit (Maxim AN6409) mapping the ratio to a percentage.
    return -45.060 * r * r + 30.354 * r + 94.845
```

1. **DC** (`red_dc`, `ir_dc`): the average brightness of each channel over a short window --
   roughly, how much light is getting through overall.
2. **AC** (`red_ac`, `ir_ac`): how much each channel wobbles within that window -- the pulse.
3. **R**: the ratio of (RED AC/DC) to (IR AC/DC). This one number is what actually correlates
   with blood oxygen.
4. A curve fit through R (values from Maxim's application note, reused across most open-source
   MAX3010x projects) converts it to an approximate SpO2 percentage.

> **Accuracy note:** real pulse oximeters are individually calibrated against a reference device
> and use additional signal processing (motion rejection, ambient-light compensation, and more).
> The formula above is a widely-used *approximation*, not a calibrated one -- treat the output as
> a rough estimate, not a medical reading. Expect it to be noisy with light finger pressure,
> movement, or ambient light, and don't be surprised if it lands a few percentage points off a
> real oximeter even when your finger is perfectly still.

---

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `Sensor not found.` | Check wiring (SDA/SCL not swapped, VCC/GND connected), confirm `i2cdetect -y 1` shows `57`. |
| `PermissionError` opening the bus | Add your user to the `i2c` group (`sudo usermod -aG i2c $USER`) and log back in, or run with `sudo`. |
| `FileNotFoundError: [Errno 2] ... /dev/i2c-1` | I2C isn't enabled, or you're on a board where it's a different bus number -- try `bus=0` and check `ls /dev/i2c*`. |
| Readings stuck near zero | No finger on the sensor, or it's pressed against something opaque with no skin contact. |
| Heart rate / SpO2 look noisy or don't appear | Hold still, use light-but-full finger contact, and keep the sensor away from direct sunlight or bright indoor lighting (both add noise to the readings). |

## Where to go next

- [`README.md`](README.md) -- full API reference, install details, how this port was verified
  against the original MicroPython driver, and the (two, non-protocol) deviations from upstream.
- [`examples/basic_usage.py`](examples/basic_usage.py) -- MVP script with sensor detection and
  acquisition-rate reporting.
- [`examples/heart_rate.py`](examples/heart_rate.py) -- full heart-rate example.
- [`examples/spo2.py`](examples/spo2.py) -- full SpO2 example.
