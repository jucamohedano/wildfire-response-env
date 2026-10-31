"""Bounded private low-water fire/weather smoke for the prior censored case."""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import stat
import time

import adapter_v1 as api
from eval.extended_scenario_drafts_v1 import build_drafts
from eval.domain_low_water_weather_v1 import prepare as prepare_low_water, LowWaterWeatherFireV1, PARENT_PIN
from eval.domain_profile_weather_v2 import CHOICE
from wildfire_expanded_incident_v1.private_output import private_destination, write_private_json, write_atomic_private_json, read_private_json


VERSION='wildfire-domain-low-water-weather-smoke-v1'
# The v2 input conversion has a distinct checkpoint identity; no automatic
# migration from earlier backend checkpoints is safe.
LEGACY_RUNNERS={}


def _read_resume(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink():raise ValueError('private resume must be an absolute regular file')
    private_destination(path.parent/('.resume-probe-'+secrets.token_hex(16)))
    info=path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
            or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>150_000_000):
        raise ValueError('invalid private resume file')
    with path.open() as f:value=json.load(f)
    if (type(value) is not dict or value.get('version') not in (VERSION,*LEGACY_RUNNERS)
            or value.get('sha256')!=api._hash({k:v for k,v in value.items() if k!='sha256'})):
        raise ValueError('resume integrity differs')
    return value


def _resume_matches(saved, current):
    """Require an exact v4 runner and input identity for continuation."""
    saved_version=saved.get('version'); identity=copy.deepcopy(saved.get('identity'))
    if type(identity) is not dict or identity.get('version')!=saved_version:
        return False
    if saved_version in LEGACY_RUNNERS:
        if identity.get('runner_sha256')!=LEGACY_RUNNERS[saved_version]:return False
        identity['version']=VERSION
        identity['runner_sha256']=current.get('runner_sha256')
    elif saved_version!=VERSION:
        return False
    return api._encoded(identity)==api._encoded(current)


def initialize_entries(cases, old, make_host):
    """A failed resume aborts before any new checkpoint can be created."""
    if old is not None:
        if len(old)!=len(cases) or set(old)!={case['id'] for case in cases}:
            raise ValueError('resume case membership differs')
        if any(type(old[case['id']].get('checkpoint')) is not dict
               or old[case['id']].get('status')=='terminal_error'
               or old[case['id']].get('error') is not None for case in cases):
            raise ValueError('resume contains missing or terminal-error checkpoint')
    entries=[]
    for case in cases:
        try:
            host=make_host(case)
            if old is not None:host.restore(old[case['id']]['checkpoint'])
            entries.append(dict(case=case,host=host,error=None))
        except Exception as exc:
            if old is not None:
                raise RuntimeError('resume restore failed; original checkpoint preserved') from exc
            entries.append(dict(case=case,host=None,error=dict(kind=type(exc).__name__,detail=str(exc))))
    return entries



def checkpoint_records(entries, durable):
    """Build private records, retaining last durable snapshot after host failure.

    The caller replaces durable only after the atomic checkpoint write succeeds.
    A retained snapshot is inspection/recovery evidence, not permission to resume
    a terminal error silently.
    """
    saved=[];current={}
    for entry in entries:
        case=entry['case'];host=entry['host'];name=case['id']
        snap=None
        if host is not None:
            try:
                state=host.situation();snap=host.checkpoint()
                current[name]=state['warmup_steps']+state['minute']
            except Exception as exc:
                entry['error']=dict(kind=type(exc).__name__,detail=str(exc))
                entry['host']=None
        if entry['host'] is None:
            snap=copy.deepcopy(durable.get(name,{}).get('checkpoint'))
            if entry['error'] is None:
                entry['error']=dict(kind='MissingHost',detail='host unavailable')
            status='terminal_error'
        else:
            status=state['status']
        saved.append(dict(case=name,checkpoint=snap,status=status,error=copy.deepcopy(entry['error'])))
    return saved,current


def persist_host_failure(entry, exc, save_checkpoint):
    """Synchronously persist terminal error and last durable snapshot."""
    entry['error']=dict(kind=type(exc).__name__,detail=str(exc))
    entry['host']=None
    save_checkpoint()


