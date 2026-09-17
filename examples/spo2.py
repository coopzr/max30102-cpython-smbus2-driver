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

# Recompute every N new samples instead of on every single one, so the
# window actually slides instead of being thrown away and refilled from
# scratch each time (SPEC_AUDIT_TRIAGE.md A10). ~0.5s at the defaults.
RECOMPUTE_EVERY = 25

# Signal-quality gate (SPEC_AUDIT_TRIAGE.md A9): without a floor on both
# the IR DC level and the perfusion index, an empty sensor still prints a
# confident-looking, meaningless SpO2 percentage. Both thresholds are
# rough starting points (the SparkFun examples this port descends from use
# ~50000 counts as their "finger present" DC floor) and need tuning
# against real hardware -- unverifiable without a sensor.
IR_DC_FINGER_PRESENT_MIN = 50000  # ADC counts
PERFUSION_INDEX_MIN = 0.002  # 0.2%


def _percentile_spread_ac(samples):
    """Estimate the AC (pulsatile) amplitude as the 5th-to-95th percentile
    spread of the window, instead of the full peak-to-peak (max - min)
    swing this replaced.

    A single motion-spike outlier becomes the new min or max and sets the
    peak-to-peak swing for the *entire* window; a percentile spread
    instead drops the top/bottom ~5% of samples (a spike among 100 samples
    lands within that dropped 5%), so one bad sample doesn't dominate the
    estimate the way max-min does. An RMS estimate was considered too
    (also in SPEC_AUDIT_TRIAGE.md A5) but rejected: because RMS sums
    squared deviations, one sufficiently large spike still dominates it,
    just less severely than max-min.
    """
    ordered = sorted(samples)
    n = len(ordered)
    lo = ordered[int(0.05 * (n - 1))]
    hi = ordered[int(0.95 * (n - 1))]
    return hi - lo


def compute_spo2(red_samples, ir_samples):
    """Estimate SpO2 (%) from equal-length windows of RED/IR samples.

    Returns (spo2, perfusion_index), or (None, perfusion_index) if the
    window doesn't look like it has a finger on it (see
    IR_DC_FINGER_PRESENT_MIN / PERFUSION_INDEX_MIN above).
    """
    red_dc = sum(red_samples) / len(red_samples)
    ir_dc = sum(ir_samples) / len(ir_samples)
    if red_dc == 0 or ir_dc == 0:
        return None, 0.0

    red_ac = _percentile_spread_ac(red_samples)
    ir_ac = _percentile_spread_ac(ir_samples)
    perfusion_index = ir_ac / ir_dc

    if ir_dc < IR_DC_FINGER_PRESENT_MIN or perfusion_index < PERFUSION_INDEX_MIN:
        # No finger present, or too weak/noisy a signal to trust.
        return None, perfusion_index

    r = (red_ac / red_dc) / (ir_ac / ir_dc)

    # Empirical quadratic fit from Maxim's AN6409 application note,
    # reproduced across many open-source MAX3010x projects. This is an
    # approximation, not a calibrated conversion -- real pulse oximeters
    # are calibrated against a reference device per unit.
    spo2 = -45.060 * r * r + 30.354 * r + 94.845
    return max(0.0, min(100.0, spo2)), perfusion_index


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
        samples_since_compute = 0

        while True:
            sensor.check()
            while sensor.available():
                red_window.append(sensor.pop_red_from_storage())
                ir_window.append(sensor.pop_ir_from_storage())
                samples_since_compute += 1

            # The deques themselves already slide (maxlen evicts the
            # oldest sample as new ones arrive) -- recompute periodically
            # instead of on every single new sample, rather than clearing
            # and refilling from scratch each time (SPEC_AUDIT_TRIAGE.md
            # A10).
            if len(red_window) == WINDOW_SIZE and samples_since_compute >= RECOMPUTE_EVERY:
                spo2, perfusion_index = compute_spo2(list(red_window), list(ir_window))
                if spo2 is not None:
                    print("SpO2 ~ {:.1f}% (perfusion index {:.3f}%)".format(
                        spo2, perfusion_index * 100
                    ))
                else:
                    print("No finger detected / signal too weak (perfusion index {:.3f}%)".format(
                        perfusion_index * 100
                    ))
                samples_since_compute = 0


if __name__ == "__main__":
    main()
