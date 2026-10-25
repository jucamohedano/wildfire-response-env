"""Build a portable measured/derived/synthetic provenance reference from local audits."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


VERSION = 'wildfire-data-provenance-v1'
PROFILE_VERSION = 'wildfire-synthetic-runtime-reference-v1'
CLASSIFICATIONS = {'measured', 'derived', 'retrospective', 'synthetic', 'mixed'}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def fingerprint(value):
    return hashlib.sha256(encoded({key: item for key, item in value.items() if key != 'sha256'})).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def metadata(root, relative):
    path = Path(root) / relative
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError('Metadata path or size is invalid')
    return json.loads(path.read_bytes(), object_pairs_hook=_unique)


def artifact(root, relative, expected=None):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Provenance paths must be project-relative')
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError('Artifact escapes its project root')
    result = dict(path=relative.as_posix(), bytes=None, sha256=None, integrity='not_cached_locally')
    if not path.is_file():
        if expected:
            result.update(bytes=expected.get('bytes'), sha256=expected.get('sha256'),
                          integrity='declared_hash_only_not_cached')
        return result
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    size = path.stat().st_size
    if expected and (size != expected['bytes'] or checksum != expected['sha256']):
        raise ValueError('Artifact differs from its pinned metadata: ' + relative.as_posix())
    result.update(bytes=size, sha256=checksum,
                  integrity='pinned_match' if expected else 'locally_hashed_not_source_authenticated')
    return result


def synthetic_reference(root, episode_path):
    from wildfire_core.malaga import CASE_IDS, MalagaDevelopmentEnv, load_pack
    from envs.wildfire_response.regional.malaga.tasks import get_task

    pack = load_pack(Path(root) / episode_path)
    environment = MalagaDevelopmentEnv(pack, allow_synthetic_conditions=True)
    observation, _ = environment.reset()
    parameters = environment._core.pack['assumptions']
    variants = []
    for identifier in CASE_IDS:
        scenario = get_task(pack, identifier)['scenario']
        variants.append(dict(case_id=identifier, seed=scenario['seed'],
            wind_relative_index=scenario['wind'], wind_events=[event for event in scenario['events'] if event['kind'] == 'wind'],
            protection_configuration=scenario['protection'], transport_configuration=scenario['transport']))
    profile = dict(version=PROFILE_VERSION, evidence_class='synthetic',
        role='documentation_of_existing_exercise_initialization', actor_direct_access=False,
        applied_to_current_runtime=True, historical_truth=False,
        generation='Fixed versioned exercise rules and pinned seeds, not sampled or reconstructed historical records',
        episode_pack_sha256=pack['sha256'],
        readiness=dict(evidence_class='synthetic', units=observation['resources'],
            aircraft_operable_at_reset=observation['aircraft_operable'],
            limitation='Legacy resource/base IDs are fictional compatibility handles, not historical Malaga readiness'),
        population=dict(evidence_class='synthetic', counts=parameters['synthetic_population_counts'],
                        movement_speed_cells_per_model_minute=parameters['population_speed_cells_per_tick'],
                        limitation='Not municipal residents present or measured compliance'),
        communication=dict(evidence_class='synthetic', model_minutes_per_tick=1,
            base_request_delay_model_minutes=1, readback_correction_delay_model_minutes=1,
            protection_ack_after_receipt_model_minutes=1,
            protection_execution_after_receipt_model_minutes=2, in_person_service_model_minutes=4,
            offer_ttl_model_minutes=parameters['offer_ttl_minutes'],
            limitation='Separate send, receipt, acknowledgement and effects; not observed historical delays'),
        transit=dict(evidence_class='synthetic', base_component_model_minutes=pack['assumed_ground_transit_minutes'],
            derivation='Rounded-up half of historical detection-to-first-ground-arrival duration; an assumed decomposition, not road travel'),
        fuel=dict(evidence_class='synthetic',
            initial_dimensionless_availability=environment._core.sim.cells[0][0]['initial_fuel'],
            physical_moisture_percent=None, physical_moisture_model_implemented=False,
            limitation='Existing kernel has normalized fuel, not measured moisture; do not invent a percentage'),
        fire_and_suppression=dict(evidence_class='synthetic',
            spread_interval_multiplier=parameters['spread_interval_multiplier'],
            suppression_probability=parameters['suppression_probability'],
            aircraft_grounding_relative_wind_index=parameters['aircraft_grounding_wind_index'],
            limitation='Uncalibrated exercise effects, not physical prediction or aviation operating limits'),
        variants=variants,
        source_code=[artifact(root, name) for name in (
            'envs/wildfire_response/core/src/envs/wildfire_response/data/huelva_regional_v2.json',
            'envs/wildfire_response/core/src/envs/wildfire_response/regional/malaga/pack.py',
            'envs/wildfire_response/core/src/envs/wildfire_response/regional/malaga/tasks.py',
            'envs/wildfire_response/core/src/envs/wildfire_response/regional/protection/tasks.py',
            'envs/wildfire_response/core/src/envs/wildfire_response/regional/protection/protocol.py',
            'envs/wildfire_response/core/src/envs/wildfire_response/regional/fire.py')])
    profile['sha256'] = fingerprint(profile)
    return profile


def _entry(root, identifier, classification, filename, *, urls=(), units=None, limitations=()):
    relative = 'data/malaga_2021/' + filename
    value = metadata(root, relative)
    return dict(id=identifier, evidence_class=classification,
                status=value.get('status', 'metadata_recorded') if value else 'not_prepared',
                source_urls=list(urls), metadata=artifact(root, relative), raw_artifacts=[],
                derived_artifacts=[], units_crs=units or {}, transformations=[],
                used_by_current_runtime=False, actor_input_eligible=False,
                source_publication_or_receipt_time_verified=False,
                limitations=list(limitations)), value


def build_document(root, snapshot_date, profile):
    date.fromisoformat(snapshot_date)
    root = Path(root)
    if (profile.get('version') != PROFILE_VERSION or profile.get('evidence_class') != 'synthetic'
            or profile.get('historical_truth') is not False or profile.get('sha256') != fingerprint(profile)):
        raise ValueError('Invalid generated runtime reference')
    entries = []
    weather, value = _entry(root, 'weather-measured', 'measured', 'halfhour_weather_v1.json',
        urls=('https://servicio.mapa.gob.es/siarweb/consultaDatos/inicio',),
        units=dict(temperature='C', humidity='percent', wind_speed='m/s', direction='degrees_source_convention_unverified', precipitation='mm'),
        limitations=('MA04 Estepona is coastal/agricultural at185m, not measured fire-front weather.',
                     'Network documentation describes2m wind measurements; station-specific2021 setup is unverified.',
                     'Observed time is not publication/receipt time. No gusts or engine-wind conversion inferred.'))
    if value:
        weather['raw_artifacts'] = [artifact(root, item['path'], item) for item in value['files']]
        weather['observation_period'] = dict(requested_dates=value['requested_dates'], source_rows=value['records'],
            source_clock='UTC per official manual', unresolved_source_midnight_labels=value['ambiguous_24h_rows'])
    entries.append(weather)
    for identifier, filename, code, classification in (
        ('weather-normalized', 'weather_normalization_v1.json', 'eval/normalize_malaga_weather.py', 'derived'),
        ('landscape-prepared', 'landscape_preparation_v1.json', 'eval/prepare_malaga_landscape.py', 'derived')):
        entry, value = _entry(root, identifier, classification, filename,
            limitations=('Preparation does not activate this data in the frozen/synthetic runtime.',))
        if value:
            for key, destination in (('input_files', 'raw_artifacts'), ('output_files', 'derived_artifacts')):
                entry[destination] = [artifact(root, item['path'], item) for item in value.get(key, [])]
            entry['checks'] = value.get('checks', {})
            entry['limitations'].extend(value.get('limitations', []))
            if identifier == 'landscape-prepared':
                entry['units_crs'] = value.get('grid', {})
        entry['transformations'] = [artifact(root, code)]
        entries.append(entry)
    terrain, value = _entry(root, 'terrain-candidate', 'derived', 'terrain_fuel_search_v1.json',
        units=dict(horizontal_crs='EPSG:25830', output_cell_metres=25, vertical_units=None),
        limitations=('Producer collection2020-21 predates fire; publication follows it.',
                     'Only window/header hashes are pinned; the203GB upstream mosaic is not fully fingerprinted.',
                     'Extreme negative values survive declared nodata; candidate stays quarantined until justified review.'))
    if value:
        source = value['terrain']
        terrain['status'] = source['status']
        terrain['source_urls'] = [source['source_url'], source['producer_catalog'], source['collection_epoch_evidence']]
        terrain['raw_artifacts'] = [artifact(root, '.runtime/malaga-terrain-fuel-search/' + source['filename'], source)]
        terrain['quality'] = source['quality']
    entries.append(terrain)
    fuel, value = _entry(root, 'fuel-baseline', 'derived', 'terrain_fuel_search_v1.json',
        urls=('https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fuels',),
        units=dict(source_crs='EPSG:3035', source_cell_metres=250, values='NFFL_categorical_codes'),
        limitations=('Citation2017 is not a verified vegetation survey epoch; filename contains2000.',
                     'Fuel class is not fuel load/moisture or an engine fuel-availability value. Unknowns remain unknown.'))
    if value:
        source = value['fuel']
        fuel['status'] = source['status']
        fuel['raw_artifacts'] = [artifact(root, '.runtime/malaga-terrain-fuel-search/FuelMap_LAEA.zip', source['archive'])]
        fuel['derived_artifacts'] = [artifact(root, '.runtime/malaga-terrain-fuel-search/' + source['filename'], source)]
    entries.append(fuel)
    for identifier, filename, urls, limits in (
        ('incident-and-maps', 'sources.lock.json', ('https://mapping.emergency.copernicus.eu/activations/EMSR545/',),
         ('Retrospective fire extents and final outcomes are evaluator-only, never initial actor evidence.',)),
        ('operations', 'operations_search_v1.json', (),
         ('Assignments/plans are not initial readiness, actual dispatches or delivery acknowledgements.',)),
        ('population-reference', 'population_sources.json', ('https://www.ine.es/jaxiT3/Tabla.htm?t=2882',),
         ('January2021 municipal registered residents were published after the incident; not exposed population.',))):
        entry, value = _entry(root, identifier, 'retrospective', filename, urls=urls, limitations=limits)
        if value and identifier == 'operations':
            entry['record_count'] = len(value['records'])
            entry['plan_count'] = len(value['operational_plan_index'])
            entry['source_urls'] = sorted({row['source_url'] for row in value['records'] if row.get('source_url')})
            entry['status'] = 'partial_operations_no_initial_readiness_log'
        entries.append(entry)
    entries.append(dict(id='generated-exercise-inputs', evidence_class='synthetic',
        status='documented_existing_runtime_defaults', source_urls=[],
        metadata=artifact(root, 'data/malaga_2021/synthetic_runtime_inputs_v1.json',
                          dict(bytes=len(encoded(profile)), sha256=hashlib.sha256(encoded(profile)).hexdigest())),
        raw_artifacts=[], derived_artifacts=[], units_crs=dict(time='assumed_model_minutes', wind='relative_index'),
        transformations=profile['source_code'], used_by_current_runtime=True, actor_input_eligible=False,
        source_publication_or_receipt_time_verified=False,
        limitations=('Explicitly generated readiness, communications, populations, normalized fuel and fire effects; not historical observations.',)))
    entries.append(dict(id='episode-configuration', evidence_class='mixed',
        status='allowlisted_source_context_and_declared_synthetic_prior', source_urls=[],
        metadata=artifact(root, '.runtime/malaga-episode-v1/episode.json'),
        raw_artifacts=[], derived_artifacts=[], units_crs=dict(geography='longitude_latitude_degrees',
            clock='source_local_interpretation_not_verified_timezone', transit='assumed_model_minutes'),
        transformations=[artifact(root, 'eval/prepare_malaga_episode.py')],
        used_by_current_runtime=True, actor_input_eligible=False,
        source_publication_or_receipt_time_verified=False,
        field_lineage=dict(region_bbox_lon_lat='Mapping study-envelope bounds only, not burned perimeter',
            detection_local='Source detection clock, not known ignition',
            assumed_ground_transit_minutes='Generated assumed half-response decomposition, not measured travel'),
        limitations=('Retrospectively selected study region; measured terrain/fuel/weather are not activated.',)))
    integration, value = _entry(root, 'prepared-integration', 'mixed', 'runtime_integration_v1.json',
        limitations=('Separate opt-in mode; historical replay and slope effects remain disabled.',))
    if value:
        integration['opt_in_runtime_usage'] = value['runtime_usage']
        integration['raw_artifacts'] = [artifact(root, value['runtime_input']['path'], value['runtime_input'])]
        integration['derived_artifacts'] = [artifact(root, item['path'], item) for item in value['validation_artifacts']]
        integration['transformations'] = [artifact(root, item['path'], item) for item in value['implementation_files']]
        integration['limitations'].extend(value['limitations'])
    entries.append(integration)
    clean_terrain, clean_value = _entry(root, 'clean-terrain-candidate', 'derived', 'clean_elevation_v1.json',
        limitations=('Numerically screened only; incomplete vertical metadata still prevents slope activation.',))
    if clean_value:
        clean_terrain['source_urls'] = [clean_value['source_acquisition']['url'],
                                      *(item['url'] for item in clean_value['evidence'])]
        clean_terrain['raw_artifacts'] = [artifact(root, item['path'], item) for item in clean_value['input_files']]
        clean_terrain['derived_artifacts'] = [artifact(root, item['path'], item) for item in clean_value['output_files']]
        clean_terrain['checks'] = clean_value['checks']
        clean_terrain['limitations'].extend(clean_value['remaining_gates'])
    entries.append(clean_terrain)
    gradients, gradient_value = _entry(root, 'relative-gradient-validation', 'derived', 'terrain_metadata_validation_v2.json',
        limitations=('Restricted offline MDT05 analysis only; no older MDT25 relabelling or simulator slope activation.',))
    if gradient_value:
        gradients['source_urls'] = [item['url'] for item in gradient_value['acquisition']]
        gradients['raw_artifacts'] = [artifact(root, item['path'], item) for item in gradient_value['input_files']]
        gradients['derived_artifacts'] = [artifact(root, item['path'], item) for item in gradient_value['output_files']]
        gradients['transformations'] = [artifact(root, item['path'], item) for item in gradient_value['implementation_files']]
        gradients['checks'] = gradient_value['checks']
        gradients['gate_decision'] = gradient_value['gate_decision']
    entries.append(gradients)
    topographic, topographic_value = _entry(root, 'topographic-experiment', 'mixed', 'topographic_runtime_v1.json',
        limitations=('Explicit complete-support experimental mode, not a historical forecast or native void certification.',))
    if topographic_value:
        item = topographic_value['runtime_input']
        topographic['raw_artifacts'] = [artifact(root, item['path'], item)]
        topographic['derived_artifacts'] = [artifact(root, item['path'], item) for item in topographic_value['validation_artifacts']]
        topographic['transformations'] = [artifact(root, item['path'], item) for item in topographic_value['implementation_files']]
        topographic['opt_in_runtime_usage'] = topographic_value['runtime_usage']
        topographic['domain'] = topographic_value['domain']
    entries.append(topographic)
    document = dict(version=VERSION, snapshot_date=snapshot_date,
        role='operator_and_evaluator_reference_not_actor_observation',
        incident=dict(name='Sierra Bermeja, Malaga', event_year=2021),
        readiness=dict(current_declared_synthetic_mode_runs=True,
            normalized_weather_or_landscape_activated_in_legacy_mode=False,
            opt_in_prepared_mode_available=value is not None,
            opt_in_topographic_mode_available=topographic_value is not None, historical_replay_ready=False,
            real_world_operational_validity_established=False),
        evidence_policy=dict(measured='Source-reported measurements with their resolution/limitations retained',
            derived='Reproducible transformations with pinned inputs; no automatic truth upgrade',
            synthetic='Explicit exercise assumptions; never relabelled observed historical events',
            quarantined='Retained for audit, excluded from physics/actor inputs',
            integrity='Byte hashes detect changes, not source authenticity, causality or optimality'),
        entries=entries,
        lineage=[dict(inputs=['weather-measured'], output='weather-normalized'),
                 dict(inputs=['terrain-candidate', 'fuel-baseline'], output='landscape-prepared'),
                 dict(inputs=['incident-and-maps'], output='episode-configuration'),
                 dict(inputs=['episode-configuration'], output='generated-exercise-inputs'),
                 dict(inputs=['episode-configuration', 'weather-normalized', 'landscape-prepared'],
                      output='prepared-integration'),
                 dict(inputs=[], output='clean-terrain-candidate', prompted_by='terrain-candidate',
                      relation='Independent authoritative acquisition; no earlier contaminated values reused'),
                 dict(inputs=[], output='relative-gradient-validation',
                      relation='Separate documented MDT05 product sampled at25m; no MDT25 reclassification'),
                 dict(inputs=['prepared-integration', 'relative-gradient-validation'], output='topographic-experiment',
                      relation='Parent exercise inputs and same documented raw MDT05 source; mean slope angles are not used as edge grades')],
        synthetic_profile_sha256=profile['sha256'],
        open_gates=['Verify data clocks/quality masks before any separately versioned runtime adapter.',
                    'No historical initial fleet or full delivery logs found; retain declared generated states.',
                    'Physical fuel moisture is not implemented; no percentage is fabricated.',
                    'Station wind height/direction and terrain/fuel effects require calibration; no direct m/s-to-index mapping.',
                    'Source-specific rights and public redistribution remain uncleared.'],
        generator=artifact(root, 'eval/build_malaga_provenance.py'))
    document['sha256'] = fingerprint(document)
    validate_document(document)
    return document


def validate_document(document):
    if (document.get('version') != VERSION or document.get('sha256') != fingerprint(document)
            or document.get('role') != 'operator_and_evaluator_reference_not_actor_observation'):
        raise ValueError('Invalid provenance document/version/digest')
    if document['readiness']['historical_replay_ready'] is not False:
        raise ValueError('Historical readiness is not established')
    identifiers = set()
    for entry in document['entries']:
        if entry['id'] in identifiers or entry['evidence_class'] not in CLASSIFICATIONS:
            raise ValueError('Duplicate entry or unknown evidence classification')
        identifiers.add(entry['id'])
        if entry['actor_input_eligible'] is not False:
            raise ValueError('Provenance records are not actor inputs')
        if entry['used_by_current_runtime'] and entry['evidence_class'] not in ('synthetic', 'mixed'):
            raise ValueError('New measured data was not activated in this runtime')
        if entry['used_by_current_runtime'] and entry['evidence_class'] == 'mixed':
            allowed = {'region_bbox_lon_lat', 'detection_local', 'assumed_ground_transit_minutes'}
            if entry['id'] != 'episode-configuration' or set(entry.get('field_lineage', {})) != allowed:
                raise ValueError('Mixed runtime configuration needs the exact field-level origin map')
        for url in entry['source_urls']:
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Invalid public source URL')
        for item in [entry['metadata'], *entry['raw_artifacts'], *entry['derived_artifacts'], *entry['transformations']]:
            path = Path(item['path'])
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Nonportable provenance path')
            if item['sha256'] is not None and not re.fullmatch('[0-9a-f]{64}', item['sha256']):
                raise ValueError('Invalid artifact digest')
            if item['integrity'] == 'pinned_match' and (type(item['bytes']) is not int or item['bytes'] <= 0):
                raise ValueError('Pinned artifact needs a positive byte count')
    for edge in document['lineage']:
        if edge['output'] not in identifiers or not set(edge['inputs']) <= identifiers:
            raise ValueError('Unresolved lineage reference')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--episode-pack', default='.runtime/malaga-episode-v1/episode.json')
    parser.add_argument('--snapshot-date', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    profile = synthetic_reference(args.root, args.episode_pack)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'synthetic_runtime_inputs_v1.json').write_bytes(encoded(profile))
    document = build_document(args.root, args.snapshot_date, profile)
    (args.output / 'provenance.json').write_bytes(encoded(document))
    print(json.dumps(dict(entries=len(document['entries']), sha256=document['sha256'],
                         synthetic_profile_sha256=profile['sha256']), indent=2))


if __name__ == '__main__':
    main()
