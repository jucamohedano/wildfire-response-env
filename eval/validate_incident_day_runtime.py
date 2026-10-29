"""Complete, resumable CPU coverage of the active incident-day operations contract.

Private call/audit/checkpoint records never enter the aggregate release report.
This checks the modeled exercise, not empirical firefighting calibration.
"""
import argparse
import copy
import gzip
import hashlib
import html
import json
import math
from pathlib import Path
import signal
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from wildfire_core.catalog_lifecycle import registry
from envs.wildfire_response.incident_day.agent import IncidentDayTools
from envs.wildfire_response.incident_day.environment import implementation_digest
from eval.hub_runtime import make_environment, verify_package
from eval.run_incident_day import canonical, peak_memory, write_gzip, write_json
from eval.check_incident_day_plausibility import audit_case, route_checks, static_audit
from eval.incident_day_policies import POLICY_NAMES, make_policy
from eval.incident_day_movement_checks import MovementObserver

VERSION = 'wildfire-runtime-matrix-v1'
ROOT = Path(__file__).resolve().parents[1]
BOUNDS = ROOT / 'data/incident_day_v2/plausibility/bounds-v1.json'
REQUIRED_OPERATIONS=('get_situation','get_reports','route_options','evacuation_options','check_action',
    'commit_orders','request_status','advance_time','requests','authorizations','releases',
    'shelter','evacuate','status_requests','waits')


