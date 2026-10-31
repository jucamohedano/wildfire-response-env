"""Fixed domain-wide synthetic built/water extension to policy v2.

No case or outcome selects cells. Source fuel is retained; component fuel,
inert interpretation and crossing/transfer are explicit exercise assumptions.
"""
import copy
import hashlib
from collections import Counter
from pathlib import Path
from eval import domain_component_policy_v2 as parent_policy
from eval.extended_map_common import digest

VERSION='wildfire-domain-built-water-fixed-policy-v1'
PROFILE_PIN='7229fdd31e5f0a30ce4c4ef1cddebfc72c847886e8f8bb25362c60794a92abc9'
PARENT_POLICY_PIN='4f547fa6ea544f763e198c97a2abecf058a8e746bbe365f3f1e44a720146c6b8'


def build_policy(profile,parent,water):
    if (profile.get('version')!='wildfire-domain-built-water-source-sensitivity-profile-v1'
            or profile.get('sha256')!=PROFILE_PIN or digest({k:v for k,v in profile.items() if k!='sha256'})!=PROFILE_PIN
            or profile.get('parent_profile_sha256')!=parent_policy.PROFILE_PIN):
        raise ValueError('built-water profile pin differs')
    selections,sea,parent_manifest=parent_policy.build_policy(parent,water)
    if parent_manifest['sha256']!=PARENT_POLICY_PIN:raise ValueError('fixed parent policy differs')
    records=profile['cells'];grid=profile['grid'];w,h=grid['width'],grid['height']
    if grid!=parent['grid'] or {(r['column'],r['row']) for r in records}!={(x,y) for y in range(h) for x in range(w)}:
        raise ValueError('domain profile coverage differs')
    extras=Counter()
    for r in records:
        if r['status']!='new_built_watercourse_source_sensitivity':continue
        x,y=r['column'],r['row'];pos=(x,y);cell=water['cells'][y][x];hist=cell['worldcover_samples']
        if pos in selections or cell['source_fuel_class']!=r['source_fuel_class'] or cell['source_fuel_class'] is None:
            raise ValueError('extension source class or parent selection differs')
        if not hist.get('50') or not sum(hist.get(k,0) for k in ('10','20','30','40')):
            raise ValueError('extension lacks both mapped sample classes')
        selections[pos]={'vegetation_class':cell['source_fuel_class'],'vegetation_initial_fuel':.8,
          'built_initial_fuel':.4,'crossing_openness':.9,'bare_assumption':'synthetic_inert',
          'water_assumption':'synthetic_inert','vegetation_absence':'unknown','built_absence':'unknown',
          'transfer':copy.deepcopy(parent_policy.TRANSFER)}
        extras['source_mixed_selected']+=1
    payload=[{'column':x,'row':y,'assumptions':selections[(x,y)]} for x,y in sorted(selections)]
    manifest={'version':VERSION,'profile_sha256':PROFILE_PIN,'parent_policy_sha256':PARENT_POLICY_PIN,
      'policy_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      'selection_sha256':digest(payload),'counts':dict(extras),
      'assumptions':'Source class retained; vegetation fuel .8, built fuel .4, crossing .9, bare synthetic inert, transfer .2; not measurements or calibration.'}
    manifest['sha256']=digest(manifest);return selections,sea,manifest