def advance_round(entries, interrupted, deadline, checkpoint_every, save_checkpoint, *, clock=time.monotonic):
    """Advance a round; an exception is checkpointed before another step."""
    live=[]
    for entry in entries:
        if entry['host'] is None:continue
        try:
            if entry['host'].situation()['status']=='active':live.append(entry)
        except Exception as exc:
            persist_host_failure(entry,exc,save_checkpoint)
    if not live:return False
    for entry in live:
        for _ in range(min(30,checkpoint_every)):
            if interrupted['signal'] or clock()>=deadline:break
            host=entry['host']
            # A different host's failure checkpoint may itself discover that
            # this host failed while gathering snapshots.
            if host is None:break
            try:
                if host.situation()['status']!='active':break
                if interrupted['signal'] or clock()>=deadline:break
                host.step()
            except Exception as exc:
                persist_host_failure(entry,exc,save_checkpoint)
                break
        if interrupted['signal'] or clock()>=deadline:break
    return True

def main():
    start=time.monotonic()
    runner_pin=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('strict','water-v2','overlay-v3','overlay-v4','parent','catalog','road','coast','water',
                 'worldcover','osm-manifest','osm-samples','dera-rivers','strict-sha256','water-v2-sha256',
                 'overlay-v3-sha256','overlay-v4-sha256','parent-sha256','catalog-sha256','road-sha256',
                 'profile','low-water-profile','fuel-archive','prior-checkpoint','prior-checkpoint-sha256','output','checkpoint-output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--candidate-class',type=int,required=True)
    p.add_argument('--low-water-crossing',type=float,required=True)
    p.add_argument('--wall-seconds',type=float,default=600.)
    p.add_argument('--checkpoint-every',type=int,default=60)
    p.add_argument('--resume')
    p.add_argument('--opt-in-weather-fire',action='store_true')
    a=p.parse_args()
    if not a.opt_in_weather_fire:raise ValueError('explicit weather-fire opt-in required')
    if not math.isfinite(a.wall_seconds) or not 0 < a.wall_seconds <= 1800:
        raise ValueError('wall-seconds must be in (0, 1800]')
    if not 1 <= a.checkpoint_every <= 120:
        raise ValueError('checkpoint interval must be in [1, 120]')
    out=private_destination(a.output);checkpoint_out=private_destination(a.checkpoint_output)
    if out==checkpoint_out:raise ValueError('report and checkpoint paths must differ')
    interrupted={'signal':None}
    def request_stop(number, _frame):
        interrupted['signal']=number
    old_handlers={number:signal.signal(number,request_stop) for number in (signal.SIGINT,signal.SIGTERM)}
    try:
        resume=_read_resume(a.resume) if a.resume else None
        values=[]
        for path in (a.strict,a.water_v2,a.overlay_v3,a.overlay_v4,a.parent,a.catalog):
            if Path(path).is_symlink():raise ValueError('input symlink unsupported')
            with open(path) as f:values.append(json.load(f))
        strict,water,v3,v4,parent,catalog=values
        profile=read_private_json(a.profile,max_bytes=25_000_000)
        low_water=read_private_json(a.low_water_profile,max_bytes=25_000_000)
        prior=read_private_json(a.prior_checkpoint,max_bytes=150_000_000)
        if (prior.get('version')!='wildfire-domain-profile-weather-diagnostic-run-v1'
                or prior.get('sha256')!=a.prior_checkpoint_sha256
                or prior['sha256']!=api._hash({k:v for k,v in prior.items() if k!='sha256'})
                or prior['identity']['input_identity']['profile_sha256']!=PARENT_PIN):
            raise ValueError('prior private checkpoint identity differs')
        inputs=prepare_low_water(low_water,profile,strict,water,v3,v4,parent,catalog,
            fuel_archive=a.fuel_archive,candidate_class=a.candidate_class,low_water_crossing=a.low_water_crossing,
            opt_in=True,strict_pin=a.strict_sha256,
            water_pin=a.water_v2_sha256,v3_pin=a.overlay_v3_sha256,v4_pin=a.overlay_v4_sha256,
            parent_pin=a.parent_sha256,catalog_pin=a.catalog_sha256,road_path=a.road,road_pin=a.road_sha256,
            coast_path=a.coast,water_path=a.water,worldcover_path=a.worldcover,osm_manifest_path=a.osm_manifest,
            osm_samples_path=a.osm_samples,dera_rivers_path=a.dera_rivers,scenario='nominal',
            crossing_proxy=.9,opt_in_mixed_experiment=True)
        drafts=build_drafts(catalog,parent['grid'],water['grid'])
        if len(drafts['cases'])!=12 or drafts['sha256']!=prior['identity']['drafts_sha256']:
            raise ValueError('prior source draft catalog differs')
        censored=[r['case'] for r in prior['cases'] if r.get('checkpoint') and r['checkpoint'].get('status')=='censored']
        if len(censored)!=1:raise ValueError('expected one prior censored case')
        selected=[case for case in drafts['cases'] if case['id']==censored[0]]
        if len(selected)!=1:raise ValueError('prior censored case missing from drafts')
        config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
        identity=dict(version=VERSION,runner_sha256=runner_pin,input_identity=dict(inputs.identity),
                      drafts_sha256=drafts['sha256'],configuration=config,pressure_scale=1,
                      scenario=CHOICE,crossing_proxy=.9,prior_checkpoint_sha256=prior['sha256'],
                      candidate_class=a.candidate_class,low_water_crossing=a.low_water_crossing)
        if resume is not None and not _resume_matches(resume,identity):
            raise ValueError('resume source, inputs or configuration differ')
        old=None if resume is None else {x['case']:x for x in resume['cases']}
        if resume is not None and len(old)!=len(resume['cases']):
            raise ValueError('duplicate resume case')
        def make_host(case):
            return LowWaterWeatherFireV1(inputs,drafts,expected_drafts_sha256=drafts['sha256'],
                case_id=case['id'],containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
        entries=initialize_entries(selected,old,make_host)

        target_identity=None
        last_saved={}
        durable={} if old is None else copy.deepcopy(old)
        def save_checkpoint():
            nonlocal target_identity,last_saved,durable
            saved,current=checkpoint_records(entries,durable)
            value=dict(version=VERSION,identity=identity,cases=saved)
            value['sha256']=api._hash(value)
            target_identity=write_atomic_private_json(checkpoint_out,value,target_identity)
            # The previous snapshot remains authoritative until the replacement succeeds.
            durable={item['case']:copy.deepcopy(item) for item in saved}
            last_saved=current
            return value

        # Durable initial checkpoint makes a later hard kill recoverable even
        # before the first hourly boundary. No simulation step follows a stop.
        checkpoint=save_checkpoint()
        deadline=start+a.wall_seconds
        while not interrupted['signal'] and time.monotonic()<deadline:
            if not advance_round(entries,interrupted,deadline,a.checkpoint_every,save_checkpoint):
                break
            due=False
            for entry in entries:
                if entry['host'] is None:continue
                try:
                    state=entry['host'].situation()
                    ticks=state['warmup_steps']+state['minute']
                    if ticks-last_saved.get(entry['case']['id'],0)>=a.checkpoint_every:due=True
                except Exception as exc:
                    persist_host_failure(entry,exc,save_checkpoint)
            if due:checkpoint=save_checkpoint()
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=runner_pin:
            raise RuntimeError('diagnostic runner source changed during execution')
        checkpoint=save_checkpoint()
        results=[]
        for entry in entries:
            case,host=entry['case'],entry['host']
            ignored=sorted({event['kind'] for event in case['events'] if event['kind']!='wind'})
            if host is None:
                result=dict(case=case['id'],status='error',error=entry['error'],non_weather_events_unmodeled=ignored)
            else:
                state=host.situation()
                if state['status']=='active':
                    status='interrupted' if interrupted['signal'] else 'budget_exhausted'
                else:status=state['status']
                result=dict(case=case['id'],status=status,minute=state['minute'],warmup_steps=state['warmup_steps'],
                    warmup_target=case['warmup_minutes'],censor_reasons=list(host._fire.reasons) if status=='censored' else [],
                    non_weather_events_unmodeled=ignored,host_identity=copy.deepcopy(host._identity))
            results.append(result)
        report=dict(version=VERSION,role='private_unscored_low_water_weather_fire_smoke',identity=identity,cases=results,
            checkpoint_sha256=checkpoint['sha256'],checkpoint_path=str(checkpoint_out),
            wall_seconds_limit=a.wall_seconds,elapsed_seconds=round(time.monotonic()-start,3),
            stop_signal=interrupted['signal'],checkpoint_every=a.checkpoint_every,
            limitations=['no scoring or operational routes','resources and population omitted',
                'all non-weather operational events are unmodeled','synthetic uncalibrated weather, fuel and water-crossing assumptions',
                'low-water candidate and crossing are explicit synthetic assumptions, not measurements',
                'one case selected from prior censor; partial result is not a full-day claim',
                'no physical step starts at or after the wall deadline; one step and checkpoint I/O are not preemptible'])
        report['sha256']=api._hash(report)
        write_private_json(out,report)
        print('Private diagnostic finished.',flush=True)
    finally:
        for number,handler in old_handlers.items():signal.signal(number,handler)


if __name__=='__main__':main()
