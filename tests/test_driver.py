"""Unit tests: buffer semantics, FIFO decoding, get_red/get_ir/get_green,
config validation, bus ownership, and scan(); plus plausible-use scenarios
on the mock hardware.
"""
import gc
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

def _make_sensor(**kwargs):
    device = DeviceSim()
    bus = FakeSMBus(device)
    return mod.MAX30102(i2c=bus, **kwargs), device, bus


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
# get_red / get_ir / get_green
# --------------------------------------------------------------------------

def _stage_samples(device, samples):
    """Fill the FIFO with `samples` (raw bytes per sample), unread -- as if
    the sensor had just written them."""
    device.load_fifo([bytes(sample) for sample in samples])


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


# --------------------------------------------------------------------------
# read_temperature(): signed TINT, masked TFRAC, and the TEMP_EN poll
# (DeviceSim clears TEMP_EN instantly -- see device_sim.py)
# --------------------------------------------------------------------------

def test_read_temperature_decodes_negative_integer_part():
    sensor, device, _ = _make_sensor()
    device.set_die_temperature(0xF6, 0x08)  # -10 degC, two's complement
    assert sensor.read_temperature() == -9.5


def test_read_temperature_masks_tfrac_to_4_bits():
    sensor, device, _ = _make_sensor()
    device.set_die_temperature(0x19, 0xFF)  # TFRAC only defines bits [3:0]
    assert sensor.read_temperature() == 25.0 + 0x0F * 0.0625


def test_read_temperature_positive_integer_part_unaffected():
    sensor, device, _ = _make_sensor()
    device.set_die_temperature(0x1E, 0x02)  # +30.125 degC
    assert sensor.read_temperature() == 30.125


# --------------------------------------------------------------------------
# led_mode=3 warns (MAX30105-only feature)
# --------------------------------------------------------------------------

def test_led_mode_3_warns():
    sensor, _, _ = _make_sensor()
    with pytest.warns(UserWarning, match="MAX30105-only"):
        sensor.set_led_mode(3)


def test_led_mode_1_and_2_do_not_warn(recwarn):
    sensor, _, _ = _make_sensor()
    sensor.set_led_mode(1)
    sensor.set_led_mode(2)
    assert len(recwarn) == 0


# --------------------------------------------------------------------------
# (sample_rate, pulse_width) legality per mode, and setup_sensor()'s
# pulse-width-before-sample-rate write order
# --------------------------------------------------------------------------

def test_setup_sensor_writes_pulse_width_before_sample_rate():
    sensor, _, _ = _make_sensor()
    call_order = []
    original_pulse_width = sensor.set_pulse_width
    original_sample_rate = sensor.set_sample_rate

    def spy_pulse_width(*args, **kwargs):
        call_order.append("pulse_width")
        return original_pulse_width(*args, **kwargs)

    def spy_sample_rate(*args, **kwargs):
        call_order.append("sample_rate")
        return original_sample_rate(*args, **kwargs)

    sensor.set_pulse_width = spy_pulse_width
    sensor.set_sample_rate = spy_sample_rate

    sensor.setup_sensor()

    assert call_order == ["pulse_width", "sample_rate"]


def test_illegal_rate_pulse_width_pair_raises_in_spo2_mode():
    # Table 11 (pag. 23): 1600 sps is only legal at 69us in SpO2 mode.
    sensor, _, _ = _make_sensor()
    with pytest.raises(ValueError, match="not legal"):
        sensor.setup_sensor(led_mode=2, sample_rate=1600, pulse_width=411)


def test_3200_sps_is_illegal_at_every_pulse_width_in_spo2_mode():
    sensor, _, _ = _make_sensor()
    sensor.set_led_mode(2)
    sensor.set_pulse_width(69)
    with pytest.raises(ValueError, match="not legal"):
        sensor.set_sample_rate(3200)


def test_3200_sps_is_legal_at_69us_in_hr_mode():
    sensor, _, _ = _make_sensor()
    sensor.set_led_mode(1)
    sensor.set_pulse_width(69)
    sensor.set_sample_rate(3200)  # must not raise
    assert sensor._sample_rate == 3200


def test_rate_pulse_width_check_skipped_before_led_mode_is_known():
    sensor, _, _ = _make_sensor()
    # No setup_sensor()/set_led_mode() yet: _active_leds is still None, so
    # there's no mode to validate against yet -- must not raise.
    sensor.set_sample_rate(3200)
    sensor.set_pulse_width(411)


