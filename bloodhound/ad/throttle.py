import time
import random
import logging
import threading
import ldap3


_throttle = None
_page_size = 250


def set_throttler(interval_seconds, jitter_min_seconds=0, jitter_max_seconds=0):
    global _throttle
    if interval_seconds > 0:
        _throttle = LDAPThrottle(interval_seconds, jitter_min_seconds, jitter_max_seconds)
        return True
    _throttle = None
    return False


def get_throttler():
    return _throttle


def set_page_size(size):
    global _page_size
    _page_size = max(1, int(size))


def get_page_size():
    return _page_size


class LDAPThrottle:
    def __init__(self, interval_seconds, jitter_min_seconds=0, jitter_max_seconds=0):
        self.interval = interval_seconds
        self.jitter_min = jitter_min_seconds
        self.jitter_max = jitter_max_seconds
        self.next_allowed = 0.0
        self.query_count = 0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            if now < self.next_allowed:
                sleep_time = self.next_allowed - now
                jitter = random.uniform(self.jitter_min, self.jitter_max)
                total = sleep_time + jitter
                logging.debug(
                    'Throttle: sleeping %.1fs before LDAP query #%d (%.1fs remaining + %.1fs jitter)',
                    total, self.query_count + 1, sleep_time, jitter
                )
                time.sleep(total)
            now = time.time()
            self.next_allowed = now + self.interval
            self.query_count += 1


class ThrottledConnection(ldap3.Connection):
    def search(self, *args, **kwargs):
        search_base = args[0] if args else kwargs.get('search_base', '?')
        search_filter = args[1] if len(args) > 1 else kwargs.get('search_filter', '?')
        throttle = get_throttler()
        if throttle:
            throttle.wait()
        logging.debug('LDAP search #%d: base=%s filter=%s',
                      throttle.query_count if throttle else 0, search_base, search_filter)
        return super().search(*args, **kwargs)
