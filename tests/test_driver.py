"""Unit tests for the ported driver: buffer semantics, decoding, validation,
bus-ownership, and the parts of the API (get_red/get_ir/get_green) that are
excluded from the upstream differential comparison because upstream's
implementation of them is broken (see circular_buffer.py).
"""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from device_sim import DeviceSim  # noqa: E402
from fake_bus import FakeSMBus  # noqa: E402

import max30102 as mod  # noqa: E402
from max30102.circular_buffer import CircularBuffer  # noqa: E402


# --------------------------------------------------------------------------
# CircularBuffer.pop_head()
# --------------------------------------------------------------------------

def test_pop_head_empty_returns_zero():
    buf = CircularBuffer(4)
    assert buf.pop_head() == 0


def test_pop_head_single_item():
    buf = CircularBuffer(4)
    buf.append(42)
    assert buf.pop_head() == 42
    assert len(buf) == 0


def test_pop_head_returns_newest_and_discards_older():
    buf = CircularBuffer(4)
    buf.append(1)
    buf.append(2)
    buf.append(3)
    assert buf.pop_head() == 3
    assert len(buf) == 0


def test_append_evicts_oldest_past_max_size():
    buf = CircularBuffer(2)
    buf.append(1)
    buf.append(2)
    buf.append(3)  # 1 should be evicted
    assert len(buf) == 2
    assert buf.pop() == 2
    assert buf.pop() == 3


# --------------------------------------------------------------------------
# fifo_bytes_to_int
# --------------------------------------------------------------------------

def _make_sensor():
    device = DeviceSim()
    bus = FakeSMBus(device)
    return mod.MAX30102(i2c=bus), device, bus


@pytest.mark.parametrize("pulse_width,code", [(69, 0), (118, 1), (215, 2), (411, 3)])
def test_fifo_bytes_to_int_uses_pulse_width_shift(pulse_width, code):
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(pulse_width=pulse_width)
    assert sensor._pulse_width == code

    raw = b"\xFF\xFF\xFF"  # 24 bits set
    expected = (0xFFFFFF & 0x3FFFF) >> (3 - code)
    assert sensor.fifo_bytes_to_int(raw) == expected


def test_fifo_bytes_to_int_matches_manual_unpack():
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(pulse_width=411)
    raw = bytes([0x01, 0x23, 0x45])
    value = struct.unpack(">i", b"\x00" + raw)[0]
    expected = (value & 0x3FFFF) >> (3 - sensor._pulse_width)
    assert sensor.fifo_bytes_to_int(raw) == expected


# --------------------------------------------------------------------------
# get_red / get_ir / get_green (excluded from the upstream differential
# comparison -- upstream's pop_head() is broken; this port fixes it)
# --------------------------------------------------------------------------

def _stage_samples(device, samples):
    """Point write_ptr/read_ptr at `len(samples)` fresh, unread samples and
    queue their raw bytes -- as if the sensor had just filled its FIFO."""
    device.set_read_ptr(0)
    device.set_write_ptr(len(samples))
    for sample in samples:
        device.push_fifo_bytes(bytes(sample))


def test_get_red_returns_newest_sample_and_drains_backlog():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2, pulse_width=411)

    _stage_samples(device, [
        (0x00, 0x00, 0x10, 0x00, 0x00, 0x20),  # red=0x000010 (stale)
        (0x00, 0x00, 0x30, 0x00, 0x00, 0x40),  # red=0x000030 (newest)
    ])

    assert sensor.get_red() == 0x30
    # pop_head() discards the stale backlog behind the newest sample.
    assert len(sensor.sense.red) == 0


def test_get_ir_returns_newest_sample_and_drains_backlog():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2, pulse_width=411)

    _stage_samples(device, [
        (0x00, 0x00, 0x10, 0x00, 0x00, 0x20),  # ir=0x000020 (stale)
        (0x00, 0x00, 0x30, 0x00, 0x00, 0x40),  # ir=0x000040 (newest)
    ])

    assert sensor.get_ir() == 0x40
    assert len(sensor.sense.IR) == 0


