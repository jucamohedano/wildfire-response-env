"""Fixed whole-domain synthetic scenario for source and estimated mixed cells.

No incident, frontier or outcome is an input. Source-only neighbor votes choose
nominal class or the lowest tied class. Values are exercise assumptions.
"""
import copy
import hashlib
from collections import Counter
from pathlib import Path
from eval import domain_component_policy_v2 as parent_policy
from eval.extended_map_common import digest

VERSION='wildfire-domain-built-water-estimated-fixed-policy-v2'
PROFILE_PIN='7b0ea639a5c6c917252d7dc78b8bbf8c5cd21de4a12211938289a2d9267bc97a'
PARENT_POLICY_PIN='4f547fa6ea544f763e198c97a2abecf058a8e746bbe365f3f1e44a720146c6b8'


def build_policy(profile,parent,water):
    if (profile.get('version')!='wildfire-domain-built-water-estimated-sensitivity-profile-v1'
            or profile.get('sha256')!=PROFILE_PIN or digest({k:v for k,v in profile.items() if k!='sha256'})!=PROFILE_PIN):
        raise ValueError('estimated profile pin differs')
    selections,sea,manifest=parent_policy.build_policy(parent,water)
    if manifest['sha256']!=PARENT_POLICY_PIN:raise ValueError('fixed parent policy differs')
    counts=Counter()
    for r in profile['cells']:
        status=r['status'];pos=(r['column'],r['row']);x,y=pos;cell=water['cells'][y][x];hist=cell['worldcover_samples']
        if status=='new_low_water_mixed_source':
            klass=cell['source_fuel_class']
            if (pos in selections or type(klass) is not int or klass not in parent_policy.fuel_data.FUEL_MAPPING
                    or r['source_fuel_class']!=klass or cell['fuel_status']!='source'):
                raise ValueError('low-water retained source class differs')
            built=.4 if hist.get('50',0) else None
            selections[pos]={'vegetation_class':klass,'vegetation_initial_fuel':.8,'built_initial_fuel':built,
              'crossing_openness':.9,'bare_assumption':'synthetic_inert','water_assumption':'synthetic_inert',
              'vegetation_absence':'unknown','built_absence':'unknown' if built is not None else 'synthetic_absent',
              'transfer':copy.deepcopy(parent_policy.TRANSFER)}
            counts['low_water_source']+=1
        elif status.startswith('new_low_water_mixed_fuel_'):
            klass,fuel,kind=parent_policy._estimate(r)
            built=.4 if hist.get('50',0) else None
            selections[pos]={'vegetation_class':klass,'vegetation_initial_fuel':fuel,'built_initial_fuel':built,
              'crossing_openness':.9,'bare_assumption':'synthetic_inert','water_assumption':'synthetic_inert',
              'vegetation_absence':'unknown','built_absence':'unknown' if built is not None else 'synthetic_absent',
              'transfer':copy.deepcopy(parent_policy.TRANSFER)}
            counts['low_water_estimated']+=1
        elif status in ('new_built_watercourse_source_sensitivity','new_built_watercourse_estimated_sensitivity'):
            if pos in selections:raise ValueError('extension replaced prior selection')
            if status=='new_built_watercourse_source_sensitivity':
                klass=cell['source_fuel_class'];fuel=.8
                if klass is None:raise ValueError('source class missing')
                counts['built_source']+=1
            else:
                if cell['source_fuel_class'] is not None:raise ValueError('estimate replaces source class')
                klass,fuel,kind=parent_policy._estimate(r);counts['built_estimated_'+kind]+=1
            selections[pos]={'vegetation_class':klass,'vegetation_initial_fuel':fuel,'built_initial_fuel':.4,
              'crossing_openness':.9,'bare_assumption':'synthetic_inert','water_assumption':'synthetic_inert',
              'vegetation_absence':'unknown','built_absence':'unknown','transfer':copy.deepcopy(parent_policy.TRANSFER)}
    payload=[{'column':x,'row':y,'assumptions':selections[(x,y)]} for x,y in sorted(selections)]
    result={'version':VERSION,'profile_sha256':PROFILE_PIN,'parent_policy_sha256':PARENT_POLICY_PIN,
      'policy_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'selection_sha256':digest(payload),
      'counts':dict(sorted(counts.items())),
      'assumptions':'source retained with vegetation fuel .8 including low-water; source-neighbor nominal or lowest tied class at factor .9; built fuel .4, crossing .9, bare/water inert, transfer .2; all synthetic'}
    result['sha256']=digest(result);return selections,sea,result
