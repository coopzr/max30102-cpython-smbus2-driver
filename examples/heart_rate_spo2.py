"""HEART RATE AND SpO2 EXAMPLE

Prints heart rate and blood-oxygen saturation (SpO2) once a second, from
max30102.hr_spo2.HrSpO2Estimator. It needs numpy and scipy:
`pip install .[hrspo2]` from this project's root.

Sit still, with light but full fingertip contact. Each reading covers the
last 6 seconds, so the first one comes about 6 s after the program starts.

Expect "No reading" whenever the signal isn't clean enough: no finger, a
finger moving or pressing unevenly, strong ambient light. The estimator
refuses those windows instead of guessing, which is why the numbers it does
print can be trusted. A heart rate can come without SpO2: SpO2 needs the red
and IR channels to agree more closely than the heart rate does.

"Heart rate below 40 BPM?" means a rhythm of 30-40 a minute was found that
isn't a normal pulse seen every second or third beat. That is most likely a
slow pulse, but breathing or finger-pressure changes at that rhythm, on a
weak pulse, look the same, and so can walking. So it is a prompt to check
with another method, not a measurement, and only while sitting still. A pulse below 30 BPM can't be detected at all and shows as
"No reading".

IMPORTANT: hobbyist / educational use only. SpO2 comes from a generic
calibration curve, not one measured for this sensor, so it can be a few
percentage points off a real oximeter. Do not use it to make health
decisions -- see the Disclaimer in README.md.
"""
import time

from max30102 import MAX30102, scan
from max30102.hr_spo2 import HrSpO2Estimator

# I2C bus number. On a Raspberry Pi, bus 1 is the usual /dev/i2c-1 header;
# run `i2cdetect -y 1` to confirm.
I2C_BUS = 1

# Some clone boards (e.g. the MH-ET LIVE MAX30102) have the red and IR LEDs
# reversed relative to the datasheet. Symptom: a steady heart rate with SpO2
# far too low (or missing), because red and IR swap places in the ratio of
# ratios. Check yours with a phone camera: in LED mode 1 only the red LED
# should light (see README.md).
SWAP_RED_IR = True

# The estimator was validated on 25 samples/s with an 18-bit ADC: 100
# samples/s averaged 4 at a time on the chip, 411us pulses
SAMPLE_RATE = 100
SAMPLE_AVG = 4
PULSE_WIDTH = 411

# The FIFO fills in 32 / 25 = 1.28 s. Polling every 0.2 s stays well inside
# that even when a loop iteration runs late.
POLL_INTERVAL_S = 0.2


def describe(estimate, slow_rhythm_confirmed):
    if estimate.heart_rate is not None:
        text = "Heart rate {0} BPM".format(estimate.heart_rate)
        if estimate.spo2 is None:
            return text + ", SpO2: no reading"
        return text + ", SpO2 ~{0:.0f}% (uncalibrated)".format(estimate.spo2)
    if slow_rhythm_confirmed:
        return ("Heart rate below 40 BPM? A rhythm slower than 40 a minute was found. "
                "Breathe normally, keep still, and check with another method.")
    if estimate.below_range:
        return "No reading (checking a slow rhythm...)"
    return "No reading (no finger, or too much movement / light)"


def main():
    with MAX30102(bus=I2C_BUS, swap_red_ir=SWAP_RED_IR) as sensor:
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

        # SpO2 needs both RED and IR channels (led_mode=2, the default)
        sensor.setup_sensor(sample_rate=SAMPLE_RATE, sample_avg=SAMPLE_AVG,
                            pulse_width=PULSE_WIDTH)
        estimator = HrSpO2Estimator(sample_rate=sensor.get_acquisition_frequency())

        # When to show estimate.below_range. Each estimate covers the last
        # window_s seconds and a new one comes every update_s, so consecutive
        # flags mostly share samples and count as one observation. Show a slow
        # rhythm only once it has been flagged in every estimate from one
        # window to the next window with no samples in common: two
        # independent observations agree. A sustained slow heart rate passes;
        # a brief 30-40 per minute component of breathing or finger pressure
        # doesn't.
        slow_needed = int(round(estimator.window_s / estimator.update_s)) + 1
        slow_streak = 0

        print("Place a fingertip gently over the sensor and keep still.")
        print("The first reading comes after about {0:.0f} s, then one every {1:.0f} s; "
              "the first few may be empty while the filter settles.\n"
              .format(estimator.window_s, estimator.update_s))

        # Start the loop from an empty FIFO rather than one that filled up
        # (and overflowed) while the program was starting
        sensor.clear_fifo()
        gaps_seen = (sensor.lost_samples, sensor.power_resets)

        while True:
            time.sleep(POLL_INTERVAL_S)
            try:
                sensor.check()
            except OSError as e:
                # A loose wire or bus glitch: the unread samples stay in the
                # sensor, so the next poll picks them up
                print("I2C error, retrying:", e)
                continue

            # Samples went missing (the loop fell behind, or the sensor was
            # power-cycled). A window spliced across the gap has the wrong
            # period, so start the estimate over.
            gaps = (sensor.lost_samples, sensor.power_resets)
            if gaps != gaps_seen:
                gaps_seen = gaps
                estimator.reset()
                slow_streak = 0

            while sensor.available():
                # Red and IR from the same FIFO sample: the SpO2 ratio
                # compares them sample by sample
                red = sensor.pop_red_from_storage()
                ir = sensor.pop_ir_from_storage()
                estimate = estimator.add_sample(red, ir)
                if estimate is not None:
                    slow_streak = slow_streak + 1 if estimate.below_range else 0
                    print(describe(estimate, slow_streak >= slow_needed))


if __name__ == "__main__":
    main()