# --------------------------------------------------------------------------
# OVF_COUNTER accessor, check() returns a count
# --------------------------------------------------------------------------

def test_get_overflow_count_reads_register():
    sensor, device, _ = _make_sensor()
    device.set_overflow_counter(7)
    assert sensor.get_overflow_count() == 7


def test_check_returns_number_of_samples_read():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    _stage_samples(device, [
        (0, 0, 1, 0, 0, 2),
        (0, 0, 3, 0, 0, 4),
        (0, 0, 5, 0, 0, 6),
    ])
    assert sensor.check() == 3


def test_check_returns_zero_falsy_when_no_new_data():
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    result = sensor.check()
    assert result == 0
    assert not result


# --------------------------------------------------------------------------
# check(): one status/pointer burst, one FIFO burst, full FIFO, overflow
# --------------------------------------------------------------------------

def _transactions(bus, since):
    return bus.log[since:]


def test_check_uses_one_transaction_for_pointers_and_one_for_data():
    sensor, device, bus = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    device.produce([(1000, 2000), (1001, 2001), (1002, 2002)])
    since = len(bus.log)
    assert sensor.check() == 3
    # FakeSMBus logs each i2c_msg: [W 0x00, R 7 bytes] then [W 0x07, R 3*6]
    log = _transactions(bus, since)
    assert [(kind, len(data)) for kind, _, data in log] == [("W", 1), ("R", 7), ("W", 1), ("R", 18)]
    assert log[0][2] == bytes([0x00]) and log[2][2] == bytes([0x07])


def test_full_fifo_is_read_not_mistaken_for_empty():
    # 32 unread samples: WR_PTR == RD_PTR, and OVF_COUNTER > 0 because the
    # chip already had to discard some (pag. 13)
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2, pulse_width=411)
    device.load_fifo([(0, 0, n, 0, 1, n) for n in range(32)], read_ptr=7, overflow=5)
    assert sensor.check() == 32
    assert sensor.lost_samples == 5
    reds = [sensor.pop_red_from_storage() for _ in range(32)]
    assert reds == list(range(32))


def test_equal_pointers_without_overflow_is_empty():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    device.load_fifo([], read_ptr=9)
    assert sensor.check() == 0
    assert sensor.lost_samples == 0


def test_exactly_full_fifo_is_read_one_sample_later():
    # 32 unread and nothing lost yet is indistinguishable from empty
    # (WR_PTR == RD_PTR, OVF_COUNTER == 0). The next sample overflows it.
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    device.produce([(n, n) for n in range(32)])
    assert sensor.check() == 0
    device.produce([(32, 32)])
    assert sensor.check() == 32
    assert sensor.lost_samples == 1
    assert sensor.pop_red_from_storage() == 1


def test_slow_loop_after_overflow_keeps_reading_and_counts_the_loss():
    # The application stops polling for longer than the FIFO lasts
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)  # rollover on
    device.produce([(n, n) for n in range(40)])  # 8 more than fit
    assert sensor.check() == 32
    assert sensor.lost_samples == 8
    assert [sensor.pop_red_from_storage() for _ in range(32)] == list(range(8, 40))
    # and polling carries on normally afterwards
    device.produce([(100, 100), (101, 101)])
    assert sensor.check() == 2
    assert sensor.lost_samples == 8


def test_fifo_read_cut_part_way_is_reread_from_a_sample_boundary():
    sensor, device, bus = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    device.produce([(1000, 2000), (1001, 2001), (1002, 2002)])

    class CutAfterTwoBytes(FakeSMBus):
        def i2c_rdwr(self, *msgs):
            if len(msgs) == 2 and bytes(msgs[0]) == bytes([0x07]):
                self.device.write(bytes(msgs[0]))
                self.device.read(2)  # bytes clocked out before the fault
                self.i2c_rdwr = super().i2c_rdwr  # only the first one fails
                raise OSError(121, "Remote I/O error")
            return super().i2c_rdwr(*msgs)

    sensor._i2c = CutAfterTwoBytes(device)
    with pytest.raises(OSError):
        sensor.check()
    assert sensor.available() == 0
    assert sensor.check() == 3
    assert [sensor.read_sample() for _ in range(3)] == [(1000, 2000), (1001, 2001), (1002, 2002)]


def test_bus_error_on_status_read_loses_nothing():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    device.produce([(1000, 2000)])
    sensor._i2c = FakeSMBus(None)  # the wire comes loose
    with pytest.raises(OSError):
        sensor.check()
    sensor._i2c = FakeSMBus(device)
    assert sensor.check() == 1
    assert sensor.read_sample() == (1000, 2000)


