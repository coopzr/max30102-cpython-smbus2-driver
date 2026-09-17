# max30102 (CPython + smbus2)

A driver for the Maxim MAX30102 pulse-oximetry / heart-rate sensor, for CPython on Linux
(e.g. Raspberry Pi), talking I2C through [`smbus2`](https://pypi.org/project/smbus2/).

This is a port of [n-elia/MAX30102-MicroPython-driver](https://github.com/n-elia/MAX30102-MicroPython-driver)
(v0.5.1). That project is the source of truth for the I2C wire protocol used here: every register
access in this port reproduces its exact read/write shape, byte for byte. See
[Deviations from upstream](#deviations-from-upstream) for the two places (neither on the wire)
where this port differs.

## Disclaimer

This work is not intended to be used in professional environments, and there are no guarantees on
its functionalities. Please do not rely on it for medical purposes or professional usage.

## Install

```bash
# Enable I2C on Raspberry Pi OS, if not already enabled
sudo raspi-config nonint do_i2c 0

# Confirm the sensor shows up on the bus (commonly bus 1, address 0x57)
sudo apt install -y i2c-tools
i2cdetect -y 1

pip install smbus2
```

Then copy the `max30102/` directory into your project (or install this repo with `pip install .`).

## Usage

```python
from max30102 import MAX30102, scan

# The driver can open and own the bus itself...
with MAX30102(bus=1) as sensor:
    if sensor.i2c_address not in scan(sensor.i2c):
        raise RuntimeError("Sensor not found")
    if not sensor.check_part_id():
        raise RuntimeError("Unexpected device on the bus")

    sensor.setup_sensor()  # led_mode=2, adc_range=16384, sample_rate=400,
                            # led_power=MEDIUM, sample_avg=8, pulse_width=411

    while True:
        sensor.check()
        while sensor.available():
            red = sensor.pop_red_from_storage()
            ir = sensor.pop_ir_from_storage()
            print(red, ir)
```

Or, mirroring upstream's dependency-injected style, hand it an `SMBus` you already opened
(and own the lifetime of) yourself:

```python
from smbus2 import SMBus
from max30102 import MAX30102

with SMBus(1) as bus:
    sensor = MAX30102(i2c=bus)
    sensor.setup_sensor()
    ...
```

See `examples/basic_usage.py` for a full runnable example (prints RED/IR samples and the
measured acquisition frequency), and `examples/heart_rate.py` for a simple peak-finding BPM
estimate from the IR channel.

### Data acquisition rate

As with upstream: the effective acquisition rate is `sample_rate / sample_avg` (e.g.
400 Hz / 8 = 50 Hz with the defaults), not `sample_rate` itself -- the sensor averages
`sample_avg` raw conversions on-chip before pushing one reading into its FIFO.
`sensor.get_acquisition_frequency()` reports this once both `set_sample_rate()` and
`set_fifo_average()` have run (both run inside `setup_sensor()`).

## Deviations from upstream

Two, and neither is a wire-protocol change:

1. **`CircularBuffer.pop_head()` is fixed.** Upstream's implementation is broken: it aliases
   `temp = self.data` (not a copy), calls `self.data.clear()` (a method MicroPython's `deque`
   does not document -- `AttributeError` on real hardware), and even given a working `clear()`,
   `temp` is the *same* object that was just emptied, so the final `temp.popleft()` raises
   `IndexError` for any buffer holding more than one sample. In practice this makes
   `get_red()`/`get_ir()`/`get_green()` unusable, which is why both upstream examples use
   `check()` + `pop_*_from_storage()` instead. This port implements the method's evident intent:
   return the newest sample and discard the stale backlog behind it. Pure buffer bookkeeping,
   zero I2C traffic on either side of it.

2. **Raw `i2c_rdwr()`, not `smbus2`'s block-read helpers.** Upstream issues a register read as
   two independent, STOP-terminated transactions (`machine.SoftI2C.writeto()` / `.readfrom()`
   default to `stop=True`, so there's no repeated START between the register-address write and
   the data read). `smbus2.SMBus.read_i2c_block_data()` / `read_byte_data()` would instead emit
   one combined transaction with a repeated START -- a different sequence on the wire. This port
   uses two separate `i2c_rdwr()` calls to reproduce upstream's exact shape; see the comment on
   `i2c_read_register` in `max30102/__init__.py`.

Everything else -- register map, bitmasks, setup sequence, FIFO decoding (including the pulse-width
shift quirk in `fifo_bytes_to_int`) -- is an unmodified transliteration.

## Testing (no sensor required)

This port was developed and verified without access to physical hardware. `tests/` includes:

- A deterministic simulated MAX30102 (`tests/device_sim.py`) and a recording fake `SMBus`
  (`tests/fake_bus.py`).
- **A differential test** (`tests/test_equivalence.py`) that loads the *unmodified* upstream
  driver source straight from the sibling `MAX30102-MicroPython-driver/` clone (via small
  `machine`/`ustruct`/`utime`/`ucollections` shims, `tests/upy_shims/`), drives both drivers
  through an identical scripted sequence against their own fresh simulated device, and asserts
  the two I2C transaction logs are **byte-for-byte identical**. This is the strongest available
  proof that the port introduces no protocol drift, short of an oscilloscope.
- Unit tests (`tests/test_driver.py`) for the parts excluded from that comparison (`pop_head()`
  and `get_red()`/`get_ir()`/`get_green()`, since upstream's version of them is broken), plus
  FIFO decoding, config validation, and bus-ownership semantics.

```bash
pip install smbus2 pytest
pytest tests/ -v
```

(`tests/conftest.py` stubs the `fcntl` module on Windows, which `smbus2` imports at module load
time but never actually calls in these tests -- real `ctypes`-based `i2c_msg` objects are used
throughout, so the transaction encoding under test is the real thing. On Linux this stub is
unused; the real `fcntl` is picked up normally.)

Once you have real hardware, the smoke test is:

```bash
i2cdetect -y 1        # expect a device at 0x57
python examples/basic_usage.py
```

Expect the part-ID check to pass, a plausible die temperature (roughly 20-40 degC), and a stream
of RED/IR sample pairs at about 50 Hz with the default configuration.
