"""Shim for MicroPython's ``ucollections.deque``.

Used only to run the unmodified upstream driver source under CPython.
Deliberately mirrors MicroPython's deque semantics rather than CPython's
``collections.deque``:

- a 3rd constructor argument (``flags``) where ``1`` means "raise
  ``IndexError`` on append/appendleft past ``maxlen``" instead of silently
  evicting the oldest item (CPython's ``deque(maxlen=n)`` always evicts
  silently, which is *not* what MicroPython does and would mask the bug in
  upstream's ``CircularBuffer.pop_head`` that this port fixes -- see
  circular_buffer.py).
- no ``clear()`` method, since MicroPython's deque does not document one.
  ``pop_head()`` in the upstream source relies on ``clear()`` existing, so
  reproducing its absence here is what makes the upstream bug reproduce
  faithfully under this shim.
"""


class deque:
    def __init__(self, iterable, maxlen, flags=0):
        self._data = list(iterable)
        self._maxlen = maxlen
        self._check_overflow = bool(flags)

    def __len__(self):
        return len(self._data)

    def __bool__(self):
        return bool(self._data)

    def __iter__(self):
        return iter(self._data)

    def append(self, item):
        if self._maxlen is not None and len(self._data) >= self._maxlen:
            if self._check_overflow:
                raise IndexError("deque full")
            self._data.pop(0)
        self._data.append(item)

    def appendleft(self, item):
        if self._maxlen is not None and len(self._data) >= self._maxlen:
            if self._check_overflow:
                raise IndexError("deque full")
            self._data.pop()
        self._data.insert(0, item)

    def pop(self):
        if not self._data:
            raise IndexError("pop from empty deque")
        return self._data.pop()

    def popleft(self):
        if not self._data:
            raise IndexError("popleft from empty deque")
        return self._data.pop(0)

    def extend(self, iterable):
        for item in iterable:
            self.append(item)
