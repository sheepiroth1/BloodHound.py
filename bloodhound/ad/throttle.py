import time
import random
import logging
import threading
import socket
import ldap3


_page_size = 250

_LDAP_PACER = None
_HOST_PACER = None
_LDAP_SEARCHES = 0
_SEARCH_WRAPPED = False


def set_page_size(size):
    global _page_size
    _page_size = max(1, int(size))


def get_page_size():
    return _page_size


def parse_duration(value, flag='--ldap-interval'):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        raise ValueError('%s cannot be empty' % flag)
    units = {'s': 1.0, 'm': 60.0, 'h': 3600.0}
    unit = 1.0
    if text[-1] in units:
        unit = units[text[-1]]
        text = text[:-1]
    try:
        amount = float(text)
    except ValueError:
        raise ValueError('Invalid %s %r (use e.g. 300, 300s, 5m, 1h)' % (flag, value))
    seconds = amount * unit
    if seconds <= 0:
        raise ValueError('%s must be greater than 0' % flag)
    return seconds


def parse_host_throttle(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        raise ValueError('--throttle cannot be empty')
    if text.endswith('ms'):
        unit = 1.0
        text = text[:-2]
    elif text.endswith('h'):
        unit = 3600000.0
        text = text[:-1]
    elif text.endswith('m'):
        unit = 60000.0
        text = text[:-1]
    elif text.endswith('s'):
        unit = 1000.0
        text = text[:-1]
    else:
        unit = 1.0
    try:
        amount = float(text)
    except ValueError:
        raise ValueError('Invalid --throttle %r (use e.g. 500, 500ms, 2s, 1m, 1h)' % value)
    milliseconds = amount * unit
    if milliseconds <= 0:
        raise ValueError('--throttle must be greater than 0')
    return milliseconds


def format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return '%ds' % seconds
    if seconds < 3600:
        return '%dm%ds' % (seconds // 60, seconds % 60)
    hours, rem = divmod(seconds, 3600)
    return '%dh%dm' % (hours, rem // 60)


def _continuation_page(kwargs):
    cookie = kwargs.get('paged_cookie')
    return cookie not in (None, b'', '')


class LdapQueryPacer:
    """
    Two-tier LDAP pacing. New searches use interval + jitter.
    Continuation pages use page_interval + page_jitter.
    Serializes all callers with a reentrant lock (member;range= causes nested searches).
    """
    def __init__(self, interval, jitter_pct, page_interval=0, page_jitter_pct=0):
        self.interval = float(interval or 0)
        self.jitter = max(0.0, min(100.0, float(jitter_pct or 0))) / 100.0
        self.page_interval = float(page_interval or 0)
        self.page_jitter = max(0.0, min(100.0, float(page_jitter_pct or 0))) / 100.0
        self._lock = threading.RLock()
        self._last_finish = None

    def _sleep_until(self, target):
        remaining = target - time.monotonic()
        while remaining > 0:
            time.sleep(min(1.0, remaining))
            remaining = target - time.monotonic()

    def _pause(self, base, jitter, is_page):
        if self._last_finish is None or base <= 0:
            return
        extra = base * jitter * random.random()
        total = base + extra
        label = 'page' if is_page else 'search'
        logging.debug('LDAP %s pacing: %.1fs delay (base %.1fs + jitter %.1fs)', label, total, base, extra)
        self._sleep_until(self._last_finish + total)

    def run(self, func, *args, **kwargs):
        with self._lock:
            is_page = _continuation_page(kwargs)
            if is_page:
                self._pause(self.page_interval, self.page_jitter, is_page=True)
            else:
                self._pause(self.interval, self.jitter, is_page=False)
            try:
                return func(*args, **kwargs)
            finally:
                self._last_finish = time.monotonic()

    def describe(self):
        parts = []
        if self.interval > 0:
            text = 'New LDAP searches at least %s apart' % format_duration(self.interval)
            if self.jitter > 0:
                text += ', plus 0-%d%% jitter' % int(round(self.jitter * 100))
            parts.append(text)
        if self.page_interval > 0:
            text = 'LDAP pages at least %s apart' % format_duration(self.page_interval)
            if self.page_jitter > 0:
                text += ', plus 0-%d%% jitter' % int(round(self.page_jitter * 100))
            parts.append(text)
        return '. '.join(parts)


class HostCallPacer:
    """
    Spaces host RPC calls across worker threads. Jitter is bidirectional
    (can go below or above the base), but the result is never negative.
    """
    def __init__(self, throttle_ms, jitter_pct):
        self.throttle = float(throttle_ms) / 1000.0
        self.jitter = max(0.0, min(100.0, float(jitter_pct or 0))) / 100.0
        self._lock = threading.RLock()
        self._last_finish = None

    def _sleep_until(self, target):
        remaining = target - time.monotonic()
        while remaining > 0:
            time.sleep(min(1.0, remaining))
            remaining = target - time.monotonic()

    def run(self, func, *args, **kwargs):
        with self._lock:
            if self._last_finish is not None and self.throttle > 0:
                span = self.throttle * self.jitter
                jitter_val = random.uniform(-span, span)
                delay = self.throttle + jitter_val
                if delay < 0:
                    delay = 0
                logging.debug('Host pacing: %.0fms delay (base %.0fms + jitter %+.0fms)',
                              delay * 1000, self.throttle * 1000, jitter_val * 1000)
                self._sleep_until(self._last_finish + delay)
            try:
                return func(*args, **kwargs)
            finally:
                self._last_finish = time.monotonic()

    def describe(self):
        if self.throttle < 1:
            text = 'Host calls at least %d ms apart' % int(round(self.throttle * 1000))
        else:
            text = 'Host calls at least %s apart' % format_duration(self.throttle)
        if self.jitter > 0:
            text += ', +/- 0-%d%% jitter' % int(round(self.jitter * 100))
        return text


def get_ldap_pacer():
    return _LDAP_PACER


def get_host_pacer():
    return _HOST_PACER


def ldap_search_count():
    return _LDAP_SEARCHES


def install_ldap_search_counter():
    global _SEARCH_WRAPPED
    if _SEARCH_WRAPPED:
        return
    original = ldap3.Connection.search

    def _counted_search(self, *args, **kwargs):
        global _LDAP_SEARCHES
        _LDAP_SEARCHES += 1
        pacer = _LDAP_PACER
        if pacer is None:
            return original(self, *args, **kwargs)
        return pacer.run(original, self, *args, **kwargs)

    ldap3.Connection.search = _counted_search
    _SEARCH_WRAPPED = True


def install_ldap_pacing(interval, jitter_pct, page_interval=0, page_jitter_pct=0):
    global _LDAP_PACER
    _LDAP_PACER = LdapQueryPacer(interval, jitter_pct, page_interval, page_jitter_pct)
    install_ldap_search_counter()
    return _LDAP_PACER


def install_host_pacing(throttle_ms, jitter_pct):
    global _HOST_PACER
    _HOST_PACER = HostCallPacer(throttle_ms, jitter_pct)
    return _HOST_PACER


def call_host(func, *args, **kwargs):
    pacer = _HOST_PACER
    if pacer is None:
        return func(*args, **kwargs)
    return pacer.run(func, *args, **kwargs)


def enable_tcp_keepalive(sock):
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (OSError, AttributeError):
        return
    if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30000, 15000))
            return
        except (OSError, AttributeError, ValueError):
            pass
    for opt, value in (('TCP_KEEPIDLE', 30), ('TCP_KEEPINTVL', 15), ('TCP_KEEPCNT', 4)):
        level = getattr(socket, 'IPPROTO_TCP', socket.SOL_SOCKET)
        name = getattr(socket, opt, None)
        if name is not None:
            try:
                sock.setsockopt(level, name, value)
            except (OSError, AttributeError):
                pass
