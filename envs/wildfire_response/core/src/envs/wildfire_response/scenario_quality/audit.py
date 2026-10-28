"""Static catalog audit without policy rollouts or held-out outcome inspection."""
from collections import Counter
import hashlib
from pathlib import Path

from ..regional.ops.generator import digest
from ..regional.stress import configuration_digest
from .rules import RULES, VERSION, validate_context, validate_task


def rules_digest():
    return digest(dict(version=VERSION, rules=RULES, files={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(__file__).parent.glob('*.py'))}))


def audit_catalog(catalog, resolve, context):
    cases, problems = [], []
    identifiers, seeds, configurations, layouts, groups = set(), set(), set(), {}, set()
    expected_counts = Counter()

    def problem(message):
        problems.append(dict(code='catalog_integrity', severity='error', location='catalog', message=message))

    fingerprint = digest({key: value for key, value in catalog.items() if key != 'sha256'})
    if fingerprint != catalog.get('sha256'):
        problem('Catalog content does not match its digest')
    if (catalog.get('prepared_inputs_sha256') != context['domain']['prepared_inputs_sha256']
            or catalog.get('topographic_inputs_sha256') != context['domain']['sha256']
            or catalog.get('source_groups') != 1):
        problem('Catalog source pins/count differ from the single-landscape input context')
    entries = []
    for group in catalog.get('groups', []):
        group = dict(group, family=group.get('family') or group.get('factors', {}).get('family'))
        split = group.get('split')
        if split not in ('train', 'validation', 'test'):
            problem('Unknown group split')
        expected_counts[split] += 1
        if group.get('id') in groups:
            problem('Configuration group ID is duplicated')
        groups.add(group.get('id'))
        if group.get('configuration_sha256') in configurations:
            problem('Physical configuration occurs in more than one group')
        configurations.add(group.get('configuration_sha256'))
        if len(group.get('cases', [])) != catalog.get('variants_per_group'):
            problem('Group variant count differs from catalog declaration')
        variants = [case.get('variant') for case in group.get('cases', [])]
        if (any(type(value) is not int for value in variants)
                or sorted(variants) != list(range(catalog.get('variants_per_group', 0)))):
            problem('Group variants must name each declared variant exactly once')
        entries.extend((group, case) for case in group.get('cases', []))
    if dict(expected_counts) != catalog.get('split_groups'):
        problem('Actual split counts differ from catalog declaration')
    for case in catalog.get('development', []):
        entries.append((dict(split='development', family=case.get('family'),
                             configuration_sha256=case.get('configuration_sha256')), case))
    for group, case in entries:
        identifier = case.get('id')
        findings = []
        if identifier in identifiers or case.get('seed') in seeds:
            findings.append(dict(code='catalog_integrity', severity='error', location='case', message='Task ID or episode seed is duplicated'))
        identifiers.add(identifier)
        seeds.add(case.get('seed'))
        try:
            task = resolve(identifier)
            findings.extend(validate_task(task, context, family=group.get('family')))
            if (task['seed'] != case['seed'] or task.get('split') != group['split']
                    or configuration_digest(task) != group['configuration_sha256']):
                findings.append(dict(code='catalog_integrity', severity='error', location='case', message='Materialized task differs from declared seed, split or configuration'))
            if catalog['version'].startswith('wildfire-synthetic-expansion'):
                from ..regional.expansion.catalog import layout_key, split_for_layout
                key = layout_key(task)
                if (group.get('layout_sha256') != key or split_for_layout(key) != group['split']
                        or layouts.setdefault(key, group['split']) != group['split']):
                    findings.append(dict(code='catalog_integrity', severity='error', location='case', message='Matching incident-location layouts cross splits or disagree with their pin'))
        except (KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
            findings.append(dict(code='compile_failure', severity='error', location='case', message=f'Cannot materialize/validate task: {type(error).__name__}'))
        cases.append(dict(task_id=identifier, group_id=group.get('id') or group.get('configuration_sha256'),
                          split=group['split'], family=group.get('family'), findings=findings))
    return dict(version=VERSION, rules_sha256=rules_digest(), catalog_version=catalog['version'],
        catalog_sha256=catalog['sha256'], catalog_findings=problems, cases=cases,
        context_findings=validate_context(context),
        role='private_evaluator_quality_findings', policy_rollouts=0, held_out_outcomes_inspected=False)


def summarize(report):
    findings = [*report['context_findings'], *report['catalog_findings'], *(item for case in report['cases'] for item in case['findings'])]
    errors = [item for item in findings if item['severity'] == 'error']
    warnings = [item for item in findings if item['severity'] == 'warning']
    warning_groups = {}
    for case in report['cases']:
        for item in case['findings']:
            if item['severity'] == 'warning':
                warning_groups.setdefault(item['code'], set()).add(case['group_id'])
    return dict(version=VERSION, rules_sha256=report['rules_sha256'], catalog_version=report['catalog_version'],
        catalog_sha256=report['catalog_sha256'], tasks_checked=len(report['cases']),
        errors=len(errors), warnings=len(warnings), findings_by_code=dict(Counter(item['code'] for item in findings)),
        tasks_with_errors=sum(any(item['severity'] == 'error' for item in case['findings']) for case in report['cases']),
        tasks_with_warnings=sum(any(item['severity'] == 'warning' for item in case['findings']) for case in report['cases']),
        configuration_groups_with_warnings=len({case['group_id'] for case in report['cases']
            if any(item['severity'] == 'warning' for item in case['findings'])}),
        warning_groups_by_code={code: len(groups) for code, groups in warning_groups.items()},
        shared_input_findings=report['context_findings'],
        splits=dict(Counter(case['split'] for case in report['cases'])),
        layout_partition_checked=report['catalog_version'].startswith('wildfire-synthetic-expansion'),
        contract_passed=not errors, review_required=bool(errors or warnings), empirically_validated=False,
        policy_rollouts=0, held_out_outcomes_inspected=False,
        interpretation='Static logical validity only. Warnings are not automatically impossible scenarios; no claim of calibrated fire physics, optimality or real-world realism')
