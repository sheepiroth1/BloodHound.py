# Per-OU Enumeration with LDAP Throttling and Resume

**Commit:** `1576d8b`
**Branch:** `bloodhound-ce`

---

## Problem

When running BloodHound.py against large Active Directory environments with LDAP throttling enabled, enumeration takes hours or days. The process will be interrupted and resumed many times. The original design uses three SUBTREE queries (users, groups, computers) that scan the entire domain. LDAP paged searches cannot resume across connections, so every interruption restarts the full SUBTREE query from page 1 — repeating all throttle delays for pages already processed.

## Solution

### 1. LDAP Query Throttling (`bloodhound/ad/throttle.py`)

`ThrottledConnection` subclasses `ldap3.Connection` and overrides `search()` to inject a configurable delay before every LDAP query, including internal paged search calls. This works because ldap3's `paged_search(generator=True)` calls `Connection.search()` once per page internally.

- `--throttle-interval`: seconds between queries
- `--throttle-jitter-min` / `--throttle-jitter-max`: random jitter range added to interval
- Page size fixed at 250

All four `Connection()` calls in `authentication.py` were replaced with `ThrottledConnection()`.

### 2. Per-OU Enumeration with Gap-Fill (`bloodhound/enumeration/memberships.py`)

Instead of three domain-wide SUBTREE queries, enumeration now proceeds in two phases:

**Phase 1 — Per-location enumeration** (new `enumerate_per_location()`):
1. Discover all OUs and non-filtered containers (2 SUBTREE queries — same ones BloodHound runs later for container collection)
2. For each location, run ONE combined ONE_LEVEL query: `(|(objectClass=group)(sAMAccountType=805306369)(&(objectCategory=person)(objectClass=user)))`
3. Process results by object type (user/group/computer), write to output files
4. Record each entry DN in state, mark location complete

**Phase 2 — Gap-fill** (existing `enumerate_users()`, `enumerate_groups()`, `enumerate_computers_dconly()`):
1. Run original SUBTREE queries across the full search base
2. Skip entries already processed in Phase 1 (checked via `state.is_entry_processed(dn)`)
3. Write only newly-discovered entries to separate `gapfill_*.json` files
4. Both per-location and gapfill output files are valid for the BloodHound CE ingester

**Why gap-fill is necessary:** ONE_LEVEL queries only find direct children of each OU/container. Objects in locations that the discovery step didn't find (e.g., objects directly under the domain root in unusual containers, or objects in newly-created OUs between discovery and enumeration) would be missed. The SUBTREE gap-fill guarantees completeness — the pre and post change output is identical.

**Why per-location runs only with `--state`:** Without state tracking, per-location adds overhead (extra queries) with no benefit since there's nothing to resume from. When `--state` is not provided, the original SUBTREE-only flow runs unchanged.

### 3. Enumeration State Tracking (`bloodhound/ad/state.py`)

State file (JSON) tracks:
- `locations`: all discovered OUs/containers
- `completed_locations`: locations fully enumerated, with per-type counts
- `processed_dns`: set of all processed entry DNs (for gap-fill skip)
- `completed_phases`: which phases (per_location, users, groups, etc.) are done

State is checkpointed every 50 entries via `record_entry()` and on every location/phase completion. Atomic writes via `os.replace()` prevent corruption on interrupt.

### 4. Search Base Scoping (`--search-base`)

Scopes bulk enumeration (users, groups, computers, OUs, containers) to a specific OU while preserving domain-level queries (trusts, schema, forest domains) against the domain root.

State/cache filenames auto-include domain and search base to prevent cross-run collisions: `<prefix>_<domain>[_<search_base_slug>].json`

### 5. Resolver Cache Persistence

`dncache` and `sidcache` are saved alongside the state file (`.cache.json`). On resume, the cache is loaded first so DN/SID resolution doesn't re-query LDAP for previously-resolved objects.

---

## Resume Behavior

| Scenario | What happens on resume |
|---|---|
| Location fully enumerated | Skipped entirely — zero LDAP queries, zero throttle cost |
| Location partially done | ONE_LEVEL re-runs for that OU only (typically 1 page) |
| Per-location phase complete, gap-fill not started | SUBTREE queries run, already-processed entries skipped |
| Per-location + gap-fill both complete | Both skipped — moves to next phase |
| No `--state` flag | Original behavior — no per-location phase |

## Query Overhead vs Original

| Phase | Queries (original) | Queries (new) |
|---|---|---|
| Discovery | 0 | 2 (OUs + containers) — same queries run again later in container collection |
| Per-location | 0 | 1 per OU/container (ONE_LEVEL combined) |
| Gap-fill | 3 SUBTREE (users, groups, computers) | Same 3 SUBTREE, but most entries skipped |
| **Total** | 3 | 2 + N + 3 (where N = number of OUs/containers) |

The tradeoff: more total queries on a clean run, but dramatically fewer queries on resume after interruption. For the intended use case (throttled enumeration stopped and resumed many times), this is a net win.

## Files Changed

| File | Change |
|---|---|
| `bloodhound/ad/throttle.py` | New — `LDAPThrottle`, `ThrottledConnection`, page size config |
| `bloodhound/ad/state.py` | New — `EnumerationState` with per-location tracking |
| `bloodhound/ad/domain.py` | Added `get_objects_in_location()`, `search_baseDN` fallback, cache save/load |
| `bloodhound/ad/authentication.py` | `Connection` → `ThrottledConnection` (4 call sites) |
| `bloodhound/ad/utils.py` | Added `SidCache.as_dict()` for cache serialization |
| `bloodhound/enumeration/memberships.py` | Added `enumerate_per_location()`, gap-fill skip logic in 3 methods |
| `bloodhound/enumeration/domains.py` | State-aware domain child object enumeration |
| `bloodhound/__init__.py` | CLI args, state file path generation, orchestration wiring |
