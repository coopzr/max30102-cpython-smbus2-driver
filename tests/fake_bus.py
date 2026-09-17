"""A fake smbus2-shaped bus that drives a DeviceSim and records traffic.

Shaped enough like :class:`smbus2.SMBus` to be a drop-in for the ported
driver's ``i2c=`` constructor argument: it only needs ``i2c_rdwr`` and
``close``. Every transaction is appended to ``log`` as
``("W", addr, bytes)`` or ``("R", addr, bytes)``, which is exactly the
format the ``machine`` shim's fake ``SoftI2C`` uses too -- so the two logs
can be compared directly in test_equivalence.py.
"""
from smbus2 import i2c_msg

I2C_M_RD = 0x0001


class FakeSMBus:
    def __init__(self, device):
        self.device = device
        self.log = []
        self._closed = False

    def i2c_rdwr(self, *msgs):
        for msg in msgs:
            if msg.flags & I2C_M_RD:
                data = self.device.read(msg.len)
                # Copy the bytes back into the caller's buffer, the way a
                # real ioctl(I2C_RDWR) would fill it in-place.
                for i, b in enumerate(data):
                    msg.buf[i] = bytes([b])
                self.log.append(("R", msg.addr, data))
            else:
                data = bytes(msg)
                self.device.write(data)
                self.log.append(("W", msg.addr, data))

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