def test_get_green_returns_newest_sample_and_drains_backlog():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=3, pulse_width=411)

    _stage_samples(device, [
        (0, 0, 1, 0, 0, 2, 0, 0, 0x10),  # green=0x10 (stale)
        (0, 0, 3, 0, 0, 4, 0, 0, 0x20),  # green=0x20 (newest)
    ])

    assert sensor.get_green() == 0x20
    assert len(sensor.sense.green) == 0


def test_get_red_returns_zero_on_timeout():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    # No data queued, and write_ptr == read_ptr (both 0): check() reports
    # nothing available immediately, so safe_check() should time out and
    # get_red() should return 0. (250ms real wait -- acceptable for a
    # single test.)
    assert sensor.get_red() == 0


# --------------------------------------------------------------------------
# Validation (ValueError paths)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,bad_value", [
    ("set_led_mode", 4),
    ("set_adc_range", 999),
    ("set_sample_rate", 999),
    ("set_pulse_width", 999),
    ("set_fifo_average", 999),
])
def test_invalid_config_values_raise(method, bad_value):
    sensor, _, _ = _make_sensor()
    with pytest.raises(ValueError):
        getattr(sensor, method)(bad_value)


def test_invalid_slot_number_raises():
    sensor, _, _ = _make_sensor()
    with pytest.raises(ValueError):
        sensor.enable_slot(5, 0x01)


# --------------------------------------------------------------------------
# Bus wiring: i2c= (injected) vs bus= (owned)
# --------------------------------------------------------------------------

class _FakeOwnedBus:
    """Stand-in for smbus2.SMBus(bus_number), used to test bus= ownership
    without touching a real /dev/i2c-N device node."""

    def __init__(self, bus_number):
        self.bus_number = bus_number
        self.closed = False

    def i2c_rdwr(self, *msgs):
        pass

    def close(self):
        self.closed = True


def test_requires_exactly_one_of_i2c_or_bus():
    with pytest.raises(ValueError):
        mod.MAX30102()
    device = DeviceSim()
    with pytest.raises(ValueError):
        mod.MAX30102(i2c=FakeSMBus(device), bus=1)


def test_bus_number_opens_and_owns_the_bus(monkeypatch):
    monkeypatch.setattr(mod, "SMBus", _FakeOwnedBus)
    sensor = mod.MAX30102(bus=3)
    assert isinstance(sensor._i2c, _FakeOwnedBus)
    assert sensor._i2c.bus_number == 3
    assert sensor._owns_i2c is True

    sensor.close()
    assert sensor._i2c.closed is True


def test_injected_i2c_is_not_closed_by_driver():
    device = DeviceSim()
    bus = FakeSMBus(device)
    sensor = mod.MAX30102(i2c=bus)
    assert sensor._owns_i2c is False

    sensor.close()
    assert bus._closed is False


def test_context_manager_closes_owned_bus(monkeypatch):
    monkeypatch.setattr(mod, "SMBus", _FakeOwnedBus)
    with mod.MAX30102(bus=1) as sensor:
        pass
    assert sensor._i2c.closed is True


# --------------------------------------------------------------------------
# Acquisition frequency bookkeeping
# --------------------------------------------------------------------------

def test_acquisition_frequency_bookkeeping():
    sensor, _, _ = _make_sensor()
    sensor.set_sample_rate(400)
    sensor.set_fifo_average(8)
    assert sensor.get_acquisition_frequency() == 50.0
    assert sensor._acq_frequency_inv == 20


def test_acquisition_frequency_unset_until_both_known():
    sensor, _, _ = _make_sensor()
    assert sensor.get_acquisition_frequency() is None
    sensor.set_sample_rate(400)
    assert sensor.get_acquisition_frequency() is None
    sensor.set_fifo_average(8)
    assert sensor.get_acquisition_frequency() == 50.0


# --------------------------------------------------------------------------
# scan() helper
# --------------------------------------------------------------------------

def test_scan_finds_device_and_skips_others():
    device = DeviceSim()
    bus = FakeSMBus(device)

    class _RaisingOnOthers(FakeSMBus):
        def i2c_rdwr(self, *msgs):
            for msg in msgs:
                if msg.addr != 0x57:
                    raise OSError(121, "Remote I/O error")
            return super().i2c_rdwr(*msgs)

    bus2 = _RaisingOnOthers(device)
    found = mod.scan(bus2, start=0x50, end=0x60)
    assert found == [0x57]
