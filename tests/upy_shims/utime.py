"""Shim for MicroPython's ``utime`` module, backed by real wall-clock time.

Used only to run the unmodified upstream driver source under CPython for
the differential test in test_equivalence.py. Real sleeps keep ticks_ms /
ticks_diff meaningful without reimplementing MicroPython's wrapping tick
counter (CPython's process lifetime never gets close to the wrap boundary).
"""
import time


def sleep_ms(ms):
    time.sleep(ms / 1000)


def ticks_ms():
    return time.monotonic_ns() // 1_000_000


def ticks_diff(a, b):
    return a - b