# --------------------------------------------------------------------------
# check(): power-on reset detection (PWR_RDY)
# --------------------------------------------------------------------------

def test_power_cycle_restores_configuration_and_sampling():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2, sample_rate=100, sample_avg=4, pulse_width=411,
                        led_power=0x24)
    configured = {reg: device._get(reg) for reg in mod.MAX30105_RESTORE_ORDER}
    device.produce([(1000, 2000)])
    assert sensor.check() == 1

    device.power_cycle()  # brown-out: registers back to power-on values
    assert device.produce([(1, 1)]) == 0  # not sampling any more
    assert sensor.check() == 0
    assert sensor.power_resets == 1
    assert {reg: device._get(reg) for reg in mod.MAX30105_RESTORE_ORDER} == configured
    assert device.led_current_ma(1) == 7.2
    assert device.fifo_rate_hz() == 25

    device.produce([(1001, 2001)])
    assert sensor.check() == 1
    assert sensor.read_sample() == (1000, 2000)  # stored before the reset
    assert sensor.read_sample() == (1001, 2001)


def test_stale_power_up_flag_does_not_trigger_a_restore():
    # PWR_RDY from the original power-up is still set when nothing has read
    # INT_STATUS_1 (no setup_sensor() soft reset here)
    sensor, device, bus = _make_sensor()
    sensor.set_led_mode(2)
    sensor.set_pulse_width(411)
    sensor.set_sample_rate(100)
    device.produce([(1000, 2000)])
    writes_before = [entry for entry in bus.log if entry[0] == "W" and len(entry[2]) == 2]
    assert sensor.check() == 1
    assert sensor.power_resets == 0
    writes_after = [entry for entry in bus.log if entry[0] == "W" and len(entry[2]) == 2]
    assert writes_after == writes_before


def test_configuration_changed_after_first_check_is_what_gets_restored():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    sensor.check()
    sensor.set_pulse_amplitude_ir(0x10)
    device.power_cycle()
    sensor.check()
    assert device._get(mod.MAX30105_LED2_PULSE_AMP) == 0x10


# --------------------------------------------------------------------------
# Host-side storage: large, and losses are counted
# --------------------------------------------------------------------------

def test_storage_keeps_several_fifo_reads_between_pops():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    for burst in range(4):
        device.produce([(burst * 30 + n, 0) for n in range(30)])
        assert sensor.check() == 30
    assert sensor.available() == 120
    assert sensor.dropped_samples == 0
    assert sensor.pop_red_from_storage() == 0


def test_storage_overflow_is_counted():
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    for n in range(mod.STORAGE_QUEUE_SIZE + 5):
        sensor.sense.red.append(n)
        sensor.sense.IR.append(n)
    assert sensor.available() == mod.STORAGE_QUEUE_SIZE
    assert sensor.dropped_samples == 5


def test_read_sample_drains_a_backlog_without_polling():
    sensor, device, bus = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    device.produce([(1000 + n, 2000 + n) for n in range(5)])
    assert sensor.read_sample() == (1000, 2000)
    since = len(bus.log)
    assert [sensor.read_sample() for _ in range(4)] == [(1000 + n, 2000 + n) for n in range(1, 5)]
    assert len(bus.log) == since


# --------------------------------------------------------------------------
# soft_reset() resets the stored config values to their power-on-state
# values
# --------------------------------------------------------------------------

def test_soft_reset_resets_cached_config_to_por_defaults():
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2, sample_rate=400, pulse_width=411, sample_avg=8)

    sensor.soft_reset()

    assert sensor._active_leds is None
    assert sensor._multi_led_read_mode is None
    assert sensor._pulse_width_us == 69
    assert sensor._sample_rate == 50
    assert sensor._sample_avg == 1
    assert sensor.get_acquisition_frequency() == 50.0


def test_fifo_bytes_to_int_does_not_crash_right_after_soft_reset():
    sensor, _, _ = _make_sensor()
    sensor.soft_reset()
    # Must not raise (e.g. TypeError from `3 - None`).
    sensor.fifo_bytes_to_int(b"\xFF\xFF\xFF")


# --------------------------------------------------------------------------
# read_sample() returns a matched (red, ir) pair
# --------------------------------------------------------------------------

def test_read_sample_returns_matched_pair():
    sensor, device, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2, pulse_width=411)
    _stage_samples(device, [
        (0x00, 0x00, 0x10, 0x00, 0x00, 0x20),
    ])
    assert sensor.read_sample() == (0x10, 0x20)


