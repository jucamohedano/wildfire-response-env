"""Record explicit day-mode dispositions for the 32 earlier structural warnings."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.expansion.catalog import get_task
from wildfire_core.scenario_quality import make_context, validate_task


def review(prepared, domain, catalog):
    if digest({key: value for key, value in catalog.items() if key != 'sha256'}) != catalog['sha256']:
        raise ValueError('Existing catalog pin differs')
    context = make_context(prepared, domain)
    if catalog['prepared_inputs_sha256'] != prepared['sha256'] or catalog['topographic_inputs_sha256'] != domain['sha256']:
        raise ValueError('Source input pins differ')
    dispositions = []
    for group in catalog['groups']:
        for case in group['cases']:
            task = get_task(catalog, context['inputs'], case['id'])
            warnings = [item for item in validate_task(task, context, family=group['family'])
                        if item['code'] in ('preignition_wind_shift', 'population_at_ignition')]
            if not warnings:
                continue
            ignition = [event for event in task['scenario']['events'] if event['kind'] == 'ignition']
            wind = [event for event in task['scenario']['events'] if event['kind'] == 'wind']
            has_population = any(item['code'] == 'population_at_ignition' for item in warnings)
            dispositions.append(dict(task_id=case['id'], group_id=group['id'], split=group['split'],
                original_configuration_sha256=group['configuration_sha256'], findings=warnings,
                inspected=dict(ignitions=ignition, wind_changes=wind,
                    population_locations={key: dict(home=value['home'], refuge=value['refuge'])
                                          for key, value in context['population'].items()}),
                disposition='exclude_from_day_training_pending_redesign' if has_population else 'retain_frozen_preignition_weather_case',
                rationale=('An initially unsafe fixed refuge needs selectable viable destinations and relocation. '
                           'Do not import this legacy case as a day task.' if has_population else
                           'All meaningful changes precede ignition; valid as pre-ignition context, '
                           'not evidence of post-ignition adaptation. Do not import this legacy case as a day task.'),
                day_mode_eligible=False, original_modified=False, expert_validated=False))
    report = dict(version='incident-day-legacy-variant-curation-v1', catalog_sha256=catalog['sha256'],
        prepared_inputs_sha256=prepared['sha256'], domain_sha256=domain['sha256'],
        role='private_evaluator_curation', held_out_outcomes_inspected=False, policy_rollouts=0,
        interpretation='Per-case structural inspection and scope decision, not operational expert validation',
        cases=dispositions)
    report['sha256'] = digest(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('prepared', 'domain', 'catalog', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    arguments = parser.parse_args()
    report = review(*(json.loads(getattr(arguments, field).read_text()) for field in ('prepared', 'domain', 'catalog')))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    arguments.output.chmod(0o600)
    print(json.dumps(dict(reviewed=len(report['cases']), original_modified=False, day_tasks_imported=0, sha256=report['sha256'])))


if __name__ == '__main__':
    main()
