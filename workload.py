"""Admission limits independent of Discord. All methods run on the event-loop thread."""
import time


class Admission:
    def __init__(self, total=16, per_channel=2, ttl=180, reserved=4):
        self.total, self.per_channel, self.ttl = total, per_channel, ttl
        self.reserved = min(reserved, max(0, total - 1))
        self.pending = {}
        self.channels = {}

    def reserve(self, key, *, owner=False):
        cid = key[0]
        limit = self.total if owner else self.total - self.reserved
        channel_limit = self.per_channel if owner else max(1, self.per_channel - 1)
        if key in self.pending or len(self.pending) >= limit or self.channels.get(cid, 0) >= channel_limit:
            return False
        self.pending[key] = time.monotonic()
        self.channels[cid] = self.channels.get(cid, 0) + 1
        return True

    def expired(self, key):
        return time.monotonic() - self.pending.get(key, -1e9) > self.ttl

    def release(self, key):
        if key in self.pending:
            del self.pending[key]
            cid = key[0]
            self.channels[cid] -= 1
            if not self.channels[cid]:
                del self.channels[cid]