def checksum(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validator_digest():
    paths = [Path(__file__), ROOT/'eval/incident_day_policies.py', ROOT/'eval/hub_runtime.py',
             ROOT/'eval/run_incident_day.py',
             ROOT/'eval/incident_day_action_checks.py', ROOT/'eval/incident_day_movement_checks.py',
             ROOT/'eval/check_incident_day_plausibility.py',
             BOUNDS, ROOT/'data/incident_day_v2/plausibility/action-bounds-v1.json']
    return checksum({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


class PublicFacade:
    """Policies receive only named tools; evaluator state stays on the runner side."""
    __slots__ = ('__tools', '__journal')

    def __init__(self, env, journal):
        self.__tools, self.__journal = IncidentDayTools(env), journal

    def execute(self, name, arguments):
        env = self.__tools.environment
        before, decision = env.minute, env.decision
        result = self.__tools.execute(name, arguments)
        retained = {key: copy.deepcopy(result[key]) for key in
                    ('info', 'reward', 'terminated', 'truncated', 'error', 'committed', 'valid') if key in result}
        self.__journal.append(dict(name=name, arguments=copy.deepcopy(arguments), before_minute=before,
            after_minute=env.minute, before_decision=decision, after_decision=env.decision, result=retained))
        return result

    def get_situation(self):
        return self.execute('get_situation', {})

    def get_reports(self, **arguments):
        return self.execute('get_reports', arguments)

    def route_options(self, resource_id, sector_id='incident'):
        return self.execute('route_options', dict(resource_id=resource_id, sector_id=sector_id))

    def evacuation_options(self, group_id, destination_id):
        return self.execute('evacuation_options', dict(group_id=group_id, destination_id=destination_id))

    def check_action(self, action):
        return self.execute('check_action', dict(action=action))


def commit(public, action):
    if set(action) == {'advance_minutes'}:
        return public.execute('advance_time', dict(max_minutes=action['advance_minutes']))
    if set(action) == {'status_requests'} and len(action['status_requests']) == 1:
        return public.execute('request_status', action['status_requests'][0])
    return public.execute('commit_orders', dict(action=action))


def expected_cells(manifest, policies=POLICY_NAMES):
    if tuple(policies) != POLICY_NAMES:
        raise ValueError('The required policy set must not silently shrink')
    active = [c for c in registry()['catalogs'] if c['status'] == 'active_development']
    result, matched = [], set()
    for runtime_id, entry in sorted(manifest['runtimes'].items()):
        match = next((c for c in active if c['inputs_sha256'] == entry['inputs_sha256']
                      and c['network_sha256'] == entry['network_sha256']), None)
        if match is None or set(entry['scenario_ids']) != set(match['case_ids']):
            raise ValueError('Runtime does not identify its complete active case set')
        if match['id'] in matched:
            raise ValueError('Duplicate active representation under different runtime names')
        matched.add(match['id'])
        for case_id in sorted(entry['scenario_ids']):
            for policy in policies:
                result.append(dict(runtime_id=runtime_id, case_id=case_id, policy=policy))
    if (matched != {c['id'] for c in active}
            or len({(c['runtime_id'], c['case_id']) for c in result}) != 12):
        raise ValueError('Both active six-case representations are required')
    return result


def cell_identity(cell):
    return tuple(cell[k] for k in ('runtime_id', 'case_id', 'policy'))


def validate_matrix(report):
    expected, cells = report['expected_cells'], report['cells']
    if (len(expected)!=48 or len({cell_identity(c) for c in expected})!=48
            or {c['policy'] for c in expected}!=set(POLICY_NAMES)
            or len({(c['runtime_id'],c['case_id']) for c in expected})!=12):
        return 'failed'
    identities = [cell_identity(c) for c in cells]
    if len(identities) != len(set(identities)) or set(identities) != {cell_identity(c) for c in expected}:
        return 'failed'
    if report.get('runtime_findings') and any(f['category'] in ('contradiction', 'blocking', 'error')
                                            for f in report['runtime_findings']):
        return 'failed'
    static=report.get('static_cases',[])
    if static and (len(static)!=12 or any(f['category'] in ('contradiction','blocking','error')
                                         for c in static for f in c.get('findings',[]))):
        return 'failed'
    for c in cells:
        if c['status'] != 'complete':
            return 'incomplete' if c['status'] in ('not_run', 'incomplete', 'blocked') else 'failed'
        if not all(c.get('verification', {}).get(k) is True for k in
                   ('passed', 'exact_replay', 'independent_reward_agreement')):
            return 'failed'
        if not c.get('resume_checks') or any(not item['passed'] for item in c['resume_checks']):
            return 'failed'
        if (c.get('minute')!=1440 or c.get('query_independence',{}).get('passed') is not True
                or any(f.get('category') in ('contradiction','blocking','error') for f in c.get('findings',[]))):
            return 'failed'
    if any(not any(c.get('operation_coverage',{}).get(name,{}).get('observed',0)>0 for c in cells)
           for name in REQUIRED_OPERATIONS):
        return 'blocked'
    return 'complete'


def duration_statistics(durations):
    groups={}
    for row in durations:
        if not row.get('inherited') and not row.get('censored'):
            groups.setdefault(row['operation'],[]).append(row['minutes'])
    return {name:dict(count=len(values),min_minutes=min(values),median_minutes=statistics.median(values),
                     max_minutes=max(values)) for name,values in sorted(groups.items())}


def refresh_report(report):
    report['status'] = validate_matrix(report)
    report['summary'] = dict(expected=len(report['expected_cells']),
        statuses={status: sum(c['status'] == status for c in report['cells'])
                  for status in sorted({c['status'] for c in report['cells']})},
        active_cases=12, policies=len(POLICY_NAMES))


def write_report(output, report):
    refresh_report(report)
    write_json(output/'report.json', report)
    rows = ''.join('<tr><td>'+html.escape(c['runtime_id']+' / '+c['case_id'])+'</td><td>'+
                   html.escape(c['policy'])+'</td><td>'+html.escape(c['status'])+'</td><td>'+
                   str(c.get('minute', '—'))+'</td><td>'+str(c.get('runtime_contradictions', '—'))+'</td></tr>'
                   for c in report['cells'])
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Operation-wide runtime coverage</title>'
            '<style>body{font:14px system-ui;max-width:1150px;margin:30px auto;padding:0 16px;color:#253139}'
            'table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:7px;border-bottom:1px solid #ddd}'
            'h1{color:#3a647c}</style><h1>Operation-wide runtime coverage</h1><p>'+html.escape(report['status'])+
            ' · all 12 active development configurations × four public-interface scripted policies.</p>'
            '<p>Complete means 1,440 modeled minutes, exact replay, independent reward, applicable invariants and checkpoint continuation checks. '
            'It does not mean physically calibrated or expert reviewed. Unsupported and unexercised operations remain visible in JSON.</p>'
            '<table><tr><th>Configuration</th><th>Policy</th><th>Status</th><th>Minute</th><th>Contradictions</th></tr>'+rows+'</table></html>')
    (output/'report.html').write_text(page)


def _continuation(env, policy):
    from wildfire_core.incident_day import IncidentDayEnv
    other = IncidentDayEnv(env.inputs, env.network.document, expected_inputs_sha256=env.inputs['sha256'],
        expected_network_sha256=env.network.sha256, allow_unapproved_data=env.inputs['approval_status'] != 'approved_synthetic_fixture')
    observation = other.restore(json.loads(json.dumps(env.checkpoint())))
    clone = make_policy(policy.name)
    clone.restore(json.loads(json.dumps(policy.checkpoint())))
    public = PublicFacade(other, [])
    action = clone.action(public, observation)
    result = commit(public, action)
    return dict(action=action, result=checksum(result), audit=checksum(other._audit),
                fire=checksum(other.fire.checkpoint()), policy=checksum(clone.checkpoint()))


def _query_independence(env):
    from wildfire_core.incident_day import IncidentDayEnv
    other = IncidentDayEnv(env.inputs, env.network.document, expected_inputs_sha256=env.inputs['sha256'],
        expected_network_sha256=env.network.sha256, allow_unapproved_data=env.inputs['approval_status'] != 'approved_synthetic_fixture')
    other.restore(env.checkpoint())
    for _ in range(2):
        other.get_situation(); other.get_reports(); other.check_action({'advance_minutes': 60})
        other.route_options(sorted(other._known_resources)[0])
    # Compare continuation on two throwaway copies, leaving the live episode unchanged.
    baseline = IncidentDayEnv(env.inputs, env.network.document, expected_inputs_sha256=env.inputs['sha256'],
        expected_network_sha256=env.network.sha256, allow_unapproved_data=env.inputs['approval_status'] != 'approved_synthetic_fixture')
    baseline.restore(env.checkpoint())
    for _ in range(3):
        a = baseline.advance_time(20); b = other.advance_time(20)
        if a != b or baseline.fire.checkpoint() != other.fire.checkpoint() or baseline.bus.checkpoint() != other.bus.checkpoint():
            raise ValueError('Extra reads changed public, weather/fire or communication continuation')
    return dict(passed=True, method='Extra public reads/draft/route queries; identical three-wait continuation')


def _checkpoint(output, report, index, env, policy, calls, checked, resume_checks, query_check, observer):
    if (validator_digest()!=report['validator_sha256']
            or implementation_digest()!=report['implementation_sha256']):
        raise ValueError('Implementation changed before saving progress')
    refresh_report(report)
    payload = dict(version=VERSION, validator_sha256=report['validator_sha256'],
        expected_sha256=checksum(report['expected_cells']), completed_cells_sha256=checksum(report['cells']),
        index=index, environment=env.checkpoint(), policy=policy.checkpoint(), calls=calls,
        checked=sorted(checked), resume_checks=resume_checks, query_independence=query_check,
        movement_observer=observer.snapshot(), progress_report=copy.deepcopy(report))
    payload['sha256'] = checksum(payload)
    write_gzip(output/'private/checkpoint.json.gz', payload)


def aggregate_findings(findings):
    counts={}
    for item in findings:
        key=(item['category'],item['code'])
        counts[key]=counts.get(key,0)+item.get('count',1)
    return [dict(category=category,code=code,count=count) for (category,code),count in sorted(counts.items())]


def load_progress(output, expected, validator):
    raw=json.loads(gzip.decompress((Path(output)/'private/checkpoint.json.gz').read_bytes()))
    if raw.get('sha256')!=checksum({k:v for k,v in raw.items() if k!='sha256'}):
        raise ValueError('Checkpoint integrity differs')
    report=raw['progress_report']
    index=raw.get('index')
    if (raw.get('version')!=VERSION or raw['validator_sha256']!=validator
            or report['validator_sha256']!=validator or report['implementation_sha256']!=implementation_digest()
            or raw['expected_sha256']!=checksum(expected) or report['expected_cells']!=expected
            or raw['completed_cells_sha256']!=checksum(report['cells'])
            or type(index) is not int or not 0<=index<len(expected)
            or raw['policy']['policy']!=expected[index]['policy']
            or raw['environment']['state']['_scenario']['id']!=expected[index]['case_id']):
        raise ValueError('Resume progress, case, policy or implementation identity differs')
    return report,raw


def run(data_dir, output, *, wall_seconds=3600, resume=False, max_cells=None):
    from eval.incident_day_action_checks import audit_runtime
    if type(wall_seconds) not in (int,float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    if max_cells is not None and (type(max_cells) is not int or max_cells < 1):
        raise ValueError('max_cells must be positive')
    manifest = verify_package(data_dir)
    expected = expected_cells(manifest)
    output = Path(output)
    start = time.perf_counter()
    bounds = json.loads(BOUNDS.read_text())
    validator = validator_digest()
    restored = None
    if resume:
        # The atomically replaced bundle is authoritative. A crash can leave the
        # public rendering absent or stale without losing a consistent generation.
        report,restored=load_progress(output,expected,validator)
        if report['status']=='complete':
            if validate_matrix(report)!='complete':
                raise ValueError('Completed progress bundle failed matrix validation')
            write_report(output,report)
            return report
    else:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output/'private').mkdir(mode=0o700)
        report = dict(version=VERSION, status='incomplete', implementation_sha256=implementation_digest(),
            validator_sha256=validator, lifecycle_sha256=registry()['sha256'],
            policies=list(POLICY_NAMES), expected_cells=expected,
            cells=[dict(c,status='not_run') for c in expected], runtime_findings=[], static_cases=[],
            workers=1, elapsed_seconds=0, peak_rss_bytes=peak_memory(),
            limitations=['Development exercises, not held-out historical incidents or expert labels.',
                        'Bounds are declared model assumptions, not empirical calibration.',
                        'Private call journals, audits and checkpoints excluded from this report.'])
        for runtime_id in sorted(manifest['runtimes']):
            env, entry = make_environment(data_dir, runtime_id, allow_unapproved_data=True)
            screened=static_audit(env.inputs, env.network.document, bounds, None, entry['scenario_ids'],
                                  entry['allow_unapproved_data'])
            report['static_cases'].extend(dict(runtime_id=runtime_id,case_id=s['case'],
                findings=aggregate_findings(s['findings']), counterfactual_status=s.get('counterfactual',{}).get('status')) for s in screened)
        write_report(output,report)
    interrupted=False
    def stop(signum,frame):
        nonlocal interrupted
        interrupted=True
    handlers={s:signal.signal(s,stop) for s in (signal.SIGINT,signal.SIGTERM)}
    count=0
    prior_elapsed=report['elapsed_seconds']
    try:
        first=restored['index'] if restored else 0
        if restored and report['cells'][first]['status']=='complete':
            first+=1
            restored=None
        for index in range(first,len(expected)):
            if validator_digest()!=validator:
                raise ValueError('Validator source changed during matrix execution')
            identity=expected[index]
            env,entry=make_environment(data_dir, identity['runtime_id'], allow_unapproved_data=True)
            policy=make_policy(identity['policy'])
            resuming=restored is not None
            if restored:
                observation=env.restore(restored['environment']);policy.restore(restored['policy'])
                calls=restored['calls'];checked=set(restored['checked']);resume_checks=restored['resume_checks']
                query_check=restored['query_independence']
                observer=MovementObserver(env);observer.restore(restored['movement_observer']);restored=None
            else:
                observation,_=env.reset(identity['case_id'])
                calls=[];checked=set();resume_checks=[];query_check=_query_independence(env)
                observer=MovementObserver(env)
            public=PublicFacade(env,calls)
            if not resuming:
                public.get_situation();public.get_reports(limit=20)
            cell_start=time.perf_counter();last_hour=env.minute//60
            while not (env.terminated or env.truncated):
                if interrupted or time.perf_counter()-start >= wall_seconds:
                    report['cells'][index]=dict(identity,status='incomplete',minute=env.minute,reason='signal' if interrupted else 'wall_budget')
                    report['elapsed_seconds']=prior_elapsed+time.perf_counter()-start
                    report['peak_rss_bytes']=peak_memory()
                    _checkpoint(output,report,index,env,policy,calls,checked,resume_checks,query_check,observer)
                    write_report(output,report)
                    return report
                phases={u['state'] for u in env.ops.resources.values()}
                candidates=sorted((phases & {'travel','on_scene','working','returning','refilling','resting'})-checked)
                if env.minute >= 60 and 'hourly' not in checked:candidates.append('hourly')
                if env.minute >= 720 and 'midday' not in checked:candidates.append('midday')
                label=candidates[0] if candidates else None
                continuation=_continuation(env,policy) if label else None
                action=policy.action(public,observation)
                result=commit(public,action)
                if 'observation' not in result:
                    raise ValueError('Scripted policy tool call was rejected: '+str(result))
                observation=result['observation']
                if continuation:
                    passed=(action==continuation['action'] and checksum(result)==continuation['result']
                        and checksum(env._audit)==continuation['audit'] and checksum(env.fire.checkpoint())==continuation['fire']
                        and checksum(policy.checkpoint())==continuation['policy'])
                    resume_checks.append(dict(boundary=label,minute=env.minute,passed=passed));checked.add(label)
                    if not passed:raise ValueError('Checkpoint continuation differs at '+label)
                if env.minute//60 > last_hour:
                    last_hour=env.minute//60
                    # Hourly progress is private and resumable even after process interruption.
                    report['cells'][index]=dict(identity,status='incomplete',minute=env.minute,reason='checkpoint')
                    report['elapsed_seconds']=prior_elapsed+time.perf_counter()-start
                    _checkpoint(output,report,index,env,policy,calls,checked,resume_checks,query_check,observer)
                    write_report(output,report)
            verification=env.verify_episode()
            checks=audit_runtime(env,calls)
            findings=checks['findings']
            movement=observer.snapshot()
            for code,entry in movement['findings'].items():
                first=entry['first']
                findings.append(dict(code=code,category='contradiction',minute=first['minute'],
                    resource=first['entity'],detail=first['detail'],count=entry['count']))
            checks['coverage'].pop('actual_routes_and_clearance',None)
            unverified=sum(movement['coverage'].get(k,0) for k in ('journey_unverifiable','position_unverifiable',
                           'multi_minute_ticks_unobserved','road_geometry_unverifiable'))
            checks['coverage']['movement_paths']=dict(status='unsupported' if unverified else 'complete' if movement['coverage'].get('timed_positions_checked',0) else 'not_exercised',
                observed=movement['coverage'].get('timed_positions_checked',0),scope='Private observer, independent timed-polyline interpolation and directed edge geometry')
            checks['coverage']['capacity_each_tick']=dict(status='complete' if movement['coverage'].get('capacity_ticks_checked')==env.minute else 'unsupported',
                observed=movement['coverage'].get('capacity_ticks_checked',0))
            checks['coverage']['physical_access_and_real_clearance']=dict(status='unsupported',observed=0,
                limitation='Modeled routes/windows/clearance do not establish field access or real aviation authorization.')
            hard=[f for f in findings if f['category'] in ('blocking','contradiction','error')]
            complete=(env.terminated and not env.truncated and env.minute==1440
                and all(verification.get(k) is True for k in ('passed','exact_replay','independent_reward_agreement')))
            status='complete' if complete and not hard and not unverified else 'failed' if hard else 'blocked'
            public_findings=aggregate_findings(findings)
            result=dict(identity,status=status,minute=env.minute,verification={k:verification[k] for k in
                ('passed','exact_replay','independent_reward_agreement')},
                pins=dict(inputs_sha256=env.inputs['sha256'],network_sha256=env.network.sha256,scenario_sha256=env._scenario['sha256']),
                outcome=env.outcomes(),runtime_contradictions=len(hard), findings=public_findings,
                operation_coverage=checks['coverage'],durations=duration_statistics(checks['durations']),
                resume_checks=[{k:v for k,v in check.items() if k!='minute'} for check in resume_checks],
                movement_evidence=movement['coverage'],
                query_independence=query_check,elapsed_seconds=round(time.perf_counter()-cell_start,4),
                command_count=env.decision,tool_call_count=len(calls))
            report['cells'][index]=result
            report['runtime_findings'].extend(dict(f, runtime_id=identity['runtime_id'],case_id=identity['case_id'],policy=identity['policy']) for f in public_findings)
            private=output/'private'/('-'.join(cell_identity(identity))+'.json.gz')
            write_gzip(private,dict(calls=calls,audit=env._audit,trajectory=env.trajectory(),reports=env.bus.reports,
                                   orders=list(env.ops.orders.values()),verification=verification,validation_findings=findings,
                                   resume_checks=resume_checks))
            report['peak_rss_bytes']=peak_memory()
            report['elapsed_seconds']=prior_elapsed+time.perf_counter()-start
            _checkpoint(output,report,index,env,policy,calls,checked,resume_checks,query_check,observer)
            write_report(output,report)
            print(json.dumps(dict(index=index+1,total=len(expected),**identity,status=status,minute=env.minute,contradictions=len(hard))),flush=True)
            count+=1
            if hard:
                return report
            if max_cells and count>=max_cells:
                return report
        if validator_digest()!=validator or implementation_digest()!=report['implementation_sha256']:
            raise ValueError('Implementation changed before final acceptance')
        report['elapsed_seconds']=prior_elapsed+time.perf_counter()-start
        write_report(output,report)
        return report
    finally:
        for sig,handler in handlers.items():signal.signal(sig,handler)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',required=True);p.add_argument('--output',required=True)
    p.add_argument('--wall-seconds',type=float,default=3600);p.add_argument('--resume',action='store_true')
    p.add_argument('--max-cells',type=int)
    a=p.parse_args();r=run(a.data_dir,a.output,wall_seconds=a.wall_seconds,resume=a.resume,max_cells=a.max_cells)
    print(json.dumps(dict(status=r['status'],summary=r['summary'],elapsed_seconds=r['elapsed_seconds'])))
    return 0 if r['status']=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
