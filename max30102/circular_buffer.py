# Python (CPython + smbus2) port of:
# https://github.com/n-elia/MAX30102-MicroPython-driver
# tests/upy_shims/ucollections.py -- deque((), max_size, True) raises
# IndexError on append() past maxlen, which upstream's append() catches to
# manually evict the oldest item. CPython's collections.deque(maxlen=n)
# evicts the oldest item automatically on append instead of raising, which
# is the same net behaviour, so the try/except collapses to a plain append.
from collections import deque


class CircularBuffer(object):
    """Very simple implementation of a circular buffer based on deque."""

    def __init__(self, max_size):
        self.data = deque(maxlen=max_size)
        self.max_size = max_size

    def __len__(self):
        return len(self.data)

    def is_empty(self):
        return not bool(self.data)

    def append(self, item):
        self.data.append(item)

    def pop(self):
        return self.data.popleft()

    def clear(self):
        self.data.clear()

    # NOTE: deviation from upstream. Upstream's pop_head() is broken: it
    # aliases `temp = self.data` (not a copy), calls `self.data.clear()`
    # (a method MicroPython's deque does not document -- AttributeError on
    # real hardware), and even given a clear(), `temp` is the same object
    # that was just emptied, so the final `temp.popleft()` raises IndexError
    # for any buffer holding more than one sample. In practice this makes
    # get_red()/get_ir()/get_green() unusable, which is why both upstream
    # examples use check() + pop_*_from_storage() instead.
    #
    # This is pure buffer bookkeeping with no I2C traffic on either side of
    # it, so fixing it has no effect on the wire protocol. The fix
    # implements the evident intent of the name: return the newest sample
    # and discard the stale backlog behind it.
    def pop_head(self):
        if not self.data:
            return 0
        head = self.data.pop()  # newest
        self.data.clear()  # discard the older, now-stale samples
        return head
