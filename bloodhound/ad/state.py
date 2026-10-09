import os
import json
import logging
import datetime

STATE_VERSION = 2


class EnumerationState:
    def __init__(self, statefile=None):
        self.statefile = statefile
        self.completed_phases = []
        self.enumeration_tree = {}
        self.phase_data = {}
        self.timestamp = None
        self.domain = None
        self.search_base = None
        self.locations = []
        self.completed_locations = {}
        self._processed_dns = set()
        self._processed_dns_count_at_save = 0
        if statefile:
            self._load()

    @property
    def enabled(self):
        return self.statefile is not None

    def _load(self):
        if not self.statefile or not os.path.exists(self.statefile):
            return
        try:
            with open(self.statefile, 'r', encoding='utf-8') as f:
                data = json.load(f)
            file_version = data.get('version', 0)
            if file_version != STATE_VERSION:
                logging.warning('State file version %d does not match expected %d. Starting fresh.',
                                file_version, STATE_VERSION)
                return
            self.completed_phases = data.get('completed_phases', [])
            self.enumeration_tree = data.get('enumeration_tree', {})
            self.phase_data = data.get('phase_data', {})
            self.timestamp = data.get('timestamp')
            self.domain = data.get('domain')
            self.search_base = data.get('search_base')
            self.locations = data.get('locations', [])
            self.completed_locations = data.get('completed_locations', {})
            self._processed_dns = set(data.get('processed_dns', []))
            self._processed_dns_count_at_save = len(self._processed_dns)
            logging.info('Loaded enumeration state from %s (%d phases complete, %d locations complete, %d entries processed)',
                         self.statefile, len(self.completed_phases),
                         len(self.completed_locations), len(self._processed_dns))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.warning('Could not load state file %s: %s. Starting fresh.', self.statefile, str(e))

    def save(self):
        if not self.statefile:
            return
        parent_dir = os.path.dirname(self.statefile)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        data = {
            'version': STATE_VERSION,
            'saved_at': datetime.datetime.now().isoformat(),
            'domain': self.domain,
            'search_base': self.search_base,
            'timestamp': self.timestamp,
            'completed_phases': self.completed_phases,
            'enumeration_tree': self.enumeration_tree,
            'phase_data': self.phase_data,
            'locations': self.locations,
            'completed_locations': self.completed_locations,
            'processed_dns': list(self._processed_dns),
        }
        tmpfile = self.statefile + '.tmp'
        with open(tmpfile, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmpfile, self.statefile)
        self._processed_dns_count_at_save = len(self._processed_dns)

    def reset(self):
        self.completed_phases = []
        self.enumeration_tree = {}
        self.phase_data = {}
        self.timestamp = None
        self.domain = None
        self.search_base = None
        self.locations = []
        self.completed_locations = {}
        self._processed_dns = set()
        self._processed_dns_count_at_save = 0
        if self.statefile and os.path.exists(self.statefile):
            os.remove(self.statefile)
            logging.info('State file removed: %s', self.statefile)

    def is_phase_complete(self, phase_name):
        return phase_name in self.completed_phases

    def mark_phase_complete(self, phase_name, count=None):
        if phase_name not in self.completed_phases:
            self.completed_phases.append(phase_name)
        if count is not None:
            self.phase_data[phase_name] = {'count': count}
        self.save()
        logging.info('Phase "%s" marked complete in state file', phase_name)

    def is_dn_enumerated(self, dn):
        return dn in self.enumeration_tree

    def record_children(self, parent_dn, child_objects):
        self.enumeration_tree[parent_dn] = child_objects
        self.save()

    def get_cached_children(self, parent_dn):
        return self.enumeration_tree.get(parent_dn)

    def get_timestamp(self):
        return self.timestamp

    def set_timestamp(self, timestamp):
        self.timestamp = timestamp
        self.save()

    def set_domain(self, domain):
        self.domain = domain
        self.save()

    def set_search_base(self, search_base):
        self.search_base = search_base
        self.save()

    def set_locations(self, locations):
        self.locations = locations
        self.save()
        logging.info('Discovered %d locations (OUs/containers) for enumeration', len(locations))

    def is_location_complete(self, dn):
        return dn in self.completed_locations

    def mark_location_complete(self, dn, counts=None):
        self.completed_locations[dn] = counts or {}
        self.save()
        logging.debug('Location complete: %s (%s)', dn, counts)

    def is_entry_processed(self, dn):
        return dn in self._processed_dns

    def record_entry(self, dn):
        self._processed_dns.add(dn)
        new_count = len(self._processed_dns)
        if new_count - self._processed_dns_count_at_save >= 50:
            self.save()
            logging.debug('State checkpoint: %d entries processed', new_count)
