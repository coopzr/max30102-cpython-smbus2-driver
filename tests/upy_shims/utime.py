"""Shim for MicroPython's ``utime`` module, backed by real wall-clock time.

Used to run the MicroPython driver under CPython in test_equivalence.py.
MicroPython's tick counter wraparound is not modelled.
"""
import time


def sleep_ms(ms):
    time.sleep(ms / 1000)


def ticks_ms():
    return time.monotonic_ns() // 1_000_000


def ticks_diff(a, b):
    return a - b
