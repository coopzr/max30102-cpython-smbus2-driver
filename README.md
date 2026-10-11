# Maxim MAX30102 CPython driver (smbus2)

A CPython + [`smbus2`](https://pypi.org/project/smbus2/) port of
[n-elia/MAX30102-MicroPython-driver](https://github.com/n-elia/MAX30102-MicroPython-driver), for Linux
boards such as the Raspberry Pi.

It _should_ work for MAX30105, too.

## Table of contents

- [Disclaimer](#disclaimer)
- [Branches](#branches)
- [Usage](#usage)
  - [1 - Including this library into your project](#1---including-this-library-into-your-project)
  - [2 - I2C setup and sensor configuration](#2---i2c-setup-and-sensor-configuration)
  - [3 - Data acquisition](#3---data-acquisition)
  - [4 - Heart rate and SpO2](#4---heart-rate-and-spo2)
- [Acknowledgements](#acknowledgements)
- [Other useful things and troubleshooting](#other-useful-things-and-troubleshooting)

## Disclaimer

[❗] This was entirely vibe-coded with ZERO human verification or validation. This software is provided "as is," which is
legal-speak for "I am not responsible if it breaks anything". Seriously, if this fries your sensor or your computer,
that's on you.

This work is not intended to be used in professional environments, and there are no guarantees on
its functionalities. Please do not rely on it for medical purposes or professional usage.

## Branches

- `master` (this branch) -- the development branch, with bug fixes and additions on top of the original driver.
- `vanilla` -- a like-for-like port of the original MicroPython driver, with no fixes or additions. Untested and unsupported.

## Usage

Driver usage is quite straightforward. You just need to import the library, and to give it an I2C bus.

A full example is provided in `examples/basic_usage.py`, and heart rate / SpO2 in `examples/heart_rate_spo2.py`. For a
step-by-step walkthrough (wiring, raw readings, heart rate, SpO2), see [USAGE.md](USAGE.md).

### 1 - Including this library into your project

Enable I2C and check that the sensor is visible on the bus (commonly bus 1, address `0x57`):

```bash
sudo raspi-config nonint do_i2c 0   # Raspberry Pi OS: enable I2C
sudo apt install -y i2c-tools
i2cdetect -y 1
```

Then install the library from this repository's root:

```bash
pip install .            # the driver (needs smbus2 only)
pip install .[hrspo2]    # plus heart rate / SpO2 (max30102.hr_spo2, needs numpy and scipy)
```

Or install `smbus2` (`pip install smbus2`, plus `numpy` and `scipy` for heart rate / SpO2) and copy the `max30102/`
directory into your project. Then, import the constructor as follows:

```python
from max30102 import MAX30102
```

### 2 - I2C setup and sensor configuration

#### I2C connection

Pass the I2C bus number, and the driver opens (and closes) the bus itself:

```python
from max30102 import MAX30102, scan

with MAX30102(bus=1) as sensor:
    if sensor.i2c_address not in scan(sensor.i2c):
        print("Sensor not found.")
    ...
```

Or pass an `smbus2.SMBus` instance that you manage yourself:

```python
from smbus2 import SMBus
from max30102 import MAX30102

with SMBus(1) as bus, MAX30102(i2c=bus) as sensor:
    ...
```

Leaving the sensor's `with` block (or calling `sensor.close()`) puts the sensor into shutdown, turning its LEDs off,
and then closes the bus if the driver opened it. A bus passed with `i2c=` is left open. Close the sensor before its
bus: once the bus is closed, the driver can't reach the sensor to shut it down.

On a Raspberry Pi, bus 1 is `/dev/i2c-1` on the 40-pin header (SDA = GPIO 2, SCL = GPIO 3).

#### Sensor setup

The library provides a method to setup the sensor at once. Leaving the arguments empty, makes the library load the
default values.

> **Default configuration values:**
>
> _Led mode_: 2 (RED + IR)  
> _ADC range_: 16384  
> _Sample rate_: 400 Hz  
> _Led power_: medium (25.4mA)  
> _Averaged samples_: 8  
> _Pulse width_: 411

```python
# Setup with default values
sensor.setup_sensor()

# Alternative example:
sensor.setup_sensor(led_mode=2, adc_range=16384, sample_rate=400)
```

The library provides the methods to change the configuration parameters one by one, too. Remember that
the `setup_sensor()` method has still to be called before modifying the single parameters.

```python
from max30102 import MAX30105_PULSE_AMP_MEDIUM

# Set the number of samples to be averaged by the chip
SAMPLE_AVG = 8  # Options: 1, 2, 4, 8, 16, 32
sensor.set_fifo_average(SAMPLE_AVG)

# Set the ADC range
ADC_RANGE = 4096  # Options: 2048, 4096, 8192, 16384
sensor.set_adc_range(ADC_RANGE)

# Set the sample rate
SAMPLE_RATE = 400  # Options: 50, 100, 200, 400, 800, 1000, 1600, 3200
sensor.set_sample_rate(SAMPLE_RATE)

# Set the Pulse Width
PULSE_WIDTH = 118  # Options: 69, 118, 215, 411
sensor.set_pulse_width(PULSE_WIDTH)

# Set the LED mode
LED_MODE = 2  # Options: 1 (red), 2 (red + IR), 3 (red + IR + g - MAX30105 only)
sensor.set_led_mode(LED_MODE)

# Set the LED brightness of each LED
LED_POWER = MAX30105_PULSE_AMP_MEDIUM
# Options:
# MAX30105_PULSE_AMP_LOWEST =  0x02 # 0.4mA
# MAX30105_PULSE_AMP_LOW =     0x1F # 6.2mA
# MAX30105_PULSE_AMP_MEDIUM =  0x7F # 25.4mA
# MAX30105_PULSE_AMP_HIGH =    0xFF # 51.0mA
# Any value from 0x00 to 0xFF works, in 0.2mA steps (datasheet, Table 8)
sensor.set_pulse_amplitude_red(LED_POWER)
sensor.set_pulse_amplitude_ir(LED_POWER)
sensor.set_pulse_amplitude_green(LED_POWER)  # MAX30105 only

# Set the LED brightness of all the active LEDs
sensor.set_active_leds_amplitude(LED_POWER)
```

Not every sample rate is available at every pulse width (datasheet, Tables 11 and 12). `set_sample_rate()`,
`set_pulse_width()` and `set_led_mode()` raise `ValueError` for a combination that is not allowed in the (new) LED mode.
For example, 3200 samples/s is only available at a 69us pulse width, in LED mode 1.

LED mode 3 is available only with MAX30105: `set_led_mode(3)` emits a warning, because on a MAX30102 it makes `check()`
decode the FIFO incorrectly.

### 3 - Data acquisition

The sensor stores its readings in a 32-sample FIFO. `check()` reads every new sample out of it into the driver's
_storage_, one queue per channel, and returns how many it read (0 if there was no new data). The application then
takes the samples out of storage with `pop_red_from_storage()` / `pop_ir_from_storage()`.

#### Read data from sensor

```python
import time

sensor.setup_sensor()
sensor.clear_fifo()  # start the loop from an empty FIFO

while True:
    time.sleep(0.1)  # see "Polling" below
    sensor.check()

    # Drain all queued samples: check() may add up to 32 per call
    while sensor.available():
        # Both values come from the same reading
        red_sample = sensor.pop_red_from_storage()
        ir_sample = sensor.pop_ir_from_storage()
        print(red_sample, ",", ir_sample)
```

To get a single RED/IR pair taken from the same reading, use `read_sample()`. It returns the oldest stored pair, and
polls the sensor (for up to 250ms, else returns `None`) only when storage is empty:

```python
sample = sensor.read_sample()
if sample is not None:
    red_sample, ir_sample = sample
```

`get_red()`, `get_ir()` and `get_green()` wait for new data and return the newest sample of a single channel (or 0
after a 250ms timeout), discarding older ones. Each of them polls the sensor on its own, so don't combine them to
build RED/IR pairs: for SpO2 the two values must come from the same reading.

#### Polling

The FIFO holds 32 samples, so it fills in 32 / acquisition rate seconds (0.64s at the default 50 samples/s, 1.28s at
25 samples/s). Call `check()` at least every ~1/3 of that, and sleep between calls rather than looping flat out: a
poll costs one I2C transaction, and one more per batch of samples.

- Don't `sleep()` between `setup_sensor()` and your loop, or call `clear_fifo()` just before the loop. The sensor
  starts sampling at `setup_sensor()`; a FIFO that has been full for a while starts the loop with lost samples.
- The Raspberry Pi's default I2C clock (100 kHz) is plenty up to ~100 samples/s. For higher rates, the MAX30102
  supports 400 kHz: `dtparam=i2c_arm_baudrate=400000` in `/boot/firmware/config.txt`.
- If you need lower latency than polling gives, wire the INT pin and check its *level*, with a timed `check()` as a
  fallback; never rely on catching edges alone, as one missed edge stops the stream. Note that `check()` reads (and so
  clears) the interrupt status registers.

#### Lost samples, errors and power loss

`check()` keeps counters that the application can watch:

- `sensor.lost_samples`: samples the sensor discarded because the FIFO was full when `check()` came (the loop fell
  behind). The sensor counts at most 31 between two calls, so this is a lower bound.
- `sensor.dropped_samples`: samples pushed out of storage because the application didn't pop them; storage holds
  `STORAGE_QUEUE_SIZE` (4096) samples per channel.
- `sensor.power_resets`: times the sensor was power-cycled (a brown-out or loose supply wire). `check()` notices it,
  writes the configuration back and carries on.

If the I2C bus fails (e.g. a loose wire), `check()` raises `OSError` and leaves the unread samples in the sensor, so
catching the error and calling `check()` again later loses nothing:

```python
try:
    sensor.check()
except OSError as e:
    print("I2C error, retrying:", e)
```

#### Notes on data acquisition rate

Considering the sensor configuration, two main parameters will affect the data throughput of the sensor itself:

- The *sample rate*, which is the number of RAW readings per second made by the sensor

- The *averaged samples*, which is the number of RAW readings averaged together for composing a single sample

Therefore, the FIFO_DATA register will contain averaged RAW readings. The rate at which that register is fed depends on
the two parameters: *real rate = sample rate / averaged samples* (e.g. 400 Hz / 8 = 50 Hz with the defaults).

The library computes this value, that can be accessed with:

```python
# Get the estimated acquisition rate
acquisition_rate = sensor.get_acquisition_frequency()
```

However, there are some limitations on sensor side and on host side that may affect the acquisition rate. It is
possible to measure the real throughput as
in [this](https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library/blob/72d5308df500ae1a64cc9d63e950c68c96dc78d5/examples/Example9_RateTesting/Example9_RateTesting.ino)
example sketch by SparkFun, using the following snippet:

```python
# (Assuming that the sensor instance has been already set-up)
import time

t_start = time.monotonic()  # Starting time of the acquisition
samples_n = 0  # Number of samples that have been collected

while True:
    time.sleep(0.1)
    sensor.check()
    while sensor.available():
        red_reading = sensor.pop_red_from_storage()
        ir_reading = sensor.pop_ir_from_storage()

        # Compute the real frequency at which we receive data
        if time.monotonic() - t_start >= 1.0:
            print("acquisition frequency = ", samples_n)
            samples_n = 0
            t_start = time.monotonic()
        else:
            samples_n = samples_n + 1
```

#### Die temperature reading

The `read_temperature()` method allows to read the internal die temperature. An example is proposed below.

```python
# Read the die temperature in Celsius degree
temperature_C = sensor.read_temperature()
print("Die temperature: ", temperature_C, "°C")
```

Note: as stated in the [datasheet](https://datasheets.maximintegrated.com/en/ds/MAX30102.pdf), the internal die
temperature sensor is intended for calibrating the temperature dependence of the SpO2 subsystem. It has an inherent
resolution of 0.0625°C, but be aware that the accuracy is ±1°C.

### 4 - Heart rate and SpO2

`max30102.hr_spo2.HrSpO2Estimator` turns RED/IR samples into a heart rate and an SpO2 estimate (install with
`pip install .[hrspo2]`). It is meant for someone sitting still. Configure the sensor for 25 samples/s, the rate the
estimator was validated at, and feed it every pair in order:

```python
import time

from max30102.hr_spo2 import HrSpO2Estimator

sensor.setup_sensor(sample_rate=100, sample_avg=4, pulse_width=411)  # 25 samples/s, 18-bit
estimator = HrSpO2Estimator(sample_rate=sensor.get_acquisition_frequency())
sensor.clear_fifo()

while True:
    time.sleep(0.2)
    sensor.check()
    while sensor.available():
        estimate = estimator.add_sample(sensor.pop_red_from_storage(), sensor.pop_ir_from_storage())
        if estimate is not None:  # once a second, from the last 6 s
            print(estimate.heart_rate, estimate.spo2)  # None = no reading
```

It is Robert Fraczkiewicz's autocorrelation algorithm ([MAX30102_by_RF](https://github.com/aromring/MAX30102_by_RF)).
Instead of picking peaks, it measures the period at which the IR waveform repeats, so the dicrotic notch on real
fingertip pulses isn't counted as an extra beat. Around it:

- **A 0.5-4 Hz band-pass in front of the period search.** RF only removes a straight line from each window, so
  breathing and slow finger-pressure changes would otherwise dominate the result.
- **6 s windows, one estimate per second.** A seated heart rate is steady, so a longer window averages more beats into
  each estimate, and noise rarely repeats for 6 s.
- **The period to a fraction of a sample.** At 25 samples/s whole samples are 6-7 BPM apart around 100 BPM.
- **Checks where RF would report half the heart rate** (after a fast rise, and above ~155 BPM), and a search above
  RF's 180 BPM limit, up to 220 BPM.

On the 22 sitting recordings of a real finger-PPG dataset with an ECG reference (PhysioNet Pulse Transit Time PPG, a
MAX30101 front end), it gave a heart rate 98 % of the time, and 99 % of those were within 5 BPM or 10 % of the ECG.
On simulated signals with a finger on the sensor but no pulse, it showed a heart rate 2 % of the time; with no finger,
never. It is not meant for movement: on the same dataset's walking and running recordings, only 49 % and 73 % of
seconds had a reading within tolerance. These are offline results: they have not yet been confirmed on a live
MAX30102.

Things to know when using it:

- **`None` means "no reading", and it is common.** The estimator answers only when the IR waveform clearly repeats; no
  finger, movement, uneven pressure or bright ambient light give `None`. That is where its accuracy comes from, so
  show "no reading" rather than holding the last value.
- **The first estimate comes after 6 s, then one per second.** Each covers the last 6 s, so a change in heart rate
  takes a few seconds to show fully, and one movement spoils several estimates.
- **A heart rate can come without SpO2.** The heart rate comes from IR alone. SpO2 also needs red and IR to rise and
  fall closely together (correlation >= 0.8), because the ratio of ratios assumes both channels see the same pulse.
  The red channel's pulse is weaker compared with its noise, so SpO2 is the one that drops out.
- **42-220 BPM.** Readings above 180 BPM have only been tested on simulated pulses; around 178-180 BPM it often gives
  no reading. The period search stops at 40 BPM because a pulse also repeats every second beat: when its single-beat
  repeat is weak, the autocorrelation can't tell 75 BPM from 37.
- **`estimate.below_range` for slower pulses.** With no heart rate, it is True when the IR repeats 30-40 times a
  minute and the spectrum shows that rhythm is the signal's own, not a normal pulse seen every second or third beat
  (a normal pulse has no energy at half its rate). That most likely means a pulse below 40 BPM, but breathing or
  finger pressure changing 30-40 times a minute looks the same when the pulse signal is weak, and a pulse below
  30 BPM can't be flagged at all. In an application:
  - Show it as a prompt to check ("Heart rate below 40 BPM?"), never as a number.
  - Act on it only once it has held for a whole window. Consecutive estimates share most of their samples, so wait
    until every estimate from one window to the next window with no samples in common is flagged: 7 in a row with
    the defaults (`round(window_s / update_s) + 1`). `examples/heart_rate_spo2.py` does this.
  - No flag doesn't rule out a very slow pulse.

  Offline, simulated pulses at 33-38 BPM were confirmed this way in 88-100 % of estimates (fewer with an irregular
  rhythm or strong breathing wander, 30 BPM rarely). On the sitting recordings (true rate 48-128 BPM) it was raised in
  3 of 10,499 estimates and never confirmed. It was confirmed in 74 % of estimates for simulated breathing at 30 a
  minute with no pulse, and in 7 % on the walking recordings, so it only means something while sitting still.
- **Call `estimator.reset()` after a gap in the samples** (`lost_samples` or `power_resets` went up): a window spliced
  across a gap has the wrong period.
- **SpO2 is uncalibrated.** It uses Maxim's generic curve; on the reference dataset it read about 2 percentage
  points high. Absolute SpO2 needs calibration against a reference oximeter on your hardware.
- Each `Estimate` also carries `r` (the ratio of ratios) and `correlation` (red/IR), handy for tuning finger
  placement and LED current.

See `examples/heart_rate_spo2.py` for a complete program.

## Acknowledgements

This work is a lot based on:

- [MAX30102-MicroPython-driver](https://github.com/n-elia/MAX30102-MicroPython-driver "GitHub | MAX30102-MicroPython-driver")

  The MicroPython driver by **n-elia**, which this library is ported from.

- [SparkFun MAX3010x Sensor Library](https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library "GitHub | SparkFun MAX3010x Sensor Library")

  Written by **Peter Jansen** and **Nathan Seidle** (SparkFun)
  This is a library written for the Maxim MAX30105 Optical Smoke Detector
  It should also work with the MAX30102. However, the MAX30102 does not have a Green LED.
  These sensors use I2C to communicate, as well as a single (optional)
  interrupt line that is not currently supported in this driver.
  Written by Peter Jansen and Nathan Seidle (SparkFun)
  BSD license, all text above must be included in any redistribution.

- [esp32-micropython](https://github.com/kandizzy/esp32-micropython/blob/master/PPG/ppg/MAX30105.py "GitHub | esp32-micropython")

  A port of the library to MicroPython by **kandizzy**

- [MAX30102_by_RF](https://github.com/aromring/MAX30102_by_RF "GitHub | MAX30102_by_RF")

  The heart rate / SpO2 algorithm in `max30102/hr_spo2.py`, by **Robert Fraczkiewicz** (see
  [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md))

## Other useful things and troubleshooting

### Sensor clones

There is an issue involving chinese clones of the Maxim MAX30102: some of them appear to have the red and IR registers
inverted (or maybe the LEDs swapped) (see [here](https://github.com/aromring/MAX30102_by_RF/issues/13)). The usual
symptom is a steady heart rate with an SpO2 far too low or missing. You can easily check if your sensor is inverted by
putting it in LED mode 1: only the red LED should work. If you see the IR LED (use your phone camera to check), pass
`swap_red_ir=True` so that "red" and "IR" in this library refer to the physical LEDs:

```python
sensor = MAX30102(bus=1, swap_red_ir=True)
```

### Heartrate and SPO2 estimation

See [4 - Heart rate and SpO2](#4---heart-rate-and-spo2) and `examples/heart_rate_spo2.py`.
