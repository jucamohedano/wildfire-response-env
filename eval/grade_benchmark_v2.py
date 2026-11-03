"""Verify a recorded run against private, externally pinned benchmark anchors.

This is a terminal evaluator command, never an actor tool. It does not execute
the simulator, contact a model, or expose anchors to the evaluated policy.
"""
import argparse
import gzip
import io
import json
from pathlib import Path
import resource

from eval.qualify_benchmark_v2 import (
    MAX_EXPANDED, MAX_GZIP, document, headroom, read, write,
)
from wildfire_benchmark_v2 import BenchmarkVerifier


def verify(args):
    headroom()
    manifest, _ = document(args.manifest)
    verifier = BenchmarkVerifier(manifest, args.expected_manifest_sha256)
    report, report_sha = document(args.source_report)
    if report_sha != args.expected_source_report_sha256:
        raise ValueError('Run report differs from its externally expected identity')
    cells = [cell for cell in report['cells']
             if cell['case_id'] == args.task_id and cell['policy'] == args.policy]
    if len(cells) != 1:
        raise ValueError('Expected exactly one matching task/policy execution')
    compressed = read(args.saved_run, MAX_GZIP)
    import hashlib
    if hashlib.sha256(compressed).hexdigest() != args.expected_saved_sha256:
        raise ValueError('Saved execution differs from its externally expected identity')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(MAX_EXPANDED + 1)
    if len(raw) > MAX_EXPANDED:
        raise ValueError('Decompressed execution exceeds the bounded size')
    saved = json.loads(raw)
    result = dict(cells[0], pins=dict(report['pins'], **cells[0]['pins']))
    verdict = verifier.verify_run(args.task_id, result, saved,
                                 allow_candidate=args.allow_candidate_diagnostic)
    verdict.update(run_report_sha256=report_sha, saved_run_sha256=args.expected_saved_sha256)
    # Exclusive creation prevents replacing either source evidence or a verdict.
    write(args.output, verdict)
    print(json.dumps({key: verdict[key] for key in (
        'task_id', 'benchmark_score', 'diagnostic_grade', 'unscored_reason',
        'qualification_reason', 'adverse_flags')}))
    return verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'source-report', 'saved-run', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-manifest-sha256', 'expected-source-report-sha256',
                 'expected-saved-sha256', 'task-id', 'policy'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--allow-candidate-diagnostic', action='store_true',
                        help='Return a diagnostic grade; never bypass release qualification')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
    verify(args)


if __name__ == '__main__':
    main()
