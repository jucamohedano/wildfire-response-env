"""Build prepared Spain development data locally; never uploads or writes a README."""
import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
import shutil
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq

from wildfire_core.catalog_lifecycle import registry, active_day_case_ids
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.incident_day.data import _check_hash
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN

VERSION = 'wildfire-response-spain-runtime-v1'
REPO_ID = 'jucamohedano/wildfire-response-spain'
ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_EVIDENCE_PINS = {
    'provenance.json': 'bd8da1253349748b15975e94334947ad28807b0b8b91beaefb4e31767971965b',
    'incident_claims.jsonl': 'c6d4840523bc43f02cd9e6475d4e5584cb6935c497b544f5a3069b5ca2b95c13',
    'municipal_population_2021.csv': 'd490bd51b3ef4894e2e92c075d3bd5e29afc8acc0ff60e6d97e497dded077ebb',
    'source_registry.json': 'a10ad79c735952b5ae8097907a2ccb4882e08172262f96875830594761114951',
}
FORBIDDEN_KEYS = {'authorization', 'cookie', 'set-cookie', 'access_token', 'api_key', 'password',
                  'private_audit', 'evaluator_state', 'checkpoint', 'future_events', 'scenario_factors',
                  'truth_labels', 'generation_annotations'}
REPORT_FORBIDDEN_KEYS = {'action_arguments', 'action_args',
                  'arguments', 'journal', 'journals', 'trajectory', 'trajectories', 'secret', 'secrets',
                  'token', 'credentials', 'audit', 'audits', 'checkpoints', 'private_audits',
                  'audit_log', 'audit_logs', 'action', 'actions', 'calls', 'call_journal',
                  'call_journals', 'per_call_journal', 'per_call_journals', 'access_key', 'client_secret'}
