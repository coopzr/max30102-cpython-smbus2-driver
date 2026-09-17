""" BASIC USAGE EXAMPLE
Port of MAX30102-MicroPython-driver/examples/basic_usage/main.py for
CPython + smbus2 on Linux.

This example shows how to use the MAX30102 sensor to collect data from the RED and IR channels.

The I2C bus is scanned to ensure that the sensor is connected, and the sensor is checked to
ensure that it is a MAX30102 or MAX30105 sensor.

The sensor is set up with the following parameters:
- Sample rate: 400 Hz
- Averaged samples: 8
- LED brightness: medium
- Pulse width: 411 us
- Led mode: 2 (RED + IR)

The temperature is read at the beginning of the acquisition.

Then, in a loop the data is printed to stdout, so that it can be redirected to a file or plotted.
Also the real acquisition frequency (i.e. the rate at which samples are collected from the sensor)
is computed and printed. It differs from the sample rate, because the sensor processes the data
and averages the samples before putting them into the FIFO queue (by default, 8 samples are
averaged).

Original author: n-elia
"""
import time

from max30102 import MAX30102, MAX30105_PULSE_AMP_MEDIUM, scan

# I2C bus number. On a Raspberry Pi, bus 1 is the usual /dev/i2c-1 header;
# run `i2cdetect -y 1` to confirm.
I2C_BUS = 1


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

        # It's possible to set up the sensor at once with the setup_sensor() method.
        # If no parameters are supplied, the default config is loaded:
        # Led mode: 2 (RED + IR)
        # ADC range: 16384
        # Sample rate: 400 Hz
        # Led power: maximum (50.0mA - Presence detection of ~12 inch)
        # Averaged samples: 8
        # pulse width: 411
        print("Setting up sensor with default configuration.\n")
        sensor.setup_sensor()

        # It is also possible to tune the configuration parameters one by one.
        # Set the sample rate to 400: 400 samples/s are collected by the sensor
        sensor.set_sample_rate(400)
        # Set the number of samples to be averaged per each reading
        sensor.set_fifo_average(8)
        # Set LED brightness to a medium value
        sensor.set_active_leds_amplitude(MAX30105_PULSE_AMP_MEDIUM)

        time.sleep(1)

        # The read_temperature() method allows to extract the die temperature in degC
        print("Reading temperature in degC.\n")
        print(sensor.read_temperature())

        # Select whether to compute the acquisition frequency or not
        compute_frequency = True

        print("Starting data acquisition from RED & IR registers...\n")
        time.sleep(1)

        t_start = time.monotonic()  # Starting time of the acquisition
        samples_n = 0  # Number of samples that have been collected

        while True:
            # The check() method has to be continuously polled, to check if
            # there are new readings into the sensor's FIFO queue. When new
            # readings are available, this function will put them into the storage queue.
            sensor.check()

            # Drain all queued samples from the queue
            while sensor.available():
                # Access the storage FIFO and gather the readings (integers)
                red_reading = sensor.pop_red_from_storage()
                ir_reading = sensor.pop_ir_from_storage()

                # Print the acquired data (so that it can be redirected to a file or plotted)
                print(red_reading, ",", ir_reading)

                # Compute the real frequency at which we receive data
                if compute_frequency:
                    if time.monotonic() - t_start >= 1.0:
                        f_hz = samples_n
                        samples_n = 0
                        print("acquisition frequency = ", f_hz)
                        t_start = time.monotonic()
                    else:
                        samples_n = samples_n + 1


if __name__ == "__main__":
    main()
