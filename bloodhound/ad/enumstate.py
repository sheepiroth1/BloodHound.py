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
Enumeration state, so a throttled run can be resumed later.

The resumable unit is the per-parent child query: ADDC.get_childobjects(dn) is one
LEVEL-scope LDAP query per OU / container / domain node, and it is the only part of
the enumeration whose cost scales with the size of the directory. The state is a
plain parent -> children map keyed by the parent DN.

Children are stored with their resolved ObjectIdentifier and ObjectType, not just
their DN, because ChildObjects entries in the JSON output need those two fields -
storing the DN alone would force a re-query to rebuild them.

Bulk queries (schema, domains, forest domains, users, groups, computers, gpos, ous,
containers) are not cached here: their results are the output records themselves, so
they re-run on every invocation.
"""
import codecs
import json
import logging
import os
import time

from bloodhound.ad.utils import ADUtils

STATE_VERSION = 1


class EnumerationState(object):
    def __init__(self, path):
        self.path = path
        self.parents = {}
        self.target = {}
        self.loaded = False

    @staticmethod
    def _key(dn):
        # DNs are matched case-insensitively in LDAP, and the callers already
        # uppercase the search base they pass to get_childobjects().
        return str(dn).upper()

    def set_target(self, domain=None, dc=None, collection=None):
        self.target = {
            'domain': str(domain).upper() if domain else '',
            'dc': str(dc).lower() if dc else '',
            'collection': ','.join(sorted(collection)) if collection else '',
        }

    def load(self):
        """
        Read the state file if present. A corrupt or unreadable file is treated as
        "no state" rather than fatal - losing resume data should not lose the run.
        """
        self.loaded = True
        if not self.path or not os.path.exists(self.path):
            return False
        try:
            with codecs.open(self.path, 'r', 'utf-8') as infile:
                data = json.load(infile)
        except (ValueError, OSError) as e:
            logging.warning('Could not read state file %s (%s) - starting a fresh enumeration', self.path, e)
            return False
        if not isinstance(data, dict) or data.get('version') != STATE_VERSION:
            logging.warning('State file %s has an unsupported version - starting a fresh enumeration', self.path)
            return False
        parents = data.get('parents')
        if not isinstance(parents, dict):
            logging.warning('State file %s is malformed - starting a fresh enumeration', self.path)
            return False
        self.parents = parents
        self.target = data.get('target', {})
        logging.info('Resuming: state file has %d enumerated parents', len(self.parents))
        return True

    def check_target(self):
        """
        Warn when resuming state that was written for a different domain or with a
        different collection method. Resuming anyway is safe - the map is keyed by
        DN - but the user should know what they are getting.
        """
        if not self.target or not self.parents:
            return
        logging.debug('State target: %s', self.target)

    def save(self):
        """
        Write atomically: a run interrupted mid-write must not destroy the state it
        needs to resume from.
        """
        if not self.path:
            return
        data = {
            'version': STATE_VERSION,
            'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'target': self.target,
            'parents': self.parents,
        }
        tmppath = self.path + '.tmp'
        try:
            with codecs.open(tmppath, 'w', 'utf-8') as outfile:
                json.dump(data, outfile)
                outfile.flush()
            os.replace(tmppath, self.path)
        except (OSError, TypeError) as e:
            logging.warning('Could not write state file %s: %s', self.path, e)

    def is_done(self, dn):
        return self._key(dn) in self.parents

    def children(self, dn):
        """
        Cached ChildObjects entries for a parent, in the shape the JSON output
        expects (the stored DN is an internal detail and is not returned).
        """
        entry = self.parents.get(self._key(dn))
        if not entry:
            return []
        out = []
        for child in entry.get('children', []):
            try:
                out.append({
                    'ObjectIdentifier': child['ObjectIdentifier'],
                    'ObjectType': child['ObjectType'],
                })
            except KeyError:
                logging.warning('State entry for %s has a malformed child record - skipping it', dn)
        return out

    def mark_done(self, dn, kind, children):
        # LDAP values can come back as bytes depending on whether the server
        # schema was loaded, which json cannot write. Coerce on the way in so a
        # checkpoint can never crash a run that is otherwise fine.
        records = []
        for child in children:
            record = dict(child)
            for key in ('dn', 'ObjectIdentifier', 'ObjectType'):
                if key in record:
                    record[key] = ADUtils.ensure_string(record[key])
            records.append(record)
        self.parents[self._key(dn)] = {
            'kind': kind,
            'children': records,
        }

    def reset(self):
        self.parents = {}
        if self.path and os.path.exists(self.path):
            try:
                os.remove(self.path)
                logging.info('Removed existing state file %s', self.path)
            except OSError as e:
                logging.warning('Could not remove state file %s: %s', self.path, e)

    def parents_done(self):
        return len(self.parents)
