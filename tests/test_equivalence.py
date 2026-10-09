"""I2C traffic comparison against the reference MicroPython driver.

This driver and n-elia's MicroPython driver (loaded from a
MAX30102-MicroPython-driver clone, through a machine.SoftI2C shim) are run
through the same sequence of calls, each against its own DeviceSim. The two
I2C transaction logs must be byte-for-byte identical.

get_red()/get_ir()/get_green() are not part of the sequence; they are
covered by test_driver.py.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from device_sim import DeviceSim  # noqa: E402
from fake_bus import FakeSMBus  # noqa: E402

REFERENCE_CLONE = Path(__file__).parent.parent / "MAX30102-MicroPython-driver"
REFERENCE_PKG_INIT = REFERENCE_CLONE / "max30102" / "__init__.py"
SHIMS_DIR = Path(__file__).parent / "upy_shims"

pytestmark = pytest.mark.skipif(
    not REFERENCE_PKG_INIT.exists(),
    reason="MAX30102-MicroPython-driver clone not found in the project root",
)


def _install_shims():
    """Install the upy_shims/*.py modules into sys.modules as machine,
    ustruct, utime and ucollections.
    """
    for name in ("ustruct", "utime", "ucollections", "machine"):
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, SHIMS_DIR / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


def _load_reference_module():
    """Load the MicroPython driver's max30102 package from source.

    Both packages are named "max30102", and the MicroPython driver imports
    `max30102.circular_buffer`. So this project's package is temporarily
    removed from sys.modules, the MicroPython package is loaded under that
    name, and then this project's package is put back.
    """
    _install_shims()

    saved = {
        k: v for k, v in sys.modules.items()
        if k == "max30102" or k.startswith("max30102.")
    }
    for k in saved:
        del sys.modules[k]
    try:
        spec = importlib.util.spec_from_file_location(
            "max30102", REFERENCE_PKG_INIT,
            submodule_search_locations=[str(REFERENCE_PKG_INIT.parent)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["max30102"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("max30102", None)
        sys.modules.pop("max30102.circular_buffer", None)
        sys.modules.update(saved)


@pytest.fixture(scope="module")
def reference():
    return _load_reference_module()


@pytest.fixture()
def driver():
    import max30102
    return max30102


def _run_scripted_sequence(sensor, device):
    """Drive `sensor` through a fixed call sequence covering every register
    access. `device` is that sensor's own DeviceSim; staging pointers and
    FIFO bytes on it does not generate I2C traffic.
    """
    sensor.setup_sensor()
    sensor.setup_sensor(
        led_mode=3, adc_range=4096, sample_rate=100,
        led_power=0x1F, sample_avg=4, pulse_width=118,
    )

    sensor.check_part_id()
    sensor.get_revision_id()

    for adc_range in (2048, 4096, 8192, 16384):
        sensor.set_adc_range(adc_range)
    for sample_rate in (50, 100, 200, 400, 800, 1000, 1600, 3200):
        sensor.set_sample_rate(sample_rate)
    for pulse_width in (69, 118, 215, 411):
        sensor.set_pulse_width(pulse_width)
    for sample_avg in (1, 2, 4, 8, 16, 32):
        sensor.set_fifo_average(sample_avg)
    for led_mode in (1, 2, 3):
        sensor.set_led_mode(led_mode)

    sensor.set_pulse_amplitude_red(0x1F)
    sensor.set_pulse_amplitude_ir(0x2A)
    sensor.set_pulse_amplitude_green(0x3B)
    sensor.set_pulse_amplitude_proximity(0x4C)
    sensor.set_active_leds_amplitude(0x50)
    sensor.set_proximity_threshold(0x10)
    sensor.set_prox_int_tresh(0x11)

    sensor.enable_a_full()
    sensor.disable_a_full()
    sensor.enable_data_rdy()
    sensor.disable_data_rdy()
    sensor.enable_alc_ovf()
    sensor.disable_alc_ovf()
    sensor.enable_prox_int()
    sensor.disable_prox_int()
    sensor.enable_die_temp_rdy()
    sensor.disable_die_temp_rdy()

    sensor.enable_slot(1, 0x01)
    sensor.enable_slot(2, 0x02)
    sensor.enable_slot(3, 0x03)
    sensor.enable_slot(4, 0x01)
    sensor.disable_slots()

    sensor.enable_fifo_rollover()
    sensor.disable_fifo_rollover()
    sensor.set_fifo_almost_full(0x05)
    sensor.clear_fifo()
    sensor.get_write_pointer()
    sensor.get_read_pointer()

    sensor.get_int_1()
    sensor.get_int_2()

    sensor.read_temperature()

    sensor.wakeup()
    sensor.shutdown()
    sensor.wakeup()

    # Clean 2-LED configuration, then drive check() through a normal pass
    # and a FIFO wrap-boundary pass (write_ptr < read_ptr).
    sensor.setup_sensor(led_mode=2)

    device.set_write_ptr(5)
    device.set_read_ptr(0)
    device.push_fifo_bytes(bytes(range(1, 5 * 6 + 1)))  # 5 samples * 2 LEDs * 3B
    sensor.check()

    device.set_write_ptr(2)
    device.set_read_ptr(30)  # (2 - 30) % 32 == 4 samples, exercises the wrap add
    device.push_fifo_bytes(bytes(range(1, 4 * 6 + 1)))
    sensor.check()

    sensor.available()
    sensor.pop_red_from_storage()
    sensor.pop_ir_from_storage()


def test_transaction_logs_are_byte_identical(reference, driver):
    reference_device = DeviceSim()
    reference_bus = sys.modules["machine"].SoftI2C(device=reference_device)
    reference_sensor = reference.MAX30102(i2c=reference_bus)

    device = DeviceSim()
    bus = FakeSMBus(device)
    sensor = driver.MAX30102(i2c=bus)

    _run_scripted_sequence(reference_sensor, reference_device)
    _run_scripted_sequence(sensor, device)

    assert bus.log == reference_bus.log
