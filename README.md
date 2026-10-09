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

A full example is provided in `examples/basic_usage.py`. For a step-by-step walkthrough (wiring, raw readings, heart
rate, SpO2), see [USAGE.md](USAGE.md).

### 1 - Including this library into your project

Enable I2C and check that the sensor is visible on the bus (commonly bus 1, address `0x57`):

```bash
sudo raspi-config nonint do_i2c 0   # Raspberry Pi OS: enable I2C
sudo apt install -y i2c-tools
i2cdetect -y 1
```

Then install the library from this repository's root:

```bash
pip install .
```

Or install `smbus2` (`pip install smbus2`) and copy the `max30102/` directory (`__init__.py` and
`circular_buffer.py`) into your project. Then, import the constructor as follows:

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

with SMBus(1) as bus:
    sensor = MAX30102(i2c=bus)
    ...
```

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

Not every sample rate is available at every pulse width (datasheet, Tables 11 and 12). `set_sample_rate()` and
`set_pulse_width()` raise `ValueError` for a combination that is not allowed in the current LED mode. For example, 3200
samples/s is only available at a 69us pulse width, in LED mode 1.

LED mode 3 is available only with MAX30105: `set_led_mode(3)` emits a warning, because on a MAX30102 it makes `check()`
decode the FIFO incorrectly.

### 3 - Data acquisition

The sensor will store all the readings into a FIFO register (FIFO_DATA). Based on the number of active LEDs and other
configuration parameters, the sensor instance will read data from that register, putting it into the _storage_.
The _storage_ is a circular buffer, that can be read using the provided methods.

The `check()` method polls the sensor to check if new samples are available in the FIFO queue. If data is available, it
will be read and put into the _storage_. We can access those samples using the provided methods such
as `pop_red_from_storage()`.

#### Read data from sensor

As a consequence, this is an example on how the library can be used to read data from the sensor:

```python
while True:
    # The check() method has to be continuously polled, to check if
    # there are new readings into the sensor's FIFO queue. When new
    # readings are available, this function will put them into the storage.
    sensor.check()

    # Drain all queued samples — check() may add multiple per call.
    while sensor.available():
        # Access the storage FIFO and gather the readings (integers)
        red_sample = sensor.pop_red_from_storage()
        ir_sample = sensor.pop_ir_from_storage()

        # Print the acquired data (so that it can be redirected to a file or plotted)
        print(red_sample, ",", ir_sample)
```

`check()` returns the number of samples it read (0 if there was no new data).

To get a single RED/IR pair taken from the same reading, use `read_sample()`. It returns `None` if no new data arrives
within 250ms:

```python
sample = sensor.read_sample()
if sample is not None:
    red_sample, ir_sample = sample
```

`get_red()`, `get_ir()` and `get_green()` return the newest sample of a single channel (or 0 after a 250ms timeout).
Each of them polls the sensor on its own, so don't combine them to build RED/IR pairs.

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

If the host does not read the sensor fast enough, the sensor's FIFO fills up and samples are lost.
`get_overflow_count()` returns how many samples were lost (up to 31). The sensor resets this count whenever a sample is
read, so call it before `check()`:

```python
lost_samples = sensor.get_overflow_count()
sensor.check()
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

## Other useful things and troubleshooting

### Sensor clones

There is an issue involving chinese clones of the Maxim MAX30102: some of them appear to have the red and IR registers
inverted (or maybe the LEDs swapped) (see [here](https://github.com/aromring/MAX30102_by_RF/issues/13)). You can easily
check if your sensor is inverted by putting it in LED mode 1: only the red LED should work. If you see the IR LED (use
your phone camera to check), pass `swap_red_ir=True` so that "red" and "IR" in this library refer to the physical LEDs:

```python
sensor = MAX30102(bus=1, swap_red_ir=True)
```

### Heartrate and SPO2 estimation

If you're looking for algorithms for extracting heartrate and SPO2 from your RAW data, take a
look [here](https://github.com/aromring/MAX30102_by_RF)
and [here](https://github.com/kandizzy/esp32-micropython/tree/master/PPG).

Basic examples are also available in `examples/heart_rate.py` and `examples/spo2.py`.
