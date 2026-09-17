"""BLOOD OXYGEN (SpO2) EXAMPLE

Estimates blood oxygen saturation (SpO2) from a rolling window of RED and
IR samples, using the "ratio of ratios" method: each channel's AC
(pulsatile) component is divided by its own DC (average) component, the
two are divided against each other to get a ratio R, and R is fed into an
empirical curve to get an SpO2 percentage.

IMPORTANT: this is an approximation intended for hobbyist / educational
use, using a widely-reproduced empirical fit (Maxim AN6409). It is NOT a
calibrated, clinically validated measurement -- see the Disclaimer in
USAGE.md and README.md. Do not use it to make health decisions.
"""
import time
from collections import deque

from max30102 import MAX30102, scan

# I2C bus number. On a Raspberry Pi, bus 1 is the usual /dev/i2c-1 header;
# run `i2cdetect -y 1` to confirm.
I2C_BUS = 1

# Number of samples averaged per SpO2 estimate. At the default 400Hz / 8
# sample rate, the sensor delivers ~50 samples/second, so 100 samples is
# about a 2-second window -- long enough to reliably span a few heartbeats.
WINDOW_SIZE = 100


def compute_spo2(red_samples, ir_samples):
    """Estimate SpO2 (%) from equal-length windows of RED/IR samples.

    Returns None if the window doesn't contain a usable pulsatile signal
    (e.g. no finger on the sensor).
    """
    red_dc = sum(red_samples) / len(red_samples)
    ir_dc = sum(ir_samples) / len(ir_samples)
    if red_dc == 0 or ir_dc == 0:
        return None

    # AC component approximated as peak-to-peak swing within the window.
    red_ac = max(red_samples) - min(red_samples)
    ir_ac = max(ir_samples) - min(ir_samples)
    if ir_ac == 0:
        # No pulsatile signal at all -- almost certainly no finger present.
        return None

    r = (red_ac / red_dc) / (ir_ac / ir_dc)

    # Empirical quadratic fit from Maxim's AN6409 application note,
    # reproduced across many open-source MAX3010x projects. This is an
    # approximation, not a calibrated conversion -- real pulse oximeters
    # are calibrated against a reference device per unit.
    spo2 = -45.060 * r * r + 30.354 * r + 94.845
    return max(0.0, min(100.0, spo2))


def main():
    with MAX30102(bus=I2C_BUS) as sensor:
        # Scan the I2C bus to ensure that the sensor is connected
        if sensor.i2c_address not in scan(sensor.i2c):
            print("Sensor not found.")
            return
        elif not sensor.check_part_id():
            # Check that the targeted sensor is compatible
            print("I2C device ID not corresponding to MAX30102 or MAX30105.")
            return
        else:
            print("Sensor connected and recognized.")

        # SpO2 needs both RED and IR channels (led_mode=2, the default).
        sensor.setup_sensor()

        print("Place a fingertip gently over the sensor...")
        time.sleep(1)

        red_window = deque(maxlen=WINDOW_SIZE)
        ir_window = deque(maxlen=WINDOW_SIZE)

        while True:
            sensor.check()
            while sensor.available():
                red_window.append(sensor.pop_red_from_storage())
                ir_window.append(sensor.pop_ir_from_storage())

            if len(red_window) == WINDOW_SIZE:
                spo2 = compute_spo2(list(red_window), list(ir_window))
                if spo2 is not None:
                    print("SpO2 ~ {:.1f}%".format(spo2))
                else:
                    print("No finger detected / signal too weak")
                # Slide the window forward instead of recomputing on every
                # single new sample.
                red_window.clear()
                ir_window.clear()


if __name__ == "__main__":
    main()
