"""New development contract; Spanish source-task v1 is not rewritten."""
import copy

from ..grounded.tasks import case_ids as source_case_ids, get_task as source_task
from ..grounded.tasks import task_manifest as source_manifest
from ..ops.generator import digest


VERSION = 'wildfire-protection-development-tasks-v1'
CONFIGURATION = dict(smoke_radius_cells=2, exposure_threshold=120,
                     open_dose_per_tick=10, sheltered_dose_per_tick=2,
                     broadcast_delay_minutes=2, first_broadcast_extra_delay_minutes=0,
                     broadcast_failures=0)


def case_ids(pack):
    return [identifier.replace('es-dev-', 'protect-dev-', 1) for identifier in source_case_ids(pack)]


def get_task(pack, identifier):
    if not isinstance(identifier, str) or identifier not in case_ids(pack):
        raise ValueError('Unknown protection development case')
    task = source_task(pack, identifier.replace('protect-dev-', 'es-dev-', 1))
    task.update(id=identifier, scenario_id=identifier, task_set='protection_development',
                generator_version=VERSION)
    task['scenario']['id'] = identifier
    task['scenario']['protection'] = copy.deepcopy(CONFIGURATION)
    if identifier.endswith('-v1'):
        task['scenario']['protection']['first_broadcast_extra_delay_minutes'] = 6
    elif identifier.endswith('-v2'):
        task['scenario']['protection']['broadcast_failures'] = 1
    return task


def task_manifest(pack):
    return dict(version=VERSION, anchor_pack_sha256=pack['sha256'], split='development',
                source_task_manifest_sha256=digest(source_manifest(pack)),
                cases=[dict(id=identifier, group_id=get_task(pack, identifier)['group_id'],
                            seed=get_task(pack, identifier)['seed'], sha256=digest(get_task(pack, identifier)))
                       for identifier in case_ids(pack)],
                protection_assumptions=dict(configuration=copy.deepcopy(CONFIGURATION),
                    timing='Delivery, acknowledgement one minute later, execution two minutes after delivery',
                    replacement='Old orders remain in flight; recipient sequence checks apply only after receipt',
                    shelter='Fivefold lower abstract nearby-smoke dose, no protection from direct fire; not medical units',
                    warning_team='One on-scene crew within two cells, occupied for four model minutes; no real route claim',
                    reward='Safety-gated objective fraction; component flags retained; not calibrated utility'))