def test_read_sample_returns_none_on_timeout():
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(led_mode=2)
    assert sensor.read_sample() is None


def test_read_sample_requires_at_least_two_active_leds():
    sensor, _, _ = _make_sensor()
    sensor.setup_sensor(led_mode=1)
    with pytest.raises(ValueError):
        sensor.read_sample()


# --------------------------------------------------------------------------
# swap_red_ir (clone boards with physically reversed LEDs)
# --------------------------------------------------------------------------

def test_swap_red_ir_exchanges_fifo_channels():
    for swap, red, ir in ((False, 0x10, 0x20), (True, 0x20, 0x10)):
        sensor, device, _ = _make_sensor(swap_red_ir=swap)
        sensor.setup_sensor(led_mode=2, pulse_width=411)
        _stage_samples(device, [(0x00, 0x00, 0x10, 0x00, 0x00, 0x20)])
        sensor.check()
        assert sensor.pop_red_from_storage() == red
        assert sensor.pop_ir_from_storage() == ir


def test_swap_red_ir_swaps_led_amplitude_registers():
    sensor, device, _ = _make_sensor(swap_red_ir=True)
    sensor.set_pulse_amplitude_red(0x11)
    sensor.set_pulse_amplitude_ir(0x22)
    assert device._get(0x0D) == 0x11  # physical red is LED2
    assert device._get(0x0C) == 0x22


# --------------------------------------------------------------------------
# Plausible-use scenarios on the mock hardware: DeviceSim does only what the
# chip can do (DeviceSim.produce() queues samples only while it is sampling)
# and FakeSMBus fails after close() like smbus2.
# --------------------------------------------------------------------------

def _patch_smbus(monkeypatch, device):
    """Make MAX30102(bus=<n>) open a FakeSMBus wired to `device`. Returns
    the list of buses it opens."""
    opened = []

    def open_bus(bus_number):
        opened.append(FakeSMBus(device))
        return opened[-1]

    monkeypatch.setattr(mod, "SMBus", open_bus)
    return opened


# Shutdown: leaving the sensor's `with` block turns the LEDs off

def test_ctrl_c_out_of_example_loop_turns_leds_off(monkeypatch):
    # examples/basic_usage.py: the loop runs until the user presses Ctrl+C
    device = DeviceSim()
    opened = _patch_smbus(monkeypatch, device)
    with pytest.raises(KeyboardInterrupt):
        with mod.MAX30102(bus=1) as sensor:
            sensor.setup_sensor()
            device.produce([(1000, 2000), (1001, 2001)])
            sensor.check()
            while sensor.available():
                sensor.pop_red_from_storage()
                sensor.pop_ir_from_storage()
            assert device.led_current_ma(1) == 25.4
            raise KeyboardInterrupt
    del sensor  # the program exits
    gc.collect()

    assert opened[0]._closed
    assert device.led_current_ma(1) == 0
    assert device.led_current_ma(2) == 0


def test_injected_bus_with_blocks_turn_leds_off():
    device = DeviceSim()
    with FakeSMBus(device) as bus, mod.MAX30102(i2c=bus) as sensor:
        sensor.setup_sensor()
        device.produce([(1000, 2000)])
        sensor.check()
    del sensor  # the program exits
    gc.collect()

    assert device.led_current_ma(1) == 0
    assert device.led_current_ma(2) == 0


def test_no_sensor_on_bus_exits_cleanly(monkeypatch):
    # Wiring fault: the examples print "Sensor not found." and return
    opened = _patch_smbus(monkeypatch, None)
    with mod.MAX30102(bus=1) as sensor:
        assert sensor.i2c_address not in mod.scan(sensor.i2c)
    assert opened[0]._closed


def test_next_run_after_close_samples_again(monkeypatch):
    device = DeviceSim()
    _patch_smbus(monkeypatch, device)
    with mod.MAX30102(bus=1) as sensor:  # first run
        sensor.setup_sensor()
    with mod.MAX30102(bus=1) as sensor:  # next run
        sensor.setup_sensor()
        assert device.produce([(1000, 2000)]) == 1
        assert sensor.read_sample() == (1000, 2000)


def test_no_bus_traffic_after_close(monkeypatch):
    device = DeviceSim()
    opened = _patch_smbus(monkeypatch, device)
    with mod.MAX30102(bus=1) as sensor:
        sensor.setup_sensor()
    transactions = len(opened[0].log)
    sensor.close()
    sensor.__del__()
    assert len(opened[0].log) == transactions


