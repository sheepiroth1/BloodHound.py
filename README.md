# BloodHound.py (Modified Fork)
![Python 3 compatible](https://img.shields.io/badge/python-3.x-blue.svg)
![License: MIT](https://img.shields.io/pypi/l/bloodhound.svg)

Modified fork of [BloodHound.py CE](https://github.com/dirkjanm/BloodHound.py) with LDAP throttling, per-OU resume, connection resilience, and stealth-oriented pacing controls.

## What This Fork Adds

| Feature | Why It Matters |
|---------|---------------|
| Two-tier LDAP pacing | Separate delays for new searches vs. continuation pages — slower where it counts for OPSEC, faster where it doesn't |
| Host RPC/SMB pacing | Throttle SMB connections during computer enumeration to avoid IDS bursts |
| Per-OU enumeration with resume | Interrupted runs skip completed OUs entirely — zero re-query, zero wasted throttle time |
| Paging cookie retry | Dropped connections mid-search resend the same paging cookie instead of restarting from page 1 |
| TCP keepalive on LDAP sockets | Prevents silent connection drops during long pacing gaps (firewall/NAT timeouts) |
| Server info caching | rootDSE and schema are read once and reused on reconnects — saves 2-3 LDAP queries per reconnect under pacing |
| Safe JSON output | Writes to `.partial` files with fsync + atomic rename — a crash never corrupts output |
| LDAP search counter | Per-phase search counts logged for visibility into query volume |
| Log file support | Debug-level output to a file for post-mortem analysis of long runs |
| Duration syntax | `--ldap-interval 5m` instead of `--ldap-interval 300` |

## Installation

```bash
git clone <this-repo>
cd bloodhound-ce_claude
pip install .
```

## Quick Start

Standard (no throttling):
```bash
bloodhound-ce-python -u user@domain.local -p 'password' -d domain.local -c DCOnly
```

Throttled with resume:
```bash
bloodhound-ce-python -u user@domain.local -k -d domain.local -c DCOnly \
  --ldap-interval 30s --ldap-jitter 20 \
  --ldap-page-interval 5s \
  --state myrun --log-file run.log
```

Resume after interruption (same command):
```bash
bloodhound-ce-python -u user@domain.local -k -d domain.local -c DCOnly \
  --ldap-interval 30s --ldap-jitter 20 \
  --ldap-page-interval 5s \
  --state myrun --log-file run.log
```

## Parameters

### Authentication

| Flag | Description |
|------|-------------|
| `-u`, `--username` | Username. Use `user@otherdomain` for cross-domain auth |
| `-p`, `--password` | Password. Omit to be prompted |
| `-k`, `--kerberos` | Authenticate using a Kerberos ccache file (set `KRB5CCNAME` env var) |
| `--hashes LM:NT` | Pass-the-hash. Use `aad3b435b51404eeaad3b435b51404ee:NTHASH` for NT-only |
| `-aesKey HEX` | Kerberos AES128 or AES256 key |
| `-no-pass` | Don't prompt for a password (useful with `-k`) |
| `--auth-method` | `auto` (default), `kerberos`, or `ntlm`. Auto tries Kerberos first, falls back to NTLM |

### Collection

| Flag | Description |
|------|-------------|
| `-c`, `--collectionmethod` | What to collect. Comma-separated. Default: `Group,LocalAdmin,Session,Trusts`. See `--help` for full list. `DCOnly` avoids connecting to member machines |
| `-d`, `--domain` | Target domain. Auto-detected from DNS if omitted |
| `-dc` | Override the domain controller hostname (must be FQDN, not IP) |
| `-gc` | Override the Global Catalog hostname |
| `-ns` | Custom DNS server IP |
| `--dns-tcp` | Use TCP for DNS queries instead of UDP |
| `--dns-timeout` | DNS timeout in seconds (default: 3) |
| `-w`, `--workers` | Threads for computer enumeration (default: 10). Only matters for non-DCOnly methods |
| `--exclude-dcs` | Skip domain controllers during computer enumeration |
| `--computerfile` | File with FQDNs to limit computer enumeration to |
| `--search-base` | Custom LDAP search base DN (e.g. `OU=Sales,DC=corp,DC=local`). Limits enumeration scope to a subtree. Trust and domain-level queries still use the domain root |

### LDAP Throttling

These control how fast LDAP queries are sent. Use them when stealth matters or to avoid rate-limiting.

| Flag | Example | What It Does |
|------|---------|-------------|
| `--ldap-interval` | `30s`, `5m`, `1h` | Minimum delay before each **new** LDAP search (new filter = new search). This is the primary stealth knob — SIEMs correlate on distinct LDAP operations |
| `--ldap-jitter` | `20` | Percentage (0-100) of random extra delay added on top of `--ldap-interval`. Makes timing less predictable. 20 means 0-20% extra |
| `--ldap-page-interval` | `5s`, `1m` | Minimum delay before each **continuation page** of the same search. Pages are the server sending the next batch from an already-open result set — less detection value than new searches, so you typically set this lower |
| `--ldap-page-jitter` | `10` | Percentage (0-100) of random extra delay added to `--ldap-page-interval` |

**Why two tiers?** A domain with 20,000 objects has ~100 pages but only ~20 distinct searches. With a single 30s delay on everything, collection takes 60 minutes. With 30s for searches and 5s for pages, it takes ~18 minutes — same search-level stealth, 3.3x faster.

Duration values accept: plain number (seconds), or suffix `s` (seconds), `m` (minutes), `h` (hours).

### Host RPC Throttling

Controls timing of SMB/RPC connections during computer enumeration (session, localadmin, rdp, dcom, psremote, loggedon). Only relevant for non-DCOnly collection methods.

| Flag | Example | What It Does |
|------|---------|-------------|
| `--throttle` | `500ms`, `2s`, `1m` | Minimum delay between host RPC calls, serialized across all worker threads. Plain number = milliseconds. Suffixes: `ms`, `s`, `m`, `h` |
| `--throttle-jitter` | `30` | Percentage (0-100) of **bidirectional** jitter. 30 means the actual delay varies ±30% around the base value |

### Resume and State

Allow interrupted runs to continue without re-querying completed work.

| Flag | What It Does |
|------|-------------|
| `--state NAME` | Enable state tracking. Creates `NAME_domain[_searchbase].json` and a `.cache.json` resolver cache. Re-run with the same `--state` value to resume — completed OUs and phases are skipped entirely |
| `--reset-state` | Discard the existing state file and start fresh |

The state file tracks which OUs and collection phases are complete. On resume, completed OUs produce zero LDAP queries and zero throttle delay. Partially-completed OUs re-query only that OU (typically one page).

### Output

| Flag | What It Does |
|------|-------------|
| `-op`, `--outputprefix` | Prepend this string to all output filenames |
| `--zip` | Compress output JSON files into a timestamped zip and delete the originals |
| `--log-file PATH` | Write debug-level log to a file. Console output level is unchanged (INFO unless `-v` is set). Useful for post-mortem debugging of long throttled runs |
| `-v` | Verbose console output (DEBUG level) |

### Other

| Flag | What It Does |
|------|-------------|
| `--use-ldaps` | Connect to LDAP over TLS (port 636) by default |
| `--ldap-channel-binding` | Use LDAP channel binding. Forces LDAPS |
| `--disable-pooling` | Disable subprocesses for ACL parsing (debug only) |
| `--disable-autogc` | Don't auto-select a Global Catalog server |
| `--cachefile` | Load a resolver cache file (experimental) |

## Connection Resilience

When a connection drops mid-search (common during multi-hour throttled runs):

1. The tool reconnects to the DC automatically
2. Resends the **same paging cookie** from the last successful page — no data is re-read
3. If the DC rejects the stale cookie (it discarded the result set), the search stops cleanly with a warning instead of silently restarting from page 1
4. After 5 consecutive failures on the same page, the tool gives up with a clear error

TCP keepalive probes (every 15s starting at 30s idle) keep connections alive through long pacing gaps and detect dead sockets within ~90s instead of minutes.

## Credits

Original BloodHound.py by Dirk-jan Mollema, Edwin van Vliet and Matthijs Gielen from [Fox-IT (NCC Group)](https://fox-it.com/). Maintained by Dirk-jan Mollema from [Outsider Security](https://outsidersecurity.nl).

Some features in this fork were informed by [bloodhound-ldapx.py](https://github.com/An0nUD4Y/bloodhound-ldapx.py) by An0nUD4Y (two-tier pacing, cookie retry, TCP keepalive, server info caching, host throttle, search counting, log file, safe output writes, duration parsing). See `comparison_bloodhound_ldapx.md` for a detailed feature comparison.
