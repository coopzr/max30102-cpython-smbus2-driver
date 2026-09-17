"""Pytest bootstrap.

``smbus2`` does ``import fcntl`` at module import time, which does not exist
on Windows. We only need the real, ctypes-based ``i2c_msg`` class and the
``SMBus`` class shape for type-checking in tests -- the fake bus below never
reaches an actual ioctl call -- so we install a minimal stub ``fcntl`` module
into ``sys.modules`` before ``smbus2`` (or anything that imports it) is
imported for the first time. This is test-only scaffolding; on Linux the
real ``fcntl`` module is used and this stub is never installed.
"""
import sys
import types

if sys.platform == "win32" and "fcntl" not in sys.modules:
    _fcntl_stub = types.ModuleType("fcntl")

    def _ioctl(fd, request, arg=0, mutate_flag=True):
        raise OSError("fcntl.ioctl is not available on this platform (test stub)")

    _fcntl_stub.ioctl = _ioctl
    sys.modules["fcntl"] = _fcntl_stub
