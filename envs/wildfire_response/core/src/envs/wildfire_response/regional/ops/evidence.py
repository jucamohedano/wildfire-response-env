"""Assessment and notice records grounded in the controller's public evidence."""
import copy
import math


EVIDENCE_VERSION = 'wildfire-ops-evidence-v1'
EVIDENCE_FIELDS = {'triage'}
MAX_EVIDENCE_AGE = 4
NOTICE_RADIUS = 6


def local_threat(observation, district):
    memory = {(cell['x'], cell['y']): cell for cell in observation['sensor_memory']}
    for cell in memory.values():
        if (cell['burning'] and observation['minute'] - cell['observed_at'] <= MAX_EVIDENCE_AGE
                and math.dist(district['home'], [cell['x'], cell['y']]) <= NOTICE_RADIUS):
            return True
    for report in observation['reports']:
        location = report.get('location')
        if location is None or math.dist(district['home'], location) > NOTICE_RADIUS:
            continue
        cell = memory.get(tuple(location))
        if (cell is None or cell['observed_at'] < report['observed_at']
                or observation['minute'] - cell['observed_at'] > MAX_EVIDENCE_AGE
                or cell['burning']):
            return True
    return False


class EvidenceLedger:
    def __init__(self):
        self._assessments = {}
        self._notices = []
        self._notice_keys = set()
        self.redundant_notices = 0
        self.unsupported_evacuations = 0

    def validate(self, action, observation):
        triage = action.get('triage', [])
        if not isinstance(triage, list) or len(triage) > 12:
            raise ValueError('Triage is a list of at most twelve assessments')
        reports = {report['id']: report for report in observation['reports']}
        memory = {(cell['x'], cell['y']): cell for cell in observation['sensor_memory']}
        selected = set()
        records = []
        for assessment in triage:
            if not isinstance(assessment, dict) or set(assessment) != {'report_ids', 'assessment'}:
                raise ValueError('Triage needs report_ids and assessment')
            identifiers = assessment['report_ids']
            if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 20:
                raise ValueError('Each assessment needs one to twenty received report IDs')
            if assessment['assessment'] not in ('fire_observed', 'no_fire_observed', 'defer'):
                raise ValueError('Choose fire_observed, no_fire_observed or defer')
            claims = []
            for identifier in identifiers:
                if not isinstance(identifier, str) or identifier not in reports or identifier in selected:
                    raise ValueError('Assess received report IDs at most once per action')
                selected.add(identifier)
                claims.append(reports[identifier])
            first = claims[0]
            if any((claim.get('location'), claim['observed_at']) !=
                   (first.get('location'), first['observed_at']) for claim in claims):
                raise ValueError('Linked duplicate reports must share a location and observation time')
            cell = memory.get(tuple(first['location'])) if first.get('location') is not None else None
            if assessment['assessment'] != 'defer':
                if (cell is None or cell['observed_at'] < first['observed_at']
                        or observation['minute'] - cell['observed_at'] > MAX_EVIDENCE_AGE):
                    raise ValueError('A conclusive assessment requires fresh sensor evidence at the report location')
                if (assessment['assessment'] == 'fire_observed') != cell['burning']:
                    raise ValueError('Assessment must match the observed sensor state')
            for identifier in identifiers:
                records.append(dict(report_id=identifier, assessment=assessment['assessment'],
                    assessed_at=observation['minute'],
                    evidence_observed_at=cell['observed_at'] if cell else None,
                    duplicate_of=identifiers[0] if identifier != identifiers[0] else None))
        return records

    def apply(self, action, observation, assessments):
        for record in assessments:
            self._assessments[record['report_id']] = copy.deepcopy(record)
        districts = {district['id']: district for district in observation['districts']}
        for message in action.get('messages', []):
            key = (message['recipient'], message['purpose'])
            self.redundant_notices += key in self._notice_keys
            self._notice_keys.add(key)
            district = districts.get(message['recipient'])
            if (district is not None and message['purpose'] == 'evacuate'
                    and not local_threat(observation, district)):
                self.unsupported_evacuations += 1
            self._notices.append(dict(issued_at=observation['minute'], **message))

    def public_state(self):
        return dict(report_assessments=copy.deepcopy(list(self._assessments.values())),
                    issued_notices=copy.deepcopy(self._notices))