VISUAL_REVIEW_SHA256 = 'fbbd4c5936f68222333e6e14601d7057e79fac13ecfa794138cb8cf51066ac98'
MATRIX_POLICIES = frozenset(('passive', 'dispatcher', 'conservative', 'stress'))
VALIDATION_PIN_PATHS = {
    'action_checker_sha256': 'eval/incident_day_action_checks.py',
    'profile_sha256': 'data/incident_day_v2/plausibility/action-bounds-v1.json',
}
BLOCKING_FINDINGS = frozenset(('contradiction', 'blocking', 'blocked', 'error', 'failed', 'failure'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def no_private_fields(value, *, report=False):
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS | REPORT_FORBIDDEN_KEYS if report else FORBIDDEN_KEYS
        if forbidden.intersection(str(k).lower() for k in value):
            raise ValueError('Private or secret field refused in data asset')
        for val in value.values(): no_private_fields(val, report=report)
    elif isinstance(value, list):
        for val in value: no_private_fields(val, report=report)


def write_json(path, value):
    no_private_fields(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b'\n')


def table(path, rows, schema=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression='zstd')


def validate_historical_evidence(evidence):
    evidence = Path(evidence)
    for name, expected in HISTORICAL_EVIDENCE_PINS.items():
        if hashlib.sha256((evidence/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Historical evidence differs from reviewed immutable snapshot')
    provenance = json.loads((evidence/'provenance.json').read_text())
    found = {}
    for item in provenance['outputs']:
        name = Path(item['path']).name
        if name in HISTORICAL_EVIDENCE_PINS and name != 'provenance.json':
            if name in found or item['sha256'] != HISTORICAL_EVIDENCE_PINS[name]:
                raise ValueError('Historical evidence output pin differs')
            found[name] = item
    if set(found) != set(HISTORICAL_EVIDENCE_PINS) - {'provenance.json'}:
        raise ValueError('Historical evidence required output entries are missing')
    return provenance


def validation_excerpt(path, runtimes, tasks, lifecycle_sha256, implementation_sha256, *, include_cells=False):
    raw = Path(path).read_bytes()
    report = json.loads(raw)
    no_private_fields(report, report=True)
    if (report.get('version') != 'wildfire-runtime-matrix-v1' or
            report.get('implementation_sha256') != implementation_sha256 or
            report.get('lifecycle_sha256') != lifecycle_sha256):
        raise ValueError('Runtime validation version or implementation/lifecycle pin differs')
    if report.get('status') != 'complete':
        raise ValueError('Runtime validation report is not complete')
    extra_pins = {}
    if 'validator_sha256' in report:
        # Import lazily: legacy exports do not depend on the optional validator.
        from eval.validate_incident_day_runtime import validator_digest
        try:
            expected_hash = validator_digest()
        except OSError as exc:
            raise ValueError('Cannot verify runtime validation validator_sha256') from exc
        if report['validator_sha256'] != expected_hash:
            raise ValueError('Runtime validation validator_sha256 differs')
        extra_pins['validator_sha256'] = expected_hash
    for key, relative in VALIDATION_PIN_PATHS.items():
        if key in report:
            try:
                expected_hash = hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
            except OSError as exc:
                raise ValueError(f'Cannot verify runtime validation {key}') from exc
            if report[key] != expected_hash:
                raise ValueError(f'Runtime validation {key} differs')
            extra_pins[key] = expected_hash
    expected = report.get('expected_cells')
    cells = report.get('cells')
    if not isinstance(expected, list) or not isinstance(cells, list):
        raise ValueError('Runtime validation matrix lists are required')

    def identity(item):
        if not isinstance(item, dict):
            raise ValueError('Invalid runtime validation cell')
        values = tuple(item.get(key) for key in ('runtime_id', 'case_id', 'policy'))
        if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', value) for value in values):
            raise ValueError('Invalid runtime validation identity')
        return values

    declared = [identity(item) for item in expected]
    policies = {key[2] for key in declared}
    if 'policies' in report:
        listed = report['policies']
        if (not isinstance(listed, list) or len(listed) != len(MATRIX_POLICIES)
                or any(not isinstance(policy, str) for policy in listed) or set(listed) != MATRIX_POLICIES):
            raise ValueError('Runtime validation declared policies differ')
    pairs = {(item['runtime_id'], item['task_id']) for item in tasks}
    required = {(runtime, case, policy) for runtime, case in pairs for policy in MATRIX_POLICIES}
    if len(pairs) != 12 or policies != MATRIX_POLICIES or len(declared) != len(set(declared)) or set(declared) != required:
        raise ValueError('Runtime validation expected cells do not cover active cases and policies')
    actual = [identity(item) for item in cells]
    if len(actual) != len(set(actual)) or set(actual) != required:
        raise ValueError('Runtime validation cells missing, duplicate or unexpected')
    task_pins = {(item['runtime_id'], item['task_id']): item['scenario_sha256'] for item in tasks}
    for cell, (runtime, case, _) in zip(cells, actual):
        verification = cell.get('verification')
        pins = cell.get('pins')
        if (cell.get('status') != 'complete' or not isinstance(verification, dict) or
                any(verification.get(key) is not True for key in
                    ('passed', 'exact_replay', 'independent_reward_agreement'))):
            raise ValueError('Runtime validation cell is incomplete or failed verification')
        if not isinstance(pins, dict) or any(pins.get(key) != value for key, value in {
                'inputs_sha256': runtimes[runtime]['inputs_sha256'],
                'network_sha256': runtimes[runtime]['network_sha256'],
                'scenario_sha256': task_pins[(runtime, case)]}.items()):
            raise ValueError('Runtime validation cell source pins differ')
    def finding_counts(findings):
        if not isinstance(findings, list):
            raise ValueError('Runtime validation findings must be a list')
        categories = []
        for finding in findings:
            category = finding.get('category') if isinstance(finding, dict) else finding
            if not isinstance(category, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', category):
                raise ValueError('Invalid runtime finding category')
            if category in BLOCKING_FINDINGS:
                raise ValueError('Runtime validation contains blocking findings')
            categories.append(category)
        return Counter(categories)

    if 'runtime_findings' not in report:
        raise ValueError('Runtime validation findings list is required')
    global_findings = finding_counts(report['runtime_findings'])
    if 'findings' in report:
        global_findings.update(finding_counts(report['findings']))
    static_cases = report.get('static_cases', [])
    if not isinstance(static_cases, list) or any(not isinstance(item, dict) for item in static_cases):
        raise ValueError('Invalid runtime validation static findings')
    for static in static_cases:
        finding_counts(static.get('findings', []))
    cell_findings = Counter()
    review_rows = []
    for cell in cells:
        categories = Counter()
        for key in ('findings', 'runtime_findings'):
            if key in cell:
                categories.update(finding_counts(cell[key]))
        cell_findings.update(categories)
        if cell.get('minute') != 1440 or type(cell.get('runtime_contradictions')) is not int or cell['runtime_contradictions'] != 0:
            raise ValueError('Runtime validation cell has incomplete minutes or violations')
        coverage = cell.get('operation_coverage')
        durations = cell.get('durations')
        if not isinstance(coverage, dict) or not isinstance(durations, dict):
            raise ValueError('Runtime validation cell needs aggregate coverage and durations')
        safe_coverage = {}
        for name, item in coverage.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name)
                    or not isinstance(item, dict) or item.get('status') not in ('complete', 'unsupported', 'not_exercised', 'review')
                    or type(item.get('observed')) is not int or item['observed'] < 0):
                raise ValueError('Invalid aggregate operation coverage')
            safe_coverage[name] = {'status': item['status'], 'observed': item['observed']}
            if 'delivered_reports' in item:
                if type(item['delivered_reports']) is not int or item['delivered_reports'] < 0:
                    raise ValueError('Invalid aggregate delivered report count')
                safe_coverage[name]['delivered_reports'] = item['delivered_reports']
        safe_durations = {}
        for name, item in durations.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name)
                    or not isinstance(item, dict) or type(item.get('count')) is not int or item['count'] < 1):
                raise ValueError('Invalid aggregate duration')
            values = [item.get(k) for k in ('min_minutes', 'median_minutes', 'max_minutes')]
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values) or not values[0] <= values[1] <= values[2]:
                raise ValueError('Invalid aggregate duration statistics')
            safe_durations[name] = dict(count=item['count'], min_minutes=values[0], median_minutes=values[1], max_minutes=values[2])
        review_rows.append(dict(runtime_id=cell['runtime_id'], case_id=cell['case_id'], policy=cell['policy'],
            status=cell['status'], minute=cell['minute'], passed=cell['verification']['passed'],
            exact_replay=cell['verification']['exact_replay'],
            independent_reward_agreement=cell['verification']['independent_reward_agreement'],
            runtime_contradictions=cell['runtime_contradictions'], findings_json=canonical(dict(sorted(categories.items()))).decode(),
            operation_coverage_json=canonical(safe_coverage).decode(), durations_json=canonical(safe_durations).decode(),
            inputs_sha256=cell['pins']['inputs_sha256'], network_sha256=cell['pins']['network_sha256'],
            scenario_sha256=cell['pins']['scenario_sha256'], actor_input_eligible=False, training_eligible=False))
    excerpt = dict(version=report['version'], status='complete', source_report_sha256=hashlib.sha256(raw).hexdigest(),
        implementation_sha256=implementation_sha256, lifecycle_sha256=lifecycle_sha256,
        **extra_pins,
        runtimes={name: {key: runtime[key] for key in ('inputs_sha256', 'network_sha256')}
                  for name, runtime in sorted(runtimes.items())},
        policies=sorted(policies), active_cases=len(pairs), completed_cells=len(actual),
        verification=dict(passed=True, exact_replay=True, independent_reward_agreement=True),
        runtime_findings=dict(sorted(global_findings.items())),
        cell_findings=dict(sorted(cell_findings.items())))
    return (excerpt, review_rows) if include_cells else excerpt


