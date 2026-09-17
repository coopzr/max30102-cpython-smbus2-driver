"""Shim for MicroPython's ``machine.SoftI2C``.

Used only to run the unmodified upstream driver source under CPython for
the differential test in test_equivalence.py. Translates the
``writeto``/``readfrom`` calls the upstream driver makes into the same
``DeviceSim`` (tests/device_sim.py) that the ported driver's smbus2-based
``i2c_rdwr`` calls drive, and records traffic in the same
``("W"|"R", addr, bytes)`` log format as ``tests/fake_bus.py`` so the two
logs can be compared directly.

MicroPython's ``writeto``/``readfrom`` default to ``stop=True`` (a STOP is
issued after every transaction -- no repeated start). The upstream driver
never passes ``stop=False``, so this shim asserts that invariant: if a
future upstream change ever did, the equivalence test would fail loudly
instead of silently comparing apples to oranges.
"""


class SoftI2C:
    def __init__(self, device, sda=None, scl=None, freq=400000):
        self.device = device
        self.log = []

    def writeto(self, addr, buf, stop=True):
        assert stop is True, "shim only models STOP-terminated transactions"
        data = bytes(buf)
        self.device.write(data)
        self.log.append(("W", addr, data))
        return len(data)

    def readfrom(self, addr, n, stop=True):
        assert stop is True, "shim only models STOP-terminated transactions"
        data = self.device.read(n)
        self.log.append(("R", addr, data))
        return data

    def scan(self):
        return [0x57]
