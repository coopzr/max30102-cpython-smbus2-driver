"""Shim for MicroPython's ``machine.SoftI2C``.

Used to run the MicroPython driver under CPython in test_equivalence.py.
Sends ``writeto``/``readfrom`` calls to a ``DeviceSim`` (tests/device_sim.py)
and records traffic in the same ``("W"|"R", addr, bytes)`` log format as
``tests/fake_bus.py``.

Only STOP-terminated transactions (``stop=True``, MicroPython's default)
are modelled; anything else fails an assertion.
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
