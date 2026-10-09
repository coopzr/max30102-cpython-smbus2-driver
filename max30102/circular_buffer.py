from collections import deque


class CircularBuffer(object):
    ''' Very simple implementation of a circular buffer based on deque '''
    def __init__(self, max_size):
        # When full, appending pops the oldest item out
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

    # Return the newest item and discard the older ones
    def pop_head(self):
        if not self.data:
            return 0
        head = self.data.pop()
        self.data.clear()
        return head
