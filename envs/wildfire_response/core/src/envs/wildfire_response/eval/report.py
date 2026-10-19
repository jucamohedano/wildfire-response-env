"""Write a readable evaluation report from completed baseline artifacts."""
import argparse
import json
from pathlib import Path
import statistics

from .run import load


def report(directory):
    root = Path(directory)
    summary = load(root/'summary.json')
    rows = [json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]
    first = load(next((root/'trajectories').glob('*.json.gz')))
    horizon = first['max_decisions']*8
    lines = ['# Wildfire Response evaluation', '',
             f"Completed episodes: {len(rows)}. Distinct scenarios: {len({row['scenario'] for row in rows})}.",
             f"Budget: {first['max_decisions']} decisions, up to {horizon} simulation ticks per episode.",
             f"Elapsed: {summary['elapsed_seconds']:.1f} seconds; peak process RSS: {summary['peak_rss_kib']} KiB.",
             f"Code source hash: `{summary['source_sha256']}`.", '',
             'Rewards are negative outcome costs; values closer to zero are better. No model was trained.', '']
    for split in ('train','validation','test'):
        subset = [row for row in rows if row['split'] == split]
        if not subset:
            continue
        lines.append(f'## {split}')
        lines.append('')
        for policy in sorted({row['policy'] for row in subset}):
            selected = [row for row in subset if row['policy'] == policy]
            lines.append(f"- {policy}: {len(selected)} episodes; mean reward "
                         f"{statistics.mean(row['total_reward'] for row in selected):.6f}; "
                         f"mean exposed population fraction {statistics.mean(row['components']['exposed_population'] for row in selected):.6f}; "
                         f"invalid actions {sum(row['components']['invalid_actions'] for row in selected)}.")
        lines.append('')
    lines += ['## Interpretation and limits', '',
              'Scores apply to this synthetic Brunete scenario distribution and this horizon. '
              'Do not combine rewards from different horizons or infer field readiness.',
              'False-alarm over-warning and missed population warnings have separate cost components. '
              'Compare those components as well as the aggregate score.',
              'Natural termination can occur before the budget; a horizon truncation is not evidence of incident resolution.',
              'Durations include host contention from other checks; they are planning measurements, not throughput guarantees.',
              'Code licensing, asset redistribution and a Docker build remain publication prerequisites.']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('directory')
    parser.add_argument('--output')
    args = parser.parse_args()
    text = report(args.directory)
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text)


if __name__ == '__main__':
    main()