def visual_review_excerpt(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != VISUAL_REVIEW_SHA256:
        raise ValueError('Visual land review differs from pinned source')
    review = json.loads(raw)
    no_private_fields(review)
    if (review['source_pins']['input_sha256'] != PACK_PIN or
            review['summary']['centres_reviewed'] != 27 or len(review['cells']) != 27 or
            any(review[key] is not False for key in
                ('runtime_changed', 'source_fuel_mask_changed', 'fuel_values_changed', 'censor_guards_removed'))):
        raise ValueError('Visual land review scope or source pins differ')
    return dict(source_record_sha256=VISUAL_REVIEW_SHA256, **{key: review[key] for key in
        ('version', 'review_date', 'scope', 'source', 'source_pins', 'method', 'summary',
         'runtime_changed', 'source_fuel_mask_changed', 'fuel_values_changed', 'censor_guards_removed', 'limitations')})


def build(output, *, real_inputs, network, weather, evidence, release_version='0.2.0',
          validation_report=None, visual_land_review=None):
    output, evidence = Path(output), Path(evidence)
    if output.exists(): raise ValueError('Use a fresh output directory')
    source = json.loads(Path(real_inputs).read_text())
    roads = json.loads(Path(network).read_text())
    if _check_hash(source, 'real input') != PACK_PIN or _check_hash(roads, 'road graph') != NETWORK_PIN:
        raise ValueError('Prepared runtime pins do not match current active source-map suite')
    weather_path = Path(weather)
    weather_pin = next(item['sha256'] for item in json.loads((ROOT/'data/malaga_2021/weather_normalization_v1.json').read_text())['output_files'] if item['path'].endswith('/weather.json'))
    if hashlib.sha256(weather_path.read_bytes()).hexdigest() != weather_pin:
        raise ValueError('Normalized weather differs from recorded source hash')
    weather = json.loads(weather_path.read_text())
    validate_historical_evidence(evidence)
    if len(weather['records']) != 384 or weather['status'] != 'source_clock_normalized':
        raise ValueError('Use the verified 384-row normalized weather document')
    fixture, fixture_roads = fixture_inputs()
    lifecycle = registry()
    if not isinstance(release_version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', release_version):
        raise ValueError('Invalid release version')
    runtimes = {}
    tasks, landscapes = [], []
    for identifier, inputs, graph in (('fixture', fixture, fixture_roads), ('es-ma-2021', source, roads)):
        ids = active_day_case_ids(inputs['sha256'], graph['sha256'])
        unapproved = identifier != 'fixture'
        runtimes[identifier] = dict(inputs=f'runtime/{identifier}/inputs.json',
            network=f'runtime/{identifier}/network.json', inputs_sha256=inputs['sha256'],
            network_sha256=graph['sha256'], allow_unapproved_data=unapproved, scenario_ids=list(ids))
        env = IncidentDayEnv(inputs, graph, expected_inputs_sha256=inputs['sha256'],
            expected_network_sha256=graph['sha256'], allow_unapproved_data=unapproved)
        for index, case in enumerate(ids):
            observation, _ = env.reset(case)
            tasks.append(dict(task_id=case, runtime_id=identifier, split='development',
                related_exercise_group=f'day-development-{index:03}',
                scenario_sha256=env._scenario['sha256'], horizon_minutes=1440,
                inputs_ref=runtimes[identifier]['inputs'], network_ref=runtimes[identifier]['network'],
                synthetic=True, historical_replay=False, training_eligible=False))
        landscapes.append(dict(runtime_id=identifier, region_id='synthetic-fixture' if identifier=='fixture' else 'es-ma-2021',
            width_cells=inputs['grid']['width'], height_cells=inputs['grid']['height'],
            cell_metres=inputs['grid']['cell_km']*1000, crs=inputs['grid']['crs'],
            inputs_ref=runtimes[identifier]['inputs'], inputs_sha256=inputs['sha256'],
            network_ref=runtimes[identifier]['network'], network_sha256=graph['sha256'],
            synthetic_geography=identifier=='fixture', real_data_approved=False))
    implementation = implementation_digest()
    validation, validation_cells = validation_excerpt(validation_report, runtimes, tasks, lifecycle['sha256'], implementation, include_cells=True) if validation_report else (None, None)
    visual = visual_review_excerpt(visual_land_review) if visual_land_review else None
    output.mkdir(parents=True)
    for identifier, inputs, graph in (('fixture', fixture, fixture_roads), ('es-ma-2021', source, roads)):
        write_json(output/'runtime'/identifier/'inputs.json', inputs)
        write_json(output/'runtime'/identifier/'network.json', graph)
    if validation is not None:
        write_json(output/'validation/runtime_matrix.json', validation)
        table(output/'validation/cells.parquet', validation_cells)
    if visual is not None:
        write_json(output/'provenance/visual_land_review.json', visual)
    table(output/'tasks/development.parquet', tasks)
    table(output/'landscapes/records.parquet', landscapes)
    fields = [('station_id',pa.string()),('source_row',pa.int64()),('source_date',pa.string()),('source_hour',pa.string()),
        ('source_timestamp_utc',pa.string()),('temperature_c',pa.float64()),('relative_humidity_percent',pa.float64()),
        ('wind_speed_m_s',pa.float64()),('wind_direction_degrees',pa.float64()),('precipitation_mm',pa.float64()),
        ('received_at_utc',pa.string()),('actor_input_eligible',pa.bool_()),('measurement_time_semantics',pa.string())]
    rows=[]
    for row in weather['records']:
        value={key:row.get(key) for key,_ in fields}
        value.update(station_id='MA04', source_timestamp_utc=row['observed_at_utc'], received_at_utc=None,
                     actor_input_eligible=False)
        rows.append(value)
    if len({r['source_timestamp_utc'] for r in rows})!=384: raise ValueError('Weather timestamps must be unique')
    table(output/'weather/records.parquet', rows, pa.schema(fields))
    claims=[json.loads(line) for line in (evidence/'incident_claims.jsonl').read_text().splitlines() if line.strip()]
    if len(claims)!=7: raise ValueError('Historical claims snapshot count differs')
    table(output/'incident_claims/records.parquet',[dict(record_json=json.dumps(row,sort_keys=True),
        actor_input_eligible=False, source_snapshot='spain-sierra-bermeja-2021-v0.1.0') for row in claims])
    with (evidence/'municipal_population_2021.csv').open() as stream: population=list(csv.DictReader(stream))
    if len(population)!=7: raise ValueError('Population snapshot count differs')
    for row in population: row['population_total'] = int(row['population_total'])
    table(output/'municipal_population/records.parquet',population)
    write_json(output/'provenance/lifecycle.json',lifecycle)
    for name in ('source_registry.json','provenance.json'):
        write_json(output/'provenance'/('historical_'+name),json.loads((evidence/name).read_text()))
    write_json(output/'provenance/weather.json',{key:weather[key] for key in
        ('version','status','station','role','checks','limitations')})
    for file in ('weather_normalization_v1.json','terrain_closeout_20261029.json'):
        record_path = ROOT/'data/malaga_2021'/file
        value = json.loads(record_path.read_text())
        if file.startswith('terrain_closeout'):
            value['retained_evidence'].pop('persistent_root', None)
            value['distribution_excerpt'] = dict(source_record_sha256=hashlib.sha256(record_path.read_bytes()).hexdigest(),
                transformation='Removed machine-specific persistent_root only; scientific limits and evidence hashes retained.')
        write_json(output/'provenance'/file,value)
    land_scope = ('The cached land-cover probe left 27 centres unclassified; a separate review of official imagery '
        'visually estimates that all 27 centres are on land. Whole-cell water fractions and historical fuel remain '
        'unverified; runtime fuel, masks and censor guards are unchanged.' if visual is not None else
        'Retained land-cover probe informs categorical assumptions;27 unresolved land/water centres remain censored near fire.')
    write_json(output/'provenance/sources.json',dict(
        historical_context='Sierra Bermeja, Malaga, 8 September 2021. Other regions can be added in new versioned releases.',
        current_scope='Two related development exercise representations, not 12 independent historical incidents; no train/validation/test split or model demonstrations.',
        sources=[
          dict(id='ign-mdt05',url='https://pnoa.ign.es/web/portal/pnoa-lidar/productos-a-descarga',attribution='IGN/CNIG, PNOA-LiDAR MDT05',scope='Nominal 5m product sampled at25m and aggregated into500m cells; exact source edition/native void metadata unresolved.'),
          dict(id='effis-fuels',url='https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fuels',attribution='EFFIS - European Forest Fire Information System - European Fuel Map,2017, European Commission',scope='NFFL categorical fuel map; survey epoch and2021 applicability unverified. Eighty estimated cells remain source-unknown.'),
          dict(id='siose-context',url='https://www.juntadeandalucia.es/medioambiente/portal/acceso-rediam',attribution='Junta de Andalucia, REDIAM/SIOSE2020',scope=land_scope),
          dict(id='osm-roads',url='https://www.openstreetmap.org/copyright',attribution='OpenStreetMap contributors',license='ODbL-1.0',scope='2026 extract; topology is not verified2021 road access. Class speeds and truck suitability are exercise assumptions.'),
          dict(id='weather',url='https://servicio.mapa.gob.es/siarweb/consultaDatos/inicio',attribution='Informacion obtenida del Portal IFAPA; IFAPA/RIA, MAPA SiAR',scope='Estepona MA04 coastal station;384 half-hour readings. Normalized source clocks, unknown receipt/interval semantics; not fire-front wind or engine forcing.'),
          dict(id='cems',url='https://mapping.emergency.copernicus.eu/activations/EMSR545/',attribution='Copernicus Emergency Management Service',scope='Retrospective historical context; no original satellite images/vector packages included.'),
          dict(id='ine',url='https://www.ine.es/jaxiT3/Tabla.htm?t=2882',attribution='Elaboracion propia con datos extraidos del sitio web del INE',scope='2021 municipal population totals; not people present or exposed at event time.')],
        redistribution='Private owner-authorized research release. Mixed source terms; no blanket code/data license or public-release clearance is asserted. Original reports, images, rasters, provider logs, evaluator audits, checkpoints and model weights are excluded.'))
    inventory=[]
    for path in sorted(output.rglob('*')):
        if path.is_file():
            raw=path.read_bytes()
            inventory.append(dict(path=path.relative_to(output).as_posix(),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
    manifest=dict(version=VERSION,release_version=release_version,dataset_repo=REPO_ID,
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        engine_implementation_sha256=implementation,lifecycle_sha256=lifecycle['sha256'],
        runtimes=runtimes,files=inventory,
        tables=dict(tasks='tasks/development.parquet',landscapes='landscapes/records.parquet',weather='weather/records.parquet',
            incident_claims='incident_claims/records.parquet',municipal_population='municipal_population/records.parquet'),
        counts=dict(tasks=12,landscapes=2,weather=384,incident_claims=7,municipal_population=7),
        source_snapshot_pins=dict(normalized_weather_sha256=weather_pin,historical_evidence_revision='3b87e651b4695c49ea5d85bec18a2b012ff49835'),
        training_corpus_available=False,publication_status='private_research_release',
        dataset_card_status=('owner_requested_private_release' if release_version != '0.2.0' and validation is not None and visual is not None
                             else 'awaiting_owner_readme_consent'))
    if validation is not None:
        manifest['validation_report'] = 'validation/runtime_matrix.json'
        manifest['tables']['validation_cells'] = 'validation/cells.parquet'
        manifest['counts']['validation_cells'] = len(validation_cells)
    if visual is not None: manifest['visual_land_review'] = 'provenance/visual_land_review.json'
    write_json(output/'manifest.json',manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('output','real-inputs','network','weather','evidence'): parser.add_argument('--'+field,required=True)
    parser.add_argument('--release-version', default='0.2.0')
    parser.add_argument('--validation-report')
    parser.add_argument('--visual-land-review')
    args=parser.parse_args()
    result=build(args.output,real_inputs=args.real_inputs,network=args.network,weather=args.weather,evidence=args.evidence,
        release_version=args.release_version, validation_report=args.validation_report, visual_land_review=args.visual_land_review)
    print(json.dumps(dict(status='built',counts=result['counts'],files=len(result['files']),total_bytes=sum(x['bytes'] for x in result['files']))))


if __name__=='__main__':main()
