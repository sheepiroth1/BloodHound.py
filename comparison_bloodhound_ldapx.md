# Feature Comparison: bloodhound-ce_claude vs bloodhound-ldapx.py

**Compared project**: [bloodhound-ldapx.py by An0nUD4Y](https://github.com/An0nUD4Y/bloodhound-ldapx.py)
**Local path**: `D:\git_repo\bloodhound-ce-throttled_other`
**Date**: 2026-10-12

---

## Recommendations to Adopt

### 1. Two-Tier LDAP Pacing — HIGH VALUE

**What they have**: Two separate delay controls for LDAP queries:
- `--ldap-interval` + `--ldap-jitter` — delay before each *new* LDAP search (e.g., "get all users", "get all groups", each `member;range=` follow-up)
- `--ldap-page-interval` + `--ldap-page-jitter` — delay before each *continuation page* (same query, next batch of 200 results)

**What we have**: A single `--throttle-interval` applied uniformly to all LDAP activity.

**Why theirs is better**: New searches and continuation pages have fundamentally different detection profiles. A new search is a distinct LDAP operation with a new filter — it's what a SIEM correlates on. A continuation page is just the server sending the next batch from an already-open result set; the filter was already sent. Throttling pages as aggressively as new searches wastes time without meaningful OPSEC benefit. Example: 20,000 objects ≈ 100 pages + 20 new searches. With a single 30s interval, that's 60 minutes. With 30s for searches and 5s for pages, it's 18 minutes — same search-level stealth, 3.3x faster.

**Implementation reference**: `bloodhound/ad/utils.py` lines 684-743 (`LdapQueryPacer` class). Thread-safe with `RLock`. Monkey-patches `ldap3.Connection.search` via `install_ldap_search_counter()` (line 801). The pacer inspects `kwargs['paged_cookie']` to distinguish new searches from continuation pages. Jitter is additive only (never reduces the interval).

**Their CLI args** (in `__init__.py`):
```
--ldap-interval DURATION      New search delay (plain number = seconds; suffixes s/m/h)
--ldap-jitter PERCENT          0-100, extra random delay on new searches
--ldap-page-interval DURATION  Continuation page delay
--ldap-page-jitter PERCENT     0-100, extra random delay on pages
```

---

### 2. Paging Cookie Retry on Disconnect — HIGH VALUE

**What they have**: Custom `ADDC._iter_paged()` (domain.py lines 276-331) that handles socket failures mid-search:
1. On `LDAPSocketReceiveError` / `LDAPSocketSendError` / `LDAPCommunicationError`, reconnects to LDAP
2. Resends the *same paging cookie* from the last successful page
3. If the DC rejects the cookie (error `00002040` / `UNAVAIL_EXTENSION`), stops cleanly with a warning instead of re-reading from page 1
4. Tracks consecutive failures; gives up after `_LDAP_SEARCH_ATTEMPTS = 5`

**What we have**: ldap3's built-in `paged_search()` generator, which doesn't handle mid-search reconnection. A dropped connection means the entire search restarts or crashes.

**Why theirs is better**: With throttled collections running for hours, TCP connections will drop (firewall timeouts, NAT tables, server-side idle limits). Their approach loses zero already-read pages. The cookie rejection detection (line 46-57, `_paging_cookie_rejected()`) is critical — without it, a stale cookie causes an infinite retry loop or silent data loss. Their approach also separates the paging logic from ldap3's internals, making it more predictable.

**Key code paths**:
- `_iter_paged()` — the paged search loop with retry logic
- `_resume_search()` — reconnect helper, respects attempt limit
- `_paging_cookie_rejected()` — detects when the DC discarded the result set
- `_next_page_cookie()` — extracts the cookie from the LDAP response controls

---

### 3. TCP Keepalive on LDAP Sockets — HIGH VALUE

**What they have**: `enable_tcp_keepalive(sock)` in `utils.py` lines 858-884. Sets:
- `SO_KEEPALIVE = 1`
- `TCP_KEEPIDLE = 30` (seconds before first probe)
- `TCP_KEEPINTVL = 15` (seconds between probes)
- `TCP_KEEPCNT = 4` (probes before giving up)
- Windows: uses `SIO_KEEPALIVE_VALS` ioctl
- Linux/Mac: uses individual `setsockopt` calls

Called in `authentication.py` line 233 after every successful LDAP bind.

**What we have**: Nothing. Default socket behavior.

**Why theirs is better**: Without keepalive, a TCP connection idle for >2-5 minutes (typical firewall timeout) gets silently dropped. The next LDAP search sends data into a dead socket, gets no response, and eventually times out after the OS-level TCP retransmit timeout (often 2+ minutes on Linux). With keepalive probes every 15s starting at 30s idle, the connection stays alive through long pacing gaps, or detects a dead connection within ~90s instead of minutes.

This is especially critical for our per-OU enumeration where a single OU might process in seconds, but the throttle delay between OUs could be minutes.

---

### 4. Duration Parsing with Unit Suffixes — MEDIUM VALUE

**What they have**: `parse_ldap_interval()` and `parse_throttle()` in `utils.py` lines 601-662.

LDAP intervals accept: `300`, `300s`, `5m`, `1h` (unit = seconds)
Host throttle accepts: `500`, `500ms`, `2s`, `1m`, `1h` (unit = milliseconds for plain numbers)

Also `format_duration()` (line 665) for log output: `5m0s`, `1h2m`.

**What we have**: Raw integer seconds/milliseconds only.

**Why theirs is better**: UX. `--ldap-interval 5m` is immediately clear; `--throttle-interval 300` requires mental conversion. The `format_duration()` helper also makes log output more readable.

---

### 5. Host RPC Throttle — MEDIUM VALUE

**What they have**: `HostCallPacer` class (utils.py lines 746-783) + `call_host()` wrapper (line 848). Every SMB/RPC call in computer enumeration (session enum, local admin, RDP, DCOM, PSRemote) goes through the pacer.

`--throttle DURATION` — delay between host calls (accepts ms/s/m/h suffixes)
`--throttle-jitter PERCENT` — bidirectional jitter (can go below or above the base, never negative)

**What we have**: Nothing for host RPC calls.

**Why theirs is better**: Computer enumeration connects to every machine in the domain over SMB/RPC. Without throttling, this is a burst of connections that triggers IDS rules. Their pacer spaces calls across all worker threads using a single lock, so even with 10 workers the RPC calls are serialized with delays.

Note: This only matters for non-DCOnly collection methods. Our per-OU work is DCOnly, but the throttle infrastructure should be in place for when users run full collection.

---

### 6. Safe JSON Output with .partial Files — MEDIUM VALUE

**What they have**: `concat_slice_files()` in `resume.py` lines 76-103 writes output JSON to `file.partial`, calls `os.fsync()`, then `os.replace(partial, file)`. The `CollectionResume.save()` method (line 315) does the same for checkpoint files.

**What we have**: We use `os.replace()` for state files (atomic rename), but the JSON output files are written directly by `OutputWorker` without atomic write protection.

**Why theirs is better**: If the process crashes or is killed mid-write, a partially-written JSON file is corrupt and cannot be ingested by BloodHound. With `.partial` + `os.replace()`, you either have the complete previous file or the complete new file, never a truncated one. The `os.fsync()` call ensures the data is flushed to disk before the rename, protecting against OS crashes (not just process crashes).

---

### 7. Server Info Caching Across Reconnects — MEDIUM VALUE

**What they have**: In `authentication.py`, after the first successful LDAP bind, they cache `conn.server.info` (rootDSE) and `conn.server.schema` in `self._cached_dsa_info` / `self._cached_schema_info`. On subsequent connections (reconnects, resolver connection, GC connection), they create the Server with `get_info=NONE` and attach the cached info via `server.attach_dsa_info()` / `server.attach_schema_info()`.

**What we have**: Every LDAP connection reads rootDSE and schema independently.

**Why theirs is better**: Each rootDSE + schema read is 2-3 LDAP searches. With pacing active, that's 2-3 × interval delay just for setup on every reconnect. Caching eliminates this overhead. The schema doesn't change during a collection run, and rootDSE changes are irrelevant.

---

### 8. LDAP Search Counting — LOW VALUE

**What they have**: `install_ldap_search_counter()` wraps `ldap3.Connection.search` to increment a global counter. Each enumeration phase logs its search count: `"users: 14 LDAP searches"`.

**Why useful**: Gives visibility into LDAP query volume per phase. Helps estimate total collection time under pacing. Also useful for comparing efficiency of different enumeration strategies.

---

### 9. Log File Support — LOW VALUE

**What they have**: `--log-file PATH` adds a `FileHandler` to the logger at DEBUG level while the console stays at INFO (unless `-v` is also set). The file gets timestamped entries with full debug output.

**Why useful**: For post-mortem debugging of failed runs. The console output scrolls away or gets truncated; a log file persists. Especially valuable for long-running throttled collections where you can't watch the terminal.

---

## Features NOT Recommended for Adoption

### objectGUID Partitioning (`--partition-ldap`)
Splits every phase into 256 slices using `(objectGUID=\XX*)` filters. Each slice is its own file; finished slices are skipped on resume. **Why not**: Our per-OU enumeration achieves the same resume goal more efficiently. Per-OU typically means 10-200 locations vs 256 fixed slices. Our ONE_LEVEL queries are cheaper than their SUBTREE-with-GUID-filter queries. Their approach is only better when you have very few OUs with millions of objects — an unusual AD topology.

### ldapx Proxy Integration
~300 lines managing ldapx child processes (`LdapxLauncher` class), plus rootDSE discovery through the proxy, LDAPS channel binding credential management, and proxy restart logic. **Why not**: ldapx is a separate tool for LDAP filter/attribute obfuscation. It's a specialized OPSEC technique that adds significant complexity (subprocess management, credential passing, listener lifecycle). If needed, it can be used externally without code changes.

### SOCKS5 for Kerberos Traffic
~100 lines patching impacket's `sendReceive` to route KRB5 TCP through a SOCKS5 proxy. **Why not**: Only needed in ldapx mode when the KDC is behind a proxy. Complex monkey-patching of impacket internals. Can be handled by OS-level proxy tools (proxychains, etc.) if needed.

### Phase-Level Resume (without partitioning)
Their base resume tracks finished phases only. If interrupted during "users" collection with 15,000/20,000 done, all 20,000 are re-queried. **Why not**: Our per-OU resume skips completed OUs entirely. If 80/100 OUs are done, only 20 are re-queried. Strictly more efficient.

---

## Implementation Priority

| Priority | Feature | Effort | Files Changed |
|----------|---------|--------|---------------|
| 1 | TCP keepalive | ~30 lines | `utils.py`, `authentication.py` |
| 2 | Two-tier LDAP pacing | ~200 lines | `utils.py`, `__init__.py` (CLI args), `throttle.py` refactor |
| 3 | Paging cookie retry | ~100 lines | `domain.py` |
| 4 | Duration parsing | ~60 lines | `utils.py`, `__init__.py` |
| 5 | Safe JSON .partial writes | ~20 lines | output worker |
| 6 | Server info caching | ~15 lines | `authentication.py` |
| 7 | LDAP search counting | ~20 lines | `utils.py` |
| 8 | Host RPC throttle | ~50 lines | `utils.py`, `computers.py` |
| 9 | Log file support | ~15 lines | `__init__.py` |