# available() counts every active channel

def test_clone_board_hr_mode_loop_receives_samples():
    # README "Sensor clones": swap_red_ir=True, and LED mode 1 then samples
    # the physical IR LED
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device), swap_red_ir=True)
    sensor.setup_sensor(led_mode=1)
    device.produce([(1000,), (1001,), (1002,)])
    sensor.check()

    ir = []
    while sensor.available():
        ir.append(sensor.pop_ir_from_storage())
    assert ir == [1000, 1001, 1002]


def test_ir_only_heart_rate_loop_terminates():
    # Heart rate only needs IR, so the user pops nothing else
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device))
    sensor.setup_sensor()
    device.produce([(1000, 2000), (1001, 2001), (1002, 2002)])
    sensor.check()

    ir = []
    for _ in range(100):  # the user's `while sensor.available():`, capped
        if not sensor.available():
            break
        ir.append(sensor.pop_ir_from_storage())
    assert ir == [2000, 2001, 2002]


# set_active_leds_amplitude() sets the LEDs the chip pulses

def test_clone_board_hr_mode_active_led_amplitude():
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device), swap_red_ir=True)
    sensor.setup_sensor(led_mode=1)  # LEDs at MEDIUM (25.4mA)
    sensor.set_active_leds_amplitude(mod.MAX30105_PULSE_AMP_LOW)

    # HR mode pulses LED1 only (datasheet Table 4)
    assert device.led_current_ma(1) == 6.2
    assert device.led_current_ma(2) == 0


# set_fifo_almost_full() only touches FIFO_A_FULL

def test_set_fifo_almost_full_with_a_sample_count():
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device))
    sensor.setup_sensor(sample_avg=4)  # 400sps / 4 = 100Hz
    try:
        sensor.set_fifo_almost_full(32)  # meant as "32 samples"
    except ValueError:
        pass
    # The chip must still average 4 samples, as the driver reports
    assert device.fifo_rate_hz() == sensor.get_acquisition_frequency() == 100


def test_set_fifo_almost_full_register_value():
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device))
    sensor.setup_sensor(sample_avg=4)
    sensor.set_fifo_almost_full(0x0F)
    assert device.a_full_unread() == 17
    assert device.fifo_rate_hz() == sensor.get_acquisition_frequency() == 100
    with pytest.raises(ValueError):
        sensor.set_fifo_almost_full(16)


# Forgetting setup_sensor() raises a clear error

def test_check_without_setup_sensor_on_fresh_chip_raises():
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device))  # setup_sensor() forgotten
    assert device.produce([(1000, 2000)]) == 0  # power-on MODE 000: idle
    with pytest.raises(ValueError, match="setup_sensor"):
        sensor.check()


def test_check_without_setup_sensor_after_previous_run_raises():
    device = DeviceSim()
    bus = FakeSMBus(device)
    previous_run = mod.MAX30102(i2c=bus)
    previous_run.setup_sensor()
    device.produce([(1000, 2000)] * 3)  # left unread
    previous_run.close()

    sensor = mod.MAX30102(i2c=bus)  # setup_sensor() forgotten
    with pytest.raises(ValueError, match="setup_sensor"):
        sensor.check()


def test_set_active_leds_amplitude_before_setup_sensor_raises():
    sensor, _, _ = _make_sensor()
    with pytest.raises(ValueError, match="setup_sensor"):
        sensor.set_active_leds_amplitude(mod.MAX30105_PULSE_AMP_LOW)


# set_led_mode() keeps the (sample rate, pulse width) pair legal

def test_switch_hr_to_spo2_rejects_disallowed_rate():
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device))
    sensor.setup_sensor(led_mode=1, sample_rate=1000, pulse_width=411)  # Table 12
    assert device.rate_allowed()
    try:
        sensor.set_led_mode(2)  # Table 11: 1000sps needs <= 118us
    except ValueError as e:
        assert "not legal" in str(e)
    assert device.rate_allowed()


# setup_sensor() starts from an empty storage

def test_reconfigure_discards_old_samples():
    device = DeviceSim()
    sensor = mod.MAX30102(i2c=FakeSMBus(device))
    sensor.setup_sensor(pulse_width=411)
    device.produce([(1000, 2000), (1001, 2001), (1002, 2002)])
    assert sensor.read_sample() == (1000, 2000)

    sensor.setup_sensor(pulse_width=118)
    device.produce([(500, 600)])
    assert sensor.read_sample() == (500, 600)
