"""Private, uncalibrated basin and tied-fuel sensitivity input construction.

This evaluator-only layer keeps the pinned source evidence unchanged. It is
not a measurement, a new source map, an access claim, or a release input.
"""
import copy
from collections import Counter
from pathlib import Path

import adapter_v1 as api
from eval import mixed_estimated_fuel_v1 as evidence
from wildfire_expanded_incident_v1 import estimated_fuel_v1 as estimated
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED, _file_sha

TARGET_PIN = '3e35ce8aeda02847b48ece49a811b1cc697d315dfbc9ecd1b0714ff65b6daf63'
BASE_PIN = '5ea3d8ffdf47e1854272a1bf6af65f6d51e12c3ca066e344720e6d4f05cb1266'
EXTENDED_PIN = '8c3f4114d0d133f769fcd87faf057b9ff8027e67d319665572d4e31fbb7542b0'
VERSION = 'wildfire-private-basin-tied-fuel-sensitivity-input-v1'


def check_basin(cell, polygons, dera_water):
    """Screen mapped inland basins; area is evidence, never a fuel factor."""
    from shapely.ops import unary_union
    if not polygons or any(p.intersects(cell) for p in dera_water):
        raise ValueError('missing basin or conflicting DERA water area')
    for geometry, tags in polygons:
        if (tags.get('landuse') != 'basin' or geometry.geom_type != 'Polygon'
                or not geometry.is_valid or not geometry.is_simple or geometry.interiors
                or not cell.contains(geometry) or cell.boundary.intersects(geometry)):
            raise ValueError('basin geometry or metadata not supported')
    geoms = [p for p, _ in polygons]
    if any(a.intersects(b) for i,a in enumerate(geoms) for b in geoms[i+1:]):
        raise ValueError('overlapping basin polygons')
    fraction = unary_union(geoms).intersection(cell).area / cell.area
    if not 0 < fraction < .05:
        raise ValueError('mapped basin area outside sensitivity screen')
    return fraction


