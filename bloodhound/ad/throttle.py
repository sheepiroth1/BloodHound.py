####################
#
# Copyright (c) 2018 Fox-IT
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
####################
"""
Global LDAP query throttle.

A single LDAP "query" in this tool is not a single operation on the wire:
ADDC.search() uses ldap3's paged_search, which calls Connection.search() once per
page (see ldap3/extend/standard/PagedSearch.py, the `while cookie:` loop). This
module throttles at that level - one wait per LDAP search operation - by
subclassing Connection rather than patching ldap3.

What gets throttled: every search issued through a connection returned by
ADAuthentication.getLDAPConnection(). That includes the paged searches in
ADDC.search()/ldap_get_single(), the schema query in ADDC.get_objecttype() which
bypasses ADDC.search(), ldap3's own rootDSE/subschema reads during connection
setup, and auto_range continuation searches.

What does not: the anonymous reachability probes in ADDC.ldap_connect() (binds,
not searches) and connections ldap3 creates internally to follow referrals
(rare for DC-to-DC AD queries).
"""
import logging
import random
import threading
import time

import ldap3

# Process-wide throttle. It has to be global because the limit applies across
# the three connections ADDC keeps (ldap, resolverldap, gcldap) and the
# ObjectResolver thread, none of which share an object that could own this.
_throttle = None

# Page size for the paged_search in ADDC.search(). The throttle counts operations,
# not queries, so this decides how many entries travel per wait: 200 (the
# historical value) moves 200 entries per slot. Lower it for finer pacing,
# raise it to burn fewer slots on large collections.
_page_size = 200


class LDAPThrottle(object):
    """
    Space out LDAP search operations by a fixed interval plus random jitter.

    The lock is deliberately held across the sleep. Enumeration is single
    threaded apart from the output writer, but the resolver connection can be
    used from ObjectResolver while the main thread is mid-generator on a paged
    search; holding the lock while sleeping serializes those too, so the
    guarantee is strictly one operation per slot, never two back to back.
    """
    def __init__(self, interval_seconds, jitter_seconds=0.0):
        self.interval = float(interval_seconds)
        self.jitter = float(jitter_seconds)
        self.lock = threading.Lock()
        # 0.0 means "the first operation is free" - we never delay the first query
        self.next_allowed = 0.0
        self.count = 0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_allowed:
                delay = self.next_allowed - now
                logging.debug('LDAP throttle: waiting %.1fs before operation #%d', delay, self.count + 1)
                time.sleep(delay)
                now = time.monotonic()
            # Schedule the next slot from the slot we just used, not from "now",
            # so a slow server response does not shift the whole schedule later.
            self.next_allowed = now + self.interval + random.uniform(0, self.jitter)
            self.count += 1

    def ops(self):
        return self.count


def set_throttler(interval_seconds, jitter_seconds=0.0):
    """
    Enable throttling process-wide. interval_seconds of 0 (or None) disables it.
    """
    global _throttle
    if not interval_seconds or float(interval_seconds) <= 0:
        _throttle = None
        return None
    _throttle = LDAPThrottle(interval_seconds, jitter_seconds or 0.0)
    logging.debug('LDAP throttling enabled: one operation per %.0fs + %.0fs jitter',
                  _throttle.interval, _throttle.jitter)
    return _throttle


def get_throttler():
    return _throttle


def set_page_size(size):
    global _page_size
    if size:
        _page_size = int(size)


def get_page_size():
    return _page_size


class ThrottledConnection(ldap3.Connection):
    """
    ldap3.Connection that waits for a throttle slot on every search operation.

    paged_search_generator calls this search() once per page, so one slot is
    consumed per page rather than per logical query.
    """
    def search(self, *args, **kwargs):
        throttle = get_throttler()
        if throttle is not None:
            throttle.wait()
        return super().search(*args, **kwargs)
