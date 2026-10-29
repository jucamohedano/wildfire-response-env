"""Read-only development diagnostics; never rescore a trajectory or change rewards."""
import argparse
import hashlib
import html
import json
import math
from pathlib import Path


def analyze(artifact):
    summary = artifact['summary']
    if summary['experiment']['split'] != 'development':
        raise ValueError('Use recorded development outcomes only')
    rows = []
    for episode in summary['episodes']:
        if episode['minute'] != 120 or not episode['scored']:
            raise ValueError('Incomplete episodes are not comparable completed outcomes')
        outcome = episode['outcomes']
        eligible = outcome['initially_burnable_unburned_cells']
        area = outcome['additional_burned_hectares']
        burned = outcome['additional_burned_cells']
        cell_hectares = area / burned if burned else 25.
        domain = eligible * cell_hectares
        if eligible <= 0 or outcome['population'] <= 0:
            raise ValueError('Diagnostic denominators must be positive')
        population_component = .8 * outcome['protected_population_fraction']
        area_component = .2 * outcome['unburned_fraction']
        if not math.isclose(population_component + area_component, outcome['graded_score'], abs_tol=1e-12):
            raise ValueError('Recorded reward does not agree with its components')
        baseline = episode['baselines']['dispatcher']
        rows.append(dict(task_id=episode['task_id'], family=episode['family'],
            recorded_score=outcome['graded_score'], population_component=population_component,
            area_component=area_component, active_fire_cells=outcome['active_fire_cells'],
            additional_burned_hectares=area,
            additional_area_vs_recorded_dispatcher=area-baseline['additional_burned_hectares'],
            eligible_domain_hectares=domain,
            one_person_score_change=.8/outcome['population'],
            one_cell_score_change=.2/eligible,
            area_equal_to_one_person_score_change_hectares=(.8/outcome['population'])/(.2/domain),
            diagnostic_budget_sensitivity=[dict(area_budget_hectares=budget,
                one_cell_linear_penalty=.2*cell_hectares/budget)
                for budget in (500, 1000, 5000, domain)]))
    return dict(version='development-reward-scale-diagnostic-v1', rows=rows,
        score_changes_applied=False, training_labels_generated=False,
        interpretation='The 80/20 priorities are unchanged. Large geographic normalization makes modest additional area and residual active fire compatible with near-perfect scalar reward.',
        limitations=['Recorded dispatcher is a reference, not an optimal policy or regret lower bound.',
            'Fixed area budgets illustrate scale sensitivity only; no alternative scores, rankings or preferred normalization are selected.',
            'Choosing any new normalization or safety condition requires a separately versioned owner-approved objective and new validation.'])


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('results/luna-subagent-benchmark-20261026.json'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('Use a fresh output directory')
    raw=args.source.read_bytes(); result=analyze(json.loads(raw))
    result.update(source_sha256=hashlib.sha256(raw).hexdigest(),source=str(args.source))
    args.output.mkdir(parents=True)
    (args.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(row[key]))+'</td>' for key in
        ('family','recorded_score','active_fire_cells','additional_burned_hectares','additional_area_vs_recorded_dispatcher'))+'</tr>' for row in result['rows'])
    (args.output/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Reward scale diagnostic</title><style>body{font:16px/1.6 system-ui;max-width:1050px;margin:40px auto;padding:20px;background:#f4f6f2}td,th{padding:12px;border-bottom:1px solid #ccd8c9;text-align:left}table{background:white;width:100%}</style><h1>Reward scale: diagnostic only</h1><p>'+html.escape(result['interpretation'])+'</p><table><tr><th>Development family</th><th>Original score</th><th>Active cells</th><th>Additional hectares</th><th>Hectares above dispatcher</th></tr>'+rows+'</table><ul>'+''.join('<li>'+html.escape(item)+'</li>' for item in result['limitations'])+'</ul><p>Original records and weights are unchanged. See <a href="report.json">JSON components and sensitivities</a> and owner decision #42.</p></html>')