def prepare(strict, water, v3, v4, parent, catalog, base_hypothesis, expanded, targets_path, *,
            fuel_class, **options):
    targets=evidence.read_pinned_json(targets_path,TARGET_PIN,'private sensitivity targets')
    if (targets.get('version')!='private-frontier-sensitivity-targets-v1'
            or targets['alternative_classes']!=[4,10] or fuel_class not in targets['alternative_classes']
            or targets['mixed_fuel_factor']!=.9 or targets['crossing_proxy']!=.9):
        raise ValueError('unsupported explicit sensitivity assumption')
    if (expanded.get('sha256')!=EXTENDED_PIN or
            api._hash({k:v for k,v in expanded.items() if k!='sha256'})!=EXTENDED_PIN or
            expanded.get('version')!='mixed-estimated-fuel-hypotheses-v1'):
        raise ValueError('expanded source reconstruction pin differs')
    # Reuse the independently gated source reconstruction and the four existing
    # estimates; this process-local pin does not change the shared host.
    estimated.HYPOTHESIS_SHA256=BASE_PIN
    base=estimated.prepare_estimated_inputs(strict,water,v3,v4,parent,catalog,base_hypothesis,
        fuel_scenario='nominal',opt_in_estimated_fuel=True,**options)
    by_pos={(r['column'],r['row']):r for r in expanded['cells']}
    if len(by_pos)!=len(expanded['cells']):raise ValueError('duplicate evidence cell')
    extra=[]
    for key in ('estimated_cell','ambiguous_cell'):
        x,y=targets[key]; record=by_pos[(x,y)];h=record['fuel_hypothesis']
        votes=Counter(s['source_class'] for s in record['neighbor_support'])
        cell=water['cells'][y][x]; original=strict['cells'][y][x]
        if (original['surface']!='land' or original['source_fuel_class'] is not None
                or cell['surface']!='unknown' or cell['source_fuel_class'] is not None
                or cell['fuel_status']!='unknown' or base.base.contract.elevation[y][x] is None
                or record['terrain_candidate']!=base.base.contract.elevation[y][x]
                or record['surface_evidence']['osm_water_area_conflicts']!=0
                or not record['surface_evidence']['watercourse_evidence_present']):
            raise ValueError('extra estimate fails source evidence')
        if key=='estimated_cell':
            if h['status']!='estimated_hypothesis':raise ValueError('unsupported spatial estimate')
            klass=h['scenarios']['nominal']['assumed_fuel_class']
        else:
            if h.get('reason')!='ambiguous_or_tied_source_vote' or votes[4]<3 or votes[10]<3 or votes[4]!=votes[10]:
                raise ValueError('tied alternatives lack documented source support')
            klass=fuel_class
        extra.append((x,y,klass,.9,.9))
    x,y=targets['water_area_cell']; cell=water['cells'][y][x]; old=strict['cells'][y][x]
    conflicts=cell['osm_water_conflicts']; refs=[z for z in conflicts if z['kind'] in ('water_area','water_area_relation')]
    if (old['surface']!='land' or cell['surface']!='unknown' or cell['fuel_status']!='source'
            or cell['source_fuel_class']!=old['source_fuel_class'] or old['source_fuel_class'] is None
            or base.base.contract.elevation[y][x] is None
            or not evidence.source.vegetation_supported(cell['worldcover_samples'])
            or not any(z['kind']=='watercourse' for z in conflicts)):
        raise ValueError('basin cell lacks mapped inland land and fuel evidence')
    features,_,metadata=evidence.load_pinned_osm_water(options['osm_manifest_path'],options['osm_samples_path'])
    from shapely.geometry import box
    g=water['grid']; west,north=g['transform'][2],g['transform'][5]
    square=box(west+x*500,north-(y+1)*500,west+(x+1)*500,north-y*500)
    hits=[(ident,kind,form,geom) for ident,kind,form,geom in features
          if kind in ('water_area','water_area_relation') and geom.intersects(square)]
    if {(a,b,c) for a,b,c,_ in hits}!={(r['way_id'],r['kind'],r['geometry']) for r in refs}:
        raise ValueError('basin conflict evidence differs from pinned features')
    dera=evidence.read_pinned_collection(options['water_path'],evidence.source.PINS['water'],94,'DERA water')
    fraction=check_basin(square,[(geom,metadata[ident]['tags']) for ident,_,_,geom in hits],dera)
    contract=base.base.contract
    matrices={key:[list(row) for row in getattr(contract,key)] for key in ('surface','fuel','elevation','access')}
    if matrices['surface'][y][x]!='unknown' or matrices['fuel'][y][x]!=old['source_fuel_class']:
        raise ValueError('basin source contract differs')
    matrices['surface'][y][x]='mixed'
    doc=dict(version=api._contract_module.VERSION,grid=dict(width=contract.width,height=contract.height,
        crs=contract.crs,transform=list(contract.transform)),domain_mask=[list(row) for row in contract.domain],
        source_pins=dict(contract.source_pins),**matrices)
    changed=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    factors=base.base.factors+((x,y,.9,.9),)
    identity=dict(base.base.identity);identity.update(sensitivity_version=VERSION,
        sensitivity_source_sha256=_file_sha(__file__),sensitivity_targets_sha256=TARGET_PIN,
        source_reconstruction_sha256=EXTENDED_PIN,basin_mapped_fraction=fraction,
        input_sha256=changed.document_sha256)
    mixed=MixedInputsV2(changed,factors,tuple(sorted(identity.items())),_VERIFIED)
    estimates=base.estimates+tuple(extra)
    source_mask=base.source_mask
    mask=[list(row) for row in base.estimated_mask]
    for a,b,*_ in extra:mask[b][a]=True
    mask=tuple(tuple(row) for row in mask)
    ident=dict(base.identity);ident.update(version=VERSION,base=identity,fuel_class_scenario=fuel_class,
        target_pin=TARGET_PIN,expanded_pin=EXTENDED_PIN,estimated_mask_sha256=api._hash(mask),
        sensitivity_source_sha256=_file_sha(__file__))
    return estimated.EstimatedInputsV1(mixed,estimates,source_mask,mask,tuple(sorted(ident.items())),estimated._VERIFIED_ESTIMATE)
