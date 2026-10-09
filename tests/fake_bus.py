"""A fake smbus2-shaped bus that drives a DeviceSim and records traffic.

Can be passed as the driver's ``i2c=`` argument: it only needs
``i2c_rdwr`` and ``close``. Every transaction is appended to ``log`` as
``("W", addr, bytes)`` or ``("R", addr, bytes)``.

Like smbus2, the bus can't be used after ``close()``. ``FakeSMBus(None)`` is
a bus with no sensor on it: every transfer is NACKed.
"""
from smbus2 import i2c_msg

I2C_M_RD = 0x0001


class FakeSMBus:
    def __init__(self, device):
        self.device = device
        self.log = []
        self._closed = False

    def i2c_rdwr(self, *msgs):
        if self._closed:
            # smbus2's close() sets fd to None, so the next ioctl() raises
            raise TypeError("argument must be an int, or have a fileno() method.")
        for msg in msgs:
            if self.device is None:
                # Nothing on the bus to ACK the transfer
                raise OSError(121, "Remote I/O error")
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
