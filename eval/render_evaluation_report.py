"""Render aggregate evaluation evidence; never include private cell artifacts."""
import argparse
import copy
import json
import os
from pathlib import Path
from eval.render_source_matrix_report import render
from eval.evaluate_packaged_runtime import refresh_report, VERSION


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=json.loads(args.report.read_text())
    if report.get('version')!=VERSION or report.get('split')!='evaluation':
        raise ValueError('Expected evaluation-only report')
    refresh_report(report)
    shown=copy.deepcopy(report)
    shown.update(report['pins'])
    page=render(shown).replace('Source matrix review','Evaluation catalog validation').replace(
        'Source-map development matrix','Evaluation-only catalog validation').replace(
        'Private development evidence for synthetic exercises.',
        'Private evaluation-only evidence. Shared landscape and parent fire seeds; no training split.')
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(page)


if __name__=='__main__': main()
